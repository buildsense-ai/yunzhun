"""RFC 3501 modified UTF-7 codec for IMAP folder names (e.g. 已发送).

Python's stdlib codec is base64 UTF-7, which differs from the IMAP variant
(shift character '&', comma instead of '/', no implicit terminators handling).
"""
from __future__ import annotations

import base64


def decode(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = s.find("-", i + 1)
        if end == -1:  # malformed; keep literal
            out.append(ch)
            i += 1
            continue
        b64 = s[i + 1 : end]
        if b64 == "":
            out.append("&")  # "&-" encodes a literal '&'
        else:
            padded = b64.replace(",", "/") + "=" * ((4 - len(b64) % 4) % 4)
            out.append(base64.b64decode(padded).decode("utf-16-be"))
        i = end + 1
    return "".join(out)


def encode(s: str) -> str:
    out: list[str] = []
    buf: list[str] = []
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            if buf:
                out.append(_b64_utf7("".join(buf)))
                buf = []
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    if buf:
        out.append(_b64_utf7("".join(buf)))
    return "".join(out)


def _b64_utf7(text: str) -> str:
    b64 = base64.b64encode(text.encode("utf-16-be")).decode("ascii").rstrip("=")
    return "&" + b64.replace("/", ",") + "-"
