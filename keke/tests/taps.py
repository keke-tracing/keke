"""Tests for keke.TraceOutput weakref tap registry and JSONL event forwarding."""

import gc
import io
import json
import os
import time
import unittest
from io import StringIO

from typing import Any, Callable, IO

import keke
from keke import EVENT, TraceOutput


def _wait_for(
    fn: Callable[[], Any], timeout: float = 2.0, interval: float = 0.005
) -> Any:
    """Spin until fn() returns truthy or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    return fn()


class NonclosingStringIO(io.StringIO):
    def close(self) -> None:
        pass


class TapRegistryTest(unittest.TestCase):
    def tearDown(self) -> None:
        if keke.TRACER is not None:
            try:
                keke.TRACER.__exit__(None, None, None)
            except Exception:
                pass

    def _make_tracer(self) -> "tuple[TraceOutput, IO[str]]":
        out = NonclosingStringIO()
        t = TraceOutput(out, close_output_file=False)
        t.__enter__()
        return t, out

    def test_add_tap_appears_in_taps(self) -> None:
        t, _ = self._make_tracer()
        tap = StringIO()
        t._add_tap(tap)
        self.assertIn(tap, list(t._taps))
        t.__exit__(None, None, None)

    def test_remove_tap_disappears(self) -> None:
        t, _ = self._make_tracer()
        tap = StringIO()
        t._add_tap(tap)
        t._remove_tap(tap)
        self.assertNotIn(tap, list(t._taps))
        t.__exit__(None, None, None)

    def test_exit_closes_registered_taps(self) -> None:
        t, _ = self._make_tracer()
        tap = StringIO()
        t._add_tap(tap)
        t.__exit__(None, None, None)
        self.assertTrue(tap.closed)

    def test_exit_with_no_taps_is_safe(self) -> None:
        t, _ = self._make_tracer()
        t.__exit__(None, None, None)  # must not raise

    def test_gc_tap_silently_removed(self) -> None:
        t, _ = self._make_tracer()
        tap = StringIO()
        t._add_tap(tap)
        del tap
        gc.collect()
        self.assertEqual(list(t._taps), [])
        t.__exit__(None, None, None)  # must not raise

    def test_removed_tap_not_closed_on_exit(self) -> None:
        t, _ = self._make_tracer()
        tap = StringIO()
        t._add_tap(tap)
        t._remove_tap(tap)
        t.__exit__(None, None, None)
        self.assertFalse(tap.closed)  # was removed — TraceOutput should not close it


class TapForwardingTest(unittest.TestCase):
    def tearDown(self) -> None:
        if keke.TRACER is not None:
            try:
                keke.TRACER.__exit__(None, None, None)
            except Exception:
                pass

    def test_events_forwarded_as_jsonl(self) -> None:
        out = NonclosingStringIO()
        tap = StringIO()
        t = TraceOutput(out, close_output_file=False)
        t.__enter__()
        t._add_tap(tap)
        t.put(EVENT({"ph": "X", "name": "hello", "ts": 1, "pid": 1}), False)
        _wait_for(lambda: tap.getvalue())
        t._remove_tap(tap)
        t.__exit__(None, None, None)
        lines = [line for line in tap.getvalue().splitlines() if line]
        self.assertGreaterEqual(len(lines), 1)
        obj = json.loads(lines[0])
        self.assertEqual(obj["name"], "hello")

    def test_broken_tap_removed_automatically(self) -> None:
        """A tap that raises OSError on write is removed without error."""
        out = NonclosingStringIO()
        t = TraceOutput(out, close_output_file=False)
        t.__enter__()
        r, w = os.pipe()
        os.close(r)  # close read end — writing to w gives BrokenPipeError
        tap = os.fdopen(w, "w")
        t._add_tap(tap)
        t.put(EVENT({"ph": "X", "name": "test", "ts": 1, "pid": 1}), False)
        _wait_for(lambda: tap not in list(t._taps))
        self.assertNotIn(tap, list(t._taps))
        t.__exit__(None, None, None)

    def test_multiple_taps_all_receive_events(self) -> None:
        out = NonclosingStringIO()
        taps = [StringIO() for _ in range(3)]
        t = TraceOutput(out, close_output_file=False)
        t.__enter__()
        for tap in taps:
            t._add_tap(tap)
        t.put(EVENT({"ph": "X", "name": "ev", "ts": 0, "pid": 0}), False)
        _wait_for(lambda: taps[-1].getvalue())
        for tap in taps:
            t._remove_tap(tap)
        t.__exit__(None, None, None)
        for tap in taps:
            lines = [line for line in tap.getvalue().splitlines() if line]
            self.assertTrue(any('"ev"' in line for line in lines))

    def test_nonblocking_set_on_add_tap(self) -> None:
        """_add_tap tries to set O_NONBLOCK on real fds."""
        out = NonclosingStringIO()
        t = TraceOutput(out, close_output_file=False)
        t.__enter__()
        r, w = os.pipe()
        tap = os.fdopen(w, "w")
        try:
            t._add_tap(tap)
            import fcntl

            flags = fcntl.fcntl(tap.fileno(), fcntl.F_GETFL)
            self.assertTrue(flags & os.O_NONBLOCK)
        except (ImportError, io.UnsupportedOperation):
            pass  # fcntl not available on this platform
        finally:
            t._remove_tap(tap)
            tap.close()
            os.close(r)
        t.__exit__(None, None, None)
