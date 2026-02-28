#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import Callable, Dict, List, Tuple

import pyperclip
from PyQt6 import QtGui, QtWidgets
from . import ZephyrSimResources_rc  # noqa: F401


def _lighten(hex_color: str, factor: float = 0.25) -> str:
    """Return a lightened version of a hex color string."""
    c = QtGui.QColor(hex_color)
    h, s, l, a = c.hslHueF(), c.hslSaturationF(), c.lightnessF(), c.alphaF()
    c.setHslF(h, s, min(1.0, l + factor * (1.0 - l)), a)
    return c.name()


def _apply_button_colors(button: QtWidgets.QPushButton, fg: str, bg: str, checked_bg=None) -> None:
    hover_bg = _lighten(bg)
    style = (
        f"QPushButton {{ color: {fg}; background-color: {bg}; }}"
        f"QPushButton:hover {{ background-color: {hover_bg}; }}"
    )
    if checked_bg is not None:
        checked_hover_bg = _lighten(checked_bg)
        style += (
            f"QPushButton:checked {{ background-color: {checked_bg}; color: white; }}"
            f"QPushButton:checked:hover {{ background-color: {checked_hover_bg}; }}"
        )
    button.setStyleSheet(style)


class MainWindowQt(QtWidgets.QMainWindow):
    def __init__(
        self,
        config: dict,
        button_sizes: Dict[str, Tuple[int, int]],
        window_size: str,
        mode_defs: List[Tuple[str, str]],
        message_display_types: List[str],
        on_mode: Callable[[str], None],
        on_tc: Callable[[], None],
        on_gps: Callable[[], None],
        on_sw: Callable[[], None],
        on_sack: Callable[[], None],
        on_raack: Callable[[], None],
        on_tmack: Callable[[], None],
        on_toggle_suspend: Callable[[], bool],
        on_exit: Callable[[], None],
        on_toggle_all_display: Callable[[], None],
        on_toggle_display: Callable[[str], None],
        on_close: Callable[[], None],
        log_port_display_name: str,
        zephyr_port_display_name: str,
    ):
        super().__init__()
        self.config = config
        self.button_sizes = button_sizes
        self.window_size = window_size
        self.mode_defs = mode_defs
        self.message_display_types = message_display_types
        self.on_mode = on_mode
        self.on_tc = on_tc
        self.on_gps = on_gps
        self.on_sw = on_sw
        self.on_sack = on_sack
        self.on_raack = on_raack
        self.on_tmack = on_tmack
        self.on_toggle_suspend = on_toggle_suspend
        self.on_exit = on_exit
        self.on_toggle_all_display = on_toggle_all_display
        self.on_toggle_display = on_toggle_display
        self.on_close_callback = on_close
        self.log_port_display_name = log_port_display_name
        self.zephyr_port_display_name = zephyr_port_display_name

        self.display_buttons = {}

        self.setWindowTitle(config["Instrument"])
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.svg"))

        self._build_ui()

    def _build_ui(self) -> None:
        cfg = self.config
        font = QtGui.QFont("Monaco", cfg["WindowParams"]["font_size"])
        self.setFont(font)

        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)

        top_row = QtWidgets.QHBoxLayout()

        mode_group = QtWidgets.QGroupBox("Mode Select")
        mode_layout = QtWidgets.QHBoxLayout(mode_group)
        for mode, tip in self.mode_defs:
            btn = QtWidgets.QPushButton(mode)
            btn.setToolTip(tip)
            self._set_button_size(btn)
            _apply_button_colors(btn, "black", "lightblue")
            btn.clicked.connect(lambda _=False, m=mode: self.on_mode(m))
            mode_layout.addWidget(btn)
        top_row.addWidget(mode_group)

        tc_group = QtWidgets.QGroupBox("Telecommands")
        tc_layout = QtWidgets.QHBoxLayout(tc_group)
        self.tc_button = QtWidgets.QPushButton("TC")
        self._set_button_size(self.tc_button)
        _apply_button_colors(self.tc_button, "black", "green")
        self.tc_button.setToolTip("Send Telecommand")
        self.tc_button.clicked.connect(self.on_tc)
        self.tc_input = QtWidgets.QLineEdit()
        self.tc_input.setFixedWidth(90)
        self.tc_input.returnPressed.connect(self.on_tc)
        self.tc_input.setToolTip("TC Text, semicolon will be appended")
        tc_layout.addWidget(self.tc_button)
        tc_layout.addWidget(self.tc_input)
        top_row.addWidget(tc_group)

        sza_group = QtWidgets.QGroupBox("SZA")
        sza_layout = QtWidgets.QHBoxLayout(sza_group)
        self.gps_button = QtWidgets.QPushButton("GPS")
        self._set_button_size(self.gps_button)
        _apply_button_colors(self.gps_button, "black", "green")
        self.gps_button.setToolTip("Send GPS")
        self.gps_button.clicked.connect(self.on_gps)
        self.gps_input = QtWidgets.QLineEdit("120.0")
        self.gps_input.setFixedWidth(90)
        self.gps_input.returnPressed.connect(self.on_gps)

        self.gps_input.setToolTip("GPS SZA value")
        sza_layout.addWidget(self.gps_button)
        sza_layout.addWidget(self.gps_input)
        top_row.addWidget(sza_group)

        zcmd_group = QtWidgets.QGroupBox("Zephyr Commands")
        zcmd_layout = QtWidgets.QHBoxLayout(zcmd_group)
        for name, tip, cb in [
            ("SW", "Send a Shutdown Warning", self.on_sw),
            ("SAck", "Send a Safety Ack", self.on_sack),
            ("RAAck", "Send a RAA Ack", self.on_raack),
            ("TMAck", "Send a TM Ack", self.on_tmack),
        ]:
            btn = QtWidgets.QPushButton(name)
            self._set_button_size(btn)
            btn.setToolTip(tip)
            btn.clicked.connect(cb)
            zcmd_layout.addWidget(btn)
        top_row.addWidget(zcmd_group)

        behavior_group = QtWidgets.QGroupBox("Behavior")
        behavior_layout = QtWidgets.QHBoxLayout(behavior_group)
        self.suspend_button = QtWidgets.QPushButton("Suspend")
        self.suspend_button.setToolTip("Suspend/Resume serial ports")
        _apply_button_colors(self.suspend_button, "white", "orange")
        self.suspend_button.clicked.connect(self._toggle_suspend)
        self.exit_button = QtWidgets.QPushButton("Exit")
        _apply_button_colors(self.exit_button, "white", "red")
        self.exit_button.setToolTip("Exit the application")
        self.exit_button.clicked.connect(self.on_exit)
        behavior_layout.addWidget(self.suspend_button)
        behavior_layout.addWidget(self.exit_button)
        top_row.addWidget(behavior_group)

        top_row.addStretch(1)
        root.addLayout(top_row)

        display_group = QtWidgets.QGroupBox("Messages to Display")
        display_layout = QtWidgets.QHBoxLayout(display_group)
        self.all_display_button = QtWidgets.QPushButton("All")
        self._set_button_size(self.all_display_button)
        _apply_button_colors(self.all_display_button, "black", "#a0a0a0")
        self.all_display_button.clicked.connect(self.on_toggle_all_display)
        display_layout.addWidget(self.all_display_button)

        for msg_type in self.message_display_types:
            btn = QtWidgets.QPushButton(msg_type)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, m=msg_type: self.on_toggle_display(m))
            self._set_button_size(btn)
            _apply_button_colors(btn, "black", "#a0a0a0", checked_bg="#4a90d9")
            self.display_buttons[msg_type] = btn
            display_layout.addWidget(btn)

        display_layout.addStretch(1)
        root.addWidget(display_group)

        output_row = QtWidgets.QHBoxLayout()
        log_group = QtWidgets.QGroupBox("StratoCore Log Messages")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_window = QtWidgets.QTextEdit()
        self.log_window.setReadOnly(True)
        log_layout.addWidget(self.log_window)

        zephyr_group = QtWidgets.QGroupBox(f"Messages TO/FROM {cfg['Instrument']}")
        zephyr_layout = QtWidgets.QVBoxLayout(zephyr_group)
        self.zephyr_window = QtWidgets.QTextEdit()
        self.zephyr_window.setReadOnly(True)
        zephyr_layout.addWidget(self.zephyr_window)

        output_row.addWidget(log_group, 1)
        output_row.addWidget(zephyr_group, 3)
        root.addLayout(output_row, 1)

        config_row = QtWidgets.QHBoxLayout()
        config_row.addWidget(QtWidgets.QLabel(f"Configuration set: {cfg['ConfigSet']}"))
        config_row.addWidget(QtWidgets.QLabel(f"Log port: {self.log_port_display_name}"))
        config_row.addWidget(QtWidgets.QLabel(f"Zephyr port: {self.zephyr_port_display_name}"))
        config_row.addWidget(QtWidgets.QLabel(f"AutoAck: {cfg['AutoAck']}"))
        config_row.addWidget(QtWidgets.QLabel(f"AutoGPS: {cfg['AutoGPS']}"))
        config_row.addStretch(1)
        root.addLayout(config_row)

        tm_row = QtWidgets.QHBoxLayout()
        tm_row.addWidget(QtWidgets.QLabel("TM directory"))
        self.tm_directory = QtWidgets.QLineEdit(" ")
        self.tm_directory.setReadOnly(True)
        tm_row.addWidget(self.tm_directory, 1)
        self.copy_tm_btn = QtWidgets.QPushButton("Copy")
        _apply_button_colors(self.copy_tm_btn, "white", "blue")
        self.copy_tm_btn.clicked.connect(self._copy_tm_directory)
        tm_row.addWidget(self.copy_tm_btn)
        root.addLayout(tm_row)

        self.setCentralWidget(central)
        self.resize(cfg["WindowParams"]["width"] * 10, cfg["WindowParams"]["height"] * 26)

    def _set_button_size(self, btn: QtWidgets.QPushButton) -> None:
        w, h = self.button_sizes.get(self.window_size, self.button_sizes["Medium"])
        btn.setFixedSize(w, h)

    def _toggle_suspend(self) -> None:
        suspended = self.on_toggle_suspend()
        if suspended:
            self.suspend_button.setText("Resume")
            _apply_button_colors(self.suspend_button, "white", "blue")
        else:
            self.suspend_button.setText("Suspend")
            _apply_button_colors(self.suspend_button, "white", "orange")

    def _copy_tm_directory(self) -> None:
        pyperclip.copy(self.tm_directory.text())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.on_close_callback()
        event.accept()
