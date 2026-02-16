#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PyQt6 GUI for the ZephyrSim simulator.

Public API exposed to the rest of the app:
- ConfigWindow
- ZephyrSimGUI
- PollWindowEvents
- AddMsgToLogDisplay
- AddMsgToZephyrDisplay
- AddDebugMsg
- SetTmDir
- EmitLogMessage
- EmitZephyrMessage
- EmitCommandMessage
- CloseAndExit
"""

import ast
import configparser
import datetime
import json
import os
from typing import Optional

import xmltodict
from PyQt6 import QtCore, QtGui, QtSerialPort, QtWidgets

from . import ZephyrSimUtils
from .ZephyrSignals import ZephyrSignalBus
from .ConfigDialog import ConfigDialog
from .MainWindowQt import MainWindowQt

# Perhaps this should be a configuration option
DEFAULT_SZA = 120

ZephyrInstModes = [
    ("SB", "Standby Mode"),
    ("FL", "Flight Mode"),
    ("LP", "Low Power Mode"),
    ("SA", "Safety Mode"),
    ("EF", "End of Flight Mode"),
]

button_sizes = {"Small": (70, 28), "Medium": (84, 30), "Large": (96, 34)}

MAXLOGLINES = 500
KEEPLOGLINES = 250

message_display_types = ["TM", "TC", "IM", "TMAck", "GPS", "TCAck", "IMAck", "IMR"]


def _settings_path() -> str:
    return os.path.abspath(os.path.expanduser("~/ZephyrSim.ini"))


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
    while True:
        dialog = ConfigDialog()
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.result_config is not None:
            return dialog.result_config
        CloseAndExit()


class ZephyrSimGUI:
    """Main GUI controller that owns the main window and mutable GUI/session state."""
    active_instance: Optional["ZephyrSimGUI"] = None

    def __init__(
        self,
        signals: ZephyrSignalBus,
        config: dict,
        logport: Optional[QtSerialPort.QSerialPort],
        zephyrport: QtSerialPort.QSerialPort,
        cmd_fname: str,
    ) -> None:
        self.config = config
        self.log_port = logport
        self.zephyr_port = zephyrport
        self.cmd_filename = cmd_fname
        self.instrument = config["Instrument"]
        self.active_config_set = config["ConfigSet"]
        self.window_size = config.get("WindowSize", "Medium")
        self.auto_ack_enabled = bool(config.get("AutoAck", False))
        self.serial_suspended = False
        self.app_exit_requested = False
        self.last_gps_timestamp = datetime.datetime.now().timestamp() - 50
        self.sza = DEFAULT_SZA
        self.message_display_filters = NormalizeMessageDisplayFilters(config.get("MessageDisplayFilters", {}))

        self.signal_bus = signals
        self.signal_bus.log_message.connect(self.add_msg_to_log_display)
        self.signal_bus.zephyr_message.connect(self.add_msg_to_zephyr_display)
        self.signal_bus.command_message.connect(self._handle_command_message)

        if config["SharedPorts"]:
            log_port_display_name = self.zephyr_port.portName()
        else:
            log_port_display_name = config["LogPort"].portName()

        self.window = MainWindowQt(
            config=config,
            button_sizes=button_sizes,
            window_size=self.window_size,
            mode_defs=ZephyrInstModes,
            message_display_types=message_display_types,
            on_mode=self._on_mode,
            on_tc=self.tc_message,
            on_gps=self.gps_message,
            on_sw=self.sw_message,
            on_sack=self.sack_message,
            on_raack=self.raack_message,
            on_tmack=self.tmack_message,
            on_toggle_suspend=self._toggle_suspend_and_get_state,
            on_exit=self.close_and_exit,
            on_toggle_all_display=self.toggle_all_message_display_filters,
            on_toggle_display=self.toggle_message_display_filter,
            on_close=self._on_window_close,
            log_port_display_name=log_port_display_name,
            zephyr_port_display_name=self.zephyr_port.portName(),
        )
        self.window.show()
        self.update_display_filter_buttons()

        # Run periodic AutoGPS checks in the Qt event loop.
        self.gps_timer = QtCore.QTimer(self.window)
        self.gps_timer.setInterval(100)
        self.gps_timer.timeout.connect(self.do_gps)
        self.gps_timer.start()

        ZephyrSimGUI.active_instance = self

    def show(self) -> None:
        self.window.show()

    def _on_mode(self, mode: str) -> None:
        if self.serial_suspended:
            return
        im_msg = ZephyrSimUtils.sendIM(self.instrument, mode, self.cmd_filename, self.zephyr_port)
        self.add_msg_to_xml_queue(im_msg)

    def _toggle_suspend_and_get_state(self) -> bool:
        self.serial_suspend()
        return self.serial_suspended

    def _on_window_close(self) -> None:
        self.app_exit_requested = True

    def do_gps(self) -> None:
        now_timestamp = datetime.datetime.now().timestamp()
        if self.config["AutoGPS"] and now_timestamp - self.last_gps_timestamp >= 60:
            self.last_gps_timestamp = now_timestamp
            gps_msg = ZephyrSimUtils.sendGPS(self.sza, self.cmd_filename, self.zephyr_port)
            self.add_msg_to_xml_queue(gps_msg)

    def poll_window_events(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 10)
        if self.app_exit_requested:
            self.close_and_exit()

    def add_msg_to_log_display(self, message: str) -> None:
        if self.window is None:
            return
        message = message.strip() + "\n"

        _trim_text_edit(self.window.log_window)

        if "ERR: " in message:
            _append_colored_text(self.window.log_window, message, "red")
        else:
            _append_colored_text(self.window.log_window, message, None)

    def add_msg_to_zephyr_display(self, message: str) -> None:
        if self.window is None:
            return
        if not self.should_display_message(message):
            return
        _trim_text_edit(self.window.zephyr_window)

        if "(TO)" in message:
            _append_colored_text(self.window.zephyr_window, message, "blue")
        elif "TM" in message and "CRIT" in message:
            _append_colored_text(self.window.zephyr_window, message, "red")
        elif "TM" in message and "WARN" in message:
            _append_colored_text(self.window.zephyr_window, message, "orange")
        elif "TM" in message:
            _append_colored_text(self.window.zephyr_window, message, "green")
        else:
            _append_colored_text(self.window.zephyr_window, message, None)

    def add_debug_msg(self, message: str, error: bool = False) -> None:
        if error:
            print("ERROR:", message)
        else:
            print(message)

    def tc_message(self) -> None:
        if self.serial_suspended or self.window is None:
            return
        timestring = _formatted_timestamp()
        tc_text = self.window.tc_input.text() + ";"
        if tc_text == ";":
            QtWidgets.QMessageBox.warning(self.window, "Input Error", "TC text must not be empty")
            return

        print(timestring + "Sending TC:", tc_text)
        msg = ZephyrSimUtils.sendTC(self.instrument, tc_text, self.cmd_filename, self.zephyr_port)
        self.add_msg_to_xml_queue(msg)

    def gps_message(self) -> None:
        if self.serial_suspended or self.window is None:
            return
        sza_text = self.window.gps_input.text()
        parsed_sza = None
        try:
            parsed_sza = float(sza_text)
            if parsed_sza > 180 or parsed_sza < 0:
                QtWidgets.QMessageBox.warning(self.window, "Input Error", "SZA must be between 0 and 180")
                return
        except Exception:
            QtWidgets.QMessageBox.warning(self.window, "Input Error", "SZA must be a float")
            return

        timestring = _formatted_timestamp()
        print(timestring + "Sending GPS, SZA =", str(parsed_sza))
        msg = ZephyrSimUtils.sendGPS(parsed_sza, self.cmd_filename, self.zephyr_port)
        self.add_msg_to_xml_queue(msg)

    def sw_message(self) -> None:
        if self.serial_suspended or self.window is None:
            return
        timestring = _formatted_timestamp()
        print(timestring + "Sending shutdown warning")
        ZephyrSimUtils.sendSW(self.instrument, self.cmd_filename, self.zephyr_port)

    def sack_message(self) -> None:
        if self.serial_suspended:
            return
        timestring = _formatted_timestamp()
        print(timestring + "Sending safety ack")
        msg = ZephyrSimUtils.sendSAck(self.instrument, "ACK", self.cmd_filename, self.zephyr_port)
        self.add_msg_to_xml_queue(msg)

    def raack_message(self) -> None:
        if self.serial_suspended:
            return
        timestring = _formatted_timestamp()
        print(timestring + "Sent RAAck")
        msg = ZephyrSimUtils.sendRAAck(self.instrument, "ACK", self.cmd_filename, self.zephyr_port)
        self.add_msg_to_xml_queue(msg)

    def tmack_message(self) -> None:
        if self.serial_suspended:
            return
        timestring = _formatted_timestamp()
        print(timestring + "Sending TM ack")
        msg = ZephyrSimUtils.sendTMAck(self.instrument, "ACK", self.cmd_filename, self.zephyr_port)
        self.add_msg_to_xml_queue(msg)

    def close_and_exit(self) -> None:
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
        QtWidgets.QApplication.quit()
        os._exit(0)

    def serial_suspend(self) -> None:
        if not self.serial_suspended:
            self.zephyr_port.close()
            if self.log_port and self.zephyr_port.portName() != self.log_port.portName():
                self.log_port.close()
            self.serial_suspended = True
        else:
            self.zephyr_port.open(QtSerialPort.QSerialPort.OpenModeFlag.ReadWrite)
            if self.log_port and self.zephyr_port.portName() != self.log_port.portName():
                self.log_port.open(QtSerialPort.QSerialPort.OpenModeFlag.ReadWrite)
            self.serial_suspended = False

    def add_msg_to_xml_queue(self, msg: str) -> None:
        if msg is None:
            return

        timestring = _formatted_timestamp()
        newmsg = "<XMLTOKEN>" + msg + "</XMLTOKEN>"
        parsed = xmltodict.parse(newmsg)
        self.emit_zephyr_message(f'{timestring}  (TO) {parsed["XMLTOKEN"]}\n')

    def emit_log_message(self, message: str) -> None:
        self.signal_bus.log_message.emit(message)

    def emit_zephyr_message(self, message: str) -> None:
        self.signal_bus.zephyr_message.emit(message)

    def emit_command_message(self, message: str) -> None:
        self.signal_bus.command_message.emit(message)

    def _handle_command_message(self, cmd: str) -> None:
        if not self.auto_ack_enabled:
            return

        timestring = _formatted_timestamp()
        if cmd == "TMAck":
            msg = ZephyrSimUtils.sendTMAck(self.instrument, "ACK", self.cmd_filename, self.zephyr_port)
            self.add_msg_to_xml_queue(msg)
            self.add_debug_msg(timestring + "Sent TMAck")
        elif cmd == "SAck":
            msg = ZephyrSimUtils.sendSAck(self.instrument, "ACK", self.cmd_filename, self.zephyr_port)
            self.add_msg_to_xml_queue(msg)
            self.add_debug_msg(timestring + "Sent SAck")
        elif cmd == "RAAck":
            msg = ZephyrSimUtils.sendRAAck(self.instrument, "ACK", self.cmd_filename, self.zephyr_port)
            self.add_msg_to_xml_queue(msg)
            self.add_debug_msg(timestring + "Sent RAAck")
        else:
            self.add_debug_msg("Unknown command", True)

    def set_tm_dir(self, filename: str) -> None:
        if self.window is not None:
            self.window.tm_directory.setText(filename)

    @staticmethod
    def message_matches_type(message: str, msg_type: str) -> bool:
        return (f"'{msg_type}':" in message) or (f'"{msg_type}":' in message) or (f"<{msg_type}>" in message)

    def should_display_message(self, message: str) -> bool:
        for msg_type in message_display_types:
            if self.message_matches_type(message, msg_type) and not self.message_display_filters[msg_type]:
                return False
        return True

    @staticmethod
    def get_display_button_color(msg_type: str, enabled: bool) -> tuple[str, str]:
        if not enabled:
            return ("white", "gray")
        if msg_type in ("IMAck", "IMR", "TCAck"):
            return ("white", "black")
        if msg_type in ("GPS", "TC", "IM", "TMAck"):
            return ("white", "blue")
        return ("black", "green")

    def update_display_filter_buttons(self) -> None:
        if self.window is None:
            return
        for msg_type in message_display_types:
            enabled = self.message_display_filters[msg_type]
            btn = self.window.display_buttons[msg_type]
            btn.setChecked(enabled)
            fg, bg = self.get_display_button_color(msg_type, enabled)
            _apply_button_colors(btn, fg, bg)

        if all(self.message_display_filters.values()):
            _apply_button_colors(self.window.all_display_button, "black", "green")
        elif any(self.message_display_filters.values()):
            _apply_button_colors(self.window.all_display_button, "black", "orange")
        else:
            _apply_button_colors(self.window.all_display_button, "white", "gray")

    def toggle_message_display_filter(self, msg_type: str) -> None:
        self.message_display_filters[msg_type] = not self.message_display_filters[msg_type]
        self.save_message_display_filters_to_settings()
        self.update_display_filter_buttons()

    def toggle_all_message_display_filters(self) -> None:
        target_state = not all(self.message_display_filters.values())
        for msg_type in message_display_types:
            self.message_display_filters[msg_type] = target_state
        self.save_message_display_filters_to_settings()
        self.update_display_filter_buttons()

    def save_message_display_filters_to_settings(self) -> None:
        if not self.active_config_set:
            return

        settings = _load_settings()
        if self.active_config_set not in settings:
            settings[self.active_config_set] = {}
        settings[self.active_config_set]["MessageDisplayFilters"] = json.dumps(
            NormalizeMessageDisplayFilters(self.message_display_filters)
        )
        _save_settings(settings)

def PollWindowEvents() -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.poll_window_events()


def AddMsgToLogDisplay(message: str) -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.add_msg_to_log_display(message)


def AddMsgToZephyrDisplay(message: str) -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.add_msg_to_zephyr_display(message)


def AddDebugMsg(message: str, error: bool = False) -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.add_debug_msg(message, error)
    elif error:
        print("ERROR:", message)
    else:
        print(message)

def EmitLogMessage(message: str) -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.emit_log_message(message)


def EmitZephyrMessage(message: str) -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.emit_zephyr_message(message)


def EmitCommandMessage(message: str) -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.emit_command_message(message)


def CloseAndExit() -> None:
    gui = _get_active_gui()
    if gui is not None:
        gui.close_and_exit()
    QtWidgets.QApplication.quit()
    os._exit(0)
