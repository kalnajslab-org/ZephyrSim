#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone framer for the Zephyr instrument->OBC serial protocol.

Feed it arbitrary chunks of bytes (as they arrive from the serial port) and it
returns complete, framed messages one at a time -- or an error indication when
it has to discard bytes. It carries no PyQt / signal-bus dependency so it can be
unit-tested headlessly; SerialProcessor drives it and does the emitting.

Wire format (see StrateoleXML XMLWriter_v5.cpp):

    <TAG>\n
    \t<Node>value</Node>\n      (0+ tab-indented field lines)
    </TAG>\n
    <CRC>nnnnn</CRC>\n           (present for EVERY message)
    START<payload><crc16><END>  (TM only; crc16 is 2 bytes, big-endian)

Only <TM> messages carry the trailing binary block, and its length is known
ONLY from the TM's <Length> field -- the block itself is not self-describing.
An empty binary block is still 10 bytes: b"START" + crc16(2) + b"END".

Control messages (IMR / S / IMAck / TCAck / RA) end at </CRC> with no binary.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

try:
    # Single source of truth when used inside the package.
    from .ZephyrSimUtils import crc16_ccitt
except ImportError:  # pragma: no cover - allows fully standalone use / tests
    def crc16_ccitt(crc: int, data: bytes) -> int:
        msb = crc >> 8
        lsb = crc & 255
        for c in data:
            x = c ^ msb
            x ^= (x >> 4)
            msb = (lsb ^ (x >> 3) ^ (x << 4)) & 255
            lsb = (x ^ (x << 5)) & 255
        return (msb << 8) + lsb


class FrameStatus(Enum):
    MESSAGE = "message"              # complete frame, CRC valid (or checking disabled)
    CRC_ERROR = "crc_error"          # complete frame, but a CRC did not match
    FRAMING_ERROR = "framing_error"  # bytes discarded (junk / malformed framing)


@dataclass
class FrameResult:
    """One unit produced by the framer.

    For MESSAGE / CRC_ERROR the frame is fully delimited and `raw` holds the
    exact bytes consumed. For FRAMING_ERROR, `raw` holds the discarded bytes.
    CRC failures are reported but NOT swallowed -- the bytes are still returned
    so the caller can decide whether to use or drop them.
    """

    status: FrameStatus
    tag: Optional[str] = None     # e.g. "TM", "IMR", "S", "IMAck", "TCAck"
    header: bytes = b""           # XML portion, through "</CRC>"
    binary: bytes = b""           # full START..END block (TM only; else b"")
    length: int = 0               # declared payload length from <Length>
    raw: bytes = b""              # exact consumed bytes (or discarded junk)
    detail: str = ""              # human-readable note (esp. for errors)

    @property
    def payload(self) -> bytes:
        """The data bytes between START and the trailing crc16+END (TM only)."""
        return self.binary[5:-5] if self.binary else b""

    @property
    def ok(self) -> bool:
        return self.status is FrameStatus.MESSAGE


