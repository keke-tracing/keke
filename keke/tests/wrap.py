"""Tests for keke.wrap -- WrappedLock span emission."""

import io
import json
import sys
import threading
import types
import unittest

from typing import Any, Dict

from keke import TraceOutput
from keke.wrap import AlreadyWrapped, WrappedLock, wrap_all, wrap_lock, restore_all


class NonclosingStringIO(io.StringIO):
    def close(self) -> None:
        pass


def _span_events(f: io.StringIO) -> "list[dict[str, object]]":
    return [
        ev
        for ev in json.loads(f.getvalue())
        if ev.get("ph") == "X" and ev.get("cat") != "gc"
    ]


class WrappedLockTest(unittest.TestCase):
    def test_acquire_emits_wait_span(self) -> None:
        n = 1.0

        def clock() -> float:
            return float(n)

        f = NonclosingStringIO()
        lock = threading.Lock()
        wrapped = wrap_lock(lock, name="mylock")

        with TraceOutput(file=f, pid=1, clock=clock, close_output_file=False):
            wrapped.acquire()
            n = 2.0
            wrapped.release()

        events = _span_events(f)
        wait_events = [ev for ev in events if ev["cat"] == "wait"]
        self.assertEqual(1, len(wait_events))
        self.assertEqual("mylock", wait_events[0]["name"])
        self.assertEqual(1_000_000, wait_events[0]["ts"])

    def test_release_emits_hold_span(self) -> None:
        n = 1.0

        def clock() -> float:
            return float(n)

        f = NonclosingStringIO()
        lock = threading.Lock()
        wrapped = wrap_lock(lock, name="mylock")

        with TraceOutput(file=f, pid=1, clock=clock, close_output_file=False):
            wrapped.acquire()
            n = 2.0
            wrapped.release()

        events = _span_events(f)
        hold_events = [ev for ev in events if ev["cat"] == "hold"]
        self.assertEqual(1, len(hold_events))
        self.assertEqual("mylock", hold_events[0]["name"])
        self.assertEqual(1_000_000, hold_events[0]["ts"])
        self.assertEqual(1_000_000, hold_events[0]["dur"])

    def test_context_manager_emits_both_spans(self) -> None:
        n = 1.0

        def clock() -> float:
            return float(n)

        f = NonclosingStringIO()
        lock = threading.Lock()
        wrapped = wrap_lock(lock, name="ctx")

        with TraceOutput(file=f, pid=1, clock=clock, close_output_file=False):
            with wrapped:
                n = 3.0

        events = _span_events(f)
        cats = {ev["cat"] for ev in events}
        self.assertIn("wait", cats)
        self.assertIn("hold", cats)

    def test_no_tracer_does_not_crash(self) -> None:
        lock = threading.Lock()
        wrapped = wrap_lock(lock)
        with wrapped:
            pass
        self.assertFalse(lock.locked())

    def test_non_blocking_failed_acquire_emits_wait_but_no_hold(self) -> None:
        n = 1.0

        def clock() -> float:
            return float(n)

        f = NonclosingStringIO()
        lock = threading.Lock()
        lock.acquire()  # pre-acquire so wrapped.acquire(blocking=False) fails
        wrapped = wrap_lock(lock, name="nb")

        with TraceOutput(file=f, pid=1, clock=clock, close_output_file=False):
            result = wrapped.acquire(blocking=False)
            n = 2.0

        events = _span_events(f)
        self.assertFalse(result)
        wait_events = [ev for ev in events if ev["cat"] == "wait"]
        hold_events = [ev for ev in events if ev["cat"] == "hold"]
        self.assertEqual(1, len(wait_events))
        self.assertEqual(0, len(hold_events))

        lock.release()

    def test_locked_delegates(self) -> None:
        lock = threading.Lock()
        wrapped = wrap_lock(lock)
        self.assertFalse(wrapped.locked())
        lock.acquire()
        self.assertTrue(wrapped.locked())
        lock.release()

    def test_restore_object_target_returns_original(self) -> None:
        lock = threading.Lock()
        wrapped = wrap_lock(lock)
        self.assertIs(lock, wrapped.restore())

    def test_restore_object_target_does_not_write_back(self) -> None:
        # When constructed from an object there is nowhere to write back to;
        # confirm no AttributeError and no side-effect.
        mod = types.ModuleType("_fake")
        lock = threading.Lock()
        mod.lk = lock  # type: ignore[attr-defined]
        wrapped = wrap_lock(mod.lk, name="lk")  # object, not string
        wrapped.restore()
        self.assertIs(mod.lk, lock)  # unchanged

    def test_restore_string_target_writes_back(self) -> None:
        mod = types.ModuleType("_fake")
        lock = threading.Lock()
        mod.lk = lock  # type: ignore[attr-defined]
        sys.modules["_fake"] = mod
        try:
            wrapped = wrap_lock("_fake.lk")
            self.assertIs(mod.lk, wrapped)
            self.assertEqual(wrapped._name, "lk")
            wrapped.restore()
            self.assertIs(mod.lk, lock)
        finally:
            del sys.modules["_fake"]

    def test_restore_string_target_custom_name(self) -> None:
        mod = types.ModuleType("_fake2")
        mod.lk = threading.Lock()  # type: ignore[attr-defined]
        sys.modules["_fake2"] = mod
        try:
            wrapped = wrap_lock("_fake2.lk", name="custom")
            self.assertEqual(wrapped._name, "custom")
        finally:
            wrapped.restore()
            del sys.modules["_fake2"]

    def test_waiter_counter_emitted(self) -> None:
        n = 1.0

        def clock() -> float:
            return float(n)

        f = NonclosingStringIO()
        lock = threading.Lock()
        wrapped = wrap_lock(lock, name="mylock")

        with TraceOutput(file=f, pid=1, clock=clock, close_output_file=False):
            wrapped.acquire()
            n = 2.0
            wrapped.release()

        counter_events = [
            ev
            for ev in json.loads(f.getvalue())
            if ev.get("ph") == "C" and ev.get("name") == "mylock waiters"
        ]
        # Two events: one increment (waiters=1) and one decrement (waiters=0)
        self.assertEqual(2, len(counter_events))
        waiter_values = [ev["args"]["value"] for ev in counter_events]
        self.assertIn(1, waiter_values)
        self.assertIn(0, waiter_values)
        self.assertEqual(0, waiter_values[-1])

    def test_double_wrap_object_raises(self) -> None:
        wrapped = wrap_lock(threading.Lock())
        with self.assertRaises(AlreadyWrapped):
            wrap_lock(wrapped)  # type: ignore[arg-type]

    def test_double_wrap_string_raises(self) -> None:
        mod = types.ModuleType("_fake3")
        mod.lk = threading.Lock()  # type: ignore[attr-defined]
        sys.modules["_fake3"] = mod
        try:
            w = wrap_lock("_fake3.lk")
            with self.assertRaises(AlreadyWrapped):
                wrap_lock("_fake3.lk")
        finally:
            w.restore()
            del sys.modules["_fake3"]

    def test_wrap_lock_invalid_string_raises(self) -> None:
        with self.assertRaises(ValueError):
            wrap_lock("nodot")


