# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""IPv4, ICMP, UDP and TCP wire codecs; no operating-system sockets."""
from dataclasses import dataclass
from ipaddress import IPv4Address
import struct

ICMP, TCP, UDP = 1, 6, 17
FIN, SYN, RST, PSH, ACK = 0x01, 0x02, 0x04, 0x08, 0x10


def checksum(data: bytes) -> int:
    """Return the Internet one's-complement checksum in network byte order."""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def pseudo_header(source: str, destination: str, protocol: int, length: int) -> bytes:
    return (IPv4Address(source).packed + IPv4Address(destination).packed
            + struct.pack("!BBH", 0, protocol, length))


@dataclass(frozen=True)
class IPv4Packet:
    source: str
    destination: str
    protocol: int
    payload: bytes
    identification: int = 0
    ttl: int = 64

    def encode(self) -> bytes:
        if not 1 <= self.ttl <= 255 or len(self.payload) > 65515:
            raise ValueError("Invalid IPv4 TTL or payload length")
        header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(self.payload),
                             self.identification, 0x4000, self.ttl, self.protocol, 0,
                             IPv4Address(self.source).packed,
                             IPv4Address(self.destination).packed)
        return header[:10] + struct.pack("!H", checksum(header)) + header[12:] + self.payload

    @classmethod
    def decode(cls, data: bytes) -> "IPv4Packet":
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


@dataclass(frozen=True)
class UdpDatagram:
    source_port: int
    destination_port: int
    payload: bytes

    def encode(self, source: str, destination: str) -> bytes:
        header = struct.pack("!HHHH", self.source_port, self.destination_port,
                             8 + len(self.payload), 0)
        body = header + self.payload
        value = checksum(pseudo_header(source, destination, UDP, len(body)) + body)
        return body[:6] + struct.pack("!H", value or 0xFFFF) + body[8:]

    @classmethod
    def decode(cls, data: bytes, source: str, destination: str) -> "UdpDatagram":
        if len(data) < 8:
            raise ValueError("Truncated UDP header")
        source_port, destination_port, length, value = struct.unpack_from("!HHHH", data)
        if length != len(data):
            raise ValueError("UDP length mismatch")
        # A zero UDP checksum is legal for IPv4, but we always generate one.
        if value and checksum(pseudo_header(source, destination, UDP, length) + data):
            raise ValueError("UDP checksum mismatch")
        return cls(source_port, destination_port, data[8:])


@dataclass(frozen=True)
class IcmpEcho:
    kind: int
    identifier: int
    sequence: int
    payload: bytes = b""

    def encode(self) -> bytes:
        if self.kind not in (0, 8):
            raise ValueError("Only ICMP echo request/reply is supported")
        body = struct.pack("!BBHHH", self.kind, 0, 0, self.identifier, self.sequence) + self.payload
        return body[:2] + struct.pack("!H", checksum(body)) + body[4:]

    @classmethod
    def decode(cls, data: bytes) -> "IcmpEcho":
        if len(data) < 8 or checksum(data):
            raise ValueError("ICMP size or checksum mismatch")
        kind, code, _, identifier, sequence = struct.unpack_from("!BBHHH", data)
        if kind not in (0, 8) or code:
            raise ValueError("Unsupported ICMP message")
        return cls(kind, identifier, sequence, data[8:])


@dataclass(frozen=True)
class TcpSegment:
    source_port: int
    destination_port: int
    sequence: int
    acknowledgment: int
    flags: int
    payload: bytes = b""
    window: int = 1024

    @property
    def sequence_length(self) -> int:
        return len(self.payload) + bool(self.flags & SYN) + bool(self.flags & FIN)

    def encode(self, source: str, destination: str) -> bytes:
        header = struct.pack("!HHIIBBHHH", self.source_port, self.destination_port,
                             self.sequence, self.acknowledgment, 0x50, self.flags,
                             self.window, 0, 0)
        body = header + self.payload
        value = checksum(pseudo_header(source, destination, TCP, len(body)) + body)
        return body[:16] + struct.pack("!H", value) + body[18:]

    @classmethod
    def decode(cls, data: bytes, source: str, destination: str) -> "TcpSegment":
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
