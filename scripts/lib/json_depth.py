"""Reject over-deep JSON before decoding, with the same result on every supported Python.

Python 3.14 measures C stack use instead of counting calls, so json.loads can decode
nesting that 3.9-3.13 refused with RecursionError. Callers that must refuse deep input
therefore check the depth themselves instead of relying on RecursionError.
"""
from __future__ import annotations

import json
import re

MAX_DEPTH = 256
_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', re.DOTALL)


class JSONDepthError(ValueError):
    pass


def check(text, limit: int = MAX_DEPTH) -> None:
    """Raise JSONDepthError when arrays/objects nest deeper than limit; strings are skipped."""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode(json.detect_encoding(text))
        except (UnicodeDecodeError, LookupError):
            # Leave the encoding error to json.loads so callers keep their existing message.
            return
    depth = 0
    for match in _TOKENS.finditer(text):
        token = match.group()
        if token in "[{":
            depth += 1
            if depth > limit:
                raise JSONDepthError(f"JSON nesting deeper than {limit} levels")
        elif token in "]}":
            depth -= 1


def loads(text, *, limit: int = MAX_DEPTH, **kwargs):
    check(text, limit)
    return json.loads(text, **kwargs)
