# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Run a three-node demonstration or an interactive physical RS-485 node."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
from queue import Empty, SimpleQueue
import shlex
import sys
import threading
import time
from . import __version__
from .framing import MAX_BODY, FrameDecoder, FrameType
from .link import PollingLink
from .packets import TCP, IPv4Packet, TcpSegment
from .physical import MemoryBus, SerialPort
from .stack import NetworkStack
from .trace import Trace

BANNER = f"PyNetStack {__version__} | Copyright 2026 Ib Helmer Nielsen | Apache-2.0"
HELP = """Commands:
  ping IP [TEXT]         Send an ICMP echo request
  udp IP PORT TEXT      Send a UDP datagram from port 40000
  tcp IP PORT TEXT      Open a TCP connection and queue UTF-8 text
  connections           Show TCP connections and states
  close LOCAL_PORT      Drain queued TCP data, then send FIN
  help                  Show this help
  quit                  Exit (use close first for graceful TCP shutdown)
"""


def run_demo(args: argparse.Namespace, output=None) -> int:
    """Exercise the same frame codecs, MAC and network stack as serial mode."""
    moment = [0.0]
    clock = lambda: moment[0]
    bus = MemoryBus()
    neighbors = {f"10.0.0.{node + 1}": node for node in range(3)}
    nodes = []
    for node in range(3):
        trace = Trace(node, console=not args.quiet, output=output,
                      hex_dump=args.hex, clock=clock)
        link = PollingLink(node, bus.connect(node), trace, peers=(1, 2) if node == 0 else (),
                           response_timeout=0.05, guard=0.002, clock=clock)
        nodes.append(NetworkStack(f"10.0.0.{node + 1}", link, neighbors, clock=clock, tcp_rto=0.3))
    client, server = nodes[1], nodes[2]
    udp_replies, ping_replies, tcp_reply = [], [], bytearray()
    server.bind_udp(7000, lambda ip, port, data: server.send_udp(ip, port, data, 7000))
    server.tcp.listen(8000, lambda connection, data: connection.send(data))
    client.bind_udp(40000, lambda ip, port, data: udp_replies.append(data))
    client.on_echo_reply = lambda source, echo: ping_replies.append(echo.payload)
    message = b"Follow this message through every layer of PyNetStack. " * 8
    dropped = [False]
    if args.drop_first_tcp_data:
        def lose_one(source: int, raw: bytes) -> bytes | None:
            frames = FrameDecoder().feed(raw)
            if source == 1 and not dropped[0] and frames and frames[0].kind == FrameType.IPV4:
                packet = IPv4Packet.decode(frames[0].payload)
                if packet.protocol == TCP and TcpSegment.decode(packet.payload, packet.source, packet.destination).payload:
                    dropped[0] = True
                    nodes[0].trace.emit("L1", "DROP", "Deliberately lost first TCP data frame")
                    return None
            return raw
        bus.filter = lose_one
    client.ping(server.ip, b"ping-demo")
    client.send_udp(server.ip, 7000, b"udp-demo")
    connection = client.tcp.connect(server.ip, 8000, lambda connection, data: tcp_reply.extend(data))
    connection.send(message)
    success = False
    for _ in range(30000):
        for node in nodes:
            node.step()
        moment[0] += 0.002
        if tcp_reply == message and udp_replies == [b"udp-demo"] and ping_replies == [b"ping-demo"]:
            connection.close()
            if connection.state == "CLOSED" and not server.tcp.connections:
                success = True
                break
        if connection.error:
            break
    if not success:
        print(f"Demo failed: state={connection.state}, error={connection.error}, bytes={len(tcp_reply)}", file=sys.stderr)
        return 1
    print(f"PASS: three nodes; ICMP echo; UDP echo; TCP {len(message)} bytes; graceful close.")
    if args.drop_first_tcp_data:
        print(f"PASS: deliberate data loss recovered by TCP retransmission: {dropped[0]}.")
    return 0


