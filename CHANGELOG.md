# Changelog

Copyright 2026 Ib Helmer Nielsen. SPDX-License-Identifier: Apache-2.0.

## 0.2.0 - 2026-09-30 (experimental ESP32-C6 branch)

Adds an ESP32-C6-DevKitC-1 source target for official MicroPython ESP32_GENERIC_C6
firmware, UART1 on GPIO21/20 and DE+/RE on GPIO18. Includes English board wiring,
firmware, source deployment, self-test and mixed PC/board instructions.

The shared protocol core no longer depends on dataclasses, IntEnum, ipaddress,
typing or collections.abc. Uses portable CRC/IPv4 helpers, direct checksums,
explicit bounded queues, selective tracing and wrap-safe integer tick deadlines.
Desktop entry points and pySerial remain separate. Board profile: 2 TCP entries,
2048-byte application send buffers, 8 link frames, 256-byte TCP data segments.

Wire version 1 is unchanged. Packet containers are now ordinary value-equality
classes rather than frozen dataclasses; treat fields as read-only. FrameType
members are integer constants, with FrameType.name(value) for display.

87 CPython unit tests pass. Four mixed 0.1.0/0.2.0 CPython interop cases pass.
MicroPython-native execution and physical RS-485 operation are **not yet tested**.
This branch should not be mistaken for a hardware-certified release.

## 0.1.0 - 2026-09-30

Initial educational CPython implementation, simulated byte bus, optional desktop
RS-485 driver, polling link, IPv4/ICMP/UDP/simplified TCP and 54 automated tests.
