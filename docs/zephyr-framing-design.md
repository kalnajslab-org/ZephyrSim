# Zephyr Message Framing — Design Notes

Design for a standalone, PyQt-free framer that turns an arbitrary byte stream
from a Zephyr instrument into complete, validated messages. Captured so we can
resume the `SerialProcessor` integration and the shared-stream demux later.

**Status:** the framer (`src/zephyrsim/ZephyrFramer.py`) is implemented and
unit-smoke-tested. The **shared-stream demux is deferred** (documented below,
not yet built) — parked while chasing a RATS/Zephyr bench error.

---

## 1. Wire format (instrument → OBC)

Source of truth: StrateoleXML `XMLWriter_v5.cpp`.

```
<TAG>\n
\t<Node>value</Node>\n      (0+ tab-indented field lines)
</TAG>\n
<CRC>nnnnn</CRC>\n           (present for EVERY message)
START<payload><crc16>END     (TM ONLY; crc16 = 2 bytes, big-endian)
```

- `TAG` ∈ `{TM, IMR, S, IMAck, TCAck}` for RATS. (`RA` exists in the protocol
  but is RACHUTS-only — RATS never emits it. ZephyrSim is multi-instrument, so
  the framer stays tag-agnostic and only special-cases `TM`.)
- **Only `<TM>` carries the trailing binary block.** The four control messages
  end at `</CRC>` with no binary.
- The binary block is **not self-describing**: its length is known *only* from
  the TM's `<Length>` field. Nothing inside `START…END` delimits the payload
  from the trailing CRC — you cannot scan for `END` (a payload/CRC byte can be
  `'E'`). An empty block is still 10 bytes: `START` + crc16(2) + `END`.
- CRC: `crc16_ccitt(0x1021, …)` (see `ZephyrSimUtils.crc16_ccitt`).
  - XML CRC is over everything before `<CRC>`.
  - Binary CRC is over the payload only (`binary[5:-5]`), stored at
    `binary[-5:-3]` big-endian.

### Framing rule for a parser

```
parse <TAG>…</TAG> ; if TAG==TM grab <Length>=N
read the <CRC>…</CRC> line          # ALWAYS, for every message
if TAG == TM:
    read exactly 5 + N + 2 + 3 bytes  # START + payload + crc16 + END
```

Read the binary block **by count**, never by scanning.

---

## 2. The framer — `ZephyrFramer`

`src/zephyrsim/ZephyrFramer.py`. No PyQt / signal-bus dependency, so it is
headless-testable; `SerialProcessor` drives it and does the emitting.

**API** (shaped for the chunked `readyRead` model):

- `push(data: bytes) -> None` — append received bytes.
- `poll() -> FrameResult | None` — next frame/error, or `None` if more bytes are
  needed. Every non-`None` return consumes ≥1 byte, so callers can loop on
  `poll()` until `None` without risk of an infinite loop.
- `feed(data) -> list[FrameResult]` — `push` + drain all currently extractable.
- iterable (`for r in framer: …`).

**`FrameResult`** — `status` (`MESSAGE` / `CRC_ERROR` / `FRAMING_ERROR`), `tag`,
`header` (XML through `</CRC>`), `binary` (full `START…END` block), `.payload`
(data only), `length`, `raw` (exact consumed bytes, or discarded junk), `detail`.

**Design choices:**

- `<Length>`-gated binary read; binary framed by count.
- CRC reused from `ZephyrSimUtils.crc16_ccitt` (single source of truth) with a
  standalone fallback so it runs outside the package / in tests.
