"""
keke -- easy chrome trace output

This is somewhat inspired by pytracing, which hasn't seen updates in a while,
but making it easier to log higher-level information about what threads are
doing.
"""

from __future__ import annotations

try:
    from ._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "dev"

import gc
import json
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from inspect import isasyncgenfunction, iscoroutine, isgeneratorfunction, signature
from queue import SimpleQueue
from typing import (
    Any,
    Callable,
    cast,
    Dict,
    Generator,
    IO,
    NewType,
    Optional,
    Set,
    TypeVar,
    Union,
)

TRACER: "Optional[TraceOutput]" = None
F = TypeVar("F", bound=Callable[..., Any])
EVENT = NewType("EVENT", Dict[str, Any])


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
        file: Optional[IO[str]],
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
        close_output_file: Optional[bool] = True,
    ) -> None:
        if file is not None:
            # Ensure we get an early, main-thread error if opened in binary mode or
            # not for writing.
            file.write("")  # if this raises, check your file mode

        self.output = file
        self.close_output_file = close_output_file
        self.queue: "SimpleQueue[Optional[EVENT]]" = SimpleQueue()

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
        self, obj: EVENT, id: Optional[int] = None, name: Optional[str] = None
    ) -> EVENT:
        """
        Modifies obj in place (but also returns it) to add thread info and emit
        name metadata if necessary.  The optional `id` and `name` params are
        intended for synthetic threads, like having GC appear as its own.
        """
        if id is None:
            if hasattr(threading, "get_native_id"):
                id = threading.get_native_id()
            else:
                id = threading.get_ident()
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
                EVENT(
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
            )
            self.queue.put(
                EVENT(
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
            )
        return obj

    def __enter__(self) -> None:
        if self.output is None:
            return
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
        if self.close_output_file:
            # prevents accidental reuse that produces invalid json, but you can
            # disable if it's e.g. a StringIO that you want to read back
            self.output.close()

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
                cast(
                    EVENT,
                    {
                        "cat": "gc",
                        "name": "collect",
                        "ph": "X",
                        "tid": 0,
                        "ts": self._gc_start,
                        "dur": ts - self._gc_start,
                        "args": info,
                    },
                ),
                False,
            )

    def writer(self) -> None:
        # This thread should never get started unless output is a file
        assert self.output is not None

        while True:
            item = self.queue.get()
            if item is None:  # Cheap shutdown sentinel
                break
            # TODO no whitespace inside
            self.output.write(json.dumps(item, separators=(",", ":")) + ",\n")

    def put(self, obj: EVENT, with_tid: bool) -> None:
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
        t.put(EVENT({"name": name, "ph": "C", "args": args}), False)


# TODO this is not a real enum
class Scope:
    THREAD = "t"
    PROCESS = "p"
    GLOBAL = "g"


def kmark(name: str, cat: str = "mark", scope: str = Scope.THREAD) -> None:
    # TODO "stack" record
    t = get_tracer()
    if t is not None:
        t.put(
            EVENT({"name": name, "cat": cat, "ph": "i", "s": scope}),
            scope == Scope.THREAD,
        )


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
            ev = EVENT(
                {
                    "name": name,
                    "cat": cat,
                    "ph": "X",
                    "ts": t0,
                    "dur": t1 - t0,
                    "args": {k: str(v) for k, v in kwargs.items()},
                }
            )
            t.put(ev, True)


def ktrace(*trace_args: str, shortname: Union[str, bool] = False) -> Callable[[F], F]:
    if trace_args and callable(trace_args[0]):
        raise TypeError(
            "This is a decorator that always takes args, to avoid confusion. Use empty parens."
        )

    def inner(func: F) -> F:
        sig = signature(func)
        if isinstance(shortname, str):
            name = shortname
        elif shortname:
            name = func.__name__
        else:
            name = func.__qualname__

        def _get_params(*args: Any, **kwargs: Any) -> Dict[str, str]:

            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()

            def safe_get(x: str) -> str:
                try:
                    return str(eval(x, bound.arguments, bound.arguments))
                except Exception as e:
                    return repr(e)

            return {k: safe_get(k) for k in trace_args}

        if iscoroutine(func):

            @wraps(func)
            async def dec(*args: Any, **kwargs: Any) -> Any:
                with kev(name, **_get_params(*args, **kwargs)):
                    await func(*args, **kwargs)

        elif isasyncgenfunction(func):

            @wraps(func)
            async def dec(*args: Any, **kwargs: Any) -> Any:
                with kev(name, **_get_params(*args, **kwargs)):
                    async for item in func(*args, **kwargs):
                        yield item

        elif isgeneratorfunction(func):

            @wraps(func)
            def dec(*args: Any, **kwargs: Any) -> Any:
                with kev(name, **_get_params(*args, **kwargs)):
                    yield from func(*args, **kwargs)

        else:

            @wraps(func)
            def dec(*args: Any, **kwargs: Any) -> Any:
                with kev(name, **_get_params(*args, **kwargs)):
                    return func(*args, **kwargs)

        return cast(F, dec)  # type: ignore[has-type]

    return inner


# Notably TRACER and TraceOutput are not here; most modules importing this don't
# need them.  They are still public though.
__all__ = ["kcount", "kmark", "kev", "ktrace"]
