# TM Serial Framing Errors

## Error 1 — TM payload framing invalid

**Log message:**
```
[WARN] TM payload error
    Framing invalid: starts=bytearray(b'\nSTAR') ends=bytearray(b'-EN')
```

**What it means:**

The binary TM payload is expected to start with exactly `START` (5 bytes) and end with `END` (3 bytes). Instead, the first 5 bytes are `\nSTAR` and the last 3 bytes are `-EN`.

The binary is shifted by 1 byte: a `\n` separator byte that the instrument sends between the TM XML and the binary `START` framing has been read as the first byte of the binary payload. This pushes everything forward by one position, so:

- `binary[:5]` = `\nSTAR` instead of `START`
- `binary[-3:]` = `[CRC_byte]EN` instead of `END` (one byte short at the end)

**Why it happens:**

The wire stream is:
```
...<CRC>18948</CRC>  \n  START<payload><2-byte CRC>END
                     ^^
                     separator
```

In `_process_zephyr_stream`, trailing `\n`/`\r` after `</CRC>` are consumed — but only if they are already in the buffer at the time the XML is parsed. When the XML chunk and the binary chunk arrive in **separate serial reads**, the separator `\n` is the first byte of the second chunk. By that point the XML has already been consumed and `_pending_tm_remaining` is set. `_consume_pending_tm_if_ready` then blindly reads the next `10 + Length` bytes starting with the `\n`.

---

## Error 2 — CRC mismatch on the following XML message

**Log message:**
```
[WARN] CRC mismatch: expected 153, computed 59915
    D
<IMR>
    <Msg>88</Msg>
    ...
</IMR>
<CRC>153</CRC>
```

**What it means:**

The CRC check on the `<IMR>` message fails because its content, as seen by the parser, is prefixed with a stray `D` character. The CRC was originally computed over just `<IMR>...</IMR>\n`, but the parser computes it over `D\n<IMR>...</IMR>\n`.

**Why it happens (cascade from Error 1):**

Because the binary was read starting 1 byte early (the `\n`), it also ends 1 byte early — the trailing `D` of the binary `END` framing is left behind in `_zephyr_buffer`. The next XML message (`<IMR>...`) arrives and is appended to that orphaned `D`, producing `D\n<IMR>...</IMR>\n<CRC>153</CRC>`. The leading `D` is not stripped (only `\n`/`\r` are stripped at the start of each parse loop iteration), so the CRC comparison fails.

---

## Root Cause Summary

| # | Error | Cause |
|---|---|---|
| 1 | `Framing invalid: starts=\nSTAR ends=-EN` | `\n` separator between XML and binary arrives in a separate read chunk; it is not consumed by the trailing-whitespace strip in the XML parser, so it becomes the first byte of the binary payload, shifting the read window by 1. |
| 2 | `CRC mismatch: expected 153, computed 59915` on IMR | Cascade from Error 1: the trailing `D` of `END` is left in the buffer and prepended to the next XML message, corrupting its CRC input. |

**Fix** (`SerialProcessor._consume_pending_tm_if_ready`):

Strip any leading `\n`/`\r` from `_zephyr_buffer` before reading binary TM data, but only before any binary bytes have been accumulated (i.e., when `_pending_tm_binary` is empty). This avoids corrupting multi-chunk binary payloads while correctly consuming the protocol separator.
