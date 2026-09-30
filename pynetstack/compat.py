# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Small, explicit CPython/MicroPython helpers; no third-party shims."""
import binascii
import os
import sys

IS_MICROPYTHON = sys.implementation.name == "micropython"

try:
    from builtins import BufferError, ConnectionError
except ImportError:
    class BufferError(Exception):
        """A bounded application or link queue has no remaining capacity."""

    class ConnectionError(OSError):
        """An operation is invalid for the current transport state."""


def crc32_fallback(data):
    """CRC-32/ISO-HDLC, reflected polynomial; intentionally table-free."""
    value = 0xFFFFFFFF
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


crc32 = getattr(binascii, "crc32", crc32_fallback)


class IPv4Address:
    """Only strict dotted-decimal text and four packed bytes are supported."""
    def __init__(self, address):
        if isinstance(address, (bytes, bytearray)):
            if len(address) != 4:
                raise ValueError("IPv4 addresses require exactly four bytes")
            self.packed = bytes(address)
            return
        if not isinstance(address, str):
            raise ValueError("IPv4 address must be dotted-decimal text or four bytes")
        parts = address.split(".")
        if len(parts) != 4:
            raise ValueError("IPv4 address must contain four decimal octets")
        octets = bytearray()
        for part in parts:
            if (not 1 <= len(part) <= 3 or (len(part) > 1 and part[0] == "0")
                    or any(char < "0" or char > "9" for char in part)):
                raise ValueError("Invalid IPv4 octet")
            value = int(part)
            if value > 255:
                raise ValueError("IPv4 octet exceeds 255")
            octets.append(value)
        self.packed = bytes(octets)

    def __str__(self):
        return ".".join(str(value) for value in self.packed)


class Record:
    """Value equality for small packet containers; treat fields as read-only."""
    def __eq__(self, other):
        return type(self) is type(other) and all(
            getattr(self, name) == getattr(other, name) for name in self._fields)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join(
            "%s=%r" % (name, getattr(self, name)) for name in self._fields))


class BoundedQueue:
    """A short FIFO that raises on overflow, rather than silently dropping data."""
    def __init__(self, capacity):
        if not isinstance(capacity, int) or not 1 <= capacity <= 128:
            raise ValueError("Queue capacity must be an integer in 1..128")
        self.capacity = capacity
        self.items = []

    def __len__(self):
        return len(self.items)

    def append(self, item):
        if len(self.items) >= self.capacity:
            raise BufferError("Queue is full")
        self.items.append(item)

    def popleft(self):
        if not self.items:
            raise IndexError("Queue is empty")
        return self.items.pop(0)


if IS_MICROPYTHON:
    class _RandomSource:
        @staticmethod
        def randbits(count):
            if count != 32:
                raise ValueError("Only 32-bit sequence numbers are required")
            # Board entropy quality is platform-specific. This is a lab stack.
            return int.from_bytes(os.urandom(4), "big")
    secrets = _RandomSource()
else:
    import secrets
