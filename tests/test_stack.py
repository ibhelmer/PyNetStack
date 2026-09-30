# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Deterministic codec, polling, transport and failure-recovery tests."""
import io
import json
import random
import struct
import unittest
from unittest.mock import patch
import zlib
from pynetstack.framing import FLAG, MAX_BODY, MTU, Frame, FrameDecoder, FrameType
from pynetstack.link import PollingLink
from pynetstack.packets import ACK, FIN, RST, SYN, TCP, UDP, IcmpEcho, IPv4Packet, TcpSegment, UdpDatagram, checksum
from pynetstack.physical import MemoryBus
from pynetstack.stack import NetworkStack
from pynetstack.tcp import add_sequence
from pynetstack.trace import Trace


class Lab:
    def __init__(self, count=3, absent=()):
        self.now = 0.0
        self.clock = lambda: self.now
        self.bus = MemoryBus()
        self.output = io.StringIO()
        self.neighbors = {f"10.0.0.{node + 1}": node for node in range(count)}
        self.nodes = {}
        self.dropped = 0
        for node in range(count):
            if node in absent:
                continue
            trace = Trace(node, console=False, output=self.output, clock=self.clock)
            link = PollingLink(node, self.bus.connect(node), trace,
                peers=tuple(range(1, count)) if node == 0 else (),
                guard=0.002, response_timeout=0.05, clock=self.clock)
            self.nodes[node] = NetworkStack(f"10.0.0.{node + 1}", link, self.neighbors,
                                           clock=self.clock, tcp_rto=0.2)
        self.client, self.server = self.nodes[1], self.nodes[2]

    def step(self):
        for node in self.nodes.values():
            node.step()
        self.now += 0.002

    def until(self, predicate, timeout=12):
        end = self.now + timeout
        while self.now < end:
            self.step()
            if predicate():
                return
        raise AssertionError(f"Condition not reached at simulation time {self.now:.3f}")

    def drop_tcp(self, predicate, count=1, corrupt=False):
        def filter_frame(source, raw):
            frames = FrameDecoder().feed(raw)
            if self.dropped < count and frames and frames[0].kind == FrameType.IPV4:
                packet = IPv4Packet.decode(frames[0].payload)
                if packet.protocol == TCP:
                    segment = TcpSegment.decode(packet.payload, packet.source, packet.destination)
                    if predicate(source, segment):
                        self.dropped += 1
                        if corrupt:
                            return raw[:-2] + bytes([raw[-2] ^ 1]) + raw[-1:]
                        return None
            return raw
        self.bus.filter = filter_frame

    def connect(self, server_callback=None, client_callback=None):
        self.server.tcp.listen(8000, server_callback or (lambda connection, data: None))
        connection = self.client.tcp.connect(self.server.ip, 8000, client_callback)
        self.until(lambda: connection.state == "ESTABLISHED" and self.server.tcp.connections
                   and next(iter(self.server.tcp.connections.values())).state == "ESTABLISHED")
        return connection

    def records(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]


