#!/usr/bin/env python3
"""
This module handles incoming serial data from instruments and emits parsed
messages through the shared Zephyr signal bus.
"""
# -*- coding: utf-8 -*-

import datetime
from typing import Optional

import xmltodict
from PyQt6 import QtCore, QtSerialPort

import ZephyrSignals


def GetDateTime() -> tuple:
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
        shared_ports: bool,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.signals = app_signals
        self.log_port = logport
        self.zephyr_port = zephyrport
        self.inst_filename = inst_filename
        self.xml_filename = xml_filename
        self.tm_dir = tm_dir
        self.instrument = instrument
        self.port_sharing = shared_ports

        self._log_buffer = bytearray()
        self._zephyr_buffer = bytearray()

        self._pending_tm_header: Optional[str] = None
        self._pending_tm_remaining = 0
        self._pending_tm_binary = bytearray()

        self.zephyr_port.clear(QtSerialPort.QSerialPort.Direction.Input)
        if not self.port_sharing and self.log_port is not None:
            self.log_port.clear(QtSerialPort.QSerialPort.Direction.Input)

        if self.port_sharing:
            self.zephyr_port.readyRead.connect(self._on_shared_ready_read)
        else:
            self.zephyr_port.readyRead.connect(self._on_zephyr_ready_read)
            if self.log_port is not None:
                self.log_port.readyRead.connect(self._on_log_ready_read)

    def _emit_log_message(self, message: str) -> None:
        message = message.rstrip() + "\n"

        _, time_val, _, milliseconds = GetDateTime()
        timestring = "[" + time_val + "." + milliseconds + "] "

        display_msg = timestring + message
        self.signals.log_message.emit(display_msg)

        with open(self.inst_filename, "a") as inst:
            inst.write(display_msg)

    def _emit_zephyr_message(self, msg_dict: dict) -> None:
        _, time_val, _, milliseconds = GetDateTime()
        timestring = "[" + time_val + "." + milliseconds + "] "
        display = f'{timestring} (FROM){msg_dict["XMLTOKEN"]}\n'
        self.signals.zephyr_message.emit(display)

        with open(self.xml_filename, "a") as xml:
            xml.write(display)

    def _write_tm_file(self, message: str, binary: bytes) -> None:
        date, _, time_file, _ = GetDateTime()
        filename = self.tm_dir + "/TM_" + date + "T" + time_file + "." + self.instrument + ".dat"

        with open(filename, "wb") as tm_file:
            tm_file.write(message.encode())
            tm_file.write(binary)

    def _start_or_emit_from_xml(self, message: str) -> None:
        try:
            msg_dict = xmltodict.parse(f"<XMLTOKEN>{message}</XMLTOKEN>")
        except Exception as exc:
            print("Error parsing XML,", exc)
            print("Message:", message)
            return

        msg_type = list(msg_dict["XMLTOKEN"].keys())[0]
        if msg_type == "TM":
            try:
                self._pending_tm_remaining = 10 + int(msg_dict["XMLTOKEN"]["TM"]["Length"])
                self._pending_tm_binary = bytearray()
                self._pending_tm_header = message
            except Exception as exc:
                print("Error parsing TM length,", exc)
                self._pending_tm_remaining = 0
                self._pending_tm_binary = bytearray()
                self._pending_tm_header = None
        elif msg_type == "S":
            self.signals.command_message.emit("SAck")
        elif msg_type == "RA":
            self.signals.command_message.emit("RAAck")

        self._emit_zephyr_message(msg_dict)

    def _consume_pending_tm_if_ready(self) -> bool:
        if self._pending_tm_remaining <= 0:
            return False
        if len(self._zephyr_buffer) < self._pending_tm_remaining:
            return True

        n_bytes = self._pending_tm_remaining
        self._pending_tm_binary.extend(self._zephyr_buffer[:n_bytes])
        del self._zephyr_buffer[:n_bytes]

        if self._pending_tm_header is not None:
            self._write_tm_file(self._pending_tm_header, bytes(self._pending_tm_binary))
            self.signals.command_message.emit("TMAck")

        self._pending_tm_header = None
        self._pending_tm_binary = bytearray()
        self._pending_tm_remaining = 0
        return False

    def _process_zephyr_stream(self) -> None:
        while True:
            if self._consume_pending_tm_if_ready():
                return

            while self._zephyr_buffer and self._zephyr_buffer[0] in (0x0A, 0x0D):
                del self._zephyr_buffer[0]

            end_marker = b"</CRC>"
            idx = self._zephyr_buffer.find(end_marker)
            if idx < 0:
                return

            end = idx + len(end_marker)
            while end < len(self._zephyr_buffer) and self._zephyr_buffer[end] in (0x0A, 0x0D):
                end += 1

            xml_bytes = bytes(self._zephyr_buffer[:end])
            del self._zephyr_buffer[:end]

            message = xml_bytes.decode("ascii", errors="ignore")
            if message:
                self._start_or_emit_from_xml(message)

    def _process_shared_stream(self) -> None:
        while True:
            if self._consume_pending_tm_if_ready():
                return

            if not self._zephyr_buffer:
                return

            if self._zephyr_buffer[0] in (0x0A, 0x0D):
                del self._zephyr_buffer[0]
                continue

            if self._zephyr_buffer[0] == ord("<"):
                end_marker = b"</CRC>"
                idx = self._zephyr_buffer.find(end_marker)
                if idx < 0:
                    return
                end = idx + len(end_marker)
                while end < len(self._zephyr_buffer) and self._zephyr_buffer[end] in (0x0A, 0x0D):
                    end += 1
                xml_bytes = bytes(self._zephyr_buffer[:end])
                del self._zephyr_buffer[:end]
                message = xml_bytes.decode("ascii", errors="ignore")
                if message:
                    self._start_or_emit_from_xml(message)
                continue

            newline_idx = self._zephyr_buffer.find(b"\n")
            if newline_idx < 0:
                return
            line = bytes(self._zephyr_buffer[: newline_idx + 1])
            del self._zephyr_buffer[: newline_idx + 1]
            self._emit_log_message(line.decode("ascii", errors="ignore"))

    def _on_log_ready_read(self) -> None:
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

    def _on_zephyr_ready_read(self) -> None:
        self._zephyr_buffer.extend(bytes(self.zephyr_port.readAll()))
        self._process_zephyr_stream()

    def _on_shared_ready_read(self) -> None:
        self._zephyr_buffer.extend(bytes(self.zephyr_port.readAll()))
        self._process_shared_stream()
