# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Wrap-safe deadlines without accumulating absolute floating-point uptime."""
import time


class Clock:
    """Use opaque ticks on MicroPython, seconds on CPython and in simulations.

    Durations are always seconds. Never compare raw timestamps directly.
    Service timers well within half the platform tick period; deep sleep is
    not supported while a bus node or TCP connection is active.
    """
    def __init__(self, source=None, ticks=None):
        if source is not None and ticks is not None:
            raise ValueError("Choose a seconds source or a tick provider, not both")
        if source is None:
            if ticks is None and hasattr(time, "ticks_ms"):
                ticks = time
            self.source = ticks.ticks_ms if ticks is not None else time.monotonic
        else:
            self.source = source
        self.ticks = ticks

    def __call__(self):
        return self.source()

    def add(self, stamp, seconds):
        if self.ticks is None:
            return stamp + seconds
        milliseconds = int(round(seconds * 1000))
        return self.ticks.ticks_add(stamp, milliseconds)

    def diff(self, later, earlier):
        if self.ticks is None:
            return later - earlier
        return self.ticks.ticks_diff(later, earlier) / 1000

    def seconds(self):
        # Display only. This value may wrap; scheduling uses add()/diff().
        stamp = self.source()
        return stamp / 1000 if self.ticks is not None else stamp


def get_clock(clock=None):
    return clock if isinstance(clock, Clock) else Clock(clock)