class FramingTests(unittest.TestCase):
    def test_all_byte_values_round_trip(self):
        frame = Frame(2, 1, FrameType.IPV4, 65535, bytes(range(256)))
        self.assertEqual(FrameDecoder().feed(frame.encode()), [frame])

    def test_every_possible_two_chunk_split(self):
        frame = Frame(2, 1, FrameType.IPV4, 7, bytes(range(256)))
        wire = frame.encode()
        for split in range(len(wire) + 1):
            decoder = FrameDecoder()
            self.assertEqual(decoder.feed(wire[:split]) + decoder.feed(wire[split:]), [frame])

    def test_maximum_frame_and_one_byte_chunks(self):
        frame = Frame(255, 0, FrameType.IPV4, payload=b"\x7e" * MTU)
        decoder, result = FrameDecoder(), []
        for value in frame.encode():
            result.extend(decoder.feed(bytes([value])))
        self.assertEqual(result, [frame])

    def test_noise_empty_flags_and_concatenated_frames(self):
        frame = Frame(1, 0, FrameType.POLL, 3)
        self.assertEqual(FrameDecoder().feed(b"noise" + b"\x7e" * 5 + frame.encode() * 2), [frame, frame])

    def test_crc_failure_and_recovery(self):
        errors = []
        frame = Frame(2, 1, FrameType.IPV4, payload=b"hello")
        raw = bytearray(frame.encode())
        raw[3] ^= 1
        self.assertEqual(FrameDecoder(errors.append).feed(bytes(raw) + frame.encode()), [frame])
        self.assertTrue(any("CRC" in error for error in errors))

    def test_invalid_escape_and_truncated_escape(self):
        frame = Frame(1, 0, FrameType.POLL, 2)
        for raw in (b"\x7e\x7d\x00\x7e", b"\x7e\x01\x7d\x7e"):
            errors = []
            self.assertEqual(FrameDecoder(errors.append).feed(raw + frame.encode()), [frame])
            self.assertTrue(errors)

    def test_oversized_receiver_is_bounded(self):
        decoder = FrameDecoder()
        decoder.feed(b"\x7e" + b"x" * 100000)
        self.assertLessEqual(len(decoder.buffer), MAX_BODY)
        frame = Frame(1, 0, FrameType.POLL, 3)
        self.assertEqual(decoder.feed(frame.encode()), [frame])

    def test_bad_length_with_valid_crc(self):
        body = struct.pack("!BBBBHH", 1, 2, 1, 1, 0, 9) + b"a"
        body += struct.pack("!I", zlib.crc32(body))
        with self.assertRaisesRegex(ValueError, "length"):
            Frame.decode(body)

    def test_bad_encoder_values(self):
        for frame in (Frame(256, 1, FrameType.IPV4), Frame(1, 255, FrameType.IPV4),
                      Frame(1, 0, FrameType.POLL, payload=b"x"),
                      Frame(1, 0, FrameType.IPV4, payload=b"x" * (MTU + 1))):
            with self.assertRaises(ValueError):
                frame.encode()

    def test_random_streams_recover(self):
        rng = random.Random(42)
        good = Frame(1, 0, FrameType.POLL, 8)
        for _ in range(200):
            noise = bytes(rng.randrange(256) for _ in range(rng.randrange(200)))
            decoded = FrameDecoder().feed(noise + good.encode())
            self.assertEqual(decoded[-1], good)


class PacketTests(unittest.TestCase):
    def test_checksum_known_vector(self):
        self.assertEqual(checksum(bytes.fromhex("0001f203f4f5f6f7")), 0x220D)
        self.assertEqual(checksum(b""), 0xFFFF)
        self.assertEqual(checksum(b"\x01"), 0xFEFF)

    def test_ipv4_round_trip(self):
        packet = IPv4Packet("10.0.0.2", "10.0.0.3", UDP, b"data", 99)
        self.assertEqual(IPv4Packet.decode(packet.encode()), packet)

    def test_ipv4_rejects_corruption_truncation_options_and_fragments(self):
        raw = IPv4Packet("10.0.0.2", "10.0.0.3", UDP, b"data").encode()
        cases = [raw[:8], raw[:-1], bytes([0x46]) + raw[1:]]
        changed = bytearray(raw)
        changed[6:8] = struct.pack("!H", 0x2000)
        changed[10:12] = b"\0\0"
        changed[10:12] = struct.pack("!H", checksum(bytes(changed[:20])))
        cases.append(bytes(changed))
        changed = bytearray(raw)
        changed[8] ^= 1
        cases.append(bytes(changed))
        for case in cases:
            with self.assertRaises(ValueError):
                IPv4Packet.decode(case)

    def test_udp_round_trip_and_checksum(self):
        datagram = UdpDatagram(40000, 7000, b"odd")
        raw = datagram.encode("10.0.0.2", "10.0.0.3")
        self.assertEqual(UdpDatagram.decode(raw, "10.0.0.2", "10.0.0.3"), datagram)
        with self.assertRaises(ValueError):
            UdpDatagram.decode(raw, "10.0.0.9", "10.0.0.3")
        with self.assertRaises(ValueError):
            UdpDatagram.decode(raw[:-1], "10.0.0.2", "10.0.0.3")

    def test_ipv4_udp_zero_checksum_is_accepted(self):
        raw = struct.pack("!HHHH", 1, 2, 9, 0) + b"x"
        self.assertEqual(UdpDatagram.decode(raw, "10.0.0.2", "10.0.0.3").payload, b"x")

    def test_icmp_echo_and_bad_checksum(self):
        echo = IcmpEcho(8, 11, 7, b"data")
        raw = echo.encode()
        self.assertEqual(IcmpEcho.decode(raw), echo)
        with self.assertRaises(ValueError):
            IcmpEcho.decode(raw[:-1] + b"z")

    def test_tcp_codec_checksum_and_sequence_space(self):
        segment = TcpSegment(50000, 8000, 0xFFFFFFFF, 12, SYN | ACK)
        raw = segment.encode("10.0.0.2", "10.0.0.3")
        self.assertEqual(TcpSegment.decode(raw, "10.0.0.2", "10.0.0.3"), segment)
        self.assertEqual(segment.sequence_length, 1)
        self.assertEqual(add_sequence(0xFFFFFFFF, 1), 0)
        with self.assertRaises(ValueError):
            TcpSegment.decode(raw[:-1] + b"x", "10.0.0.2", "10.0.0.3")