def _fake_modules() -> Dict[str, Any]:
    """Return a fresh fake sys.modules dict with one module containing a lock."""
    mod = types.ModuleType("_fake_wrapall")
    mod.lk = threading.Lock()  # type: ignore[attr-defined]
    return {"_fake_wrapall": mod}


class WrapAllTest(unittest.TestCase):
    def tearDown(self) -> None:
        # Belt-and-suspenders: always restore and clear the registry.
        restore_all()

    def test_wrap_all_wraps_module_locks(self) -> None:
        mods = _fake_modules()
        registry = wrap_all(mods)
        self.assertIn("_fake_wrapall.lk", registry)
        self.assertIsInstance(mods["_fake_wrapall"].lk, WrappedLock)

    def test_wrap_all_returns_registry_copy(self) -> None:
        mods = _fake_modules()
        registry = wrap_all(mods)
        # Mutating the returned copy does not affect the internal registry.
        registry.clear()
        restore_all()  # should still restore without error (registry not empty internally)

    def test_wrap_all_uses_module_dot_attr_as_key(self) -> None:
        mods = _fake_modules()
        registry = wrap_all(mods)
        self.assertIn("_fake_wrapall.lk", registry)

    def test_wrap_all_names_lock_by_attr(self) -> None:
        mods = _fake_modules()
        wrap_all(mods)
        self.assertEqual(mods["_fake_wrapall"].lk._name, "lk")

    def test_restore_all_writes_back(self) -> None:
        mods = _fake_modules()
        original = mods["_fake_wrapall"].lk
        wrap_all(mods)
        restore_all()
        self.assertIs(mods["_fake_wrapall"].lk, original)

    def test_restore_all_clears_registry(self) -> None:
        from keke.wrap import _wrapped_registry

        mods = _fake_modules()
        wrap_all(mods)
        restore_all()
        self.assertEqual(_wrapped_registry, {})

    def test_wrap_all_idempotent(self) -> None:
        # Second call should skip already-wrapped locks.
        mods = _fake_modules()
        wrap_all(mods)
        wrap_all(mods)  # must not raise or double-wrap
        self.assertIsInstance(mods["_fake_wrapall"].lk, WrappedLock)

    def test_wrap_all_skips_manually_pre_wrapped(self) -> None:
        # Manual wrap before bulk wrap: bulk wrap should leave it alone.
        mods = _fake_modules()
        original = mods["_fake_wrapall"].lk
        manual = wrap_lock(original, name="manual")
        mods["_fake_wrapall"].lk = manual
        registry = wrap_all(mods)
        self.assertNotIn("_fake_wrapall.lk", registry)
        self.assertIs(mods["_fake_wrapall"].lk, manual)

    def test_manual_wrap_after_wrap_all_raises_already_wrapped(self) -> None:
        mods = _fake_modules()
        wrap_all(mods)
        wrapped = mods["_fake_wrapall"].lk
        with self.assertRaises(AlreadyWrapped):
            wrap_lock(wrapped)

    def test_restore_all_then_manual_wrap_works(self) -> None:
        mods = _fake_modules()
        wrap_all(mods)
        restore_all()
        # After restore, the lock is raw again — manual wrap must succeed.
        original = mods["_fake_wrapall"].lk
        w = wrap_lock(original, name="manual")
        self.assertIsInstance(w, WrappedLock)
        # Clean up.
        mods["_fake_wrapall"].lk = w.restore()