- **CRC failures are reported, not swallowed** — the bytes are still returned
  with `CRC_ERROR` status + `detail`, so the caller decides whether to use or
  drop them (mirrors SerialProcessor's current warn-but-process behavior).
- Benign inter-message newlines (`writeCRC()` ends every message with
  `</CRC>\n`) are skipped silently; only genuine non-newline garbage before `<`
  yields `FRAMING_ERROR`.
- Tag-agnostic; only `TM` triggers a binary read.

---

## 3. SerialProcessor integration (dedicated port)

`_on_zephyr_ready_read` → `framer.feed(bytes(...))`, then loop the returned
`FrameResult`s. Replace the `_process_zephyr_stream` / `_consume_pending_tm_if_ready`
framing logic; keep SerialProcessor's file-writing and signal emits. Map:
`TM` → TM handling + `TMAck`; `S` → `SAck`; ack the others as appropriate.

---

## 4. DEFERRED — shared-stream demux (log text + Zephyr XML on one port)

Not yet built. In shared mode one port interleaves plain-text log lines
(`\n`-terminated, **not** `<…>`-framed) with Zephyr messages. Naive newline/`<`
splitting breaks because a TM's binary payload can contain `\n` and `<`.

**Key correctness property:** make the log-vs-XML decision *only at a message
boundary*. Once a `<` boundary is seen, the framer consumes the whole message
(incl. binary) **by `<Length>` count**, so binary bytes never masquerade as a
log-line boundary.

**Why a shared buffer:** at a `<` boundary you can't hand the framer "everything
in the buffer" — a log line may follow the XML in the same chunk and would be
mis-framed as junk. So the framer must consume *only its one message* and leave
the rest. Cleanest: demux and framer share one buffer; `poll()` removes exactly
one message and the demux resumes routing on what's left.

### Enabling framer change (one line)

Make the buffer injectable:

```python
def __init__(self, verify_crc: bool = True, buffer: Optional[bytearray] = None) -> None:
    self._buf = buffer if buffer is not None else bytearray()
    self._verify_crc = verify_crc

@property
def buffer(self) -> bytearray:
    return self._buf
```

Dedicated mode is unchanged (`buffer=None` → owns its own).

### Demux algorithm

```
loop:
    strip leading \n / \r
    if buffer empty: wait
    if buffer[0] == '<':
        r = framer.poll()          # consumes exactly ONE message or nothing
        if r is None: wait
        dispatch(r)
    else:                          # plain-text log line
        find '\n'; if none: wait
        emit_log(line); drop line
```

### Demux sketch

```python
class SharedStreamDemux:
    """Split an interleaved log(text) + Zephyr(XML+binary) byte stream.

    Log lines are '\n'-terminated plain text; Zephyr messages start with '<'
    and are framed by ZephyrFramer. A TM's binary block is consumed by count,
    so its bytes never masquerade as log-line boundaries.
    """
    def __init__(self, on_log, on_frame, verify_crc=True):
        self._buf = bytearray()
        self._framer = ZephyrFramer(verify_crc=verify_crc, buffer=self._buf)
        self._on_log = on_log       # callable(str)
        self._on_frame = on_frame   # callable(FrameResult)

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)
        while True:
            while self._buf and self._buf[0] in (0x0A, 0x0D):
                del self._buf[:1]
            if not self._buf:
                return
            if self._buf[0] == 0x3C:                 # '<'
                result = self._framer.poll()
                if result is None:
                    return                            # incomplete; await more bytes
                self._on_frame(result)
            else:
                nl = self._buf.find(b"\n")
                if nl < 0:
                    return                            # partial log line; wait
                line = bytes(self._buf[:nl + 1])
                del self._buf[:nl + 1]
                self._on_log(line.decode("ascii", errors="ignore"))
```

Wiring: `_on_shared_ready_read` → `self._demux.feed(...)` with
`on_log=self._emit_log_message` and `on_frame=<dispatch>`.

### Caveats (same ones `_process_shared_stream` already lives with)

- **A log line beginning with `<`** is ambiguous — routed to the framer. If it
  has no `</CRC>`, `poll()` returns `None` forever and the demux stalls (buffer
  grows). In practice firmware log lines don't start with `<` (matches the
  existing `buffer[0] == ord('<')` assumption). Hardening: cap the
  "waiting-for-`</CRC>`" span and, on overflow, treat the leading `<` as a log
  byte.
- **Contiguity assumption:** a TM's XML+binary arrives as one uninterrupted run
  (no log line spliced into the middle of a binary block). True for the
  single-threaded firmware writer; relied upon by the demux.
