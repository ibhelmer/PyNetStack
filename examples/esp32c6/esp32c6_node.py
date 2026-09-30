# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Cooperative RS-485 echo node or coordinator for ESP32-C6-DevKitC-1."""
import gc
import os
import sys
import time
import board_config as config
from pynetstack import __version__
from pynetstack.framing import MAX_BODY
from pynetstack.link import PollingLink
from pynetstack.stack import NetworkStack
from pynetstack.timing import Clock
from pynetstack.trace import Trace
from pynetstack.uart import open_esp32c6_port


def check_platform():
    if sys.implementation.name != "micropython" or sys.platform != "esp32":
        raise RuntimeError("Use MicroPython ESP32_GENERIC_C6 firmware on this board")
    description = os.uname().machine.upper().replace("-", "").replace("_", "")
    if "ESP32C6" not in description:
        raise RuntimeError("This pin profile is only intended for ESP32-C6")


def run():
    """Serve ICMP echo, UDP/7000 and TCP/8000 until interrupted with Ctrl-C."""
    check_platform()
    maximum_wire_time = (2 * MAX_BODY + 2) * 10 / config.UART_BAUDRATE
    minimum_timeout = maximum_wire_time + 2 * config.POLL_GUARD + 0.05
    if config.POLL_RESPONSE_TIMEOUT <= minimum_timeout:
        raise ValueError("Poll timeout is too short for a maximum escaped frame")
    peers = tuple(sorted(node for node in config.NEIGHBORS.values()
                         if node != config.COORDINATOR_ID))
    # RTO starts when a segment is queued. Allow a pessimistic polling round.
    minimum_rto = 2 * (len(peers) + 1) * (
        config.POLL_RESPONSE_TIMEOUT + config.POLL_GUARD)
    clock = Clock()
    print("PyNetStack %s | Copyright 2026 Ib Helmer Nielsen | Apache-2.0" % __version__)
    print("Board: ESP32-C6-DevKitC-1; UART1 TX=GPIO%d RX=GPIO%d DE=GPIO%d" % (
        config.UART_TX_PIN, config.UART_RX_PIN, config.RS485_DE_PIN))
    print("Node %d, IP %s, coordinator %d" % (
        config.NODE_ID, config.LOCAL_IP, config.COORDINATOR_ID))
    print("Press Ctrl-C to stop. This is an experimental, unvalidated hardware port.")
    trace = Trace(config.NODE_ID, console=config.TRACE_ENABLED,
                  hex_dump=config.TRACE_HEX, layers=config.TRACE_LAYERS, clock=clock)
    port = open_esp32c6_port(baudrate=config.UART_BAUDRATE,
        tx_pin=config.UART_TX_PIN, rx_pin=config.UART_RX_PIN,
        de_pin=config.RS485_DE_PIN, rx_buffer=config.UART_RX_BUFFER,
        tx_buffer=config.UART_TX_BUFFER, clock=clock)
    try:
        link = PollingLink(config.NODE_ID, port, trace,
            coordinator=config.COORDINATOR_ID,
            peers=peers if config.NODE_ID == config.COORDINATOR_ID else (),
            response_timeout=config.POLL_RESPONSE_TIMEOUT,
            guard=config.POLL_GUARD, clock=clock, max_queue=config.LINK_QUEUE_LENGTH)
        stack = NetworkStack(config.LOCAL_IP, link, config.NEIGHBORS, clock=clock,
            tcp_rto=max(config.TCP_RTO, minimum_rto),
            tcp_max_connections=config.MAX_TCP_CONNECTIONS,
            tcp_max_buffer=config.TCP_SEND_BUFFER, tcp_mss=config.TCP_MSS)
        stack.bind_udp(config.UDP_ECHO_PORT,
            lambda ip, source_port, data: stack.send_udp(
                ip, source_port, data, config.UDP_ECHO_PORT))
        stack.tcp.listen(config.TCP_ECHO_PORT, lambda connection, data: connection.send(data))
        gc.collect()
        print("Free Python heap after setup: %d bytes" % gc.mem_free())
        while True:
            stack.step()
            # Yield to the runtime without long blocking application work.
            time.sleep_ms(1)
    except KeyboardInterrupt:
        print("Stopped; RS-485 transmitter disabled.")
    finally:
        port.close()
