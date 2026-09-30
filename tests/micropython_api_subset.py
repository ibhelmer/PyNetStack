# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""CPython-only import/API restriction harness; NOT a MicroPython interpreter."""
import binascii
import builtins
import json
from pathlib import Path
import runpy
import struct
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
original_import = builtins.__import__
original_implementation = sys.implementation
original_modules = dict(sys.modules)
original_time = time
blocked = {"dataclasses", "enum", "typing", "ipaddress", "argparse", "pathlib",
           "queue", "threading", "secrets", "serial", "collections.abc"}


def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name in blocked or (name == "builtins" and "BufferError" in (fromlist or ())):
        raise ImportError("Module/API excluded by the MicroPython subset harness: " + name)
    return original_import(name, globals, locals, fromlist, level)


try:
    # Do not claim this changes the actual running interpreter.
    implementation = dict(vars(original_implementation))
    implementation["name"] = "micropython"
    sys.implementation = types.SimpleNamespace(**implementation)
    period = 1 << 30
    sys.modules["time"] = types.SimpleNamespace(
        ticks_ms=lambda: int(original_time.monotonic() * 1000) % period,
        ticks_add=lambda stamp, delta: (stamp + delta) % period,
        ticks_diff=lambda later, earlier: ((later - earlier + period // 2) % period) - period // 2,
        sleep_ms=lambda delay: original_time.sleep(delay / 1000),
        sleep_us=lambda delay: original_time.sleep(delay / 1000000))
    sys.modules["struct"] = types.SimpleNamespace(**{
        name: getattr(struct, name) for name in ("pack", "unpack", "unpack_from", "calcsize")})
    # Intentionally omit native crc32 to exercise the optional fallback.
    sys.modules["binascii"] = types.SimpleNamespace(hexlify=binascii.hexlify, unhexlify=binascii.unhexlify)
    sys.modules["json"] = types.SimpleNamespace(dumps=lambda obj: json.dumps(obj), loads=json.loads)
    # Load this before restricting further imports made by the harness itself.
    source = (ROOT / "tests/micropython_selftest.py").read_text(encoding="utf-8")
    builtins.__import__ = restricted_import
    print("CPython compatibility harness (not MicroPython): restricting optional APIs")
    namespace = {"__name__": "subset_selftest", "HARNESS_LABEL": "CPython API restriction (NOT MicroPython)"}
    exec(compile(source, "micropython_selftest.py", "exec"), namespace)
    namespace["main"]()
finally:
    builtins.__import__ = original_import
    sys.implementation = original_implementation
    for name in ("time", "struct", "binascii", "json"):
        sys.modules[name] = original_modules[name]
print("PASS: CPython API-restriction harness only; native MicroPython execution still required.")
