# Architecture and implementation scope

Copyright 2026 Ib Helmer Nielsen. SPDX-License-Identifier: Apache-2.0.

## Purpose

PyNetStack is a small teaching implementation in which students can inspect every
encapsulation and decapsulation step. It is designed for a closed lab, not as a
replacement for a host operating system's network stack.

The initial repository implementation follows the RS-485, addressed-frame and
coordinator-polling design. It is a new implementation, not an import of an
unavailable earlier source archive.

## Modules

| Module | Responsibility |
|---|---|
| `framing.py` | Frame schema, escaping, bounded incremental decoder, CRC-32 |
| `physical.py` | Byte-port interface, in-memory byte bus, optional serial backend |
| `link.py` | Transmit queues, single-coordinator polling, grant validation, destination filtering |
| `packets.py` | Internet checksum and IPv4/ICMP/UDP/TCP binary codecs |
| `tcp.py` | Connection table, handshake, segmentation, acknowledgments, retries, closure |
| `stack.py` | IPv4 host, static neighbors, UDP ports, ICMP echo and protocol dispatch |
| `trace.py` | Console and JSON Lines trace events |
| `cli.py` | Three-node demonstration and interactive serial-node application |

`__main__.py` provides `python -m pynetstack`; the installed entry point is
`pynetstack`. The package metadata, source headers, README, NOTICE and program
banner identify Ib Helmer Nielsen as the copyright holder.

## Data path

A UDP send first validates application length and ports. The UDP codec builds a
header and computes the checksum including the IPv4 pseudo-header. The IPv4
codec then adds a 20-byte header and its own header checksum. Static neighbor
resolution selects a destination link ID. The link enqueues the packet, obtains
a polling opportunity, builds the custom frame, appends CRC-32, escapes the body
and sends bytes. Reception performs the inverse steps and delivers a datagram
only to its registered destination port.

TCP follows the same lower layers, but maintains state for each connection. Its
send buffer is a byte stream: one application call may become several segments,
and one receive callback must not be treated as an application-message boundary.
The echo service returns bytes without adding message framing.

RS-485 provides electrical signaling. Polling, node addressing, frame boundaries
and CRC are link-layer behavior defined by this project. The frame format is not
Ethernet, SLIP, PPP or Modbus, and is not compatible with those link protocols.
The escaping idea resembles PPP's HDLC-like framing, but the actual frame header,
CRC byte order and media-access rules are different [4].

## Addressing and multiplexing

`examples/three_nodes.json` maps `10.0.0.1 -> 0`, `10.0.0.2 -> 1` and
`10.0.0.3 -> 2`. These are independent address spaces: an IP address is not derived
from a node ID by the protocol. This release permits one IP per node and requires
the local mapping to be present. It rejects duplicate mapped node IDs.

A receiver checks that a source IP matches the configured source node. This is
configuration validation, **not cryptographic authentication**: another device
can forge both fields.

The coordinator controls access to the bus; it does not route peer packets.
Node 1 can send directly to node 2 after receiving a grant. Node 0 observes the
frame for scheduling and filters it instead of sending another IP copy.

UDP is dispatched by destination port. A TCP engine owns one local IP, and its
connection key is `(local_port, remote_ip, remote_port)`. Together these form the
TCP endpoint four-tuple. Several clients can therefore communicate with the same
listening server port without sharing connection state [3].

## TCP subset

The wire codec produces a fixed 20-byte TCP header and checks its pseudo-header
checksum. The state machine implements active/passive opening, SYN/SYN-ACK/ACK,
byte sequence numbers modulo 2^32, and a maximum 256-byte data segment.

There is at most one unacknowledged sequence-consuming segment per connection.
Data is buffered and segmented. SYN and FIN each consume one sequence number.
The receiver delivers only the next expected sequence; it acknowledges but does
not redeliver duplicate data. Out-of-order data is discarded rather than stored.

Acknowledgments release the pending segment only when they acknowledge its full
end sequence. The code respects a peer's advertised window when choosing the
next data length, but advertises a fixed local window. It has no window scaling,
persist probes or general-purpose receive-buffer flow control.