class PollingAndNetworkTests(unittest.TestCase):
    def test_peer_cannot_transmit_without_coordinator(self):
        bus = MemoryBus()
        peer = PollingLink(1, bus.connect(1), Trace(1, console=False))
        observer = bus.connect(2)
        peer.send(2, b"queued")
        for _ in range(100):
            peer.step()
        self.assertEqual(observer.read(), b"")
        self.assertEqual(len(peer.queue), 1)

    def test_one_response_per_duplicate_poll(self):
        bus, now = MemoryBus(), [0.0]
        controller = bus.connect(0)
        peer = PollingLink(1, bus.connect(1), Trace(1, console=False),
                           clock=lambda: now[0], guard=0.001)
        poll = Frame(1, 0, FrameType.POLL, 20).encode()
        controller.write(poll)
        peer.step()
        now[0] = 0.01
        peer.step()
        self.assertEqual(len(FrameDecoder().feed(controller.read())), 1)
        controller.write(poll)
        peer.step()
        now[0] = 0.02
        peer.step()
        self.assertEqual(controller.read(), b"")

    def test_direct_peer_delivery_and_destination_filter(self):
        lab, received = Lab(), []
        lab.server.bind_udp(7000, lambda source, port, data: received.append(data))
        lab.client.send_udp(lab.server.ip, 7000, b"peer-to-peer")
        lab.until(lambda: bool(received))
        self.assertEqual(received, [b"peer-to-peer"])
        lab.step()  # The coordinator observes the broadcast on its next event-loop turn.
        self.assertTrue(any(record["node"] == 0 and record["direction"] == "FILTER" for record in lab.records()))
        self.assertFalse(any(record["node"] == 0 and record["layer"] == "IPv4" and record["direction"] == "TX" for record in lab.records()))

    def test_coordinator_sends_and_receives_data(self):
        lab, received = Lab(), []
        controller = lab.nodes[0]
        controller.bind_udp(7000, lambda source, port, data: received.append(data))
        lab.server.bind_udp(7001, lambda ip, port, data: lab.server.send_udp(ip, port, data, 7001))
        controller.send_udp(lab.server.ip, 7001, b"controller", 7000)
        lab.until(lambda: bool(received))
        self.assertEqual(received, [b"controller"])

    def test_absent_node_does_not_block_bus(self):
        lab, received = Lab(4, absent=(3,)), []
        lab.server.bind_udp(7000, lambda source, port, data: received.append(data))
        for _ in range(3):
            lab.client.send_udp(lab.server.ip, 7000, b"ok")
        lab.until(lambda: len(received) == 3)
        self.assertTrue(any(record["direction"] == "TIMEOUT" for record in lab.records()))

    def test_ping_echo(self):
        lab, received = Lab(), []
        lab.client.on_echo_reply = lambda source, echo: received.append((source, echo))
        lab.client.ping(lab.server.ip, b"hello", identifier=34, sequence=56)
        lab.until(lambda: bool(received))
        self.assertEqual(received, [(lab.server.ip, IcmpEcho(0, 34, 56, b"hello"))])

    def test_unknown_neighbor_mtu_and_bounded_queue(self):
        lab = Lab()
        with self.assertRaises(ValueError):
            lab.client.send_udp("10.0.0.99", 7000, b"x")
        with self.assertRaises(ValueError):
            lab.client.send_udp(lab.server.ip, 7000, b"x" * MTU)
        for _ in range(128):
            lab.client.link.send(2, b"x")
        with self.assertRaises(BufferError):
            lab.client.link.send(2, b"x")

    def test_source_map_mismatch_is_rejected(self):
        lab, received = Lab(), []
        lab.server.bind_udp(7000, lambda source, port, data: received.append(data))
        payload = UdpDatagram(40000, 7000, b"spoof").encode(lab.client.ip, lab.server.ip)
        packet = IPv4Packet(lab.client.ip, lab.server.ip, UDP, payload).encode()
        lab.server.receive_ip(0, packet)
        self.assertEqual(received, [])
        self.assertTrue(any("configured link neighbor" in record["event"] for record in lab.records()))

    def test_invalid_link_configuration(self):
        bus = MemoryBus()
        for kwargs in ({"peers": (1, 1)}, {"peers": (0,)}, {"guard": 1, "response_timeout": 1}):
            with self.assertRaises(ValueError):
                PollingLink(0, bus.connect(len(bus.ports)), Trace(0, console=False), **kwargs)


