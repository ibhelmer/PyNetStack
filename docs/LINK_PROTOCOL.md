# PyNetStack link protocol, version 1

Copyright 2026 Ib Helmer Nielsen. SPDX-License-Identifier: Apache-2.0.

This is a project-specific protocol for a controlled, single-coordinator RS-485
lab. It is not a published industry standard and must not share a bus with
incompatible equipment. All multi-byte integer fields are **big-endian**.

## Wire format

```text
0x7E | escaped(header + payload + CRC-32) | 0x7E
```

Unescaped body:

| Offset | Bytes | Field | Meaning |
|---:|---:|---|---|
| 0 | 1 | Version | 1 |
| 1 | 1 | Destination | Node ID 0..254, or 255 for link broadcast |
| 2 | 1 | Source | Node ID 0..254; broadcast is never a valid source |
| 3 | 1 | Type | 1 = IPv4, 2 = POLL, 3 = IDLE |
| 4 | 2 | Grant | Current polling grant; 0 for coordinator-originated data |
| 6 | 2 | Length | Payload byte count; excludes header, CRC and escaping |
| 8 | N | Payload | Complete IPv4 packet, maximum 1024 bytes |
| 8+N | 4 | CRC-32 | Checksum of unescaped header plus payload |

POLL and IDLE carry zero payload bytes. The header is 8 bytes; the maximum
unescaped body is 1036 bytes. The absolute worst-case wire length is 2074 bytes:
two flags plus twice the maximum body length. This bound deliberately assumes
every body byte requires escaping.

Examples before CRC generation and escaping:

```text
Coordinator grants node 1 transmission opportunity 42:
01 01 00 02 00 2A 00 00

Node 1 reports an empty queue to coordinator 0:
01 00 01 03 00 2A 00 00

Node 1 sends a 60-byte IPv4 packet directly to node 2:
01 02 01 01 00 2A 00 3C | 60 payload bytes
```

The grant ID was added to make polling opportunities visible and to reject
responses belonging to a different observed grant. It is not a transport sequence
number, not a packet acknowledgment and not a security token.

## Escaping

Transform the entire header, payload and CRC, but not the surrounding flags:

| Original byte | Wire bytes |
|---|---|
| `7E` | `7D 5E` |
| `7D` | `7D 5D` |
| Any other value | Unchanged |

The receiver reverses escaping before interpreting the length and CRC. It ignores
bytes before the first flag and accepts repeated flags as empty boundaries. An
invalid escape, a dangling escape at a flag, an invalid header, a length mismatch
or a CRC mismatch discards that frame. A frame exceeding the bounded receive
buffer is discarded until the next flag. A start flag of a subsequent frame is
also a resynchronization point.

There is no time-based inter-byte framing rule. Truncated frames remain bounded
and are rejected or replaced at the next flag; the polling response timer is
separate from byte decoding.

## CRC-32

Use the value produced by Python's `zlib.crc32(header + payload) & 0xffffffff`,
then serialize that integer as four big-endian bytes. The portable core uses
`binascii.crc32` or an equivalent table-free fallback; the resulting value is identical. This is CRC-32/ISO-HDLC
(the commonly used reflected IEEE CRC-32), not CRC-32C. The parameter convention
is polynomial 0x04C11DB7, reflected input/output, initial register 0xFFFFFFFF and
final XOR 0xFFFFFFFF. Its check value for ASCII `123456789` is `CBF43926`.

The CRC detects many transmission errors. It does not authenticate a sender and
is trivial for an attacker to recompute. The receiver additionally checks the
IP header checksum and applicable ICMP/UDP/TCP checksums.

## Polling state machine

Exactly one configured coordinator is allowed on the bus. By default it is node
0, but all nodes may consistently configure another coordinator ID.

The coordinator rotates through a round containing its own node ID followed by
all configured peers. Its own turn permits at most one queued IPv4 packet. On a
peer turn it increments a 16-bit grant counter through 1..65535, sends POLL to
that peer, and waits for one response or a timeout.

Every receiver observes a valid POLL, including polls addressed to another node.
It records the granted source, grant ID and local expiration time. The addressed
peer waits one guard interval and then sends exactly one queued IPv4 packet to
its actual destination, or IDLE to the coordinator. Duplicate copies of the same
poll do not authorize multiple responses.

All listeners accept a non-coordinator response only if source and grant match
the observed, unexpired poll. One valid response consumes that permission.
Destination filtering occurs after this check. The coordinator observes valid
peer-to-peer responses even when it is not their destination, then waits a guard
interval before moving to its next turn. CRC-invalid responses do not end the
wait early; a missing/invalid response eventually times out.

Only grant 0 is used for the coordinator's own data. An adapter's echo of its own
transmission is ignored by source ID. A peer whose process misses its response
deadline remains silent and keeps its queued data for a later poll.

## Timing and failure behavior

Default serial settings are 115200 baud, 8 data bits, no parity, one stop bit,
no software or hardware flow control, a 10 ms guard and a 500 ms response timeout.
At 8N1, 2074 wire bytes take approximately 180 ms at 115200 baud. The CLI rejects
a timeout not exceeding that serialization bound, two guards and a 50 ms margin.
These checks are budgeting rules, not proof of hardware timing correctness.

The serial backend flushes writes before the coordinator starts its response
wait. USB buffering, driver-enable delays and OS scheduling can still move real
edges. The link relies on a promptly serviced event loop and suitable hardware.
Python on a general-purpose OS cannot guarantee deadlines or prevent a badly
behaved/late physical transmitter from causing a collision.

An absent peer consumes a timeout but does not permanently stop the polling
round. A missing coordinator stops all peer transmissions: there is no election
or automatic failover. Lost or CRC-invalid data is not retransmitted by the
link; TCP may recover it, while UDP and ICMP applications must tolerate loss.
A faulty sender, duplicate node ID, duplicate coordinator or malicious device is
outside this protocol's collision-avoidance assumptions.

## Compatibility

This format replaces the earlier conceptual frame sketch with an explicit
versioned header and grant field. Every participating device must use version 1
of this exact format. Although byte escaping resembles RFC 1662, there is no PPP
negotiation and the format is not PPP-compatible.
