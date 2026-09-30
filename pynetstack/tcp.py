# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Educational TCP subset: handshake, stop-and-wait data, retries and FIN.

This is deliberately NOT a complete RFC 9293 implementation. It has no
congestion control, TCP options, out-of-order reassembly or persist timer.
"""
from .compat import IPv4Address
from .compat import secrets, BufferError, ConnectionError
from .packets import ACK, FIN, PSH, RST, SYN, TcpSegment
from .timing import get_clock

MASK = 0xFFFFFFFF
MSS = 256
MAX_BUFFER = 65536


def add_sequence(value, amount):
    return (value + amount) & MASK


def flag_names(flags):
    return "|".join(name for bit, name in ((SYN, "SYN"), (ACK, "ACK"),
                    (PSH, "PSH"), (FIN, "FIN"), (RST, "RST")) if flags & bit) or "NONE"


class Pending(object):
    def __init__(self, segment, sent_at, interval, retries=0):
        self.segment = segment
        self.sent_at = sent_at
        self.interval = interval
        self.retries = retries


class TcpConnection:
    def __init__(self, engine, local_port, remote_ip,
                 remote_port, on_data):
        self.engine = engine
        self.local_port, self.remote_ip, self.remote_port = local_port, remote_ip, remote_port
        self.on_data = on_data
        self.initial_sequence = secrets.randbits(32)
        self.send_next = self.initial_sequence
        self.receive_next = 0
        self.peer_window = 1024
        self.state = "CLOSED"
        self.changed_at = engine.clock()
        self.pending = None
        self.outbound = bytearray()
        self.close_requested = False
        self.error = None

    @property
    def key(self):
        # The local IP is supplied by the owning engine: together these form a 4-tuple.
        return self.local_port, self.remote_ip, self.remote_port

    def _state(self, state):
        previous, self.state = self.state, state
        self.changed_at = self.engine.clock()
        self.engine.trace.emit("TCP", "STATE", state, previous=previous,
                               local_port=self.local_port, remote_ip=self.remote_ip,
                               remote_port=self.remote_port)

    def send(self, data):
        if self.state not in ("SYN_SENT", "SYN_RECEIVED", "ESTABLISHED", "CLOSE_WAIT") or self.close_requested:
            raise ConnectionError("Connection is not open for writing")
        if len(self.outbound) + len(data) > self.engine.max_buffer:
            raise BufferError("TCP application send buffer is full")
        self.outbound.extend(data)
        self.engine.trace.emit("APP", "TX", "TCP bytes queued", length=len(data),
                               local_port=self.local_port)

    def close(self):
        """Finish queued data, then close both directions after the FIN exchange."""
        self.close_requested = True

    def _send(self, flags, data = b"", reliable = False):
        segment = TcpSegment(self.local_port, self.remote_port, self.send_next,
                             self.receive_next if flags & ACK else 0, flags, data)
        if reliable:
            if self.pending is not None:
                raise RuntimeError("Stop-and-wait permits only one unacknowledged segment")
            self.pending = Pending(segment, self.engine.clock(), self.engine.rto)
            self.send_next = add_sequence(self.send_next, segment.sequence_length)
        self.engine._transmit(self.remote_ip, segment)

    def _fail(self, message):
        self.error = message
        self.pending = None
        self.outbound = bytearray()
        self.engine.trace.emit("TCP", "ERROR", message, local_port=self.local_port)
        self._state("CLOSED")

    def _pump(self):
        if self.pending is not None or self.state not in ("ESTABLISHED", "CLOSE_WAIT"):
            return
        if self.outbound and self.peer_window:
            length = min(len(self.outbound), self.engine.mss, self.peer_window)
            data = bytes(self.outbound[:length])
            self.outbound = self.outbound[length:]
            self._send(ACK | PSH, data, reliable=True)
        elif not self.outbound and self.close_requested:
            self._state("LAST_ACK" if self.state == "CLOSE_WAIT" else "FIN_WAIT_1")
            self._send(FIN | ACK, reliable=True)

    def receive(self, segment):
        if segment.flags & RST:
            valid = (bool(segment.flags & ACK) and segment.acknowledgment == self.send_next
                     if self.state == "SYN_SENT" else segment.sequence == self.receive_next)
            if valid:
                self._fail("Connection reset by peer")
            return
        if self.state == "SYN_SENT":
            if segment.flags == SYN | ACK and segment.acknowledgment == self.send_next and not segment.payload:
                self.receive_next = add_sequence(segment.sequence, 1)
                self.peer_window = segment.window
                self.pending = None
                self._state("ESTABLISHED")
                self._send(ACK)
                self._pump()
            return
        if self.state == "SYN_RECEIVED" and segment.flags == SYN:
            if segment.sequence == add_sequence(self.receive_next, -1) and self.pending:
                self.engine._transmit(self.remote_ip, self.pending.segment)
            return
        if (self.state == "ESTABLISHED" and segment.flags == SYN | ACK
                and segment.acknowledgment == add_sequence(self.initial_sequence, 1)
                and add_sequence(segment.sequence, 1) == self.receive_next):
            # Re-acknowledge a SYN-ACK when the final handshake ACK was lost.
            self.engine._transmit(self.remote_ip, TcpSegment(
                self.local_port, self.remote_port, segment.acknowledgment,
                self.receive_next, ACK))
            return
        if self.state == "TIME_WAIT":
            if segment.flags & FIN and add_sequence(segment.sequence, segment.sequence_length) == self.receive_next:
                self._send(ACK)
                self.changed_at = self.engine.clock()
            return
        if not segment.flags & ACK or segment.flags & SYN or self.state == "CLOSED":
            return
        if segment.sequence != self.receive_next:
            # Duplicate or out-of-order data is never delivered twice.
            self._send(ACK)
            return
        self.peer_window = segment.window
        if self.state == "SYN_RECEIVED" and segment.acknowledgment != self.send_next:
            return
        if self.pending is not None and segment.acknowledgment == self.send_next:
            self.pending = None
            if self.state == "SYN_RECEIVED":
                self._state("ESTABLISHED")
            elif self.state == "FIN_WAIT_1":
                self._state("FIN_WAIT_2")
            elif self.state == "CLOSING":
                self._state("TIME_WAIT")
            elif self.state == "LAST_ACK":
                self._state("CLOSED")
                return
        if segment.payload:
            if self.state not in ("ESTABLISHED", "FIN_WAIT_1", "FIN_WAIT_2"):
                self._send(ACK)
                return
            self.receive_next = add_sequence(self.receive_next, len(segment.payload))
            self.engine.trace.emit("APP", "RX", "TCP bytes delivered", length=len(segment.payload),
                                   local_port=self.local_port, remote_ip=self.remote_ip)
            self.on_data(self, segment.payload)
        if segment.flags & FIN:
            self.receive_next = add_sequence(self.receive_next, 1)
            if self.state == "ESTABLISHED":
                self._state("CLOSE_WAIT")
                self.close_requested = True
            elif self.state == "FIN_WAIT_1":
                self._state("CLOSING")
            elif self.state == "FIN_WAIT_2":
                self._state("TIME_WAIT")
        if segment.payload or segment.flags & FIN:
            self._send(ACK)
        self._pump()

    def tick(self):
        now = self.engine.clock()
        if self.pending and self.engine.clock.diff(now, self.pending.sent_at) >= self.pending.interval:
            if self.pending.retries >= self.engine.max_retries:
                self._fail("Retransmission limit reached")
                return
            self.pending.retries += 1
            self.pending.sent_at = now
            self.pending.interval = min(self.pending.interval * 2, self.engine.max_rto)
            self.engine.trace.emit("TCP", "RETRY", "Retransmitting segment",
                                   sequence=self.pending.segment.sequence, retry=self.pending.retries)
            self.engine._transmit(self.remote_ip, self.pending.segment)
        if self.state == "TIME_WAIT" and self.engine.clock.diff(now, self.changed_at) >= self.engine.time_wait:
            self._state("CLOSED")
        elif self.state == "FIN_WAIT_2" and self.engine.clock.diff(now, self.changed_at) >= 60:
            self._fail("Peer did not finish closing")
        self._pump()


class TcpEngine:
    def __init__(self, ip, send_packet, trace,
                 *, clock = None, rto = 3.0,
                 max_retries = 5, time_wait = None,
                 max_connections=64, max_buffer=MAX_BUFFER, mss=MSS):
        if rto <= 0 or max_retries < 0 or (time_wait is not None and time_wait <= 0):
            raise ValueError("Invalid TCP timer settings")
        if not 1 <= max_connections <= 64 or not 1 <= max_buffer <= MAX_BUFFER or not 1 <= mss <= MSS:
            raise ValueError("Invalid TCP resource limits")
        self.max_connections, self.max_buffer, self.mss = max_connections, max_buffer, mss
        self.ip, self.send_packet, self.trace, self.clock = ip, send_packet, trace, get_clock(clock)
        self.rto, self.max_retries = rto, max_retries
        self.max_rto = max(rto, min(30.0, 4 * rto))
        self.time_wait = time_wait if time_wait is not None else max(2.0, 2 * self.max_rto)
        self.listeners = {}
        self.connections = {}
        self.next_port = 49152

    def listen(self, port, on_data):
        if not 1 <= port <= 65535 or port in self.listeners:
            raise ValueError("Invalid or already bound TCP port")
        self.listeners[port] = on_data

    def connect(self, remote_ip, remote_port,
                on_data = None):
        remote_ip = str(IPv4Address(remote_ip))
        if not 1 <= remote_port <= 65535 or remote_ip == self.ip:
            raise ValueError("Invalid remote TCP endpoint")
        if len(self.connections) >= self.max_connections:
            raise BufferError("TCP connection table is full")
        used = {key[0] for key in self.connections} | set(self.listeners)
        for _ in range(16384):
            port = self.next_port
            self.next_port = 49152 + (port - 49152 + 1) % 16384
            if port not in used:
                break
        else:
            raise BufferError("No ephemeral TCP port available")
        connection = TcpConnection(self, port, remote_ip, remote_port,
                                   on_data or (lambda connection, data: None))
        self.connections[connection.key] = connection
        connection._state("SYN_SENT")
        try:
            connection._send(SYN, reliable=True)
        except Exception:
            del self.connections[connection.key]
            raise
        return connection

    def _transmit(self, destination, segment):
        self.trace.emit("TCP", "TX", flag_names(segment.flags),
                        source_port=segment.source_port, destination_port=segment.destination_port,
                        sequence=segment.sequence, acknowledgment=segment.acknowledgment,
                        length=len(segment.payload))
        self.send_packet(destination, segment.encode(self.ip, destination))

    def receive(self, source, raw):
        segment = TcpSegment.decode(raw, source, self.ip)
        self.trace.emit("TCP", "RX", flag_names(segment.flags),
                        source_port=segment.source_port, destination_port=segment.destination_port,
                        sequence=segment.sequence, acknowledgment=segment.acknowledgment,
                        length=len(segment.payload), checksum="valid")
        key = segment.destination_port, source, segment.source_port
        connection = self.connections.get(key)
        if connection is None:
            callback = self.listeners.get(segment.destination_port)
            if callback and segment.flags == SYN and not segment.payload and len(self.connections) < self.max_connections:
                connection = TcpConnection(self, segment.destination_port, source,
                                           segment.source_port, callback)
                connection.receive_next = add_sequence(segment.sequence, 1)
                connection.peer_window = segment.window
                self.connections[key] = connection
                connection._state("SYN_RECEIVED")
                connection._send(SYN | ACK, reliable=True)
            elif not segment.flags & RST:
                flags = RST if segment.flags & ACK else RST | ACK
                sequence = segment.acknowledgment if segment.flags & ACK else 0
                acknowledgment = 0 if segment.flags & ACK else add_sequence(segment.sequence, segment.sequence_length)
                self._transmit(source, TcpSegment(segment.destination_port, segment.source_port,
                                                  sequence, acknowledgment, flags))
        else:
            connection.receive(segment)

    def tick(self):
        for key, connection in list(self.connections.items()):
            connection.tick()
            if connection.state == "CLOSED":
                del self.connections[key]
