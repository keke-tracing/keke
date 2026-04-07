"""
keke.wrap -- wrappers that annotate synchronization primitives with trace spans.
"""

from __future__ import annotations

import _thread
import importlib
import sys
import threading
from typing import Any, Dict, Optional, Union

from . import EVENT, get_tracer, kcount, to_microseconds

_LOCK_TYPE = type(_thread.allocate_lock())
_wrapped_registry: Dict[str, "WrappedLock"] = {}


class AlreadyWrapped(TypeError):
    """Raised when wrap_lock is called on an already-wrapped lock."""


class WrappedLock:
    """A threading.Lock wrapper that emits 'wait' and 'hold' trace spans.

    'wait' spans cover the time from acquire() call until the lock is obtained.
    'hold' spans cover the time from acquisition until release().
    Periods outside both spans are idle (no annotation).
    """

    def __init__(
        self,
        lock: threading.Lock,
        name: str = "lock",
        _patch_target: Optional[Any] = None,
        _patch_attr: Optional[str] = None,
    ) -> None:
        self._lock = lock
        self._name = name
        self._patch_target = _patch_target
        self._patch_attr = _patch_attr
        self._hold_start: threading.local = threading.local()
        self._waiters: int = 0
        self._waiters_lock: threading.Lock = threading.Lock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        t = get_tracer()
        if t is None:
            return self._lock.acquire(blocking, timeout)

        with self._waiters_lock:
            self._waiters += 1
            kcount(f"{self._name} waiters", self._waiters)

        t0 = to_microseconds(t.clock())
        result = self._lock.acquire(blocking, timeout)
        t1 = to_microseconds(t.clock())

        with self._waiters_lock:
            self._waiters -= 1
            kcount(f"{self._name} waiters", self._waiters)

        t.put(
            EVENT(
                {"name": self._name, "cat": "wait", "ph": "X", "ts": t0, "dur": t1 - t0}
            ),
            True,
        )

        if result:
            self._hold_start.ts = t1

        return result

    def release(self) -> None:
        t = get_tracer()
        hold_ts: Optional[float] = getattr(self._hold_start, "ts", None)
        if t is not None and hold_ts is not None:
            t1 = to_microseconds(t.clock())
            self._hold_start.ts = None
            self._lock.release()
            t.put(
                EVENT(
                    {
                        "name": self._name,
                        "cat": "hold",
                        "ph": "X",
                        "ts": hold_ts,
                        "dur": t1 - hold_ts,
                    }
                ),
                True,
            )
        else:
            self._lock.release()

    def restore(self) -> threading.Lock:
        """Return the original unwrapped lock.

        If this wrapper was created via ``wrap_lock("module.attr")``, the
        original lock is also written back to that attribute automatically --
        no further action required by the caller.

        If this wrapper was created by passing a lock instance directly, no
        write-back occurs; the caller must reassign the return value wherever
        the wrapped lock was installed.
        """
        if self._patch_target is not None and self._patch_attr is not None:
            setattr(self._patch_target, self._patch_attr, self._lock)
        return self._lock

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> "WrappedLock":
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


def wrap_lock(
    target: Union[threading.Lock, str], name: Optional[str] = None
) -> WrappedLock:
    """Wrap a threading.Lock to emit 'wait' and 'hold' trace spans.

    *target* can be a lock instance or a dotted string naming the attribute to
    patch (the same style as ``mock.patch``).  The two forms differ in how much
    bookkeeping ``wrap_lock`` handles for you:

    **String target** -- wrap_lock does the install *and* the restore::

        from keke import wrap_lock

        _w = wrap_lock("concurrent.futures._shutdown_lock")
        # wrap_lock imported concurrent.futures, fetched the lock, and
        # replaced the attribute with the wrapper automatically.
        # name defaults to the attribute name ("_shutdown_lock").
        ...
        _w.restore()
        # restore() writes the original lock back to
        # concurrent.futures._shutdown_lock; no manual reassignment needed.

    **Object target** -- the caller owns both the install and the restore::

        import concurrent.futures
        from keke import wrap_lock

        concurrent.futures._shutdown_lock = _w = wrap_lock(
            concurrent.futures._shutdown_lock, name="shutdown_lock"
        )
        ...
        concurrent.futures._shutdown_lock = _w.restore()
        # restore() returns the original lock but does NOT write it back;
        # the caller must reassign it explicitly.
    """
    if isinstance(target, str):
        module_path, _, attr = target.rpartition(".")
        if not module_path:
            raise ValueError(f"wrap_lock target must be a dotted path, got {target!r}")
        module = importlib.import_module(module_path)
        lock = getattr(module, attr)
        if isinstance(lock, WrappedLock):
            raise AlreadyWrapped(f"{target!r} is already wrapped")
        wrapped = WrappedLock(lock, name if name is not None else attr, module, attr)
        setattr(module, attr, wrapped)
        return wrapped
    else:
        if isinstance(target, WrappedLock):
            raise AlreadyWrapped("lock is already wrapped")
        return WrappedLock(target, name if name is not None else "lock")


def wrap_all(
    modules: Optional[Dict[str, Any]] = None,
) -> Dict[str, WrappedLock]:
    """Wrap every module-level ``threading.Lock`` found in *modules*.

    *modules* defaults to ``sys.modules``.  Any ``{name: module}`` mapping
    can be supplied instead, which is useful for tests and for limiting the
    scan to a specific set of modules.

    Each discovered raw lock is replaced with a :class:`WrappedLock` named
    ``"module_name.attr"``.  Locks that are already wrapped are silently
    skipped, so ``wrap_all`` plays nicely with manual :func:`wrap_lock`
    calls made **before** the bulk wrap.  Calling ``wrap_all`` a second time
    is a no-op for the same reason.

    Returns a copy of the internal registry mapping ``"module.attr"`` to the
    :class:`WrappedLock` that replaced it.  Call :func:`restore_all` to undo.

    Limitations: only module-level attributes are reachable.  Locks held in
    instance attributes, local variables, or C-extension internals are not
    affected.
    """
    targets = list((modules if modules is not None else sys.modules).items())
    for mod_name, mod in targets:
        if mod is None or not hasattr(mod, "__dict__"):
            continue
        for attr, val in list(vars(mod).items()):
            if type(val) is _LOCK_TYPE:
                key = f"{mod_name}.{attr}"
                wrapped = WrappedLock(val, attr, mod, attr)
                setattr(mod, attr, wrapped)
                _wrapped_registry[key] = wrapped
    return dict(_wrapped_registry)


def restore_all() -> None:
    """Restore every lock wrapped by :func:`wrap_all`.

    Each :class:`WrappedLock` in the internal registry has its original lock
    written back via :meth:`WrappedLock.restore`.  The registry is cleared
    afterwards, so a subsequent :func:`wrap_all` will perform a fresh scan.
    """
    for wrapped in _wrapped_registry.values():
        wrapped.restore()
    _wrapped_registry.clear()
