# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Single-coordinator polling MAC for a shared half-duplex serial bus."""
from .framing import BROADCAST, MTU, Frame, FrameDecoder, FrameType
from .compat import BoundedQueue, BufferError
from .timing import get_clock


class PollingLink:
    def __init__(self, node, port, trace, *, coordinator = 0,
                 peers = (), response_timeout = 0.5,
                 guard = 0.01, clock = None, max_queue=128):
        clock = get_clock(clock)
        if not all(0 <= value <= 254 for value in (node, coordinator, *peers)):
            raise ValueError("Node addresses must be in 0..254")
        if len(set(peers)) != len(peers) or coordinator in peers:
            raise ValueError("Poll list must contain unique non-coordinator nodes")
        if peers and node != coordinator:
            raise ValueError("Only the coordinator takes a poll list")
        if guard < 0 or response_timeout <= 2 * guard:
            raise ValueError("Response timeout must exceed twice the guard time")
        self.node, self.port, self.trace = node, port, trace
        self.coordinator, self.peers, self.clock = coordinator, peers, clock
        self.response_timeout, self.guard = response_timeout, guard
        self.queue = BoundedQueue(max_queue)
        self.on_packet = lambda source, packet: None
        self.decoder = FrameDecoder(lambda error: trace.emit("L2", "DROP", error))
        self.turns, self.turn = (coordinator, *peers), 0
        self.grant = 0
        self.waiting = None
        self.deadline = 0.0
        self.ready_at = clock.add(clock(), guard)
        self.allowed = None
        self.allow_until = 0.0
        self.response_at = None
        self.last_poll = None

    def send(self, destination, packet):
        if not 0 <= destination <= BROADCAST or destination == self.node:
            raise ValueError("Invalid destination; local loopback is not implemented")
        if len(packet) > MTU:
            raise ValueError(f"IPv4 packet exceeds the {MTU}-byte link MTU")
        if len(self.queue) >= self.queue.capacity:
            raise BufferError("Link transmit queue is full")
        self.queue.append((destination, bytes(packet)))
        self.trace.emit("L2", "QUEUE", "IPv4", destination=destination, length=len(packet))

    def _write(self, frame):
        raw = frame.encode()
        self.trace.emit("L2", "TX", FrameType.name(frame.kind), source=frame.source,
                        destination=frame.destination, grant=frame.grant,
                        length=len(frame.payload), crc="generated")
        self.trace.emit("L1", "TX", "bytes", length=len(raw), raw=raw)
        self.port.write(raw)

    def _send_data_or_idle(self, grant):
        if self.queue:
            destination, packet = self.queue.popleft()
            self._write(Frame(destination, self.node, FrameType.IPV4, grant, packet))
        else:
            self._write(Frame(self.coordinator, self.node, FrameType.IDLE, grant))

    def _receive(self, frame):
        now = self.clock()
        if frame.source == self.node:  # Ignore adapters that echo their own output.
            return
        self.trace.emit("L2", "RX", FrameType.name(frame.kind), source=frame.source,
                        destination=frame.destination, grant=frame.grant, crc="valid")
        if frame.kind == FrameType.POLL:
            if frame.source != self.coordinator or frame.destination in (self.coordinator, BROADCAST):
                self.trace.emit("L2", "DROP", "Invalid poll")
                return
            self.allowed = (frame.destination, frame.grant)
            self.allow_until = self.clock.add(now, self.response_timeout)
            self.response_at = None
            if frame.destination == self.node and frame.grant != self.last_poll:
                self.last_poll = frame.grant
                self.response_at = self.clock.add(now, self.guard)
            return
        permitted = (frame.source == self.coordinator and frame.grant == 0
                     or self.allowed == (frame.source, frame.grant) and self.clock.diff(self.allow_until, now) >= 0)
        if not permitted:
            self.trace.emit("L2", "DROP", "Unsolicited or stale transmission")
            return
        if frame.kind == FrameType.IDLE and frame.destination != self.coordinator:
            self.trace.emit("L2", "DROP", "Invalid idle destination")
            return
        # A valid grant authorizes only one frame, even for non-addressed listeners.
        if frame.source != self.coordinator:
            self.allowed = None
        if self.node == self.coordinator and self.waiting == (frame.source, frame.grant):
            self.waiting = None
            self.ready_at = self.clock.add(now, self.guard)
        if frame.kind == FrameType.IPV4:
            if frame.destination in (self.node, BROADCAST):
                self.on_packet(frame.source, frame.payload)
            else:
                self.trace.emit("L2", "FILTER", "Frame is addressed to another node")

    def step(self):
        raw = self.port.read()
        if raw:
            self.trace.emit("L1", "RX", "bytes", length=len(raw), raw=raw)
            for frame in self.decoder.feed(raw):
                self._receive(frame)
        now = self.clock()
        if self.node != self.coordinator:
            if self.response_at is not None and self.clock.diff(now, self.response_at) >= 0:
                self.response_at = None
                if self.clock.diff(self.allow_until, now) >= self.guard and self.allowed is not None:
                    self._send_data_or_idle(self.allowed[1])
                    self.allowed = None
                else:
                    self.trace.emit("L2", "DROP", "Missed polling deadline")
            return
        if self.waiting is not None:
            if self.clock.diff(now, self.deadline) < 0:
                return
            self.trace.emit("L2", "TIMEOUT", "Poll response missing", peer=self.waiting[0])
            self.waiting = self.allowed = None
            self.ready_at = self.clock.add(now, self.guard)
        if self.clock.diff(now, self.ready_at) < 0:
            return
        # The coordinator gets one data opportunity per round, not unlimited priority.
        peer = self.turns[self.turn]
        self.turn = (self.turn + 1) % len(self.turns)
        if peer == self.node:
            if self.queue:
                self._send_data_or_idle(0)
            self.ready_at = self.clock.add(self.clock(), self.guard)
        else:
            self.grant = self.grant % 65535 + 1
            self.waiting = self.allowed = (peer, self.grant)
            self._write(Frame(peer, self.node, FrameType.POLL, self.grant))
            self.deadline = self.allow_until = self.clock.add(self.clock(), self.response_timeout)
