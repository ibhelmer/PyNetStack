# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Serial API tests use fakes, not physical hardware."""
from contextlib import redirect_stderr, redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pynetstack.cli import main
from pynetstack.physical import SerialPort


class SerialBackendTests(unittest.TestCase):
    def fake_modules(self):
        device = Mock()
        device.write.side_effect = lambda data: min(2, len(data))
        serial = SimpleNamespace(Serial=Mock(return_value=device))
        rs485 = SimpleNamespace(RS485Settings=Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs)))
        return device, serial, rs485

    def test_auto_direction_partial_writes_and_flush(self):
        device, serial, rs485 = self.fake_modules()
        with patch.dict("sys.modules", {"serial": serial, "serial.rs485": rs485}):
            port = SerialPort("COM3")
            port.write(b"abcdef")
            self.assertEqual(device.write.call_count, 3)
            device.flush.assert_called_once()
            self.assertFalse(serial.Serial.call_args.kwargs["xonxoff"])
            self.assertFalse(serial.Serial.call_args.kwargs["rtscts"])
            rs485.RS485Settings.assert_not_called()
            port.close()
            device.close.assert_called_once()

    def test_native_direction_and_active_low(self):
        device, serial, rs485 = self.fake_modules()
        with patch.dict("sys.modules", {"serial": serial, "serial.rs485": rs485}):
            SerialPort("COM3", direction="native", rts_active_low=True)
            self.assertFalse(device.rs485_mode.rts_level_for_tx)
            self.assertTrue(device.rs485_mode.rts_level_for_rx)
            device.open.assert_called_once()

    def test_zero_progress_fails(self):
        device, serial, rs485 = self.fake_modules()
        device.write.side_effect = lambda data: 0
        with patch.dict("sys.modules", {"serial": serial, "serial.rs485": rs485}):
            port = SerialPort("COM3")
            with self.assertRaises(OSError):
                port.write(b"abc")

    def test_open_error_closes_port(self):
        device, serial, rs485 = self.fake_modules()
        device.open.side_effect = OSError("Unsupported native RS-485 mode")
        with patch.dict("sys.modules", {"serial": serial, "serial.rs485": rs485}):
            with self.assertRaises(OSError):
                SerialPort("COM3", direction="native")
            device.close.assert_called_once()

    def test_invalid_serial_settings(self):
        for kwargs in ({"baudrate": 0}, {"direction": "unknown"}):
            with self.assertRaises(ValueError):
                SerialPort("COM3", **kwargs)


class CliTests(unittest.TestCase):
    def test_normal_demo(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(main(["demo", "--quiet"]), 0)
        self.assertIn("Copyright 2026 Ib Helmer Nielsen", stream.getvalue())
        self.assertIn("graceful close", stream.getvalue())

    def test_loss_demo(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(main(["demo", "--quiet", "--drop-first-tcp-data"]), 0)
        self.assertIn("recovered by TCP retransmission: True", stream.getvalue())

    def test_bad_baud_rate_is_rejected(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["node", "--port", "COM3", "--node-id", "0", "--ip", "10.0.0.1",
                  "--neighbors", "examples/three_nodes.json", "--baudrate", "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_missing_neighbor_file_is_reported(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            result = main(["node", "--port", "COM3", "--node-id", "0", "--ip", "10.0.0.1",
                           "--neighbors", "missing-test-file.json"])
        self.assertEqual(result, 1)
        self.assertIn("Error:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
