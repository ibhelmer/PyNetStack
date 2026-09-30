# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Portable, bounded byte-bus simulation; not an electrical timing model."""
from .compat import BufferError


class MemoryBus:
    """Broadcast bytes, not Python packet objects. No electrical timing model."""

    def __init__(self):
        self.ports = {}
        self.filter = lambda source, data: data

    def connect(self, node):
        if node in self.ports:
            raise ValueError("Duplicate bus node")
        port = MemoryPort(self, node)
        self.ports[node] = port
        return port


class MemoryPort:
    def __init__(self, bus, node):
        self.bus, self.node = bus, node
        self.buffer = bytearray()

    def read(self, size = 4096):
        result = bytes(self.buffer[:size])
        self.buffer = self.buffer[size:]
        return result

    def write(self, data):
        filtered = self.bus.filter(self.node, data)
        if filtered is not None:
            for node, port in self.bus.ports.items():
                if node != self.node:
                    if len(port.buffer) + len(filtered) > 1024 * 1024:
                        raise BufferError("Simulated receiver is not being serviced")
                    port.buffer.extend(filtered)

    def close(self):
        self.bus.ports.pop(self.node, None)