class TcpTests(unittest.TestCase):
    def test_handshake_stream_segmentation_and_close(self):
        lab, received, echoed = Lab(), bytearray(), bytearray()
        def echo(connection, data):
            received.extend(data)
            connection.send(data)
        connection = lab.connect(echo, lambda connection, data: echoed.extend(data))
        message = bytes(range(256)) * 9
        connection.send(message)
        lab.until(lambda: echoed == message)
        connection.close()
        lab.until(lambda: connection.state == "CLOSED" and not lab.server.tcp.connections)
        self.assertEqual(received, message)
        self.assertIsNone(connection.error)

    def test_simultaneous_connections_are_demultiplexed(self):
        lab, results = Lab(), [bytearray() for _ in range(4)]
        lab.server.tcp.listen(8000, lambda connection, data: connection.send(data))
        connections = []
        for index in range(4):
            connection = lab.client.tcp.connect(lab.server.ip, 8000,
                lambda connection, data, index=index: results[index].extend(data))
            connection.send(f"client-{index}".encode())
            connections.append(connection)
        lab.until(lambda: all(results[i] == f"client-{i}".encode() for i in range(4)))
        self.assertEqual(len({connection.local_port for connection in connections}), 4)

    def test_lost_syn(self):
        lab = Lab()
        lab.drop_tcp(lambda source, segment: source == 1 and segment.flags == SYN)
        connection = lab.connect()
        self.assertEqual(connection.state, "ESTABLISHED")
        self.assertEqual(lab.dropped, 1)

    def test_lost_syn_ack(self):
        lab = Lab()
        lab.drop_tcp(lambda source, segment: source == 2 and segment.flags == SYN | ACK)
        lab.connect()
        self.assertEqual(lab.dropped, 1)

    def test_lost_final_handshake_ack(self):
        lab = Lab()
        lab.drop_tcp(lambda source, segment: source == 1 and segment.flags == ACK)
        lab.connect()
        self.assertEqual(lab.dropped, 1)

    def test_lost_handshake_ack_and_first_data(self):
        lab, received = Lab(), bytearray()
        lab.server.tcp.listen(8000, lambda connection, data: received.extend(data))
        lab.drop_tcp(lambda source, segment: source == 1 and bool(segment.flags & ACK), count=2)
        connection = lab.client.tcp.connect(lab.server.ip, 8000)
        connection.send(b"handshake recovery")
        lab.until(lambda: received == b"handshake recovery" and connection.pending is None)
        self.assertEqual(lab.dropped, 2)

    def test_lost_data_and_corrupted_crc_recover(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                lab, received = Lab(), bytearray()
                connection = lab.connect(lambda connection, data: received.extend(data))
                lab.drop_tcp(lambda source, segment: source == 1 and bool(segment.payload), corrupt=corrupt)
                connection.send(b"reliable" * 100)
                lab.until(lambda: received == b"reliable" * 100 and connection.pending is None)
                self.assertEqual(lab.dropped, 1)
                self.assertTrue(any(record["direction"] == "RETRY" for record in lab.records()))

    def test_lost_data_ack_does_not_duplicate_delivery(self):
        lab, received = Lab(), bytearray()
        connection = lab.connect(lambda connection, data: received.extend(data))
        lab.drop_tcp(lambda source, segment: source == 2 and segment.flags == ACK)
        connection.send(b"no duplicates" * 60)
        lab.until(lambda: received == b"no duplicates" * 60 and connection.pending is None)
        self.assertEqual(lab.dropped, 1)
        self.assertTrue(any(record["direction"] == "RETRY" for record in lab.records()))

    def test_lost_fin_recovers(self):
        lab = Lab()
        connection = lab.connect()
        lab.drop_tcp(lambda source, segment: source == 1 and bool(segment.flags & FIN))
        connection.close()
        lab.until(lambda: connection.state == "CLOSED" and not lab.server.tcp.connections)
        self.assertIsNone(connection.error)
        self.assertEqual(lab.dropped, 1)

    def test_lost_last_ack_recovers_in_time_wait(self):
        lab = Lab()
        connection = lab.connect()
        lab.drop_tcp(lambda source, segment: source == 1 and segment.flags == ACK)
        connection.close()
        lab.until(lambda: connection.state == "CLOSED" and not lab.server.tcp.connections)
        self.assertIsNone(connection.error)
        self.assertEqual(lab.dropped, 1)

    def test_simultaneous_close(self):
        lab = Lab()
        connection = lab.connect()
        peer = next(iter(lab.server.tcp.connections.values()))
        connection.close()
        peer.close()
        lab.until(lambda: connection.state == peer.state == "CLOSED")
        self.assertIsNone(connection.error)
        self.assertIsNone(peer.error)

    def test_close_drains_queued_data(self):
        lab, received = Lab(), bytearray()
        connection = lab.connect(lambda connection, data: received.extend(data))
        connection.send(b"x" * 2000)
        connection.close()
        lab.until(lambda: connection.state == "CLOSED")
        self.assertEqual(received, b"x" * 2000)

    def test_connection_refused_by_rst(self):
        lab = Lab()
        connection = lab.client.tcp.connect(lab.server.ip, 9999)
        lab.until(lambda: connection.state == "CLOSED")
        self.assertIn("reset", connection.error)

    def test_retransmission_exhaustion(self):
        lab = Lab()
        lab.drop_tcp(lambda source, segment: source == 1, count=100)
        connection = lab.client.tcp.connect(lab.server.ip, 8000)
        lab.until(lambda: connection.state == "CLOSED")
        self.assertEqual(connection.error, "Retransmission limit reached")
        self.assertNotIn(connection.key, lab.client.tcp.connections)

    def test_sequence_number_wraparound(self):
        with patch("pynetstack.tcp.secrets.randbits", return_value=0xFFFFFFF0):
            lab, received = Lab(), bytearray()
            connection = lab.connect(lambda connection, data: received.extend(data))
            connection.send(bytes(range(100)))
            lab.until(lambda: len(received) == 100 and connection.pending is None)
            self.assertEqual(connection.send_next, (0xFFFFFFF1 + 100) & 0xFFFFFFFF)
            self.assertEqual(received, bytes(range(100)))

    def test_bad_ack_does_not_clear_pending(self):
        lab = Lab()
        connection = lab.connect()
        peer = next(iter(lab.server.tcp.connections.values()))
        connection.send(b"pending")
        connection.tick()
        self.assertIsNotNone(connection.pending)
        connection.receive(TcpSegment(8000, connection.local_port, peer.send_next,
                                      add_sequence(connection.send_next, 1), ACK))
        self.assertIsNotNone(connection.pending)

    def test_out_of_order_payload_not_delivered(self):
        lab, received = Lab(), bytearray()
        connection = lab.connect(lambda connection, data: received.extend(data))
        peer = next(iter(lab.server.tcp.connections.values()))
        peer.receive(TcpSegment(connection.local_port, 8000, add_sequence(connection.send_next, 10),
                                peer.send_next, ACK, b"out of order"))
        self.assertEqual(received, b"")
        connection.send(b"in order")
        lab.until(lambda: received == b"in order")

    def test_send_buffer_limit(self):
        lab = Lab()
        connection = lab.connect()
        with self.assertRaises(BufferError):
            connection.send(b"x" * 65537)


class TraceTests(unittest.TestCase):
    def test_json_trace_has_all_layers_and_english_events(self):
        lab, received = Lab(), []
        lab.server.bind_udp(7000, lambda source, port, data: received.append(data))
        lab.client.send_udp(lab.server.ip, 7000, b"hello")
        lab.until(lambda: bool(received))
        layers = {record["layer"] for record in lab.records()}
        self.assertTrue({"APP", "UDP", "IPv4", "L2", "L1"} <= layers)
        self.assertTrue(any("raw" in record for record in lab.records()))


if __name__ == "__main__":
    unittest.main()