class ZephyrFramer:
    """Incremental framer. push() bytes, then poll()/feed() to extract frames."""

    _CRC_INIT = 0x1021
    _CRC_CLOSE = b"</CRC>"
    _NEWLINES = (0x0A, 0x0D)

    _OPEN_RE = re.compile(rb"^<([A-Za-z][A-Za-z0-9]*)>")
    _LEN_RE = re.compile(rb"<Length>\s*(\d+)\s*</Length>")

    def __init__(self, verify_crc: bool = True) -> None:
        self._buf = bytearray()
        self._verify_crc = verify_crc

    # -- input -------------------------------------------------------------
    def push(self, data: bytes) -> None:
        """Append newly received bytes to the internal buffer.

        Use push() when you want to add data and extract frames separately.
        For the common readyRead case — add a chunk and get all ready frames
        at once — prefer feed(), which combines push() + poll()-drain.
        """
        self._buf.extend(data)

    def feed(self, data: bytes) -> List[FrameResult]:
        """Append data and return every frame currently extractable.

        Convenience wrapper for a serial readyRead handler: push(data) followed
        by poll() in a loop until None. Typical usage:

            for result in framer.feed(raw):
                dispatch(result)

        Returns an empty list when data arrives mid-frame and no complete frame
        is ready yet; the bytes are buffered and will appear in a future call.
        """
        self.push(data)
        results: List[FrameResult] = []
        while True:
            result = self.poll()
            if result is None:
                return results
            results.append(result)

    def __iter__(self):
        """Drain all currently extractable frames as a Python iterator.

        Makes ZephyrFramer directly iterable after push()-ing data:

            framer.push(raw)
            for result in framer:
                dispatch(result)

        Equivalent to calling poll() in a loop until None, yielding each
        FrameResult. Stops as soon as the buffer lacks enough bytes to
        complete the next frame — it does NOT block waiting for more data.
        For the combined push-and-drain pattern, prefer feed().
        """
        while True:
            result = self.poll()
            if result is None:
                return
            yield result

    # -- extraction --------------------------------------------------------
    def poll(self) -> Optional[FrameResult]:
        """Return the next frame/error, or None if more bytes are needed.

        Every non-None return consumes at least one byte, so a caller may loop
        on poll() until it returns None without risking an infinite loop.
        """
        # 1. Skip benign inter-message separators. writeCRC() ends every message
        #    with "</CRC>\n", so a leading newline is expected, not junk.
        while self._buf and self._buf[0] in self._NEWLINES:
            del self._buf[:1]

        # Resync to the start of a tag. Any remaining non-newline bytes before
        # the first '<' can never be part of a message, so discard and report.
        idx = self._buf.find(b"<")
        if idx < 0:
            return None  # no tag start yet; wait for more (buffer left intact)
        if idx > 0:
            junk = bytes(self._buf[:idx])
            del self._buf[:idx]
            return FrameResult(
                FrameStatus.FRAMING_ERROR, raw=junk,
                detail=f"discarded {len(junk)} byte(s) before '<'",
            )

        # 2. The XML part runs through the first "</CRC>".
        crc_end = self._buf.find(self._CRC_CLOSE)
        if crc_end < 0:
            return None  # XML not fully arrived
        xml_end = crc_end + len(self._CRC_CLOSE)
        header = bytes(self._buf[:xml_end])

        # 3. Validate the opening tag. A stray '<' (e.g. after a desync) that is
        #    not a real opening tag is dropped so we can resync on the next one.
        m = self._OPEN_RE.match(header)
        if not m:
            del self._buf[:1]
            return FrameResult(
                FrameStatus.FRAMING_ERROR, raw=b"<",
                detail="'<' not followed by a valid opening tag",
            )
        tag = m.group(1).decode("ascii", "replace")

        # 4. Non-TM messages end at </CRC>; only TM carries a binary block.
        if tag != "TM":
            del self._buf[:xml_end]
            return self._finish(tag, header, b"", 0, header)

        len_match = self._LEN_RE.search(header)
        if not len_match:
            del self._buf[:xml_end]
            return FrameResult(
                FrameStatus.FRAMING_ERROR, tag=tag, header=header, raw=header,
                detail="TM without a <Length> field",
            )
        length = int(len_match.group(1))

        # Skip the newline(s) the instrument sends between </CRC> and START;
        # they may not all have arrived yet.
        pos = xml_end
        while pos < len(self._buf) and self._buf[pos] in self._NEWLINES:
            pos += 1
        block_len = 5 + length + 2 + 3  # START + payload + crc16 + END
        if len(self._buf) - pos < block_len:
            return None  # binary block not fully arrived

        binary = bytes(self._buf[pos:pos + block_len])
        raw = bytes(self._buf[:pos + block_len])
        del self._buf[:pos + block_len]

        if binary[:5] != b"START" or binary[-3:] != b"END":
            return FrameResult(
                FrameStatus.FRAMING_ERROR, tag=tag, header=header, binary=binary,
                length=length, raw=raw,
                detail=f"bad binary framing: {binary[:5]!r}..{binary[-3:]!r}",
            )

        return self._finish(tag, header, binary, length, raw)

    # -- helpers -----------------------------------------------------------
    def _finish(self, tag: str, header: bytes, binary: bytes,
                length: int, raw: bytes) -> FrameResult:
        status = FrameStatus.MESSAGE
        detail = ""
        if self._verify_crc:
            good, detail = self._check_crc(header, binary)
            if not good:
                status = FrameStatus.CRC_ERROR
        return FrameResult(
            status, tag=tag, header=header, binary=binary,
            length=length, raw=raw, detail=detail,
        )

    def _check_crc(self, header: bytes, binary: bytes) -> Tuple[bool, str]:
        # XML CRC is computed over everything before "<CRC>".
        crc_open = header.rfind(b"<CRC>")
        crc_close = header.rfind(b"</CRC>")
        if crc_open < 0 or crc_close < 0:
            return False, "missing CRC tag"
        try:
            expected = int(header[crc_open + 5:crc_close])
        except ValueError:
            return False, "non-numeric XML CRC value"
        computed = crc16_ccitt(self._CRC_INIT, header[:crc_open])
        if computed != expected:
            return False, f"XML CRC mismatch: expected {expected}, computed {computed}"

        # Binary CRC (2 bytes, big-endian) sits just before END, over the payload.
        if binary:
            payload = binary[5:-5]
            exp_bin = int.from_bytes(binary[-5:-3], "big")
            got_bin = crc16_ccitt(self._CRC_INIT, payload)
            if got_bin != exp_bin:
                return False, f"binary CRC mismatch: expected {exp_bin}, computed {got_bin}"
        return True, ""


