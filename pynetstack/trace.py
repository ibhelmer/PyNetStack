# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Optional human-readable and JSON Lines traces on either interpreter."""
import binascii
import json
from .timing import get_clock


class Trace:
    def __init__(self, node, *, console=True, output=None, hex_dump=False,
                 clock=None, layers=None):
        self.node = node
        self.console, self.output, self.hex_dump = console, output, hex_dump
        self.clock = get_clock(clock)
        self.layers = None if layers is None else tuple(layers)

    def emit(self, layer, direction, event, **details):
        if not self.console and self.output is None:
            return
        if self.layers is not None and layer not in self.layers:
            return
        fields = {}
        for key, value in details.items():
            if key == "raw" and self.output is None and not self.hex_dump:
                continue
            fields[key] = binascii.hexlify(value).decode() if isinstance(value, bytes) else value
        if self.output is not None:
            record = dict(time=round(self.clock.seconds(), 6), node=self.node,
                          layer=layer, direction=direction, event=event)
            record.update(fields)
            self.output.write(json.dumps(record) + "\n")
            self.output.flush()
        if self.console:
            text = " ".join("%s=%s" % (key, value) for key, value in fields.items()
                            if key != "raw" or self.hex_dump)
            print("[node=%s %-4s %-5s] %s %s" % (self.node, layer, direction, event, text))
