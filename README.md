# PyNetStack

**An observable educational TCP/IP stack in Python, using a shared RS-485 bus.**

Copyright 2026 **Ib Helmer Nielsen**. Licensed under [Apache License 2.0](LICENSE).

PyNetStack builds and parses its own IPv4, ICMP, UDP and simplified TCP packets.
It does not use the operating system's TCP sockets to simulate its transport layer.
A custom link protocol adds framing, node addresses, escaping, CRC-32 and polling.
Run the same stack on an in-memory byte bus or through optional USB-to-RS-485 adapters.

**Status: 0.2.0, educational prototype with an experimental MicroPython port.**
The CPython tests pass. Native MicroPython execution and physical RS-485 operation
have **not** been validated. Do not read desktop/API-fake tests as board certification.

The `esp32c6-micropython` branch adds an ESP32-C6-DevKitC-1 target. Read the
[board installation guide](docs/ESP32_C6.md) for pin assignments, firmware,
source deployment, a software self-test and a PC-to-board lab. The same portable
protocol core runs behind the desktop and UART entry points; the wire format is
unchanged from 0.1.0.
The TCP implementation is a deliberately limited teaching subset, not a production
or fully RFC-conformant TCP implementation. See [scope and limitations](docs/ARCHITECTURE.md#scope-and-limitations).

## Start without hardware

For the desktop entry point, CPython 3.10 or later is required. From a local checkout, the demo needs no external
Python packages and does not require administrator privileges.

```console
git clone https://github.com/ibhelmer/PyNetStack.git
cd PyNetStack
python -m pynetstack demo
```

Display raw hexadecimal bytes, or deliberately lose the first TCP data frame:

```console
python -m pynetstack demo --hex
python -m pynetstack demo --drop-first-tcp-data
python -m pynetstack demo --quiet --json trace.jsonl
```

The demo connects three software nodes: node 0 coordinates the bus; node 1 is the
client; node 2 provides echo services. It checks ICMP echo, UDP echo, a 440-byte TCP
stream and connection teardown. A successful quiet run ends with:

```text
PASS: three nodes; ICMP echo; UDP echo; TCP 440 bytes; graceful close.
```

The loss demonstration must additionally recover the missing data through TCP
retransmission. Its simulated clock advances without real-time sleeping: do not
interpret simulation runtime as RS-485 performance.

## Follow the packet

```text
Application bytes
       |
TCP / UDP / ICMP          transport segments / control messages
       |
IPv4                     source/destination IP, protocol, header checksum
       |
Polling link             node addresses, grant ID, escaping, CRC-32
       |
Byte port                simulated bus OR UART over RS-485
```

Trace records identify node, layer, direction and event. TCP records include
sequence and acknowledgment numbers; link records include source, destination,
grant ID and CRC status. JSON Lines output contains complete raw hex at the byte
and IP layers. Application data is therefore visible in trace files: use lab data,
not passwords or other secrets.

## Physical RS-485

Install the optional dependency in a virtual environment:

```console
python -m venv .venv
```

Activate it in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or activate it in a Linux/macOS shell:

```sh
. .venv/bin/activate
```

Then install from this checkout:

```console
python -m pip install ".[serial]"
```

Use **one process per RS-485 interface**, unique node IDs and identical copies of
[`examples/three_nodes.json`](examples/three_nodes.json) on all nodes. The following
commands are intended for three computers. Replace each COM port with its actual
local device name, for example `/dev/ttyUSB0` on Linux.

Coordinator, node 0:

```console
python -m pynetstack node --port COM3 --node-id 0 --ip 10.0.0.1 --neighbors examples/three_nodes.json
```

Client, node 1:

```console
python -m pynetstack node --port COM3 --node-id 1 --ip 10.0.0.2 --neighbors examples/three_nodes.json
```

Echo server, node 2:

```console
python -m pynetstack node --port COM3 --node-id 2 --ip 10.0.0.3 --neighbors examples/three_nodes.json --udp-echo 7000 --tcp-echo 8000
```

Enter these commands in node 1's console:

```text
ping 10.0.0.3 Hello
udp 10.0.0.3 7000 Hello over UDP
tcp 10.0.0.3 8000 Hello over TCP
connections
close 49152
```

Use the actual local port printed by `tcp`, not necessarily 49152. `close` drains
queued data and initiates FIN; `quit` stops the process immediately. Use
`--no-console` for a service controlled with Ctrl+C, and `--duration 30` for a
30-second run. `python -m pynetstack node --help` lists all options.

**The default `--direction auto` requires an adapter with hardware automatic
transmit-direction control.** It is not hardware detection. `--direction native`
requests operating-system/driver RS-485 RTS control; support depends on hardware
and drivers. There is no silent fallback to unreliable user-space RTS switching.
Read [the hardware and timing guide](docs/RS485.md) before wiring a bus.

These IP addresses exist **inside PyNetStack**, not on Windows/Linux network
interfaces. The system `ping` command, browsers and ordinary socket applications
cannot use them. Use the PyNetStack console or Python API.

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Modules, encapsulation, addressing, TCP subset, API and limitations |
| [Link protocol](docs/LINK_PROTOCOL.md) | Exact binary format, CRC, escaping, polling and timeouts |
| [RS-485 guide](docs/RS485.md) | Wiring, adapter modes, three-node configuration and troubleshooting |
| [Teaching exercises](docs/TEACHING.md) | Trace analysis, packet loss, multiplexing and design exercises |
| [ESP32-C6-DevKitC-1](docs/ESP32_C6.md) | Experimental MicroPython port, wiring, deployment and bring-up |
| [Validation](docs/VALIDATION.md) | Tests actually run and what remains unverified |

All source identifiers, comments, docstrings, console messages and documentation
are written in English.

## Tests and installation

```console
python -m unittest discover -s tests -v
python -m compileall -q pynetstack
python tests/micropython_selftest.py
python -m pip install .
pynetstack demo --quiet
```

The core has no third-party runtime dependencies. The optional `serial` extra
uses pySerial. Development and test commands above use only the standard library,
apart from the packaging tools used by `pip install`.

## License

```text
Copyright 2026 Ib Helmer Nielsen
SPDX-License-Identifier: Apache-2.0
```

The full license is in [LICENSE](LICENSE), with attribution in [NOTICE](NOTICE).
This software is provided without warranties, as described in the license.
