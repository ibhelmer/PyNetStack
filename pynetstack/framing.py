# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Bounded, incremental serial framing with escaping and CRC-32."""
import struct
from .compat import Record
from .compat import crc32

FLAG = 0x7E
ESCAPE = 0x7D
VERSION = 1
MTU = 1024
BROADCAST = 255
HEADER_FORMAT = "!BBBBHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAX_BODY = HEADER_SIZE + MTU + 4


class FrameType:
    IPV4 = 1
    POLL = 2
    IDLE = 3

    @staticmethod
    def validate(value):
        if value not in (1, 2, 3):
            raise ValueError("Unsupported frame type")
        return value

    @staticmethod
    def name(value):
        return ("", "IPV4", "POLL", "IDLE")[FrameType.validate(value)]


class Frame(Record):
    _fields = ('destination', 'source', 'kind', 'grant', 'payload')

    def __init__(self, destination, source, kind, grant=0, payload=b''):
        self.destination = destination
        self.source = source
        self.kind = kind
        self.grant = grant
        self.payload = payload

    def encode(self):
        if not 0 <= self.destination <= 255 or not 0 <= self.source <= 254:
            raise ValueError("Invalid link address")
        if not 0 <= self.grant <= 65535 or len(self.payload) > MTU:
            raise ValueError("Invalid grant or oversized payload")
        kind = FrameType.validate(self.kind)
        if kind != FrameType.IPV4 and self.payload:
            raise ValueError("Control frames cannot carry payload")
        body = struct.pack(HEADER_FORMAT, VERSION, self.destination, self.source, kind,
                           self.grant, len(self.payload)) + self.payload
        body += struct.pack("!I", crc32(body) & 0xFFFFFFFF)
        escaped = bytearray([FLAG])
        for value in body:
            if value in (FLAG, ESCAPE):
                escaped.append(ESCAPE)
                escaped.append(value ^ 0x20)
            else:
                escaped.append(value)
        escaped.append(FLAG)
        return bytes(escaped)

    @classmethod
    def decode(cls, body):
        if not HEADER_SIZE + 4 <= len(body) <= MAX_BODY:
            raise ValueError("Invalid frame size")
        if crc32(body[:-4]) & 0xFFFFFFFF != struct.unpack("!I", body[-4:])[0]:
            raise ValueError("CRC-32 mismatch")
        version, destination, source, kind, grant, length = struct.unpack_from(HEADER_FORMAT, body)
        if version != VERSION or source == BROADCAST:
            raise ValueError("Unsupported version or invalid source")
        if length != len(body) - HEADER_SIZE - 4:
            raise ValueError("Payload length mismatch")
        frame_type = FrameType.validate(kind)
        if frame_type != FrameType.IPV4 and length:
            raise ValueError("Control frame has payload")
        return cls(destination, source, frame_type, grant, body[HEADER_SIZE:-4])


class FrameDecoder:
    """Discard malformed/oversized frames; resynchronize at the next flag."""

    def __init__(self, on_error = None):
        self.on_error = on_error or (lambda message: None)
        self.buffer = bytearray()
        self.active = False
        self.escaped = False
        self.discard = False

    def feed(self, data):
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
                self.buffer = bytearray()
                self.active, self.escaped, self.discard = True, False, False
            elif self.active and not self.discard:
                if self.escaped:
                    if value not in (FLAG ^ 0x20, ESCAPE ^ 0x20):
                        self.on_error("Invalid escape sequence")
                        self.buffer = bytearray()
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
                    self.buffer = bytearray()
                    self.discard = True
        return frames
