# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""One IPv4 host on the shared bus, with static neighbor resolution."""
from .compat import IPv4Address
from .framing import MTU
from .link import PollingLink
from .packets import ICMP, TCP, UDP, IcmpEcho, IPv4Packet, UdpDatagram
from .tcp import TcpEngine


class NetworkStack:
    def __init__(self, ip, link, neighbors, *,
                 clock = None, tcp_rto = 3.0,
                 tcp_max_connections=64, tcp_max_buffer=65536, tcp_mss=256):
        self.ip, self.link, self.trace = str(IPv4Address(ip)), link, link.trace
        self.neighbors = {str(IPv4Address(address)): node for address, node in neighbors.items()}
        if not all(isinstance(node, int) and 0 <= node <= 254 for node in self.neighbors.values()):
            raise ValueError("Neighbor IDs must be integers in 0..254")
        if len(set(self.neighbors.values())) != len(self.neighbors):
            raise ValueError("This version supports one IPv4 address per bus node")
        if self.neighbors.get(self.ip) != link.node:
            raise ValueError("The neighbor map must include the local IP and node ID")
        self.identification = 0
        self.udp_handlers = {}
        self.on_echo_reply = lambda source, echo: None
        self.tcp = TcpEngine(self.ip, lambda destination, packet: self.send_ip(destination, TCP, packet),
                             self.trace, clock=clock if clock is not None else link.clock, rto=tcp_rto,
                             max_connections=tcp_max_connections, max_buffer=tcp_max_buffer,
                             mss=tcp_mss)
        self.link.on_packet = self.receive_ip

    def send_ip(self, destination, protocol, payload):
        destination = str(IPv4Address(destination))
        if destination not in self.neighbors:
            raise ValueError(f"No static neighbor mapping for {destination}")
        packet = IPv4Packet(self.ip, destination, protocol, payload, self.identification)
        raw = packet.encode()
        if len(raw) > MTU:
            raise ValueError(f"Packet exceeds MTU {MTU}; fragmentation is not implemented")
        self.identification = (self.identification + 1) & 0xFFFF
        self.trace.emit("IPv4", "TX", "packet", source=self.ip, destination=destination,
                        protocol=protocol, identification=packet.identification, length=len(raw), raw=raw)
        self.link.send(self.neighbors[destination], raw)

    def bind_udp(self, port, callback):
        if not 1 <= port <= 65535 or port in self.udp_handlers:
            raise ValueError("Invalid or already bound UDP port")
        self.udp_handlers[port] = callback

    def send_udp(self, destination, destination_port, payload, source_port = 40000):
        if not 1 <= source_port <= 65535 or not 1 <= destination_port <= 65535:
            raise ValueError("UDP ports must be in 1..65535")
        if len(payload) > MTU - 28:
            raise ValueError(f"UDP payload exceeds {MTU - 28} bytes")
        self.trace.emit("APP", "TX", "UDP datagram", length=len(payload))
        datagram = UdpDatagram(source_port, destination_port, payload)
        self.trace.emit("UDP", "TX", "datagram", source_port=source_port,
                        destination_port=destination_port, length=len(payload))
        self.send_ip(destination, UDP, datagram.encode(self.ip, destination))

    def ping(self, destination, payload = b"PyNetStack", *,
             identifier = 1, sequence = 1):
        if len(payload) > MTU - 28:
            raise ValueError("ICMP echo payload exceeds MTU")
        self.trace.emit("ICMP", "TX", "Echo request", identifier=identifier, sequence=sequence)
        self.send_ip(destination, ICMP, IcmpEcho(8, identifier, sequence, payload).encode())

    def receive_ip(self, source_node, raw):
        try:
            packet = IPv4Packet.decode(raw)
            if packet.destination != self.ip:
                self.trace.emit("IPv4", "DROP", "Not the local IP address")
                return
            if self.neighbors.get(packet.source) != source_node:
                raise ValueError("IPv4 source does not match the configured link neighbor")
            self.trace.emit("IPv4", "RX", "packet", source=packet.source,
                            destination=packet.destination, protocol=packet.protocol,
                            identification=packet.identification, checksum="valid", raw=raw)
            if packet.protocol == UDP:
                datagram = UdpDatagram.decode(packet.payload, packet.source, self.ip)
                self.trace.emit("UDP", "RX", "datagram", source_port=datagram.source_port,
                                destination_port=datagram.destination_port, length=len(datagram.payload))
                callback = self.udp_handlers.get(datagram.destination_port)
                if callback:
                    self.trace.emit("APP", "RX", "UDP datagram delivered", length=len(datagram.payload))
                    callback(packet.source, datagram.source_port, datagram.payload)
                else:
                    self.trace.emit("UDP", "DROP", "No listener; ICMP port-unreachable is not implemented")
            elif packet.protocol == ICMP:
                echo = IcmpEcho.decode(packet.payload)
                self.trace.emit("ICMP", "RX", "Echo request" if echo.kind == 8 else "Echo reply",
                                identifier=echo.identifier, sequence=echo.sequence)
                if echo.kind == 8:
                    self.trace.emit("ICMP", "TX", "Echo reply", sequence=echo.sequence)
                    self.send_ip(packet.source, ICMP, IcmpEcho(0, echo.identifier, echo.sequence, echo.payload).encode())
                else:
                    self.on_echo_reply(packet.source, echo)
            elif packet.protocol == TCP:
                self.tcp.receive(packet.source, packet.payload)
            else:
                self.trace.emit("IPv4", "DROP", "Unsupported upper-layer protocol", protocol=packet.protocol)
        except ValueError as error:
            self.trace.emit("IP+", "DROP", str(error))

    def step(self):
        self.link.step()
        self.tcp.tick()
