"""
keke -- easy chrome trace output

This is somewhat inspired by pytracing, which hasn't seen updates in a while,
but making it easier to log higher-level information about what threads are
doing.
"""

import gc
import json
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from inspect import signature
from queue import SimpleQueue
from typing import Any, Callable, Dict, Generator, IO, Optional, Set, Union

TRACER: "Optional[TraceOutput]" = None


def get_tracer() -> "Optional[TraceOutput]":
    t = TRACER
    if t is not None and t.enabled:
        return t
    else:
        return None


def to_microseconds(s: float) -> float:
    return s * 1_000_000


class TraceOutput:
    def __init__(
        self,
        file: IO[str],
        # These sort key substrings are neat and work in chrome://tracing but
        # notably do _not_ work in perfetto when using json input.
        # https://groups.google.com/g/perfetto-dev/c/zOe_Y2FxGGk
        thread_sortkeys: Dict[str, int] = {
            "MainThread": -1,
            "Opener": 1,
            "ThreadPoolExecutor": 2,
            "Closer": 3,
        },
        pid: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if file is not None:
            # Ensure we get an early, main-thread error if opened in binary mode or
            # not for writing.
            file.write("")  # if this raises, check your file mode

        self.output = file
        self.queue: "SimpleQueue[Optional[Dict[Any, Any]]]" = SimpleQueue()

        # There are two good reasons for overriding the pid value -- one is in
        # distributed systems, where the pid might get reused (or even reused
        # across machines; the other is for testing).  When in doubt, you can
        # always post-process to change the pid value.
        self.pid = pid or os.getpid()
        self.clock = clock or time.monotonic
        self.enabled = False

        self._thread_sortkeys = thread_sortkeys
        self._thread_name_output: Set[int] = set()

    def with_tid(
        self, obj: Dict[str, Any], id: Optional[int] = None, name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Modifies obj in place (but also returns it) to add thread info and emit
        name metadata if necessary.  The optional `id` and `name` params are
        intended for synthetic threads, like having GC appear as its own.
        """
        if id is None:
            id = threading.get_native_id()
        obj["tid"] = id

        if id not in self._thread_name_output:
            self._thread_name_output.add(id)
            n = 0  # TODO rethink?
            if name is None:
                name = threading.current_thread().name
            for k, v in self._thread_sortkeys.items():
                if k in name:
                    n = v
            self.queue.put(
                {
                    "pid": self.pid,
                    "tid": id,
                    "ts": 0,
                    "ph": "M",
                    "cat": "__metadata",
                    "name": "thread_name",
                    "args": {"name": name},
                }
            )
            self.queue.put(
                {
                    "pid": self.pid,
                    "tid": id,
                    "ts": 9,
                    "ph": "M",
                    "cat": "__metadata",
                    "name": "thread_sort_index",
                    "args": {"sort_index": n},
                }
            )
        return obj

    def __enter__(self) -> None:
        if self.output is None:
            return self
        self.output.write("[\n")
        self._writer = threading.Thread(target=self.writer)
        self._writer.start()
        self.enabled = True
        global TRACER
        TRACER = self
        gc.callbacks.append(self._gc_callback)

    def __exit__(self, *unused_args: Any) -> None:
        if self.output is None:
            return
        gc.callbacks.remove(self._gc_callback)
        self.enabled = False
        global TRACER
        TRACER = None
        self.queue.put(None)
        self._writer.join()
        self.output.write("{}]\n")
        self.output.close()  # prevent accidental reuse that produces invalid json

    def _gc_callback(self, phase: str, info: Dict[str, int]) -> None:
        # TODO We'd like to use begin/end async events, but those don't appear
        # to be recognized in Perfetto, rather unaopologeticly
        # https://github.com/google/perfetto/issues/60
        ts = to_microseconds(self.clock())
        if phase == "start":
            self._gc_start = ts
        else:
            # Ideally this would be recorded as an async event, but that doesn't
            # appear to work in Perfetto so we invent a fake thread.
            self.put(
                {
                    "cat": "gc",
                    "name": "collect",
                    "ph": "X",
                    "tid": 0,
                    "ts": self._gc_start,
                    "dur": ts - self._gc_start,
                    "args": info,
                },
                False,
            )

    def writer(self) -> None:
        while True:
            item = self.queue.get()
            if item is None:  # Cheap shutdown sentinel
                break
            # TODO no whitespace inside
            self.output.write(json.dumps(item, separators=(",", ":")) + ",\n")

    def put(self, obj: Dict[str, Any], with_tid: bool) -> None:
        if "pid" not in obj:
            obj["pid"] = self.pid
        if "ts" not in obj:
            obj["ts"] = to_microseconds(self.clock())
        if with_tid:
            obj = self.with_tid(obj)
        self.queue.put(obj)


def kcount(name: str, value: Optional[int] = None, **kwargs: int) -> None:
    args: Dict[str, int] = {}
    if value is not None:
        args = {"value": value}
    if kwargs:
        args.update(kwargs)

    assert args

    t = get_tracer()
    if t is not None:
        t.put({"name": name, "ph": "C", "args": args}, False)


# TODO this is not a real enum
class Scope:
    THREAD = "t"
    PROCESS = "p"
    GLOBAL = "g"


def kmark(name: str, cat: str = "mark", scope: str = Scope.THREAD) -> None:
    # TODO "stack" record
    t = get_tracer()
    if t is not None:
        t.put({"name": name, "cat": cat, "ph": "i", "s": scope}, scope == Scope.THREAD)


@contextmanager
def kev(name: str, cat: str = "dur", **kwargs: Any) -> Generator[None, None, None]:
    enabled = False
    t = get_tracer()
    if t is not None:
        enabled = True
        t0 = to_microseconds(t.clock())

    try:
        yield
    finally:
        if enabled:
            assert t is not None
            t1 = to_microseconds(t.clock())
            ev = {
                "name": name,
                "cat": cat,
                "ph": "X",
                "ts": t0,
                "dur": t1 - t0,
                "args": kwargs,
            }
            t.put(ev, True)


def ktrace(*trace_args: str, shortname: Union[str, bool] = False) -> Any:
    if trace_args and callable(trace_args[0]):
        raise TypeError(
            "This is a decorator that always takes args, to avoid confusion. Use empty parens."
        )

    def inner(func: Any) -> Callable[..., Any]:
        sig = signature(func)
        if isinstance(shortname, str):
            name = shortname
        elif shortname:
            name = func.__name__
        else:
            name = func.__qualname__

        @wraps(func)
        def dec(*args: Any, **kwargs: Any) -> Any:
            t = get_tracer()
            if t is None:
                return func(*args, **kwargs)

            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()

            def safe_get(x: str) -> str:
                try:
                    return str(eval(x, bound.arguments, bound.arguments))
                except Exception as e:
                    return repr(e)

            params = {k: safe_get(k) for k in trace_args}
            with kev(name, **params):
                return func(*args, **kwargs)

        return dec

    return inner


# Notably TRACER and TraceOutput are not here; most modules importing this don't
# need them.  They are still public though.
__all__ = ["kcount", "kmark", "kev", "ktrace"]
