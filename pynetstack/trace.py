# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Human-readable and JSON Lines layer tracing."""
import json
import time
from collections.abc import Callable
from typing import TextIO


class Trace:
    def __init__(self, node: int, *, console: bool = True,
                 output: TextIO | None = None, hex_dump: bool = False,
                 clock: Callable[[], float] = time.monotonic):
        self.node = node
        self.console = console
        self.output = output
        self.hex_dump = hex_dump
        self.clock = clock

    def emit(self, layer: str, direction: str, event: str, **details: object) -> None:
        fields = {key: value.hex() if isinstance(value, bytes) else value
                  for key, value in details.items()}
        record = dict(time=round(self.clock(), 6), node=self.node, layer=layer,
                      direction=direction, event=event, **fields)
        if self.output is not None:
            self.output.write(json.dumps(record, ensure_ascii=True) + "\n")
            self.output.flush()
        if self.console:
            visible = {key: value for key, value in fields.items()
                       if key != "raw" or self.hex_dump}
            text = " ".join(f"{key}={value}" for key, value in visible.items())
            print(f"[node={self.node} {layer:4s} {direction:5s}] {event} {text}")
