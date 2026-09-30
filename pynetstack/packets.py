# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""IPv4, ICMP, UDP and TCP wire codecs; no operating-system sockets."""
from .compat import IPv4Address
import struct
from .compat import Record

ICMP, TCP, UDP = 1, 6, 17
FIN, SYN, RST, PSH, ACK = 0x01, 0x02, 0x04, 0x08, 0x10


def checksum(data):
    """Return the Internet one's-complement checksum in network byte order."""
    # Accumulate directly: no temporary tuple containing every 16-bit word.
    total = 0
    for offset in range(0, len(data) - 1, 2):
        total += (data[offset] << 8) | data[offset + 1]
    if len(data) & 1:
        total += data[-1] << 8
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def pseudo_header(source, destination, protocol, length):
    return (IPv4Address(source).packed + IPv4Address(destination).packed
            + struct.pack("!BBH", 0, protocol, length))


class IPv4Packet(Record):
    _fields = ('source', 'destination', 'protocol', 'payload', 'identification', 'ttl')

    def __init__(self, source, destination, protocol, payload, identification=0, ttl=64):
        self.source = source
        self.destination = destination
        self.protocol = protocol
        self.payload = payload
        self.identification = identification
        self.ttl = ttl

    def encode(self):
        if not 1 <= self.ttl <= 255 or len(self.payload) > 65515:
            raise ValueError("Invalid IPv4 TTL or payload length")
        header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(self.payload),
                             self.identification, 0x4000, self.ttl, self.protocol, 0,
                             IPv4Address(self.source).packed,
                             IPv4Address(self.destination).packed)
        return header[:10] + struct.pack("!H", checksum(header)) + header[12:] + self.payload

    @classmethod
    def decode(cls, data):
        if len(data) < 20:
            raise ValueError("Truncated IPv4 header")
        version, _, length, ident, fragment, ttl, protocol, _, source, destination = (
            struct.unpack_from("!BBHHHBBH4s4s", data))
        if version != 0x45:
            raise ValueError("Only IPv4 without options is supported")
        if length != len(data) or checksum(data[:20]) != 0:
            raise ValueError("IPv4 length or checksum mismatch")
        if fragment & 0xBFFF:
            raise ValueError("IPv4 fragmentation/reserved flag is unsupported")
        if ttl == 0:
            raise ValueError("IPv4 TTL is zero")
        return cls(str(IPv4Address(source)), str(IPv4Address(destination)),
                   protocol, data[20:], ident, ttl)


class UdpDatagram(Record):
    _fields = ('source_port', 'destination_port', 'payload')

    def __init__(self, source_port, destination_port, payload):
        self.source_port = source_port
        self.destination_port = destination_port
        self.payload = payload

    def encode(self, source, destination):
        header = struct.pack("!HHHH", self.source_port, self.destination_port,
                             8 + len(self.payload), 0)
        body = header + self.payload
        value = checksum(pseudo_header(source, destination, UDP, len(body)) + body)
        return body[:6] + struct.pack("!H", value or 0xFFFF) + body[8:]

    @classmethod
    def decode(cls, data, source, destination):
        if len(data) < 8:
            raise ValueError("Truncated UDP header")
        source_port, destination_port, length, value = struct.unpack_from("!HHHH", data)
        if length != len(data):
            raise ValueError("UDP length mismatch")
        # A zero UDP checksum is legal for IPv4, but we always generate one.
        if value and checksum(pseudo_header(source, destination, UDP, length) + data):
            raise ValueError("UDP checksum mismatch")
        return cls(source_port, destination_port, data[8:])


class IcmpEcho(Record):
    _fields = ('kind', 'identifier', 'sequence', 'payload')

    def __init__(self, kind, identifier, sequence, payload=b''):
        self.kind = kind
        self.identifier = identifier
        self.sequence = sequence
        self.payload = payload

    def encode(self):
        if self.kind not in (0, 8):
            raise ValueError("Only ICMP echo request/reply is supported")
        body = struct.pack("!BBHHH", self.kind, 0, 0, self.identifier, self.sequence) + self.payload
        return body[:2] + struct.pack("!H", checksum(body)) + body[4:]

    @classmethod
    def decode(cls, data):
        if len(data) < 8 or checksum(data):
            raise ValueError("ICMP size or checksum mismatch")
        kind, code, _, identifier, sequence = struct.unpack_from("!BBHHH", data)
        if kind not in (0, 8) or code:
            raise ValueError("Unsupported ICMP message")
        return cls(kind, identifier, sequence, data[8:])


class TcpSegment(Record):
    _fields = ('source_port', 'destination_port', 'sequence', 'acknowledgment', 'flags', 'payload', 'window')

    def __init__(self, source_port, destination_port, sequence, acknowledgment, flags, payload=b'', window=1024):
        self.source_port = source_port
        self.destination_port = destination_port
        self.sequence = sequence
        self.acknowledgment = acknowledgment
        self.flags = flags
        self.payload = payload
        self.window = window

    @property
    def sequence_length(self):
        return len(self.payload) + bool(self.flags & SYN) + bool(self.flags & FIN)

    def encode(self, source, destination):
        header = struct.pack("!HHIIBBHHH", self.source_port, self.destination_port,
                             self.sequence, self.acknowledgment, 0x50, self.flags,
                             self.window, 0, 0)
        body = header + self.payload
        value = checksum(pseudo_header(source, destination, TCP, len(body)) + body)
        return body[:16] + struct.pack("!H", value) + body[18:]

    @classmethod
    def decode(cls, data, source, destination):
        if len(data) < 20:
            raise ValueError("Truncated TCP header")
        sp, dp, seq, ack, offset, flags, window, _, urgent = struct.unpack_from("!HHIIBBHHH", data)
        if offset != 0x50 or urgent or flags & ~0x1F:
            raise ValueError("TCP options, reserved bits, ECN and urgent data are unsupported")
        if not sp or not dp or (flags & SYN and flags & FIN):
            raise ValueError("Invalid TCP ports or flags")
        if checksum(pseudo_header(source, destination, TCP, len(data)) + data):
            raise ValueError("TCP checksum mismatch")
        return cls(sp, dp, seq, ack, flags, data[20:], window)
