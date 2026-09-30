# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Desktop regression tests for the portable core and mocked MicroPython APIs."""
import ast
import binascii
import io
import json
from pathlib import Path
import random
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pynetstack.compat import BoundedQueue, IPv4Address, crc32_fallback
from pynetstack.framing import Frame, FrameType
from pynetstack.link import PollingLink
from pynetstack.simulation import MemoryBus
from pynetstack.stack import NetworkStack
from pynetstack.timing import Clock
from pynetstack.trace import Trace
from pynetstack.uart import UartPort, open_esp32c6_port
from micropython_selftest import FakeTicks, check_codecs, check_network

ROOT = Path(__file__).resolve().parents[1]


class CompatibilityTests(unittest.TestCase):
    def test_crc_fallback_matches_native(self):
        rng = random.Random(2468)
        for length in (0, 1, 2, 7, 256, 1036):
            data = bytes(rng.randrange(256) for _ in range(length))
            self.assertEqual(crc32_fallback(data), binascii.crc32(data))

    def test_strict_ipv4_text_and_packed_round_trip(self):
        for text in ("0.0.0.0", "10.0.0.3", "255.255.255.255"):
            self.assertEqual(str(IPv4Address(IPv4Address(text).packed)), text)

    def test_invalid_ipv4_addresses_rejected(self):
        for text in ("1.2.3", "1.2.3.4.5", "1.2.3.256", "01.2.3.4", " 1.2.3.4",
                     "+1.2.3.4", "1.2.3.-1", "1.2.3.\u0661", b"123", None, 1):
            with self.subTest(address=text), self.assertRaises(ValueError):
                IPv4Address(text)

    def test_queue_overflow_never_evicts_data(self):
        queue = BoundedQueue(2)
        queue.append("first")
        queue.append("second")
        with self.assertRaises(BufferError):
            queue.append("lost")
        self.assertEqual(queue.popleft(), "first")
        self.assertEqual(queue.popleft(), "second")
        self.assertFalse(queue)
        with self.assertRaises(IndexError):
            queue.popleft()

    def test_queue_capacity_validation(self):
        for capacity in (0, -1, 129, 1.5):
            with self.assertRaises(ValueError):
                BoundedQueue(capacity)

    def test_wire_golden_vectors_from_v010(self):
        with patch("builtins.print"):
            check_codecs()

    def test_trace_filter_and_json(self):
        output = io.StringIO()
        trace = Trace(2, console=False, output=output, layers=("TCP",))
        trace.emit("L1", "RX", "hidden", raw=b"secret")
        trace.emit("TCP", "RX", "visible", raw=b"\x7e")
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["raw"], "7e")

    def test_muted_trace_skips_formatting(self):
        with patch("pynetstack.trace.binascii.hexlify", side_effect=AssertionError("Unnecessary hex")):
            Trace(0, console=False).emit("L1", "TX", "silent", raw=b"large")


class TimerTests(unittest.TestCase):
    def test_deadline_across_tick_wrap(self):
        ticks = FakeTicks()
        clock = Clock(ticks=ticks)
        start = clock()
        end = clock.add(start, 0.05)
        self.assertLess(end, start)
        ticks.total += 49
        self.assertLess(clock.diff(clock(), end), 0)
        ticks.total += 1
        self.assertEqual(clock.diff(clock(), end), 0)
        self.assertAlmostEqual(clock.diff(clock(), start), 0.05)

    def test_tick_clock_does_not_accumulate_float_uptime(self):
        ticks = FakeTicks(123456789123456789)
        clock = Clock(ticks=ticks)
        before = clock()
        ticks.total += 1
        self.assertIsInstance(clock(), int)
        self.assertEqual(clock.diff(clock(), before), 0.001)

    def test_seconds_clock_preserves_desktop_injection(self):
        clock = Clock(lambda: 123.25)
        self.assertEqual(clock(), 123.25)
        self.assertEqual(clock.add(clock(), 0.5), 123.75)
        self.assertEqual(clock.diff(124.25, clock()), 1.0)

    def test_clock_rejects_ambiguous_provider(self):
        with self.assertRaises(ValueError):
            Clock(lambda: 0, ticks=FakeTicks())

    def test_network_lost_data_across_tick_wrap(self):
        with patch("builtins.print"):
            check_network(True, True)

    def test_network_under_small_memory_limits(self):
        with patch("builtins.print"):
            check_network(False, False)


