# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Portable software self-test: no unittest, sockets, threads or hardware needed."""
import binascii
import gc
import sys
sys.path.insert(0, ".")
from pynetstack.compat import crc32, crc32_fallback, IPv4Address
from pynetstack.framing import Frame, FrameDecoder, FrameType, MTU
from pynetstack.packets import IPv4Packet, UdpDatagram, IcmpEcho, TcpSegment, SYN, TCP, UDP, checksum
from pynetstack.link import PollingLink
from pynetstack.simulation import MemoryBus
from pynetstack.stack import NetworkStack
from pynetstack.timing import Clock
from pynetstack.trace import Trace


def hex_text(raw):
    return binascii.hexlify(raw).decode()


def check_codecs():
    # Golden bytes generated with the unmodified PyNetStack 0.1.0 release.
    source, destination = "10.0.0.1", "10.0.0.3"
    assert crc32(b"123456789") == 0xCBF43926
    assert crc32_fallback(b"123456789") == 0xCBF43926
    assert checksum(binascii.unhexlify("0001f203f4f5f6f7")) == 0x220D
    assert hex_text(Frame(2, 0, FrameType.POLL, 42).encode()) == "7e01020002002a0000710d5e887e"
    assert hex_text(Frame(2, 1, 1, 65535, b"\x00\x7d\x7e\xff").encode()) == (
        "7e01020101ffff0004007d5d7d5effc1cfe65c7e")
    udp = UdpDatagram(40000, 7000, b"Hello C6").encode(source, destination)
    assert hex_text(udp) == "9c401b580010cd0948656c6c6f204336"
    assert hex_text(IPv4Packet(source, destination, UDP, udp, 123).encode()) == (
        "45000024007b40004011264b0a0000010a0000039c401b580010cd0948656c6c6f204336")
    assert hex_text(IcmpEcho(8, 12, 34, b"Hello C6").encode()) == "080090a9000c002248656c6c6f204336"
    assert hex_text(TcpSegment(49152, 8000, 0xFFFFFFFE, 0, SYN).encode(source, destination)) == (
        "c0001f40fffffffe0000000050020400b89f0000")
    for payload in (bytes(range(256)), b"\x7e" * MTU):
        frame = Frame(2, 1, 1, 7, payload)
        decoder, decoded = FrameDecoder(), []
        for value in frame.encode():
            decoded.extend(decoder.feed(bytes([value])))
        assert decoded == [frame]
    assert str(IPv4Address(b"\x0a\x00\x00\x03")) == destination
    print("PASS: CRC, checksum, v0.1.0 golden bytes, escaping and maximum frame")


class FakeTicks:
    """A ring counter lets tests cross wrap boundaries without waiting days."""
    PERIOD = 1 << 20

    def __init__(self, start=None):
        self.total = self.PERIOD - 20 if start is None else start

    def ticks_ms(self):
        return self.total % self.PERIOD

    def ticks_add(self, stamp, delta):
        if not -self.PERIOD // 2 <= delta < self.PERIOD // 2:
            raise ValueError("Tick interval is outside the valid half period")
        return (stamp + delta) % self.PERIOD

    def ticks_diff(self, later, earlier):
        half = self.PERIOD // 2
        return ((later - earlier + half) % self.PERIOD) - half


class ChunkPort:
    """Imitate fragmented UART reads without importing machine or pySerial."""
    def __init__(self, port):
        self.port = port

    def read(self, size=4096):
        return self.port.read(min(size, 31))

    def write(self, data):
        self.port.write(data)


def check_network(drop_data=False, wrap=False):
    counter = FakeTicks()
    moment = [0.0]
    clock = Clock(ticks=counter) if wrap else Clock(lambda: moment[0])
    bus, nodes = MemoryBus(), []
    neighbors = {"10.0.0.1": 0, "10.0.0.2": 1, "10.0.0.3": 2}
    for node in range(3):
        trace = Trace(node, console=False, clock=clock)
        link = PollingLink(node, ChunkPort(bus.connect(node)), trace,
            peers=(1, 2) if node == 0 else (), guard=0.002,
            response_timeout=0.15, clock=clock, max_queue=8)
        nodes.append(NetworkStack("10.0.0.%d" % (node + 1), link, neighbors,
            clock=clock, tcp_rto=0.4, tcp_max_connections=2, tcp_max_buffer=2048))
    client, server = nodes[1], nodes[2]
    udp_reply, ping_reply, tcp_reply = [], [], bytearray()
    client.bind_udp(40000, lambda ip, port, data: udp_reply.append(data))
    client.on_echo_reply = lambda ip, echo: ping_reply.append(echo.payload)
    server.bind_udp(7000, lambda ip, port, data: server.send_udp(ip, port, data, 7000))
    server.tcp.listen(8000, lambda connection, data: connection.send(data))
    dropped = [False]
    if drop_data:
        def filter_wire(source, raw):
            frame = FrameDecoder().feed(raw)[0]
            if not dropped[0] and source == 1 and frame.kind == FrameType.IPV4:
                packet = IPv4Packet.decode(frame.payload)
                if packet.protocol == TCP:
                    segment = TcpSegment.decode(packet.payload, packet.source, packet.destination)
                    if segment.payload:
                        dropped[0] = True
                        return None
            return raw
        bus.filter = filter_wire
    client.ping(server.ip, b"C6 ping")
    client.send_udp(server.ip, 7000, b"C6 UDP")
    connection = client.tcp.connect(server.ip, 8000,
        lambda connection, data: tcp_reply.extend(data))
    message = b"C6 binary \x00\x7d\x7e\xff " * 40
    connection.send(message)
    finished = False
    for _ in range(12000):
        for node in nodes:
            node.step()
        moment[0] += 0.002
        counter.total += 2
        if not finished and tcp_reply == message and connection.pending is None:
            finished = True
            connection.close()
        if finished and not client.tcp.connections and not server.tcp.connections:
            break
    assert udp_reply == [b"C6 UDP"], "UDP echo failed"
    assert ping_reply == [b"C6 ping"], "ICMP echo failed"
    assert tcp_reply == message, "TCP data differs"
    assert not client.tcp.connections and not server.tcp.connections, "TCP close failed"
    assert not drop_data or dropped[0], "Loss was not injected"
    print("PASS: three nodes, 31-byte reads, ICMP/UDP/TCP echo, close; loss=%s wrap=%s" % (
        drop_data, wrap))


def main():
    print("PyNetStack software self-test on %s" % globals().get("HARNESS_LABEL", sys.implementation.name))
    check_codecs()
    for drop, wrap in ((False, False), (True, False), (True, True)):
        check_network(drop, wrap)
        gc.collect()
    if hasattr(gc, "mem_free"):
        print("Free Python heap after software tests: %d bytes" % gc.mem_free())
    print("PASS: software tests complete. UART wiring and RS-485 electrical timing were NOT tested.")


if __name__ == "__main__":
    main()
