# Teaching exercises

Copyright 2026 Ib Helmer Nielsen. SPDX-License-Identifier: Apache-2.0.

## 1. Follow one UDP datagram

Run `python -m pynetstack demo --json trace.jsonl`. Find node 1's UDP application
message and follow its UDP, IPv4, link and byte-port events. Match the received
packet on node 2 by IP identification and payload hex. Explain which fields
belong to a transport segment, IP packet and link frame.

Expected observations: the source/destination ports survive IP encapsulation;
IP addresses and link node IDs have different purposes; escaping can increase
wire length without increasing the original IP length.

## 2. Observe bus access

Identify POLL, IDLE and peer-to-peer data frames. Which node grants a send
opportunity? Does the coordinator retransmit the peer's IP packet? Explain why a
TCP acknowledgment is queued for a later polling opportunity rather than sent
immediately onto the physical bus.

Change the simulated poll list to include an absent node. Confirm that a timeout
occurs and that other nodes continue receiving opportunities.

## 3. Find CRC and checksum boundaries

Inspect `Frame.encode()`, `checksum()` and `pseudo_header()`. Explain exactly
which bytes are covered by the link CRC, IPv4 header checksum and UDP/TCP
checksum. Use a framing test to corrupt one wire byte, then explain why that
packet never reaches the IPv4 receiver.

Extension: alter a UDP payload and recompute only the link CRC. Predict and test
which higher layer rejects the changed packet.

## 4. Recover lost TCP data

Run `python -m pynetstack demo --drop-first-tcp-data --json loss.jsonl`.
Locate the deliberate loss, a later TCP RETRY and the eventual acknowledgment.
Compare sequence numbers: a retransmission reuses sequence space and must not
be delivered twice to the receiving application.

Inspect `test_lost_data_ack_does_not_duplicate_delivery` to distinguish loss of
data from loss of an acknowledgment. Explain why CRC alone cannot recover a
missing packet.

## 5. Demonstrate multiplexing

Run `test_simultaneous_connections_are_demultiplexed` from the test suite or open
several connections to port 8000 from the physical client's console. Record each
connection's local IP/port and remote IP/port. Explain how several connections
share the same server port while maintaining separate sequence numbers.

## 6. Compare a teaching subset with production TCP

Read the implementation's limitations before comparing it with RFC 9293. Explain
why stop-and-wait is easy to inspect but wastes capacity on a long-delay link.
Discuss sliding windows, congestion control, receive-window management, SACK,
out-of-order buffering and adaptive RTO estimation. Do not present PyNetStack as
a complete implementation of those mechanisms.

## 7. Design an application protocol

The TCP echo example does not preserve message boundaries. Define an English,
length-prefixed application protocol and implement a receive buffer that handles
split and coalesced messages. Specify maximum length and invalid-input behavior.
Keep application framing separate from the serial link's framing.

## Suggested evidence

Submit a short English explanation, selected trace records and a reproducible
test. State whether each observation came from software simulation, a mocked
serial API, or physical hardware. Record hardware models and timing settings
when reporting actual RS-485 measurements.
