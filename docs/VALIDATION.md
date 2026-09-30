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
