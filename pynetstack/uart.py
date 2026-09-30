# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""MicroPython UART/DE backend for an external 3.3 V RS-485 transceiver."""
import time
from .framing import MAX_BODY
from .timing import get_clock


class UartPort:
    """Adapt machine.UART and a tied DE + /RE GPIO to the byte-port API.

    GPIO high: driver enabled, receiver disabled. GPIO low: receive mode.
    read() returns b'' when idle; write() drains TX before releasing the bus.
    A failed write releases DE and raises; it must not be silently retried by
    this layer. The framing layer can resynchronize at a subsequent flag.
    """
    def __init__(self, uart, enable_pin, *, clock=None, delay_us=None,
                 write_timeout=0.5, setup_us=10, hold_us=10, read_size=256):
        if write_timeout <= 0 or setup_us < 0 or hold_us < 0 or read_size < 1:
            raise ValueError("Invalid UART timing or receive size")
        if not hasattr(uart, "flush"):
            raise RuntimeError("This backend requires UART.flush() support")
        self.uart, self.enable_pin = uart, enable_pin
        self.clock = get_clock(clock)
        self.delay_us = delay_us if delay_us is not None else time.sleep_us
        self.write_timeout, self.setup_us, self.hold_us = write_timeout, setup_us, hold_us
        self.read_size = read_size
        self.closed = False
        self.enable_pin.value(0)

    def read(self, size=None):
        if self.closed:
            raise OSError("UART port is closed")
        amount = self.read_size if size is None else min(size, self.read_size)
        if amount < 1 or not self.uart.any():
            return b""
        # any() may report 1 even when several bytes can be read.
        return self.uart.read(amount) or b""

    def write(self, data):
        if self.closed:
            raise OSError("UART port is closed")
        if len(data) > 2 * MAX_BODY + 2:
            raise ValueError("Write exceeds maximum encoded frame length")
        if not data:
            return
        started, offset = self.clock(), 0
        view = memoryview(data)
        try:
            self.enable_pin.value(1)
            self.delay_us(self.setup_us)
            while offset < len(data):
                if self.clock.diff(self.clock(), started) >= self.write_timeout:
                    raise OSError("UART write timed out")
                written = self.uart.write(view[offset:])
                if written is None or written == 0:
                    self.delay_us(1000)
                    continue
                if not 0 < written <= len(data) - offset:
                    raise OSError("UART returned an invalid write count")
                offset += written
            # Completion means bytes have left the UART, not just its TX queue.
            # ESP32 UART.flush() has its own driver-managed timeout.
            self.uart.flush()
            self.delay_us(self.hold_us)
        finally:
            self.enable_pin.value(0)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.enable_pin.value(0)
            finally:
                self.uart.deinit()


def open_esp32c6_port(*, baudrate=115200, tx_pin=21, rx_pin=20, de_pin=18,
                     rx_buffer=4096, tx_buffer=512, clock=None):
    """Open UART1 on ESP32-C6-DevKitC-1; does not use the USB/REPL UART."""
    from machine import Pin, UART
    if len({tx_pin, rx_pin, de_pin}) != 3 or baudrate <= 0:
        raise ValueError("Use distinct TX, RX and DE pins and a positive baud rate")
    if rx_buffer < 256 or tx_buffer < 256:
        raise ValueError("UART buffers must be at least 256 bytes")
    enable = Pin(de_pin, Pin.OUT, value=0)
    uart = None
    try:
        uart = UART(1, baudrate=baudrate, bits=8, parity=None, stop=1,
                    tx=Pin(tx_pin), rx=Pin(rx_pin), rxbuf=rx_buffer,
                    txbuf=tx_buffer, timeout=0, timeout_char=0, flow=0)
        # Allow at least 500 ms, even at the default 115200 baud.
        write_timeout = max(0.5, (2 * MAX_BODY + 2) * 10 / baudrate + 0.1)
        return UartPort(uart, enable, clock=clock, write_timeout=write_timeout)
    except Exception:
        enable.value(0)
        if uart is not None:
            uart.deinit()
        raise
