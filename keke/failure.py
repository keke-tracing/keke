"""
Contextmanager that buffer a trace to only save when there is an exception.
"""

import contextlib
import io
import logging
import os
import time
from functools import partial, wraps
from pathlib import Path
from types import TracebackType
from typing import Callable, Optional, Type, TypeVar

try:
    from typing import ParamSpec
except ImportError:  # <3.10 compat
    from typing import TypeVar as ParamSpec  # type: ignore[assignment]


from keke import get_tracer, TraceOutput

Param = ParamSpec("Param")
RetType = TypeVar("RetType")

# Will save files like /tmp/failure_traces/{pid}_{date}_0001
DEFAULT_TRACE_DIR = Path("/tmp/failure_traces")
SEQUENCE = 0
SEQUENCE_DIGITS = 4

LOG = logging.getLogger(__name__)
KEEP_MAX = 100


class TraceOnFailure:
    def __init__(
        self, always_trace: bool = False, path: Path = DEFAULT_TRACE_DIR
    ) -> None:
        self.always_trace = always_trace
        self.buf = io.StringIO()
        self.path = path
        self.exit_stack = contextlib.ExitStack()

    def __enter__(self) -> "TraceOnFailure":
        if get_tracer():
            LOG.warning("Already tracing, TraceOnFailure is a no-op")
        else:
            self.exit_stack.enter_context(
                TraceOutput(self.buf, close_output_file=False)
            )
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.exit_stack.close()

        reason: Optional[str] = None
        if exc_type:
            reason = exc_type.__name__
        elif self.always_trace:
            reason = "AlwaysTrace"

        if reason and self.buf.tell():
            try:
                _save_trace_internal(self.buf, reason=reason, path=self.path)
            except Exception:
                LOG.exception("While saving for %s", reason)


def save_trace_on_failure(
    func: "Callable[Param, RetType]",
) -> "Callable[Param, RetType]":
    """
    Decorator to store traces on disk.

    Set save_trace_on_failure.always_trace if you want them to be stored regardless of success or failure.
    """

    @wraps(func)
    def inner(*args: "Param.args", **kwargs: "Param.kwargs") -> RetType:
        with TraceOnFailure(
            save_trace_on_failure.always_trace,  # type: ignore[attr-defined]
            save_trace_on_failure.path,  # type: ignore[attr-defined]
        ):
            return func(*args, **kwargs)

    return inner


save_trace_on_failure.always_trace = False  # type: ignore[attr-defined]
save_trace_on_failure.path = DEFAULT_TRACE_DIR  # type: ignore[attr-defined]

# Internal
# ========


def _save_trace_internal(
    buf: io.StringIO, reason: str, path: Path = DEFAULT_TRACE_DIR
) -> None:
    _carefully_remove_oldest(path)
    output = _trace_path(path)
    LOG.warning("Saving trace to %s because %s", output, reason)
    with open(output, "w") as f:
        f.write(buf.getvalue())


def _trace_path(path: Path) -> Path:
    global SEQUENCE
    n = SEQUENCE
    SEQUENCE = (SEQUENCE + 1) % 10**SEQUENCE_DIGITS
    path.mkdir(parents=True, exist_ok=True)
    date = time.strftime("%Y-%m-%d_%H%M%S")
    seq = str(n).rjust(SEQUENCE_DIGITS, "0")
    pid = os.getpid()

    return path / f"{pid}_{date}_{seq}.trace"


def _get_ctime(path: Path, filename: str) -> float:
    try:
        return (path / filename).stat().st_ctime
    except OSError:
        return time.time()  # make deleted files less likely to be targets


def _carefully_remove_oldest(path: Path = DEFAULT_TRACE_DIR) -> None:
    try:
        _remove_oldest(path)
    except OSError:  # pragma: no cover
        # I actually can't come up with a test to trigger this.
        LOG.exception("_remove_oldest() failed")


def _remove_oldest(path: Path, keep: int = KEEP_MAX) -> None:
    # Remember: this might be called concurrently from multiple processes.
    # Deleting a file twice is something we need to handle, and worst-case this
    # shouldn't block the saving of traces anyway (that's why it's wrapped)

    # Assume everything in the dir is a trace file, and that there are also
    # no subdirs.
    try:
        entries = sorted(os.listdir(path), key=partial(_get_ctime, path))
    except FileNotFoundError:
        return

    while entries and len(entries) >= keep:
        entry = entries.pop(0)
        with contextlib.suppress(OSError):
            (path / entry).unlink()
