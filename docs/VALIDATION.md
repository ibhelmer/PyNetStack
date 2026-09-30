# Validation record

Copyright 2026 Ib Helmer Nielsen. SPDX-License-Identifier: Apache-2.0.

## Initial version, 2026-09-30

Recorded execution environment: Linux, CPython 3.13.5. Python 3.10+ is the declared
language requirement; other Python/OS combinations were not executed in this
validation session.

Commands executed:

```console
python -m unittest discover -s tests -v
python -m compileall -q pynetstack
python -m pynetstack demo --quiet
python -m pynetstack demo --quiet --drop-first-tcp-data
```

Result: **54 automated tests passed**, and both demo modes completed successfully.
The demo transferred and echoed a 440-byte TCP stream and completed a FIN-based
close. The loss demo deliberately removed one TCP data frame and recovered it.

Test coverage includes byte-value round trips, arbitrary frame chunk boundaries,
maximum frame length, CRC failure, invalid escaping, resynchronization, bounded
receivers, known checksum values, packet corruption, UDP zero checksums for IPv4,
polling permissions, duplicate polls, destination filtering, absent-node timeout,
coordinator and peer traffic, ICMP echo, MTU and buffer limits, source-map
validation, TCP handshakes, multiple connections, lost SYN/SYN-ACK/ACK/data/FIN,
duplicate suppression, sequence wraparound, connection refusal, exhausted retries,
out-of-order rejection, simultaneous close, CLI behavior and trace output.

The serial tests use mocked pySerial modules to verify partial-write handling,
flush calls, native-mode configuration, requested RTS polarity, zero-progress
errors and cleanup after opening fails. They **do not establish electrical or
physical RS-485 correctness**.

## Not yet verified

No physical USB-to-RS-485 adapters, wiring, DE timing, termination, real bus
collisions, USB buffering or cross-machine operation were tested. No Windows or
macOS execution, throughput benchmark, long-duration soak test, production TCP
interoperability test, complete RFC conformance test or security audit has been
performed. No GitHub Actions result is implied by this local test record.

A passing simulator test confirms behavior of this implementation under its
modeled conditions; it does not prove hardware suitability or deadline safety.

## Version 0.2.0 experimental ESP32-C6 port, 2026-09-30

Execution environment: **Linux, CPython 3.13.5**. An actual MicroPython interpreter
and ESP32-C6 hardware were not available in this session. MicroPython 1.29.0
ESP32_GENERIC_C6 is the intended target selected from official documentation,
not a version on which execution has been established.

Results recorded locally:

| Check | Result and exact scope |
|---|---|
| `python -m unittest discover -s tests -v` | 87 tests pass: the original 54 plus 33 portability/UART/timer/resource/deployment tests |
| Desktop demo, normal and injected first TCP-data loss | Both pass under CPython |
| `python tests/micropython_selftest.py` | Passes under CPython; ready to run on the board, but not yet run there |
| `python tests/micropython_api_subset.py` | Passes under a CPython harness that restricts selected imports/APIs and simulates tick functions; this is NOT a MicroPython interpreter |
| Legacy mixed-version interoperability | Four CPython scenarios pass: each direction between unmodified 0.1.0 and 0.2.0, with and without dropped TCP data; ICMP/UDP echo, 1024-byte TCP stream and FIN close |
| Syntax/byte compilation | Shared modules and entry points compile under CPython; this is not MicroPython bytecode validation |

The UART tests inject fake UART, Pin and time providers. They verify pin selection,
partial/zero-progress writes, write timeout across counter wrap, flush-before-DE
release, error cleanup, bounded reads and idempotent close. No GPIO voltage, real
UART FIFO, DE edge, USB adapter or cable was measured.

The API harness explicitly labels itself as CPython. Changing a compatibility
branch or substituting module APIs does not validate MicroPython's parser, object
model, garbage collector, float configuration, peripheral drivers or peak heap.
The software self-test contains static byte vectors generated with the original
0.1.0 source, not values generated solely by the new encoder under test.

To reproduce legacy checking, provide an unmodified 0.1.0 checkout outside this
working tree:

```console
python tools/check_legacy_interop.py --reference /path/to/PyNetStack-0.1.0
```

Before treating this as a board-supported release, run the portable self-test on
ESP32-C6-DevKitC-1, record firmware/heap results, then perform a real two-node and
three-node RS-485 test with logic-analyzer verification, error injection and a
long-duration stability test. No native MicroPython, physical interoperability,
performance, long-running uptime or security result is implied by the local tests.
