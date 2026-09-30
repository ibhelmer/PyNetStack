# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Build a source-only MicroPython deployment folder; never flash or erase a board."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = (
    "__init__.py", "compat.py", "timing.py", "framing.py", "packets.py",
    "link.py", "tcp.py", "stack.py", "trace.py", "simulation.py", "uart.py",
)


def prepare(destination: Path, node_id: int, peer_ids: tuple[int, ...], autostart: bool) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Destination is not empty; choose a new staging folder")
    if not peer_ids or len(set(peer_ids)) != len(peer_ids) or any(
            not 1 <= node <= 253 for node in peer_ids):
        raise ValueError("Peer IDs must be unique integers in 1..253")
    if node_id not in (0, *peer_ids):
        raise ValueError("Node ID must be coordinator 0 or one of the peer IDs")
    # This example uses one /24 and IP last octet = node ID + 1 by convention.
    neighbors = {"10.0.0.%d" % (node + 1): node for node in (0, *peer_ids)}
    config = (ROOT / "examples/esp32c6/board_config.py").read_text(encoding="utf-8")
    config = config.replace("NODE_ID = 2", "NODE_ID = %d" % node_id)
    config = config.replace('LOCAL_IP = "10.0.0.3"', 'LOCAL_IP = "10.0.0.%d"' % (node_id + 1))
    start = config.index("NEIGHBORS = ")
    end = config.index("\n", start)
    config = config[:start] + "NEIGHBORS = " + repr(neighbors) + config[end:]
    config = config.replace("AUTOSTART = False", "AUTOSTART = %s" % autostart)
    (destination / "pynetstack").mkdir(parents=True, exist_ok=True)
    for name in CORE_MODULES:
        (destination / "pynetstack" / name).write_bytes((ROOT / "pynetstack" / name).read_bytes())
    for name in ("main.py", "esp32c6_node.py"):
        (destination / name).write_bytes((ROOT / "examples/esp32c6" / name).read_bytes())
    (destination / "board_config.py").write_text(config, encoding="utf-8")
    (destination / "selftest.py").write_bytes((ROOT / "tests/micropython_selftest.py").read_bytes())
    for name in ("LICENSE", "NOTICE"):
        (destination / name).write_bytes((ROOT / name).read_bytes())
    print("Prepared node %d in %s" % (node_id, destination))
    print("Manual startup" if not autostart else "AUTOSTART ENABLED after a two-second boot delay")
    print("No hardware was accessed. Review board_config.py before copying files.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node-id", type=int, default=2)
    parser.add_argument("--peer-ids", default="1,2", help="Comma-separated non-coordinator IDs")
    parser.add_argument("--autostart", action="store_true", help="Opt in to running after every board reset")
    args = parser.parse_args()
    try:
        peer_ids = tuple(int(value) for value in args.peer_ids.split(","))
        prepare(args.output, args.node_id, peer_ids, args.autostart)
    except (ValueError, OSError) as error:
        print("Preparation failed: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
