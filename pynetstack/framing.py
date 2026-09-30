# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Bounded, incremental serial framing with escaping and CRC-32."""
from dataclasses import dataclass
from enum import IntEnum
import struct
import zlib
from collections.abc import Callable

FLAG = 0x7E
ESCAPE = 0x7D
VERSION = 1
MTU = 1024
BROADCAST = 255
HEADER = struct.Struct("!BBBBHH")
MAX_BODY = HEADER.size + MTU + 4


class FrameType(IntEnum):
    IPV4 = 1
    POLL = 2
    IDLE = 3


@dataclass(frozen=True)
class Frame:
    destination: int
    source: int
    kind: FrameType
    grant: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        if not 0 <= self.destination <= 255 or not 0 <= self.source <= 254:
            raise ValueError("Invalid link address")
        if not 0 <= self.grant <= 65535 or len(self.payload) > MTU:
            raise ValueError("Invalid grant or oversized payload")
        kind = FrameType(self.kind)
        if kind != FrameType.IPV4 and self.payload:
            raise ValueError("Control frames cannot carry payload")
        body = HEADER.pack(VERSION, self.destination, self.source, kind,
                           self.grant, len(self.payload)) + self.payload
        body += struct.pack("!I", zlib.crc32(body) & 0xFFFFFFFF)
        escaped = bytearray([FLAG])
        for value in body:
            if value in (FLAG, ESCAPE):
                escaped.extend((ESCAPE, value ^ 0x20))
            else:
                escaped.append(value)
        escaped.append(FLAG)
        return bytes(escaped)

    @classmethod
    def decode(cls, body: bytes) -> "Frame":
        if not HEADER.size + 4 <= len(body) <= MAX_BODY:
            raise ValueError("Invalid frame size")
        if zlib.crc32(body[:-4]) & 0xFFFFFFFF != struct.unpack("!I", body[-4:])[0]:
            raise ValueError("CRC-32 mismatch")
        version, destination, source, kind, grant, length = HEADER.unpack_from(body)
        if version != VERSION or source == BROADCAST:
            raise ValueError("Unsupported version or invalid source")
        if length != len(body) - HEADER.size - 4:
            raise ValueError("Payload length mismatch")
        frame_type = FrameType(kind)
        if frame_type != FrameType.IPV4 and length:
            raise ValueError("Control frame has payload")
        return cls(destination, source, frame_type, grant, body[HEADER.size:-4])


class FrameDecoder:
    """Discard malformed/oversized frames; resynchronize at the next flag."""

    def __init__(self, on_error: Callable[[str], None] | None = None):
        self.on_error = on_error or (lambda message: None)
        self.buffer = bytearray()
        self.active = False
        self.escaped = False
        self.discard = False

    def feed(self, data: bytes) -> list[Frame]:
        frames = []
        for value in data:
            if value == FLAG:
                if self.active and not self.discard:
                    if self.escaped:
                        self.on_error("Truncated escape sequence")
                    elif self.buffer:
                        try:
                            frames.append(Frame.decode(bytes(self.buffer)))
                        except ValueError as error:
                            self.on_error(str(error))
                self.buffer.clear()
                self.active, self.escaped, self.discard = True, False, False
            elif self.active and not self.discard:
                if self.escaped:
                    if value not in (FLAG ^ 0x20, ESCAPE ^ 0x20):
                        self.on_error("Invalid escape sequence")
                        self.buffer.clear()
                        self.discard = True
                        continue
                    self.buffer.append(value ^ 0x20)
                    self.escaped = False
                elif value == ESCAPE:
                    self.escaped = True
                else:
                    self.buffer.append(value)
                if len(self.buffer) > MAX_BODY:
                    self.on_error("Frame exceeds receive limit")
                    self.buffer.clear()
                    self.discard = True
        return frames
