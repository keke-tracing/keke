"""
Remember to start these as daemon threads.
"""

import os
import sys
import time
from threading import Thread
from typing import Iterable

import keke

try:
    import psutil
except ImportError:
    pass

AVAILABLE_STATS = ("cpu", "fd")
DEFAULT_STATS = ("cpu", "fd")


def start(which: Iterable[str] = DEFAULT_STATS, delay: float = 0.5) -> None:
    for x in which:
        t = Thread(target=globals()[f"_{x}_stats_thread"], daemon=True, args=(delay,))
        t.start()


def _cpu_stats_thread(delay: float) -> None:
    prev_ts = None
    prev_process_time = None
    try:
        while True:
            ts = time.time()
            process_time = time.process_time()
            if prev_ts is not None:
                keke.kcount(
                    "proc_cpu_pct",
                    100 * (process_time - prev_process_time) / (ts - prev_ts),
                )

            prev_ts = ts
            prev_process_time = process_time
            time.sleep(delay)
    except StopIteration:
        pass  # for testing


def get_fd_count() -> int:
    if sys.platform == "win32":
        return psutil.Process().num_handles()
    elif sys.platform == "darwin":
        return len(os.listdir("/dev/fd"))
    elif sys.platform == "linux":
        return len(os.listdir("/proc/self/fd"))
    else:  # pragma: no cover
        return 0


def _fd_stats_thread(delay: float) -> None:
    try:
        while True:
            keke.kcount("num_fds", get_fd_count())
            time.sleep(delay)
    except StopIteration:
        pass  # for testing
