#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PyQt6 GUI for the ZephyrSim simulator.

Public API is intentionally kept compatible with ZephyrSim.py:
- ConfigWindow
- MainWindow
- PollWindowEvents
- AddMsgToLogDisplay
- AddMsgToZephyrDisplay
- AddDebugMsg
- SetTmDir
"""

import ast
import configparser
import json
import os
import queue
import sys
from typing import Optional

import serial
import xmltodict
from PyQt6 import QtCore, QtGui, QtWidgets

import ZephyrSimResources_rc  # noqa: F401
import ZephyrSimUtils
from ConfigDialog import ConfigDialog
from MainWindowQt import MainWindowQt


ZephyrMessagesNoParams = [
    ("SW", "Send a Shutdown Warning"),
    ("SAck", "Send a Safety Ack"),
    ("RAAck", "Send a RAA Ack"),
    ("TMAck", "Send a TM Ack"),
]

ZephyrInstModes = [
    ("SB", "Standby Mode"),
    ("FL", "Flight Mode"),
    ("LP", "Low Power Mode"),
    ("SA", "Safety Mode"),
    ("EF", "End of Flight Mode"),
]

window_sizes = ["Small", "Medium", "Large"]
window_params = {
    "Small": {"font_size": 8, "width": 100, "height": 20},
    "Medium": {"font_size": 10, "width": 140, "height": 30},
    "Large": {"font_size": 12, "width": 180, "height": 40},
}
button_sizes = {"Small": (70, 28), "Medium": (84, 30), "Large": (96, 34)}

MAXLOGLINES = 500
KEEPLOGLINES = 250

message_display_types = ["TM", "TC", "IM", "TMAck", "GPS", "TCAck", "IMAck", "IMR"]
message_display_filters = {msg_type: True for msg_type in message_display_types}

main_window = None
qt_app = None
xml_queue = None
log_port = None
zephyr_port = None
cmd_filename = ""
instrument = ""
serial_suspended = False
active_config_set = None
window_size = "Medium"
_app_exit_requested = False


def _settings_path() -> str:
    return os.path.abspath(os.path.expanduser("~/ZephyrSim.ini"))


def _ensure_app() -> QtWidgets.QApplication:
    global qt_app
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(":/icons/icon.svg"))
    qt_app = app
    return app


def _load_settings() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(_settings_path())
    if "-Main-" not in cfg:
        cfg["-Main-"] = {}

    config_names = [s for s in cfg.sections() if s != "-Main-"]
    if not config_names:
        cfg["NewSet"] = {}
        config_names = ["NewSet"]

    selected = cfg["-Main-"].get("SelectedConfig", config_names[0])
    if selected not in cfg:
        cfg[selected] = {}
    cfg["-Main-"]["SelectedConfig"] = selected
    return cfg


def _save_settings(cfg: configparser.ConfigParser) -> None:
    with open(_settings_path(), "w", encoding="utf-8") as fp:
        cfg.write(fp)


def NormalizeMessageDisplayFilters(filters) -> dict:
    parsed_filters = {}
    if isinstance(filters, dict):
        parsed_filters = filters
    elif isinstance(filters, str):
        try:
            json_filters = json.loads(filters)
            if isinstance(json_filters, dict):
                parsed_filters = json_filters
        except Exception:
            try:
                literal_filters = ast.literal_eval(filters)
                if isinstance(literal_filters, dict):
                    parsed_filters = literal_filters
            except Exception:
                parsed_filters = {}

    normalized = {}
    for msg_type in message_display_types:
        normalized[msg_type] = bool(parsed_filters.get(msg_type, True))
    return normalized


def _apply_button_colors(button: QtWidgets.QPushButton, fg: str, bg: str) -> None:
    button.setStyleSheet(f"QPushButton {{ color: {fg}; background-color: {bg}; }}")


def _trim_text_edit(edit: QtWidgets.QTextEdit) -> None:
    lines = edit.toPlainText().splitlines()
    if len(lines) > MAXLOGLINES:
        edit.setPlainText("\n".join(lines[-KEEPLOGLINES:]))
        cursor = edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        edit.setTextCursor(cursor)

def _append_colored_text(edit: QtWidgets.QTextEdit, message: str, color_name: Optional[str]) -> None:
    cursor = edit.textCursor()
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
    fmt = QtGui.QTextCharFormat()
    if color_name:
        fmt.setForeground(QtGui.QBrush(QtGui.QColor(color_name)))
    cursor.insertText(message, fmt)
    edit.setTextCursor(cursor)
    edit.ensureCursorVisible()


def _formatted_timestamp() -> str:
    time_val, millis = ZephyrSimUtils.GetTime()
    return "[" + time_val + "." + millis + "] "


def ConfigWindow() -> dict:
    global window_size
    _ensure_app()
    while True:
        dialog = ConfigDialog()
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.result_config is not None:
            window_size = dialog.result_config.get("WindowSize", "Medium")
            return dialog.result_config
        CloseAndExit()


def MainWindow(
    config: dict,
    logport: serial.Serial,
    zephyrport: serial.Serial,
    cmd_fname: str,
    xmlqueue: queue.Queue,
) -> None:
    global main_window
    global log_port
    global zephyr_port
    global instrument
    global cmd_filename
    global xml_queue
    global message_display_filters
    global active_config_set
    global window_size

    _ensure_app()

    instrument = config["Instrument"]
    log_port = logport
    zephyr_port = zephyrport
    cmd_filename = cmd_fname
    xml_queue = xmlqueue
    active_config_set = config["ConfigSet"]
    window_size = config.get("WindowSize", "Medium")
    message_display_filters = NormalizeMessageDisplayFilters(config.get("MessageDisplayFilters", {}))

    def _on_mode(mode: str) -> None:
        if serial_suspended:
            return
        im_msg = ZephyrSimUtils.sendIM(instrument, mode, cmd_filename, zephyr_port)
        AddMsgToXmlQueue(im_msg)

    def _toggle_suspend_and_get_state() -> bool:
        SerialSuspend()
        return serial_suspended

    def _on_window_close() -> None:
        global _app_exit_requested
        _app_exit_requested = True

    if config["SharedPorts"]:
        log_port_display_name = zephyr_port.name
    else:
        log_port_display_name = config["LogPort"].name

    main_window = MainWindowQt(
        config=config,
        button_sizes=button_sizes,
        window_size=window_size,
        mode_defs=ZephyrInstModes,
        message_display_types=message_display_types,
        on_mode=_on_mode,
        on_tc=TCMessage,
        on_gps=GPSMessage,
        on_sw=SWMessage,
        on_sack=SAckMessage,
        on_raack=RAAckMessage,
        on_tmack=TMAckMessage,
        on_toggle_suspend=_toggle_suspend_and_get_state,
        on_exit=CloseAndExit,
        on_toggle_all_display=ToggleAllMessageDisplayFilters,
        on_toggle_display=ToggleMessageDisplayFilter,
        on_close=_on_window_close,
        log_port_display_name=log_port_display_name,
        zephyr_port_display_name=config["ZephyrPort"].name,
    )
    main_window.show()
    UpdateDisplayFilterButtons()


def AddMsgToLogDisplay(message: str) -> None:
    if main_window is None:
        return
    message = message.strip() + "\n"

    _trim_text_edit(main_window.log_window)

    if "ERR: " in message:
        _append_colored_text(main_window.log_window, message, "red")
    else:
        _append_colored_text(main_window.log_window, message, None)


def AddMsgToZephyrDisplay(message: str) -> None:
    if main_window is None:
        return
    if not ShouldDisplayMessage(message):
        return
    _trim_text_edit(main_window.zephyr_window)

    if "(TO)" in message:
        _append_colored_text(main_window.zephyr_window, message, "blue")
    elif "TM" in message and "CRIT" in message:
        _append_colored_text(main_window.zephyr_window, message, "red")
    elif "TM" in message and "WARN" in message:
        _append_colored_text(main_window.zephyr_window, message, "orange")
    elif "TM" in message:
        _append_colored_text(main_window.zephyr_window, message, "green")
    else:
        _append_colored_text(main_window.zephyr_window, message, None)


def AddDebugMsg(message: str, error: bool = False) -> None:
    if error:
        print("ERROR:", message)
    else:
        print(message)


def PollWindowEvents() -> None:
    _ensure_app().processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 10)
    if _app_exit_requested:
        CloseAndExit()


def TCMessage() -> None:
    if serial_suspended or main_window is None:
        return
    timestring = _formatted_timestamp()
    tc_text = main_window.tc_input.text() + ";"
    if tc_text == ";":
        QtWidgets.QMessageBox.warning(main_window, "Input Error", "TC text must not be empty")
        return

    print(timestring + "Sending TC:", tc_text)
    msg = ZephyrSimUtils.sendTC(instrument, tc_text, cmd_filename, zephyr_port)
    AddMsgToXmlQueue(msg)


def GPSMessage() -> None:
    if serial_suspended or main_window is None:
        return
    sza_text = main_window.gps_input.text()
    sza = None
    try:
        sza = float(sza_text)
        if sza > 180 or sza < 0:
            QtWidgets.QMessageBox.warning(main_window, "Input Error", "SZA must be between 0 and 180")
            return
    except Exception:
        QtWidgets.QMessageBox.warning(main_window, "Input Error", "SZA must be a float")
        return

    timestring = _formatted_timestamp()
    print(timestring + "Sending GPS, SZA =", str(sza))
    msg = ZephyrSimUtils.sendGPS(sza, cmd_filename, zephyr_port)
    AddMsgToXmlQueue(msg)


def SWMessage() -> None:
    if serial_suspended or main_window is None:
        return
    timestring = _formatted_timestamp()
    print(timestring + "Sending shutdown warning")
    ZephyrSimUtils.sendSW(instrument, cmd_filename, zephyr_port)


def SAckMessage() -> None:
    if serial_suspended:
        return
    timestring = _formatted_timestamp()
    print(timestring + "Sending safety ack")
    msg = ZephyrSimUtils.sendSAck(instrument, "ACK", cmd_filename, zephyr_port)
    AddMsgToXmlQueue(msg)


def RAAckMessage() -> None:
    if serial_suspended:
        return
    timestring = _formatted_timestamp()
    print(timestring + "Sent RAAck")
    msg = ZephyrSimUtils.sendRAAck(instrument, "ACK", cmd_filename, zephyr_port)
    AddMsgToXmlQueue(msg)


def TMAckMessage() -> None:
    if serial_suspended:
        return
    timestring = _formatted_timestamp()
    print(timestring + "Sending TM ack")
    msg = ZephyrSimUtils.sendTMAck(instrument, "ACK", cmd_filename, zephyr_port)
    AddMsgToXmlQueue(msg)


def CloseAndExit() -> None:
    global main_window
    if main_window is not None:
        try:
            main_window.close()
        except Exception:
            pass
    QtWidgets.QApplication.quit()
    os._exit(0)


def SerialSuspend() -> None:
    global serial_suspended
    global log_port
    global zephyr_port

    if not serial_suspended:
        zephyr_port.close()
        if log_port and zephyr_port.name != log_port.name:
            log_port.close()
        serial_suspended = True
    else:
        zephyr_port.open()
        if log_port and zephyr_port.name != log_port.name:
            log_port.open()
        serial_suspended = False


def AddMsgToXmlQueue(msg: str) -> None:
    global xml_queue
    if msg is None:
        return

    time_val, millis = ZephyrSimUtils.GetTime()
    timestring = "[" + time_val + "." + millis + "] "
    newmsg = "<XMLTOKEN>" + msg + "</XMLTOKEN>"
    parsed = xmltodict.parse(newmsg)
    xml_queue.put(f'{timestring}  (TO) {parsed["XMLTOKEN"]}\n')


def SetTmDir(filename: str) -> None:
    if main_window is not None:
        main_window.tm_directory.setText(filename)


def MessageMatchesType(message: str, msg_type: str) -> bool:
    return (f"'{msg_type}':" in message) or (f'"{msg_type}":' in message) or (f"<{msg_type}>" in message)


def ShouldDisplayMessage(message: str) -> bool:
    for msg_type in message_display_types:
        if MessageMatchesType(message, msg_type) and not message_display_filters[msg_type]:
            return False
    return True


def GetDisplayButtonColor(msg_type: str, enabled: bool) -> tuple[str, str]:
    if not enabled:
        return ("white", "gray")
    if msg_type in ("IMAck", "IMR", "TCAck"):
        return ("white", "black")
    if msg_type in ("GPS", "TC", "IM", "TMAck"):
        return ("white", "blue")
    return ("black", "green")


def UpdateDisplayFilterButtons() -> None:
    if main_window is None:
        return
    for msg_type in message_display_types:
        enabled = message_display_filters[msg_type]
        btn = main_window.display_buttons[msg_type]
        btn.setChecked(enabled)
        fg, bg = GetDisplayButtonColor(msg_type, enabled)
        _apply_button_colors(btn, fg, bg)

    if all(message_display_filters.values()):
        _apply_button_colors(main_window.all_display_button, "black", "green")
    elif any(message_display_filters.values()):
        _apply_button_colors(main_window.all_display_button, "black", "orange")
    else:
        _apply_button_colors(main_window.all_display_button, "white", "gray")


def ToggleMessageDisplayFilter(msg_type: str) -> None:
    message_display_filters[msg_type] = not message_display_filters[msg_type]
    SaveMessageDisplayFiltersToSettings()
    UpdateDisplayFilterButtons()


def ToggleAllMessageDisplayFilters() -> None:
    target_state = not all(message_display_filters.values())
    for msg_type in message_display_types:
        message_display_filters[msg_type] = target_state
    SaveMessageDisplayFiltersToSettings()
    UpdateDisplayFilterButtons()


def SaveMessageDisplayFiltersToSettings() -> None:
    global active_config_set
    if not active_config_set:
        return

    settings = _load_settings()
    if active_config_set not in settings:
        settings[active_config_set] = {}
    settings[active_config_set]["MessageDisplayFilters"] = json.dumps(NormalizeMessageDisplayFilters(message_display_filters))
    _save_settings(settings)
