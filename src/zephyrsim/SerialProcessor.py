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
from . import ZephyrSimUtils
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
        shared_ports: bool,
        corrupt_serial: bool = False,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        """Wire up serial ports and connect readyRead signals.

        Args:
            app_signals:    Shared signal bus used to emit log, XML, command,
                            and diagnostics events to the rest of the app.
            logport:        Serial port carrying plain-text instrument log lines
                            (dedicated-port mode only; None if unused).
            zephyrport:     Serial port carrying Zephyr XML/binary messages.
                            In shared-port mode this port also carries log lines
                            interleaved with Zephyr messages.
            inst_filename:  Path to the file where log lines are appended.
            xml_filename:   Path to the file where Zephyr XML messages are appended.
            tm_dir:         Directory where received TM binary files are written.
            instrument:     Instrument identifier string used in TM filenames.
            shared_ports:   True if log and Zephyr traffic share a single port;
                            False if they arrive on separate ports.
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
        self.port_sharing = shared_ports
        self.corrupt_serial = corrupt_serial

        self._log_buffer = bytearray()
        self._zephyr_buffer = bytearray()

        # Used by the dedicated-port framer.
        self._framer = ZephyrFramer()

        # Used by the shared-stream path (deferred; see zephyr-framing-design.md).
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

    @staticmethod
    def _xml_header(message: str) -> str:
        """Return only the XML portion of a message (everything before START)."""
        start_idx = message.find("START")
        return message[:start_idx].strip() if start_idx >= 0 else message.strip()

    def _verify_crc(self, message: str) -> bool:
        """Check the XML CRC tag and emit a diagnostic if it does not match.

        Used by the shared-stream path. The dedicated-port path delegates CRC
        checking to ZephyrFramer. Returns True if the CRC is valid.
        """
        crc_open = message.rfind("<CRC>")
        crc_close = message.rfind("</CRC>")
        if crc_open < 0 or crc_close < 0:
            self.signals.diagnostics_message.emit(ERROR, "CRC error", f"Missing CRC tag: {self._xml_header(message)}")
            return False
        content = message[:crc_open]
        try:
            expected = int(message[crc_open + 5:crc_close])
        except ValueError:
            self.signals.diagnostics_message.emit(ERROR, "CRC error", f"Non-numeric CRC value: {self._xml_header(message)}")
            return False
        computed = ZephyrSimUtils.crc16_ccitt(0x1021, content.encode("ASCII"))
        if computed != expected:
            self.signals.diagnostics_message.emit(
                WARNING, f"CRC mismatch: expected {expected}, computed {computed}", self._xml_header(message)
            )
            return False
        return True

    def _start_or_emit_from_xml(self, message: str) -> None:
        """Parse one XML message from the shared-stream path and act on it.

        Verifies the CRC, parses with xmltodict, dispatches acks, and emits
        the zephyr_message signal. For TM messages, sets _pending_tm_* so that
        _consume_pending_tm_if_ready can collect the following binary block.
        """
        self._verify_crc(message)
        try:
            msg_dict = xmltodict.parse(f"<XMLTOKEN>{message}</XMLTOKEN>")
        except Exception as exc:
            self.signals.diagnostics_message.emit(ERROR, "Error parsing XML", f"{exc}\n{message}")
            return

        msg_type = list(msg_dict["XMLTOKEN"].keys())[0]
        if msg_type == "TM":
            try:
                self._pending_tm_remaining = 10 + int(msg_dict["XMLTOKEN"]["TM"]["Length"])
                self._pending_tm_binary = bytearray()
                self._pending_tm_header = message
            except Exception as exc:
                self.signals.diagnostics_message.emit(ERROR, "Error parsing TM length", f"{exc}\n{message}")
                self._pending_tm_remaining = 0
                self._pending_tm_binary = bytearray()
                self._pending_tm_header = None
        elif msg_type == "S":
            self.signals.command_message.emit("SAck")
        elif msg_type == "RA":
            self.signals.command_message.emit("RAAck")

        self._emit_zephyr_message(msg_dict)

    def _verify_tm_binary_crc(self, binary: bytearray, header: str) -> None:
        """Check the START/END framing and binary CRC of a TM payload.

        Used by the shared-stream path. Emits a WARNING diagnostic for any
        framing or CRC failure but does not raise; the caller continues regardless.
        """
        xml = self._xml_header(header)
        if len(binary) < 10:
            self.signals.diagnostics_message.emit(WARNING, "TM payload error", f"Binary too short to contain framing\n{xml}")
            return
        if binary[:5] != b'START' or binary[-3:] != b'END':
            self.signals.diagnostics_message.emit(
                WARNING, "TM payload error",
                f"Framing invalid: starts={binary[:5]!r} ends={binary[-3:]!r}\n{xml}"
            )
            return
        payload = binary[5:-5]
        expected = int.from_bytes(binary[-5:-3], 'big')
        computed = ZephyrSimUtils.crc16_ccitt(0x1021, bytes(payload))
        if computed != expected:
            self.signals.diagnostics_message.emit(
                WARNING, "TM payload error", f"CRC mismatch: expected {expected}, computed {computed}\n{xml}"
            )

    def _consume_pending_tm_if_ready(self) -> bool:
        """Pull the TM binary block from _zephyr_buffer when enough bytes have arrived.

        Used by the shared-stream path. Returns True if a binary block is still
        expected but the buffer does not yet contain enough bytes (caller should
        wait for more data). Returns False when no binary is pending or when the
        block has just been consumed and verified.
        """
        if self._pending_tm_remaining <= 0:
            return False
        # Strip the newline separator that the instrument sends between the XML
        # and the binary START framing. It often arrives in a separate read chunk
        # and is therefore not consumed by the trailing-whitespace strip in the
        # XML parser. Only do this before any binary bytes have been buffered.
        if not self._pending_tm_binary:
            while self._zephyr_buffer and self._zephyr_buffer[0] in (0x0A, 0x0D):
                del self._zephyr_buffer[0]
        if len(self._zephyr_buffer) < self._pending_tm_remaining:
            return True

        n_bytes = self._pending_tm_remaining
        self._pending_tm_binary.extend(self._zephyr_buffer[:n_bytes])
        del self._zephyr_buffer[:n_bytes]

        if self._pending_tm_header is not None:
            self._verify_tm_binary_crc(self._pending_tm_binary, self._pending_tm_header)
            raw = self._pending_tm_header.encode() + bytes(self._pending_tm_binary)
            self._write_tm_file(raw)
            self.signals.command_message.emit("TMAck")

        self._pending_tm_header = None
        self._pending_tm_binary = bytearray()
        self._pending_tm_remaining = 0
        return False

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

    def _process_shared_stream(self) -> None:
        """Drain _zephyr_buffer in shared-port mode (log lines + Zephyr XML on one port).

        Routes each byte run to either _start_or_emit_from_xml (when the buffer
        starts with '<') or _emit_log_message (plain-text log lines). Returns
        whenever the buffer is exhausted or a complete unit cannot yet be formed.
        Note: replacing this with ZephyrFramer-based SharedStreamDemux is deferred
        (see docs/zephyr-framing-design.md).
        """
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

    def _on_shared_ready_read(self) -> None:
        """Slot: buffer incoming bytes and run the shared-stream demux."""
        self._zephyr_buffer.extend(bytes(self.zephyr_port.readAll()))
        self._process_shared_stream()
