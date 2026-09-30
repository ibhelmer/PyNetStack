# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""A byte-bus simulator and an optional pySerial RS-485 backend."""
from collections.abc import Callable
from typing import Protocol


class BytePort(Protocol):
    def read(self, size: int = 4096) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


class MemoryBus:
    """Broadcast bytes, not Python packet objects. No electrical timing model."""

    def __init__(self):
        self.ports: dict[int, MemoryPort] = {}
        self.filter: Callable[[int, bytes], bytes | None] = lambda source, data: data

    def connect(self, node: int) -> "MemoryPort":
        if node in self.ports:
            raise ValueError("Duplicate bus node")
        port = MemoryPort(self, node)
        self.ports[node] = port
        return port


class MemoryPort:
    def __init__(self, bus: MemoryBus, node: int):
        self.bus, self.node = bus, node
        self.buffer = bytearray()

    def read(self, size: int = 4096) -> bytes:
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def write(self, data: bytes) -> None:
        filtered = self.bus.filter(self.node, data)
        if filtered is not None:
            for node, port in self.bus.ports.items():
                if node != self.node:
                    if len(port.buffer) + len(filtered) > 1024 * 1024:
                        raise BufferError("Simulated receiver is not being serviced")
                    port.buffer.extend(filtered)

    def close(self) -> None:
        self.bus.ports.pop(self.node, None)


class SerialPort:
    """Use auto-direction adapters by default; native RTS mode is explicit."""

    def __init__(self, port: str, baudrate: int = 115200,
                 direction: str = "auto", rts_active_low: bool = False):
        if baudrate <= 0 or direction not in ("auto", "native"):
            raise ValueError("Invalid baud rate or direction mode")
        try:
            import serial
            from serial.rs485 import RS485Settings
        except ImportError as error:
            raise RuntimeError('Install serial support with: python -m pip install ".[serial]"') from error
        self.serial = serial.Serial(port=None, baudrate=baudrate, timeout=0,
                                    write_timeout=5, xonxoff=False, rtscts=False,
                                    dsrdtr=False)
        try:
            self.serial.port = port
            # Set receive polarity before opening to reduce driver-enable glitches.
            self.serial.rts = rts_active_low
            if direction == "native":
                self.serial.rs485_mode = RS485Settings(
                    rts_level_for_tx=not rts_active_low,
                    rts_level_for_rx=rts_active_low)
            self.serial.open()
        except Exception:
            self.serial.close()
            raise

    def read(self, size: int = 4096) -> bytes:
        return self.serial.read(size)

    def write(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            written = self.serial.write(data[offset:])
            if not written:
                raise OSError("Serial write made no progress")
            offset += written
        self.serial.flush()

    def close(self) -> None:
        self.serial.close()