class ResourceTests(unittest.TestCase):
    def make_stack(self, **kwargs):
        bus = MemoryBus()
        trace = Trace(0, console=False)
        link = PollingLink(0, bus.connect(0), trace, peers=(1, 2), max_queue=2)
        return NetworkStack("10.0.0.1", link,
                            {"10.0.0.1": 0, "10.0.0.2": 1, "10.0.0.3": 2}, **kwargs)

    def test_link_small_queue_limit(self):
        stack = self.make_stack()
        stack.ping("10.0.0.2")
        stack.ping("10.0.0.3")
        with self.assertRaises(BufferError):
            stack.ping("10.0.0.2")
        self.assertEqual(len(stack.link.queue), 2)

    def test_tcp_connection_limit(self):
        stack = self.make_stack(tcp_max_connections=2)
        stack.tcp.connect("10.0.0.2", 8000)
        stack.tcp.connect("10.0.0.3", 8000)
        with self.assertRaises(BufferError):
            stack.tcp.connect("10.0.0.2", 8001)

    def test_tcp_send_buffer_limit(self):
        stack = self.make_stack(tcp_max_buffer=2048)
        connection = stack.tcp.connect("10.0.0.2", 8000)
        connection.send(b"x" * 2048)
        with self.assertRaises(BufferError):
            connection.send(b"x")
        self.assertEqual(len(connection.outbound), 2048)

    def test_invalid_tcp_limits(self):
        for kwargs in ({"tcp_max_connections": 0}, {"tcp_max_buffer": 0}, {"tcp_mss": 257}):
            with self.assertRaises(ValueError):
                self.make_stack(**kwargs)


class FakePin:
    def __init__(self):
        self.level = None
        self.transitions = []

    def value(self, level=None):
        if level is not None:
            self.level = level
            self.transitions.append(level)
        return self.level


class FakeUart:
    def __init__(self, pin):
        self.pin = pin
        self.sent = bytearray()
        self.incoming = b""
        self.results = []
        self.flush_error = None
        self.flushes = 0
        self.deinits = 0

    def any(self):
        return 1 if self.incoming else 0

    def read(self, size):
        result, self.incoming = self.incoming[:size], self.incoming[size:]
        return result or None

    def write(self, data):
        if self.pin.level != 1:
            raise AssertionError("Driver was not enabled")
        result = self.results.pop(0) if self.results else len(data)
        if isinstance(result, Exception):
            raise result
        if result:
            self.sent.extend(data[:result])
        return result

    def flush(self):
        if self.pin.level != 1:
            raise AssertionError("Driver disabled before flush")
        self.flushes += 1
        if self.flush_error:
            raise self.flush_error

    def deinit(self):
        self.deinits += 1