An initial RTO is configured per stack. Retransmission uses exponential backoff
capped at `max(initial_rto, min(30, 4 * initial_rto))` seconds, with five retries
by default. Its timer starts at enqueue time, so the RTO must include polling
and serialization delays. There is no RTT estimator or congestion controller.

Closure uses FIN_WAIT_1, FIN_WAIT_2, CLOSE_WAIT, CLOSING, LAST_ACK and TIME_WAIT.
A received FIN automatically requests a return FIN after queued outgoing data
is drained; the public API does not expose independent half-close behavior.
TIME_WAIT defaults to the greater of two seconds and twice the maximum configured
RTO. This is a teaching timer, **not an implementation of the RFC's 2*MSL rule**.
Use consistent timer settings across a lab. FIN_WAIT_2 times out after 60 seconds.

The connection table is limited to 64 entries and each application send buffer
to 65,536 queued bytes. Link queues have their own 128-packet limit. Exhausting a
limit raises an exception rather than providing production-grade backpressure.

## Python API

The event loop owns all stack objects. Service them frequently from one thread;
application callbacks run synchronously in that thread. The CLI input thread
only supplies commands through a queue and does not manipulate stack state.

```python
# Given two configured and regularly serviced NetworkStack instances:
server.bind_udp(7000, lambda ip, port, data:
    server.send_udp(ip, port, data, source_port=7000))

server.tcp.listen(8000, lambda connection, data: connection.send(data))

client.bind_udp(40000, lambda ip, port, data: print(repr(data)))
client.send_udp(server.ip, 7000, b"Hello")

connection = client.tcp.connect(
    server.ip, 8000,
    on_data=lambda connection, data: print(repr(data)),
)
connection.send(b"A stream of bytes")
# Continue calling step() on every simulated node, or on the local serial node.
# Call connection.close() to drain pending data and initiate FIN.
```

See `run_demo()` and the test `Lab` class for complete, runnable setup and event
loops. Application callbacks should be short; blocking inside them delays bus
service. A callback must handle its own application-level errors.

## Scope and limitations

| Area | Implemented | Not implemented / not claimed |
|---|---|---|
| Physical transport | In-memory byte bus; pySerial auto-direction/native RS-485 backend | Electrical simulation, real-time guarantees, hardware validation |
| Link | Framing, CRC-32, escaping, addresses, polling, grant IDs, bounded queues | Link retransmission, coordinator election, collision recovery, authentication |
| IPv4 | Fixed header, checksum, TTL validation, static neighbors, one shared segment | Options, fragmentation/reassembly, forwarding, routing table, ARP, DHCP, DNS |
| ICMP | Echo request/reply | Other errors, destination-unreachable, traceroute |
| UDP | Datagram framing, ports, checksums | Reliability, congestion control, multicast API |
| TCP | Fixed header, 4-tuples, handshake, stop-and-wait stream, retries, FIN/RST | Full RFC 9293 compliance, congestion control, SACK, options, simultaneous open, persist timer |
| Host integration | Custom CLI and Python API | TUN/TAP, OS sockets, ordinary browser/system-ping access |
| Security | Input bounds, checksums, configuration checks | Encryption, authenticated identity, replay protection, hostile-network hardening |

Broadcast address 255 exists in the link format, but no broadcast/multicast IP
service is exposed. There is no local IP loopback. No claim of interoperability
with a production TCP/IP stack is made. Do not attach this software to an existing
industrial-control or safety-critical RS-485 bus.

## Primary references

The following specifications explain the underlying formats and concepts;
PyNetStack does not claim to implement all their requirements.

1. [RFC 791: IPv4](https://www.rfc-editor.org/rfc/rfc791.html)
2. [RFC 768: UDP](https://www.rfc-editor.org/rfc/rfc768)
3. [RFC 9293: TCP](https://www.rfc-editor.org/rfc/rfc9293.html)
4. [RFC 1662: PPP in HDLC-like Framing](https://www.rfc-editor.org/rfc/rfc1662.html)
5. [RFC 792: ICMP](https://www.rfc-editor.org/rfc/rfc792.html)
6. [pySerial API and RS-485 support](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#rs485-support)
