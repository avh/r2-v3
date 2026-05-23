"""
Stateful stream parser that detects <<TAG: name ... >> blocks in streamed text.

Supported tags: Q, A, NOTE, FYI
Sentinel \x00THINK\x00<text> is emitted by model backends for thinking content.

Events emitted via parse(chunk) as a list:
  ("text",    text)               — plain assistant text
  ("think",   text)               — thinking content
  ("tag_open", tag, name)         — opening of a tag detected (tag = "Q"|"A"|"NOTE"|"FYI")
  ("tag",     tag, name, body)    — complete tag captured
"""

from dataclasses import dataclass, field
from typing import Iterator

_OPEN = "<<"
_CLOSE = ">>"

# Tags that carry a name/comment after the colon, e.g. <<Q: help  or  <<FYI: time
_NAMED_TAGS = {"Q", "A", "FYI"}
# Tags without a name, e.g. <<NOTE:
_ANON_TAGS = {"NOTE", "REMEMBER"}
_ALL_TAGS = _NAMED_TAGS | _ANON_TAGS


@dataclass
class StreamParser:
    _buf: str = field(default="", init=False)
    _in_tag: bool = field(default=False, init=False)
    _tag_buf: str = field(default="", init=False)
    _current_tag: str = field(default="", init=False)
    _current_name: str = field(default="", init=False)

    def parse(self, chunk: str) -> list[tuple]:
        events: list[tuple] = []

        # Handle think sentinel injected by model backends
        if chunk.startswith("\x00THINK\x00"):
            events.append(("think", chunk[7:]))
            return events

        self._buf += chunk

        while self._buf:
            if self._in_tag:
                # Search the combined buffer so >> split across chunks is detected.
                combined = self._tag_buf + self._buf
                close_pos = combined.find(_CLOSE)
                if close_pos == -1:
                    self._tag_buf = combined
                    self._buf = ""
                    break
                else:
                    body = combined[:close_pos].strip()
                    self._buf = combined[close_pos + 2:]
                    events.append(("tag", self._current_tag, self._current_name, body))
                    self._in_tag = False
                    self._tag_buf = ""
                    self._current_tag = ""
                    self._current_name = ""
            else:
                open_pos = self._buf.find(_OPEN)
                if open_pos == -1:
                    # check if end of buffer could be start of <<
                    if self._buf.endswith("<"):
                        events.append(("text", self._buf[:-1]))
                        self._buf = "<"
                        break  # wait for next chunk to confirm << or not
                    else:
                        events.append(("text", self._buf))
                        self._buf = ""
                else:
                    if open_pos > 0:
                        events.append(("text", self._buf[:open_pos]))
                        self._buf = self._buf[open_pos:]

                    # try to parse tag header
                    # need at least "<<X:" before we know what tag this is
                    # find the first newline or >> to get the header line
                    after_open = self._buf[2:]  # skip <<
                    nl_pos = after_open.find("\n")
                    close_pos = after_open.find(">>")

                    header_end = None
                    if nl_pos != -1 and (close_pos == -1 or nl_pos < close_pos):
                        header_end = nl_pos
                    elif close_pos != -1:
                        header_end = close_pos

                    if header_end is None:
                        # not enough data yet
                        break

                    header = after_open[:header_end].strip()
                    # parse "TAG: name" or "TAG:"
                    if ":" in header:
                        tag_part, name_part = header.split(":", 1)
                        tag = tag_part.strip().upper()
                        name = name_part.strip()
                    else:
                        tag = header.strip().upper()
                        name = ""

                    if tag in _ALL_TAGS:
                        self._in_tag = True
                        self._current_tag = tag
                        self._current_name = name
                        self._tag_buf = ""
                        events.append(("tag_open", tag, name))
                        # advance buffer past the header line
                        self._buf = after_open[header_end + 1:]
                    else:
                        # not a known tag, emit << as text and continue
                        events.append(("text", "<<"))
                        self._buf = self._buf[2:]

        return events

    def flush(self) -> list[tuple]:
        events: list[tuple] = []
        if self._in_tag and self._tag_buf:
            body = self._tag_buf.strip()
            # Strip a partial or accidental closer the model may have included
            if body.endswith(">>"):
                body = body[:-2].strip()
            elif body.endswith(">"):
                body = body[:-1].strip()
            events.append(("tag", self._current_tag, self._current_name, body))
            self._in_tag = False
            self._tag_buf = ""
        if self._buf:
            events.append(("text", self._buf))
            self._buf = ""
        return events
