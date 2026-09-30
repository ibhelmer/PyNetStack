# ESP32-C6-DevKitC-1: experimental MicroPython port

Copyright 2026 Ib Helmer Nielsen. SPDX-License-Identifier: Apache-2.0.

**Status: source port prepared; native MicroPython execution and real hardware are
not yet validated.** Desktop tests, an API-restriction harness, and mixed 0.1.0/0.2.0
software nodes pass. None of these prove UART timing, available heap or electrical
operation. Run the self-test below on the board before connecting the bus. See
[the exact validation record](VALIDATION.md).

## Target and design

The target is Espressif's **ESP32-C6-DevKitC-1**, not a generic unspecified ESP32.
Use official **ESP32_GENERIC_C6** firmware. At the time of this guide (2026-09-30),
the official download page lists **MicroPython 1.29.0** as the stable release [1].
This is the intended validation target, not a claim that this project has run on it.

PyNetStack builds and parses its own IPv4/ICMP/UDP/TCP packets. It does not use
ESP32 Wi-Fi sockets or the operating system's TCP stack for these protocols.
The GPIO UART connects to an external RS-485 transceiver. Link protocol version
1, its 1024-byte MTU, CRC, escaping, node IDs and polling grants are unchanged.
A PC and a board can therefore use the same wire protocol. Software compatibility
has been checked; a real PC-to-board test remains necessary.

## Wiring

Power off both sides while wiring. Use a **3.3 V half-duplex RS-485 transceiver**
with a 3.3 V-compatible receiver output, for example an appropriate SN65HVD7x
circuit/module [2]. The module's actual circuit and pin names take precedence over
its sales description. A module with a 5 V RO output must not feed ESP32 GPIO20.
Never connect GPIO TX/RX directly to A/B.

The selected pins are brought out on DevKitC-1's J3 header [3]:

| Board signal | Header location | Transceiver connection |
|---|---|---|
| GPIO21, UART1 TX | J3 pin 7, label `21` | DI / D, driver input |
| GPIO20, UART1 RX | J3 pin 8, label `20` | RO / R, receiver output |
| GPIO18, direction control | J3 pin 10, label `18` | Tie DE and active-low /RE together |
| 3V3 | J1 pin 1 | VCC for a 3.3 V transceiver |
| GND | For example J3 pin 1 | Local transceiver ground/reference |

These are GPIO numbers, not chip package pin numbers. UART1 is explicitly routed
to these GPIOs. The profile leaves UART0 GPIO16/17, USB GPIO12/13 and the board's
boot/strapping pins alone [3,4]. The chip UART supplies logic-level serial data;
it is not an RS-485 line driver.

With DE and /RE tied together, GPIO18 low selects receive mode, and high selects
transmit mode with the local receiver disabled. Add a **10 kohm pull-down from the
DE+/RE node to GND** so that reset, boot, flashing or an import failure cannot leave
the driver enabled by a floating input. Check that the chosen module has no
conflicting pull-up or automatic-direction circuit. Provide local decoupling
according to the transceiver datasheet; modules often already include it.

Use a linear twisted-pair trunk with short stubs. For a 120-ohm cable, install
one 120-ohm termination across A/B at each physical end, **not at every node**.
Check existing module termination and bias networks before adding resistors.
A/B naming is not universal: verify polarity using both manufacturers' diagrams.
Provide a suitable signal reference; use isolation where ground differences may
exceed the transceiver's common-mode range. Do not blindly connect distant grounds
or multiple power-supply outputs [5]. Start on a small, controlled lab bench.

## Firmware installation (only when needed)

Download the **.bin**, not .app-bin, from the official ESP32_GENERIC_C6 page [1].
Copy it to your working directory as `firmware.bin`. Keep the board disconnected
from the RS-485 bus during initial flashing. Use a data-capable USB cable.

On Windows PowerShell:

```powershell
python -m pip install --upgrade esptool mpremote
mpremote connect list
```

Replace COM5 below with the board's actual management port. **The erase command
removes all existing firmware and files on the board. Back them up first.** Skip
this section when the intended MicroPython firmware is already installed.

```powershell
python -m esptool --chip esp32c6 --port COM5 erase-flash
python -m esptool --chip esp32c6 --port COM5 --baud 460800 write-flash 0x0 firmware.bin
```