class UartTests(unittest.TestCase):
    def setUp(self):
        self.pin, self.ticks = FakePin(), FakeTicks()
        self.uart = FakeUart(self.pin)
        self.delays = []
        def delay(microseconds):
            self.delays.append(microseconds)
            self.ticks.total += max(1, microseconds // 1000)
        self.port = UartPort(self.uart, self.pin, clock=Clock(ticks=self.ticks), delay_us=delay)

    def test_driver_starts_in_receive_mode(self):
        self.assertEqual(self.pin.level, 0)

    def test_partial_writes_flush_before_direction_release(self):
        self.uart.results = [None, 2, 0, 1]
        self.port.write(b"abcdef")
        self.assertEqual(self.uart.sent, b"abcdef")
        self.assertEqual(self.uart.flushes, 1)
        self.assertEqual(self.pin.transitions, [0, 1, 0])

    def test_write_exception_releases_driver(self):
        self.uart.results = [OSError("Disconnected")]
        with self.assertRaises(OSError):
            self.port.write(b"abc")
        self.assertEqual(self.pin.level, 0)

    def test_flush_exception_releases_driver(self):
        self.uart.flush_error = OSError("Flush timed out")
        with self.assertRaises(OSError):
            self.port.write(b"abc")
        self.assertEqual(self.pin.level, 0)

    def test_write_timeout_across_tick_wrap_releases_driver(self):
        self.uart.results = [None] * 600
        with self.assertRaisesRegex(OSError, "timed out"):
            self.port.write(b"abc")
        self.assertEqual(self.pin.level, 0)

    def test_read_empty_and_fragmented(self):
        self.assertEqual(self.port.read(), b"")
        self.uart.incoming = b"x" * 300
        self.assertEqual(len(self.port.read()), 256)
        self.assertEqual(len(self.port.read()), 44)
        self.assertEqual(self.port.read(), b"")

    def test_empty_write_does_not_enable_driver(self):
        self.port.write(b"")
        self.assertEqual(self.pin.transitions, [0])

    def test_oversized_write_rejected_before_enabling(self):
        with self.assertRaises(ValueError):
            self.port.write(b"x" * 2075)
        self.assertEqual(self.pin.level, 0)

    def test_close_is_idempotent_and_rejects_operations(self):
        self.port.close()
        self.port.close()
        self.assertEqual(self.uart.deinits, 1)
        self.assertEqual(self.pin.level, 0)
        with self.assertRaises(OSError):
            self.port.write(b"x")
        with self.assertRaises(OSError):
            self.port.read()

    def test_missing_flush_is_rejected(self):
        with self.assertRaises(RuntimeError):
            UartPort(object(), self.pin)

    def test_factory_selects_uart1_and_board_pins(self):
        calls = []
        def pin_factory(number, *args, **kwargs):
            calls.append((number, kwargs))
            return self.pin if number == 18 else number
        pin_factory.OUT = 1
        def uart_factory(number, **kwargs):
            calls.append(("UART", number, kwargs))
            return self.uart
        machine = types.SimpleNamespace(Pin=pin_factory, UART=uart_factory)
        with patch.dict(sys.modules, machine=machine), patch(
                "pynetstack.uart.time.sleep_us", lambda value: None, create=True):
            port = open_esp32c6_port()
            port.close()
        uart_call = next(call for call in calls if call[0] == "UART")
        self.assertEqual(uart_call[1], 1)
        self.assertEqual(uart_call[2]["tx"], 21)
        self.assertEqual(uart_call[2]["rx"], 20)
        self.assertEqual(uart_call[2]["flow"], 0)
        self.assertIn((18, {"value": 0}), calls)


class DeploymentTests(unittest.TestCase):
    def load_tool(self):
        return runpy.run_path(str(ROOT / "tools/prepare_esp32c6.py"))

    def test_staging_copies_only_portable_modules(self):
        tool = self.load_tool()
        with tempfile.TemporaryDirectory() as folder, patch("builtins.print"):
            target = Path(folder) / "node2"
            tool["prepare"](target, 2, (2,), False)
            config = runpy.run_path(str(target / "board_config.py"))
            self.assertEqual(config["NEIGHBORS"], {"10.0.0.1": 0, "10.0.0.3": 2})
            self.assertFalse(config["AUTOSTART"])
            self.assertFalse((target / "pynetstack/cli.py").exists())
            self.assertFalse((target / "pynetstack/physical.py").exists())
            for name in tool["CORE_MODULES"]:
                self.assertEqual((target / "pynetstack" / name).read_bytes(),
                                 (ROOT / "pynetstack" / name).read_bytes())

    def test_staging_refuses_to_overwrite_nonempty_directory(self):
        tool = self.load_tool()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "keep.txt").write_text("Keep me")
            with self.assertRaises(ValueError):
                tool["prepare"](Path(folder), 2, (2,), False)
            self.assertEqual((Path(folder) / "keep.txt").read_text(), "Keep me")

    def test_staging_rejects_duplicate_ids(self):
        tool = self.load_tool()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                tool["prepare"](Path(folder), 2, (1, 1), False)

    def test_core_has_no_runtime_type_annotations(self):
        tool = self.load_tool()
        for name in tool["CORE_MODULES"]:
            tree = ast.parse((ROOT / "pynetstack" / name).read_text())
            for node in ast.walk(tree):
                self.assertNotIsInstance(node, ast.AnnAssign, name)
                if isinstance(node, ast.arg):
                    self.assertIsNone(node.annotation, name)
                if isinstance(node, ast.FunctionDef):
                    self.assertIsNone(node.returns, name)


if __name__ == "__main__":
    unittest.main()
