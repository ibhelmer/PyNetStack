# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Compare mixed old/new stacks on a byte bus. Both run under CPython."""
import argparse
import importlib
import importlib.util
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pynetstack.framing import FrameDecoder, FrameType
from pynetstack.packets import IPv4Packet, TcpSegment, TCP
from pynetstack.simulation import MemoryBus
from pynetstack import link, stack, trace


def load_reference(directory):
    package = directory / "pynetstack"
    spec = importlib.util.spec_from_file_location(
        "pynetstack_reference", package / "__init__.py",
        submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return tuple(importlib.import_module("pynetstack_reference." + name)
                 for name in ("link", "stack", "trace"))


def check(reference, reverse=False, lose_data=False):
    current = (link, stack, trace)
    versions = (current, current, reference) if reverse else (reference, reference, current)
    now = [0.0]
    clock = lambda: now[0]
    bus, nodes = MemoryBus(), []
    neighbors = {"10.0.0.%d" % (node + 1): node for node in range(3)}
    for node, (link_module, stack_module, trace_module) in enumerate(versions):
        logger = trace_module.Trace(node, console=False, clock=clock)
        mac = link_module.PollingLink(node, bus.connect(node), logger,
            peers=(1, 2) if node == 0 else (), response_timeout=0.05, guard=0.002, clock=clock)
        nodes.append(stack_module.NetworkStack("10.0.0.%d" % (node + 1), mac, neighbors,
                                              clock=clock, tcp_rto=0.3))
    client, server = nodes[1:]
    udp, ping, stream, dropped = [], [], bytearray(), [False]
    server.bind_udp(7000, lambda ip, port, data: server.send_udp(ip, port, data, 7000))
    server.tcp.listen(8000, lambda connection, data: connection.send(data))
    client.bind_udp(40000, lambda ip, port, data: udp.append(data))
    client.on_echo_reply = lambda ip, echo: ping.append(echo.payload)
    if lose_data:
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
    client.ping(server.ip, b"legacy ping")
    client.send_udp(server.ip, 7000, b"legacy UDP")
    connection = client.tcp.connect(server.ip, 8000,
        lambda connection, data: stream.extend(data))
    message = bytes(range(256)) * 4
    connection.send(message)
    closing = False
    for _ in range(15000):
        for node in nodes:
            node.step()
        now[0] += 0.002
        if stream == message and connection.pending is None and not closing:
            closing = True
            connection.close()
        if closing and not client.tcp.connections and not server.tcp.connections:
            break
    assert ping == [b"legacy ping"] and udp == [b"legacy UDP"]
    assert stream == message and not client.tcp.connections and not server.tcp.connections
    assert not lose_data or dropped[0]
    print("PASS: mixed reference/current ICMP, UDP, TCP 1024 bytes and FIN; reverse=%s loss=%s" % (
        reverse, lose_data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Root of an unmodified older checkout")
    args = parser.parse_args()
    reference = load_reference(args.reference.resolve())
    for reverse in (False, True):
        for lose in (False, True):
            check(reference, reverse, lose)
    print("Both implementations ran under CPython; this is not a MicroPython or hardware test.")


if __name__ == "__main__":
    main()