def run_node(args: argparse.Namespace, output=None) -> int:
    neighbors = json.loads(Path(args.neighbors).read_text(encoding="utf-8"))
    if not isinstance(neighbors, dict):
        raise ValueError("Neighbor file must contain an IPv4-to-node JSON object")
    peers = tuple(sorted(node for node in neighbors.values() if node != args.coordinator_id))
    worst_wire_time = (2 * MAX_BODY + 2) * 10 / args.baudrate
    if args.response_timeout <= worst_wire_time + 2 * args.guard + 0.05:
        raise ValueError(f"Response timeout is too short; use more than {worst_wire_time + 2 * args.guard + 0.05:.3f} seconds")
    rto = args.tcp_rto or max(3.0, 2 * len(neighbors) * (args.response_timeout + args.guard) + 1)
    trace = Trace(args.node_id, console=not args.quiet, output=output, hex_dump=args.hex)
    port = SerialPort(args.port, args.baudrate, args.direction, args.rts_active_low)
    try:
        link = PollingLink(args.node_id, port, trace, coordinator=args.coordinator_id,
                           peers=peers if args.node_id == args.coordinator_id else (),
                           response_timeout=args.response_timeout, guard=args.guard)
        stack = NetworkStack(args.ip, link, neighbors, tcp_rto=rto)
        stack.bind_udp(40000, lambda ip, remote_port, data: print(f"UDP {ip}:{remote_port}: {data!r}"))
        stack.on_echo_reply = lambda ip, echo: print(f"ICMP reply from {ip}: sequence={echo.sequence}, bytes={len(echo.payload)}")
        if args.udp_echo:
            stack.bind_udp(args.udp_echo, lambda ip, remote_port, data: stack.send_udp(ip, remote_port, data, args.udp_echo))
        if args.tcp_echo:
            stack.tcp.listen(args.tcp_echo, lambda connection, data: connection.send(data))
        commands: SimpleQueue[str | None] = SimpleQueue()
        if not args.no_console:
            def read_commands() -> None:
                for line in sys.stdin:
                    commands.put(line)
                commands.put(None)
            threading.Thread(target=read_commands, daemon=True, name="console-input").start()
            print(HELP)
        print(f"Node {args.node_id}, IP {stack.ip}, {args.port}, {args.baudrate} baud, TCP RTO {rto:.2f}s")
        started, ping_sequence = time.monotonic(), 0
        while not args.duration or time.monotonic() - started < args.duration:
            stack.step()
            try:
                line = commands.get_nowait()
            except Empty:
                line = ""
            if line is None:
                break
            try:
                words = shlex.split(line)
                if words:
                    command = words[0].lower()
                    if command == "quit":
                        break
                    if command == "help":
                        print(HELP)
                    elif command == "ping" and len(words) >= 2:
                        ping_sequence = ping_sequence % 65535 + 1
                        stack.ping(words[1], " ".join(words[2:] or ["PyNetStack"]).encode(),
                                   identifier=args.node_id + 1, sequence=ping_sequence)
                    elif command == "udp" and len(words) >= 4:
                        stack.send_udp(words[1], int(words[2]), " ".join(words[3:]).encode())
                    elif command == "tcp" and len(words) >= 4:
                        connection = stack.tcp.connect(words[1], int(words[2]),
                            lambda connection, data: print(f"TCP local_port={connection.local_port}: {data!r}"))
                        connection.send(" ".join(words[3:]).encode())
                        print(f"TCP connection created: local_port={connection.local_port}")
                    elif command == "connections":
                        for connection in stack.tcp.connections.values():
                            print(connection.key, connection.state, connection.error or "")
                    elif command == "close" and len(words) == 2:
                        matches = [connection for connection in stack.tcp.connections.values()
                                   if connection.local_port == int(words[1])]
                        if not matches:
                            raise ValueError("No connection uses that local port")
                        for connection in matches:
                            connection.close()
                    elif command != "help":
                        print("Unknown command or invalid arguments. Type help.")
            except (ValueError, ConnectionError, BufferError) as error:
                print(f"Command error: {error}", file=sys.stderr)
            time.sleep(0.001)
    finally:
        port.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="An observable educational TCP/IP stack over RS-485")
    parser.add_argument("--version", action="version", version=BANNER)
    subcommands = parser.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="Run a hardware-free three-node simulation")
    demo.add_argument("--drop-first-tcp-data", action="store_true", help="Demonstrate TCP retransmission")
    node = subcommands.add_parser("node", help="Run one physical RS-485 node")
    node.add_argument("--port", required=True, help="COM3 or /dev/ttyUSB0, for example")
    node.add_argument("--node-id", type=int, required=True)
    node.add_argument("--ip", required=True)
    node.add_argument("--neighbors", required=True, help="JSON file mapping IPv4 addresses to link IDs")
    node.add_argument("--coordinator-id", type=int, default=0)
    node.add_argument("--baudrate", type=int, default=115200)
    node.add_argument("--direction", choices=("auto", "native"), default="auto")
    node.add_argument("--rts-active-low", action="store_true")
    node.add_argument("--response-timeout", type=float, default=0.5)
    node.add_argument("--guard", type=float, default=0.01)
    node.add_argument("--tcp-rto", type=float)
    node.add_argument("--udp-echo", type=int)
    node.add_argument("--tcp-echo", type=int)
    node.add_argument("--no-console", action="store_true")
    node.add_argument("--duration", type=float, default=0, help="Stop after this many seconds; zero runs until interrupted")
    for command in (demo, node):
        command.add_argument("--quiet", action="store_true", help="Disable console layer traces")
        command.add_argument("--hex", action="store_true", help="Include raw hexadecimal data in console traces")
        command.add_argument("--json", metavar="PATH", help="Write JSON Lines traces; includes complete packet hex")
    args = parser.parse_args(argv)
    if args.command == "node" and (args.baudrate <= 0 or args.duration < 0):
        parser.error("Baud rate must be positive and duration must be nonnegative")
    print(BANNER)
    try:
        context = open(args.json, "w", encoding="utf-8") if args.json else nullcontext(None)
        with context as output:
            return run_demo(args, output) if args.command == "demo" else run_node(args, output)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except (OSError, ValueError, RuntimeError, BufferError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