The full C6 image starts at address **0x0** [1]. The commands use esptool's current
hyphenated syntax [6]. If flashing fails partway through, omit the baud option.
If automatic download-mode entry fails, hold BOOT, press/release RESET, release
BOOT, then retry [3]. The native ESP32-C6 USB port and the USB-to-UART bridge are
different management paths; port names can change after flashing/reset.

After reset, verify the runtime, using the port that exposes the MicroPython REPL:

```powershell
mpremote connect list
mpremote connect COM5 exec "import sys, os; print(sys.implementation); print(os.uname())"
```

Do not confuse this port with a separate USB-to-RS-485 adapter used by the PC node.
Only one tool may own a serial port at a time [7].

## Prepare and copy the source

Get the experimental branch:

```powershell
git clone --branch esp32c6-micropython https://github.com/ibhelmer/PyNetStack.git
cd PyNetStack
```

For a first lab with **one PC coordinator (node 0) and one board (node 2)**:

```powershell
python tools/prepare_esp32c6.py --node-id 2 --peer-ids 2 --output build/c6-node2
```

This only creates local files. It never connects to hardware, erases flash or
modifies the source. It refuses a nonempty output folder. Choose a fresh folder
for another build. The generated `board_config.py` contains this consistent map:

```python
NODE_ID = 2
LOCAL_IP = "10.0.0.3"
NEIGHBORS = {"10.0.0.1": 0, "10.0.0.3": 2}
AUTOSTART = False
```

Review the generated configuration, then copy these files. **The copy replaces
same-named files on the board**, including main.py and board_config.py. Back up
existing board applications first. No boot.py is supplied or overwritten.

```powershell
mpremote connect COM5 fs cp -r build/c6-node2/pynetstack :
mpremote connect COM5 fs cp build/c6-node2/board_config.py build/c6-node2/esp32c6_node.py build/c6-node2/main.py build/c6-node2/selftest.py build/c6-node2/LICENSE build/c6-node2/NOTICE :
```

`mpremote fs cp` uses `:` for the device filesystem [7]. There is no pip install
on the board, and the desktop wheel is not a MicroPython installation package.
The preparation tool excludes argparse, threading, pathlib, typing and pySerial
entry points. Only the portable source modules and board entry points are copied.

## Run the software self-test on the board

Leave the bus disconnected for this step:

```powershell
mpremote connect COM5 run tests/micropython_selftest.py
```

It checks CRC-32 and Internet checksum vectors, exact bytes from 0.1.0, maximum
escaped frames, three software nodes, fragmented byte reads, ICMP/UDP/TCP echo,
TCP close, injected data loss and tick wraparound. It prints the interpreter name
and the remaining heap when available. A successful run ends with:

```text
PASS: software tests complete. UART wiring and RS-485 electrical timing were NOT tested.
```

This is the expected success message, **not a board result recorded by the
project author**. A MemoryError or other exception is a failed bring-up test;
save the complete traceback, firmware version and board revision. Do not proceed
by ignoring it. The virtual test runs faster than wall-clock bus time and is not
a throughput measurement.

## First physical PC-to-board test

After the software test passes, wire the bus as described above. For this two-node
lab, terminate both ends. The PC needs a separate USB-to-RS-485 adapter with known
automatic transmit-direction control, or another supported desktop direction mode.

Start the board in one terminal and leave it running:

```powershell
mpremote connect COM5 exec "import esp32c6_node; esp32c6_node.run()"
```

The board prints its pins, node/IP, free heap after setup and a warning that the
port remains experimental. It answers ICMP echo, UDP port 7000 and TCP port 8000.
It only transmits after the coordinator polls it.

In a second terminal, from the repository root, start the PC coordinator.
**COM6 is an example USB-to-RS-485 adapter port, not the board's COM5 REPL port.**

```powershell
python -m pip install ".[serial]"
python -m pynetstack node --port COM6 --node-id 0 --ip 10.0.0.1 --neighbors examples/two_nodes.json
```

The default desktop direction mode assumes an automatic-direction adapter. It
cannot detect or fix an incorrectly wired converter. See [RS-485 modes](RS485.md).
At the PyNetStack console prompt, send one request at a time:

```text
ping 10.0.0.3 Hello C6
udp 10.0.0.3 7000 Hello over UDP
tcp 10.0.0.3 8000 Hello over TCP
connections
close 49152
```

Use the actual local TCP port printed by `tcp`, not necessarily 49152. These are
PyNetStack commands, not Windows/Linux ping or ordinary socket operations. The
10.0.0.x addresses exist only inside the teaching stack.

Observe TX, RX and DE with a logic analyzer: GPIO18 must rise before TX starts
and return low after the last stop bit. The driver waits for UART.flush() before
releasing DE and releases it in a finally block after write/flush errors [4].
Measure real turnaround and check CRC errors before increasing load. Python
scheduling, garbage collection and USB/UART buffering still require hardware
validation; software polling is not a hard real-time collision guarantee.

## Three or more nodes, coordinator choice and startup

For PC node 0 plus boards 1 and 2, prepare each with `--peer-ids 1,2`, selecting
its own `--node-id`. Use `examples/three_nodes.json` on the PC. Every node must
agree on the mapping, coordinator ID, baud and polling timing. IDs must be unique.
The preparation helper uses IP last octet = node ID + 1 as a lab convention; the
wire protocol itself does not require that relationship.

An ESP32-C6 can instead be coordinator: prepare node 0 with its peers listed.
Never run the PC coordinator at the same time. Only one coordinator may transmit
polls. Missing peers consume a timeout; a missing coordinator stops peer traffic.

Manual startup is the default. For deliberate autostart, prepare a fresh output
folder with `--autostart`, or set AUTOSTART=True in the reviewed configuration and
copy it back. main.py then waits two seconds before starting. Ctrl-C interrupts
the service and the UART is deinitialized. The external DE pull-down remains
important during resets and firmware recovery.

## Resource and timing profile

| Parameter | Board default |
|---|---:|
| UART | UART1, 115200 baud, 8N1, no flow control |
| RX / TX UART buffers | 4096 / 512 bytes |
| Link MTU | 1024 bytes, unchanged |
| Link transmit queue | 8 packets |
| TCP connections, including closing states | 2 |
| TCP queued application bytes per connection | 2048 |
| TCP maximum data segment | 256 bytes |
| Guard / poll response timeout | 10 ms / 500 ms |
| GPIO setup / hold around UART transmission | 10 us / 10 us |

These are implementation settings, **not measured heap or capacity guarantees**.
The board calculates a conservative TCP RTO floor from node count and polling
settings. Queue capacity is enforced with exceptions instead of silent eviction.
The core remains an intentionally incomplete TCP teaching implementation.

Short APP/ICMP/UDP/TCP traces are enabled by default. To inspect every layer,
set TRACE_LAYERS=None; TRACE_HEX=True also shows raw bytes. Verbose USB printing
can disturb timing, and traces reveal plaintext payloads. Do not store secrets
in them. There is no encryption or authenticated bus membership.

Timers keep integer tick timestamps and use ticks_add/ticks_diff across rollover
[8]. Do not pause a live node in a debugger or enter deep sleep while it owns bus
or TCP state. Callbacks must return promptly; the main loop services stack.step()
and yields with sleep_ms(1). Wi-Fi/BLE services are not started by this example.

## Primary references

[1] MicroPython C6 firmware: https://micropython.org/download/ESP32_GENERIC_C6/

[2] Texas Instruments, 3.3 V half-duplex example: https://www.ti.com/product/SN65HVD75

[3] Espressif DevKitC-1 guide and header tables: https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c6/esp32-c6-devkitc-1/user_guide.html

[4] MicroPython 1.29 UART API and flush semantics: https://docs.micropython.org/en/v1.29.0/library/machine.UART.html

[5] Analog Devices, RS-485 wiring: https://www.analog.com/en/resources/technical-articles/rs485-cable-specification-guide--maxim-integrated.html

[6] Espressif esptool commands: https://docs.espressif.com/projects/esptool/en/latest/esp32c6/esptool/basic-commands.html

[7] MicroPython mpremote: https://docs.micropython.org/en/v1.29.0/reference/mpremote.html

[8] MicroPython tick timers: https://docs.micropython.org/en/v1.29.0/library/time.html