if __name__ == "__main__":
    # ------------------------------------------------------------------ helpers
    def _crc_line(xml: str) -> bytes:
        b = xml.encode("ascii")
        return b + b"<CRC>" + str(crc16_ccitt(0x1021, b)).encode() + b"</CRC>\n"

    def _tm(payload: bytes) -> bytes:
        xml = f"<TM>\n\t<Inst>RATS</Inst>\n\t<Length>{len(payload)}</Length>\n</TM>\n"
        head = _crc_line(xml)
        crc = crc16_ccitt(0x1021, payload)
        return head + b"START" + payload + bytes([crc >> 8, crc & 0xFF]) + b"END"

    def _tm_with_sep(payload: bytes) -> bytes:
        """TM that has a \\n separator between </CRC> and START (mirrors the
        original SerialProcessor bug where this byte arrived in a separate chunk
        and was mis-read as the first byte of the binary block)."""
        xml = f"<TM>\n\t<Inst>RATS</Inst>\n\t<Length>{len(payload)}</Length>\n</TM>\n"
        head = _crc_line(xml)
        crc = crc16_ccitt(0x1021, payload)
        return head + b"\n" + b"START" + payload + bytes([crc >> 8, crc & 0xFF]) + b"END"

    def _tm_truncated(payload: bytes, missing: int = 2) -> bytes:
        """TM where <Length> declares len(payload) but the binary block is
        `missing` bytes short. The framer reads those missing bytes from
        whatever follows, desynchronising the stream."""
        xml = f"<TM>\n\t<Inst>RATS</Inst>\n\t<Length>{len(payload)}</Length>\n</TM>\n"
        head = _crc_line(xml)
        truncated = payload[:-missing]
        crc = crc16_ccitt(0x1021, truncated)
        return head + b"START" + truncated + bytes([crc >> 8, crc & 0xFF]) + b"END"

    def _tm_bad_xml_crc(payload: bytes) -> bytes:
        """TM whose XML <CRC> value is deliberately wrong."""
        xml = f"<TM>\n\t<Inst>RATS</Inst>\n\t<Length>{len(payload)}</Length>\n</TM>\n"
        head = xml.encode("ascii") + b"<CRC>0</CRC>\n"  # 0 is almost never correct
        crc = crc16_ccitt(0x1021, payload)
        return head + b"START" + payload + bytes([crc >> 8, crc & 0xFF]) + b"END"

    def _tm_bad_binary_crc(payload: bytes) -> bytes:
        """TM with a correct XML CRC but a corrupted binary CRC."""
        xml = f"<TM>\n\t<Inst>RATS</Inst>\n\t<Length>{len(payload)}</Length>\n</TM>\n"
        head = _crc_line(xml)
        bad_crc = crc16_ccitt(0x1021, payload) ^ 0x0101  # flip two bits
        return head + b"START" + payload + bytes([bad_crc >> 8, bad_crc & 0xFF]) + b"END"

    def _summary(n_sent: int, results: list) -> None:
        n_ok = sum(1 for r in results if r.status is FrameStatus.MESSAGE)
        print(f"  Summary: {n_sent} sent, {n_ok} decoded, {n_sent - n_ok} lost")

    def _check(label: str, result: FrameResult,
               expect_status: FrameStatus, expect_tag: str,
               expect_payload: bytes = b"") -> None:
        """Assert one FrameResult and print a PASS/FAIL line.

        Compares status, tag, and payload against the expected values.
        On failure, prints both the received and expected values so the
        mismatch is immediately visible. Always prints result.detail when
        it is non-empty (e.g. CRC mismatch description).
        """
        ok = (result.status is expect_status
              and result.tag == expect_tag
              and result.payload == expect_payload)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}")
        if not ok:
            print(f"         got  status={result.status.value} tag={result.tag!r} "
                  f"payload={result.payload!r}")
            print(f"         want status={expect_status.value} tag={expect_tag!r} "
                  f"payload={expect_payload!r}")
        if result.detail:
            print(f"         detail: {result.detail}")

    # ------------------------------------------------ test 1: happy-path stream
    print("Test 1: happy-path (data TM, control IMR, empty TM) in 7-byte chunks")
    imr = _crc_line("<IMR>\n\t<Inst>RATS</Inst>\n</IMR>\n")
    stream = _tm(b"hello") + imr + _tm(b"")
    framer = ZephyrFramer()
    got: List[FrameResult] = []
    for i in range(0, len(stream), 7):
        got.extend(framer.feed(stream[i:i + 7]))
    assert len(got) == 3, f"expected 3 frames, got {len(got)}"
    _check("TM  payload=hello", got[0], FrameStatus.MESSAGE, "TM",  b"hello")
    _check("IMR control msg",   got[1], FrameStatus.MESSAGE, "IMR", b"")
    _check("TM  empty payload", got[2], FrameStatus.MESSAGE, "TM",  b"")
    _summary(3, got)

    # --------------------------------- test 2: bad XML CRC then good TM recovery
    print("\nTest 2: bad XML CRC → CRC_ERROR, followed by good TM → MESSAGE")
    stream2 = _tm_bad_xml_crc(b"corrupt") + _tm(b"recover")
    got2 = ZephyrFramer().feed(stream2)
    assert len(got2) == 2, f"expected 2 frames, got {len(got2)}"
    _check("bad XML CRC  → CRC_ERROR",  got2[0], FrameStatus.CRC_ERROR, "TM", b"corrupt")
    _check("good TM      → MESSAGE",    got2[1], FrameStatus.MESSAGE,   "TM", b"recover")
    _summary(2, got2)

    # ------------------------------ test 3: bad binary CRC then good TM recovery
    print("\nTest 3: bad binary CRC → CRC_ERROR, followed by good TM → MESSAGE")
    stream3 = _tm_bad_binary_crc(b"bincorrupt") + _tm(b"recover")
    got3 = ZephyrFramer().feed(stream3)
    assert len(got3) == 2, f"expected 2 frames, got {len(got3)}"
    _check("bad binary CRC → CRC_ERROR", got3[0], FrameStatus.CRC_ERROR, "TM", b"bincorrupt")
    _check("good TM        → MESSAGE",   got3[1], FrameStatus.MESSAGE,   "TM", b"recover")
    _summary(2, got3)

    # ----------- test 4: blank line between two 2 kB TMs, fed one byte at a time
    # Exercises the inter-message \n skip AND the intra-message \n separator
    # between </CRC> and START (the original SerialProcessor chunk-split bug),
    # both under maximum delivery stress.
    print("\nTest 4: blank between two 2 kB TMs, fed 1 byte at a time")
    payload_2k = os.urandom(2048)
    # TM1 has the \n between </CRC> and START; a blank \n sits between TM1 and TM2.
    stream4 = _tm_with_sep(payload_2k) + b"\n" + _tm(payload_2k)
    got4: List[FrameResult] = []
    framer4 = ZephyrFramer()
    for byte in stream4:
        got4.extend(framer4.feed(bytes([byte])))
    assert len(got4) == 2, f"expected 2 frames, got {len(got4)}"
    _check("TM1 with intra-sep → MESSAGE", got4[0], FrameStatus.MESSAGE, "TM", payload_2k)
    _check("TM2 after blank    → MESSAGE", got4[1], FrameStatus.MESSAGE, "TM", payload_2k)
    _summary(2, got4)

    # -------- test 5: <Length> +2 vs actual payload (stream desync then recovery)
    # The framer reads 2 bytes from the following TM's header into the binary
    # block, producing 4 cascading errors before resyncing on the next message.
    print("\nTest 5: <Length> +2 vs actual payload → desync, then two good TMs recover")
    recover1, recover2 = os.urandom(32), os.urandom(32)
    stream5 = _tm_truncated(os.urandom(64)) + _tm(b"lost") + _tm(recover1) + _tm(recover2)
    got5 = ZephyrFramer().feed(stream5)
    assert len(got5) == 6, f"expected 6 results, got {len(got5)}"
    # [0] truncated TM: binary[-3:] = b'D<T' (consumed 2 bytes of next TM header)
    assert got5[0].status is FrameStatus.FRAMING_ERROR and got5[0].tag == "TM", \
        f"[0] expected FRAMING_ERROR/TM, got {got5[0].status.value}/{got5[0].tag}"
    # [1] 4 junk bytes ('M>\n\t') left over from the shredded TM header
    assert got5[1].status is FrameStatus.FRAMING_ERROR and got5[1].tag is None, \
        f"[1] expected FRAMING_ERROR/None, got {got5[1].status.value}/{got5[1].tag}"
    # [2] 'lost' TM body mis-parsed starting at <Inst> with wrong CRC
    assert got5[2].status is FrameStatus.CRC_ERROR and got5[2].tag == "Inst", \
        f"[2] expected CRC_ERROR/Inst, got {got5[2].status.value}/{got5[2].tag}"
    # [3] 14 junk bytes: START+lost(4)+crc16(2)+END — no '<' so all discarded
    assert got5[3].status is FrameStatus.FRAMING_ERROR and got5[3].tag is None, \
        f"[3] expected FRAMING_ERROR/None, got {got5[3].status.value}/{got5[3].tag}"
    # [4-5] framer resyncs on the two recovery TMs
    print(f"  [PASS] truncated TM      → FRAMING_ERROR ({got5[0].detail})")
    print(f"  [PASS] shredded header   → FRAMING_ERROR ({got5[1].detail})")
    print(f"  [PASS] lost TM destroyed → CRC_ERROR     ({got5[2].detail})")
    print(f"  [PASS] binary as junk    → FRAMING_ERROR ({got5[3].detail})")
    _check("recover1 → MESSAGE", got5[4], FrameStatus.MESSAGE, "TM", recover1)
    _check("recover2 → MESSAGE", got5[5], FrameStatus.MESSAGE, "TM", recover2)
    _summary(4, got5)
