#!/usr/bin/env python3
"""
This module handles incoming serial data from instruments and emits parsed
messages through the shared Zephyr signal bus.
"""
# -*- coding: utf-8 -*-

import datetime
import random
from typing import Optional

import xmltodict
from PyQt6 import QtCore, QtSerialPort

from . import ZephyrSignals
from .DiagnosticsWidget import ERROR, WARNING
from .ZephyrFramer import FrameResult, FrameStatus, ZephyrFramer


def GetDateTime() -> tuple:
    """Return (date, time, time_for_filename, milliseconds) strings for now."""
    # create date and time strings
    current_datetime = datetime.datetime.now()
    date = str(current_datetime.date().strftime("%Y-%m-%d"))
    curr_time = str(current_datetime.time().strftime("%H:%M:%S"))
    curr_time_file = str(current_datetime.time().strftime("%H-%M-%S"))
    milliseconds = str(current_datetime.time().strftime("%f"))[:-3]

    return date, curr_time, curr_time_file, milliseconds


class SerialProcessor(QtCore.QObject):
    """Consumes QSerialPort data via readyRead signals (no polling loop)."""

    def __init__(
        self,
        app_signals: ZephyrSignals.ZephyrSignalBus,
        logport: Optional[QtSerialPort.QSerialPort],
        zephyrport: QtSerialPort.QSerialPort,
        inst_filename: str,
        xml_filename: str,
        tm_dir: str,
        instrument: str,
        corrupt_serial: bool = False,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        """Wire up serial ports and connect readyRead signals.

        Args:
            app_signals:    Shared signal bus used to emit log, XML, command,
                            and diagnostics events to the rest of the app.
            logport:        Serial port carrying plain-text instrument log lines
                            (None if unused).
            zephyrport:     Serial port carrying Zephyr XML/binary messages.
            inst_filename:  Path to the file where log lines are appended.
            xml_filename:   Path to the file where Zephyr XML messages are appended.
            tm_dir:         Directory where received TM binary files are written.
            instrument:     Instrument identifier string used in TM filenames.
            corrupt_serial: If True, randomly flip or drop bytes to exercise CRC
                            and framing error paths during bench testing.
            parent:         Optional Qt parent object.
        """
        super().__init__(parent)
        self.signals = app_signals
        self.log_port = logport
        self.zephyr_port = zephyrport
        self.inst_filename = inst_filename
        self.xml_filename = xml_filename
        self.tm_dir = tm_dir
        self.instrument = instrument
        self.corrupt_serial = corrupt_serial

        self._log_buffer = bytearray()
        self._framer = ZephyrFramer()

        self.zephyr_port.clear(QtSerialPort.QSerialPort.Direction.Input)
        self.zephyr_port.readyRead.connect(self._on_zephyr_ready_read)
        if self.log_port is not None:
            self.log_port.clear(QtSerialPort.QSerialPort.Direction.Input)
            self.log_port.readyRead.connect(self._on_log_ready_read)

    def _emit_log_message(self, message: str) -> None:
        """Timestamp, emit, and persist one plain-text instrument log line."""
        message = message.rstrip() + "\n"

        _, time_val, _, milliseconds = GetDateTime()
        timestring = "[" + time_val + "." + milliseconds + "] "

        display_msg = timestring + message
        self.signals.log_message.emit(display_msg)

        with open(self.inst_filename, "a") as inst:
            inst.write(display_msg)

    def _emit_zephyr_message(self, msg_dict: dict) -> None:
        """Timestamp, emit, and persist one parsed Zephyr XML message."""
        _, time_val, _, milliseconds = GetDateTime()
        timestring = "[" + time_val + "." + milliseconds + "] "
        display = f'{timestring} (FROM){msg_dict["XMLTOKEN"]}\n'
        self.signals.zephyr_message.emit(display)

        with open(self.xml_filename, "a") as xml:
            xml.write(display)

    def _write_tm_file(self, raw: bytes) -> None:
        """Write a TM message to a timestamped .dat file in tm_dir.

        raw must be the exact bytes received from the instrument: the XML header
        through </CRC>\\n, followed immediately by the binary block START...END.
        """
        date, _, time_file, milliseconds = GetDateTime()
        filename = self.tm_dir + "/TM_" + date + "T" + time_file + "-" + milliseconds + "." + self.instrument + ".dat"

        with open(filename, "wb") as tm_file:
            tm_file.write(raw)

    def _dispatch_frame(self, result: FrameResult) -> None:
        """Handle one FrameResult produced by ZephyrFramer (dedicated-port path)."""
        if result.status is FrameStatus.FRAMING_ERROR:
            self.signals.diagnostics_message.emit(WARNING, "Framing error", result.detail)
            return

        if result.status is FrameStatus.CRC_ERROR:
            self.signals.diagnostics_message.emit(WARNING, "CRC error", result.detail)

        header_str = result.header.decode("ascii", errors="ignore")
        try:
            msg_dict = xmltodict.parse(f"<XMLTOKEN>{header_str}</XMLTOKEN>")
        except Exception as exc:
            self.signals.diagnostics_message.emit(ERROR, "Error parsing XML", f"{exc}\n{header_str}")
            return

        if result.tag == "TM":
            self._write_tm_file(result.raw)
            self.signals.command_message.emit("TMAck")
        elif result.tag == "S":
            self.signals.command_message.emit("SAck")
        elif result.tag == "RA":
            self.signals.command_message.emit("RAAck")

        self._emit_zephyr_message(msg_dict)

    def _on_log_ready_read(self) -> None:
        """Slot: drain the dedicated log port and emit complete lines."""
        if self.log_port is None:
            return
        self._log_buffer.extend(bytes(self.log_port.readAll()))

        while True:
            newline_idx = self._log_buffer.find(b"\n")
            if newline_idx < 0:
                return
            line = bytes(self._log_buffer[: newline_idx + 1])
            del self._log_buffer[: newline_idx + 1]
            self._emit_log_message(line.decode("ascii", errors="ignore"))

    # TEMPORARY: flip one bit every N bytes to test CRC verification
    _corrupt_counter = 0
    _CORRUPT_EVERY = 2000

    def _corrupt_for_testing(self, data: bytes) -> bytes:
        """Randomly flip or drop one byte every _CORRUPT_EVERY bytes for bench testing."""
        result = bytearray(data)
        for i in range(len(result)):
            SerialProcessor._corrupt_counter += 1
            if SerialProcessor._corrupt_counter >= self._CORRUPT_EVERY:
                SerialProcessor._corrupt_counter = 0
                if random.random() < 0.5:
                    result[i] ^= 0x01  # flip LSB
                else:
                    del result[i]      # drop byte
                return bytes(result)
        return bytes(result)

    def _on_zephyr_ready_read(self) -> None:
        """Slot: feed incoming bytes to ZephyrFramer and dispatch each frame."""
        raw = self.zephyr_port.readAll().data()
        if self.corrupt_serial:
            raw = self._corrupt_for_testing(raw)
        for result in self._framer.feed(raw):
            self._dispatch_frame(result)

