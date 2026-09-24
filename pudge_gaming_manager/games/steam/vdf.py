"""A reader for Valve's KeyValues text format (VDF / ACF).

``libraryfolders.vdf`` and every ``appmanifest_*.acf`` use this format: a tree
of quoted keys and values, nested with braces::

    "AppState"
    {
        "appid"       "730"
        "name"        "Counter-Strike 2"
        "installdir"  "Counter-Strike Global Offensive"
    }

This is a *text* format, not JSON, and Steam files are UTF-8. The parser is
deliberately small and total: it never executes anything, tolerates the
comment lines and stray whitespace Steam writes, and returns a plain nested
``dict``. Malformed input raises :class:`VdfError` rather than guessing —
these files decide what gets deleted, so a misread must fail loudly.
"""

from __future__ import annotations


class VdfError(ValueError):
    """The KeyValues text could not be parsed."""


def loads(text: str) -> dict:
    """Parse KeyValues text into a nested dict of str -> (str | dict)."""
    return _Parser(text).parse()


class _Parser:
    __slots__ = ("_text", "_i", "_n")

    def __init__(self, text: str) -> None:
        self._text = text
        self._i = 0
        self._n = len(text)

    def parse(self) -> dict:
        root: dict = {}
        while True:
            self._skip_trivia()
            if self._i >= self._n:
                return root
            key = self._read_string()
            value = self._read_value()
            root[key] = value

    def _read_value(self) -> str | dict:
        self._skip_trivia()
        if self._i >= self._n:
            raise VdfError("a key has no value")
        if self._text[self._i] == "{":
            return self._read_object()
        return self._read_string()

    def _read_object(self) -> dict:
        self._i += 1  # consume '{'
        obj: dict = {}
        while True:
            self._skip_trivia()
            if self._i >= self._n:
                raise VdfError("unterminated object")
            if self._text[self._i] == "}":
                self._i += 1
                return obj
            key = self._read_string()
            obj[key] = self._read_value()

    def _read_string(self) -> str:
        self._skip_trivia()
        if self._i >= self._n or self._text[self._i] != '"':
            raise VdfError(f"expected a quoted string at offset {self._i}")
        self._i += 1
        chars: list[str] = []
        while self._i < self._n:
            ch = self._text[self._i]
            if ch == "\\":
                # Steam escapes \\ and \" ; keep other escapes literal.
                nxt = self._text[self._i + 1] if self._i + 1 < self._n else ""
                chars.append(nxt if nxt in ('"', "\\") else ch + nxt)
                self._i += 2
                continue
            if ch == '"':
                self._i += 1
                return "".join(chars)
            chars.append(ch)
            self._i += 1
        raise VdfError("unterminated string")

    def _skip_trivia(self) -> None:
        """Skip whitespace and ``//`` line comments between tokens."""
        while self._i < self._n:
            ch = self._text[self._i]
            if ch in " \t\r\n":
                self._i += 1
            elif ch == "/" and self._text[self._i : self._i + 2] == "//":
                newline = self._text.find("\n", self._i)
                self._i = self._n if newline == -1 else newline + 1
            else:
                return
