#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ast
import configparser
import json
import os

from PyQt6 import QtSerialPort, QtWidgets


window_sizes = ["Small", "Medium", "Large"]
window_params = {
    "Small": {"font_size": 8, "width": 100, "height": 20},
    "Medium": {"font_size": 10, "width": 140, "height": 30},
    "Large": {"font_size": 12, "width": 180, "height": 40},
}
message_display_types = ["TM", "TC", "IM", "TMAck", "GPS", "TCAck", "IMAck", "IMR"]


def _settings_path() -> str:
    return os.path.abspath(os.path.expanduser("~/ZephyrSim.ini"))


def _apply_button_colors(button: QtWidgets.QPushButton, fg: str, bg: str) -> None:
    button.setStyleSheet(f"QPushButton {{ color: {fg}; background-color: {bg}; }}")


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


def _bool_from_section(section: configparser.SectionProxy, key: str, default: bool) -> bool:
    try:
        raw = section.get(key, None)
        if raw is None:
            return bool(default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            val = raw.strip().lower()
            if val in ("1", "true", "yes", "on"):
                return True
            if val in ("0", "false", "no", "off"):
                return False
            return bool(default)
        return bool(raw)
    except Exception:
        return bool(default)


def _list_ports() -> list:
    ports = [port.portName() for port in QtSerialPort.QSerialPortInfo.availablePorts()]
    return [port for port in ports if "Bluetooth" not in port]


def _open_serial_port(port_name: str) -> QtSerialPort.QSerialPort:
    port = QtSerialPort.QSerialPort()
    port.setPortName(port_name)
    port.setBaudRate(115200)
    port.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
    port.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
    port.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
    port.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
    if not port.open(QtSerialPort.QSerialPort.OpenModeFlag.ReadWrite):
        raise RuntimeError(f"{port_name}: {port.errorString()}")
    port.clear(QtSerialPort.QSerialPort.Direction.Input)
    return port


class ConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure")
        self.resize(760, 460)

        self.settings = _load_settings()
        self.selected_config = self.settings["-Main-"]["SelectedConfig"]
        self.result_config = None

        self._build_ui()
        self._load_config_set(self.selected_config)

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)

        cfg_row = QtWidgets.QHBoxLayout()
        cfg_row.addWidget(QtWidgets.QLabel("Configuration set:"))
        self.config_combo = QtWidgets.QComboBox()
        self.config_combo.addItems([s for s in self.settings.sections() if s != "-Main-"])
        idx = self.config_combo.findText(self.selected_config)
        if idx >= 0:
            self.config_combo.setCurrentIndex(idx)
        self.config_combo.currentTextChanged.connect(self._on_config_changed)
        cfg_row.addWidget(self.config_combo, 1)

        self.new_btn = QtWidgets.QPushButton("New")
        self.new_btn.clicked.connect(self._new_config)
        self.rename_btn = QtWidgets.QPushButton("Rename")
        self.rename_btn.clicked.connect(self._rename_config)
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete_config)
        cfg_row.addWidget(self.new_btn)
        cfg_row.addWidget(self.rename_btn)
        cfg_row.addWidget(self.delete_btn)
        root.addLayout(cfg_row)

        form = QtWidgets.QFormLayout()

        self.instrument_combo = QtWidgets.QComboBox()
        self.instrument_combo.addItems(["RATS", "LPC", "RACHUTS", "FLOATS"])
        form.addRow("Instrument:", self.instrument_combo)

        self.window_size_combo = QtWidgets.QComboBox()
        self.window_size_combo.addItems(window_sizes)
        form.addRow("Window size:", self.window_size_combo)

        self.auto_ack_checkbox = QtWidgets.QCheckBox("Automatically respond with ACKs")
        self.auto_gps_checkbox = QtWidgets.QCheckBox("Automatically send GPS")
        form.addRow(self.auto_ack_checkbox)
        form.addRow(self.auto_gps_checkbox)

        data_dir_row = QtWidgets.QHBoxLayout()
        self.data_dir_edit = QtWidgets.QLineEdit()
        self.data_dir_edit.setMinimumWidth(520)
        self.data_dir_btn = QtWidgets.QPushButton("Select")
        self.data_dir_btn.clicked.connect(self._select_data_dir)
        data_dir_row.addWidget(self.data_dir_edit, 1)
        data_dir_row.addWidget(self.data_dir_btn)
        form.addRow("Data directory:", data_dir_row)

        ports = _list_ports()
        self.zephyr_port_combo = QtWidgets.QComboBox()
        self.zephyr_port_combo.addItems(ports)
        form.addRow("Zephyr port:", self.zephyr_port_combo)

        self.log_port_combo = QtWidgets.QComboBox()
        self.log_port_combo.addItems(ports)
        form.addRow("Log port:", self.log_port_combo)

        root.addLayout(form)

        root.addWidget(QtWidgets.QLabel("Select the same Log and Zephyr ports when StratoCore<INST> uses shared ports."))

        btn_row = QtWidgets.QHBoxLayout()
        self.continue_btn = QtWidgets.QPushButton("Continue")
        _apply_button_colors(self.continue_btn, "white", "blue")
        self.continue_btn.clicked.connect(self._continue_clicked)
        self.exit_btn = QtWidgets.QPushButton("Exit")
        _apply_button_colors(self.exit_btn, "white", "red")
        self.exit_btn.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(self.continue_btn)
        btn_row.addWidget(self.exit_btn)
        root.addLayout(btn_row)

    def _config_section(self, name: str) -> configparser.SectionProxy:
        if name not in self.settings:
            self.settings[name] = {}
        return self.settings[name]

    def _save_current_widgets(self) -> None:
        name = self.config_combo.currentText()
        if not name:
            return
        sec = self._config_section(name)
        sec["Instrument"] = self.instrument_combo.currentText()
        sec["AutoAck"] = str(self.auto_ack_checkbox.isChecked())
        sec["AutoGPS"] = str(self.auto_gps_checkbox.isChecked())
        sec["WindowSize"] = self.window_size_combo.currentText()
        sec["DataDirectory"] = self.data_dir_edit.text().strip()
        sec["ZephyrPort"] = self.zephyr_port_combo.currentText().strip()
        sec["LogPort"] = self.log_port_combo.currentText().strip()
        if "MessageDisplayFilters" not in sec:
            sec["MessageDisplayFilters"] = json.dumps({msg_type: True for msg_type in message_display_types})

    def _load_config_set(self, name: str) -> None:
        sec = self._config_section(name)

        instrument_name = sec.get("Instrument", "RATS")
        idx = self.instrument_combo.findText(instrument_name)
        self.instrument_combo.setCurrentIndex(idx if idx >= 0 else 0)

        win_size = sec.get("WindowSize", "Medium")
        idx = self.window_size_combo.findText(win_size)
        self.window_size_combo.setCurrentIndex(idx if idx >= 0 else 1)

        self.auto_ack_checkbox.setChecked(_bool_from_section(sec, "AutoAck", True))
        self.auto_gps_checkbox.setChecked(_bool_from_section(sec, "AutoGPS", True))
        self.data_dir_edit.setText(sec.get("DataDirectory", ""))

        ports = _list_ports()
        for combo in (self.zephyr_port_combo, self.log_port_combo):
            combo.clear()
            combo.addItems(ports)

        zephyr_name = sec.get("ZephyrPort", "")
        log_name = sec.get("LogPort", "")
        if zephyr_name and self.zephyr_port_combo.findText(zephyr_name) < 0:
            self.zephyr_port_combo.addItem(zephyr_name)
        if log_name and self.log_port_combo.findText(log_name) < 0:
            self.log_port_combo.addItem(log_name)

        z_idx = self.zephyr_port_combo.findText(zephyr_name)
        l_idx = self.log_port_combo.findText(log_name)
        self.zephyr_port_combo.setCurrentIndex(z_idx if z_idx >= 0 else 0)
        self.log_port_combo.setCurrentIndex(l_idx if l_idx >= 0 else 0)

    def _on_config_changed(self, new_name: str) -> None:
        if not new_name:
            return
        self._save_current_widgets()
        self._load_config_set(new_name)

    def _select_data_dir(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select the data directory", self.data_dir_edit.text() or os.path.expanduser("~"))
        if folder:
            self.data_dir_edit.setText(folder)

    def _new_config(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New Configuration", "Enter name for new configuration set:")
        name = name.strip()
        if not ok or not name:
            return
        if not name.isprintable():
            QtWidgets.QMessageBox.warning(self, "Error", "Configuration set name must be printable")
            return
        if name not in self.settings:
            self._save_current_widgets()
            self.settings[name] = dict(self.settings[self.config_combo.currentText()])
            self.config_combo.addItem(name)
        self.config_combo.setCurrentText(name)

    def _rename_config(self) -> None:
        old_name = self.config_combo.currentText().strip()
        if not old_name:
            return
        new_name, ok = QtWidgets.QInputDialog.getText(self, "Rename Configuration", "Enter new configuration set name:", text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if not new_name.isprintable():
            QtWidgets.QMessageBox.warning(self, "Error", "Configuration set name must be printable")
            return

        self._save_current_widgets()
        self.settings[new_name] = dict(self.settings[old_name])
        self.settings.remove_section(old_name)

        i = self.config_combo.findText(old_name)
        if i >= 0:
            self.config_combo.removeItem(i)
        self.config_combo.addItem(new_name)
        self.config_combo.setCurrentText(new_name)

    def _delete_config(self) -> None:
        name = self.config_combo.currentText().strip()
        config_names = [s for s in self.settings.sections() if s != "-Main-"]
        if len(config_names) <= 1:
            QtWidgets.QMessageBox.warning(self, "Error", "Cannot delete the last configuration set")
            return
        answer = QtWidgets.QMessageBox.question(self, "Delete", f"Delete configuration set '{name}'?")
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self.settings.remove_section(name)
        i = self.config_combo.findText(name)
        if i >= 0:
            self.config_combo.removeItem(i)
        self.config_combo.setCurrentIndex(0)

    def _continue_clicked(self) -> None:
        self._save_current_widgets()
        config_set = self.config_combo.currentText().strip()
        sec = self._config_section(config_set)

        data_directory = sec.get("DataDirectory", "").strip()
        zephyr_port_name = sec.get("ZephyrPort", "").strip()
        log_port_name = sec.get("LogPort", "").strip()
        instrument_name = sec.get("Instrument", "").strip()
        window_size = sec.get("WindowSize", "Medium")

        if not all([data_directory, zephyr_port_name, log_port_name, instrument_name, window_size]):
            QtWidgets.QMessageBox.warning(self, "Error", "Please specify all items")
            return

        try:
            zephyr = _open_serial_port(zephyr_port_name)
            if log_port_name != zephyr_port_name:
                log = _open_serial_port(log_port_name)
                shared_ports = False
            else:
                log = None
                shared_ports = True
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Error", f"Error opening serial port: {exc}")
            try:
                zephyr.close()
            except Exception:
                pass
            return

        msg_display_filters = NormalizeMessageDisplayFilters(sec.get("MessageDisplayFilters", "{}"))
        sec["MessageDisplayFilters"] = json.dumps(msg_display_filters)

        self.settings["-Main-"]["SelectedConfig"] = config_set
        _save_settings(self.settings)

        self.result_config = {
            "ZephyrPort": zephyr,
            "LogPort": log,
            "SharedPorts": shared_ports,
            "Instrument": instrument_name,
            "AutoAck": _bool_from_section(sec, "AutoAck", True),
            "AutoGPS": _bool_from_section(sec, "AutoGPS", True),
            "WindowParams": window_params.get(window_size, window_params["Medium"]),
            "WindowSize": window_size,
            "DataDirectory": data_directory,
            "ConfigSet": config_set,
            "MessageDisplayFilters": msg_display_filters,
        }

        print("Instrument:", self.result_config["Instrument"])
        print("Zephyr Port:", self.result_config["ZephyrPort"])
        print("Log Port:", self.result_config["LogPort"])
        print("AutoAck:", self.result_config["AutoAck"])

        self.accept()
