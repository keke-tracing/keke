import io
import json
import multiprocessing
import os
import threading
import time
import unittest
from functools import partial
from typing import Any

from keke import (
    _consume_multiprocessing_thread,
    _setup_multiprocessing,
    kev,
    ktrace,
    TraceOutput,
)


class NonclosingStringIO(io.StringIO):
    def close(self) -> None:
        pass


class TraceOutputTest(unittest.TestCase):
    def test_basic(self) -> None:
        n = 123.0

        def clock() -> float:
            return float(n)

        f = NonclosingStringIO()

        with TraceOutput(file=f, pid=4, clock=clock):  # chosen by dice roll
            with kev("name_here", "cat_here", arg=1):
                n = 125.0
            with kev("name2_here", "cat_here"):
                n = 125.5

        events = [ev for ev in json.loads(f.getvalue()) if ev.get("cat") != "gc"]
        self.assertEqual(5, len(events))

        # 0 = thread_name
        self.assertEqual(4, events[0]["pid"])
        self.assertEqual("M", events[0]["ph"])
        self.assertEqual("thread_name", events[0]["name"])

        # 1 = thread_sort_index
        self.assertEqual(4, events[1]["pid"])
        self.assertEqual("M", events[1]["ph"])
        self.assertEqual("thread_sort_index", events[1]["name"])

        # 2 = X event
        self.assertEqual(4, events[2]["pid"])
        self.assertEqual("name_here", events[2]["name"])
        self.assertEqual("cat_here", events[2]["cat"])
        self.assertEqual("X", events[2]["ph"])
        self.assertEqual(123_000_000, events[2]["ts"])
        self.assertEqual(2_000_000, events[2]["dur"])

        # 3 = X event
        self.assertEqual(4, events[3]["pid"])
        self.assertEqual("name2_here", events[3]["name"])
        self.assertEqual("cat_here", events[3]["cat"])
        self.assertEqual("X", events[3]["ph"])
        self.assertEqual(125_000_000, events[3]["ts"])
        self.assertEqual(500_000, events[3]["dur"])

        # 4 = comma-on-a-line hack
        self.assertEqual({}, events[4])

    def test_ktrace(self) -> None:
        def func(a: Any, b: int = 1, c: int = 2) -> Any:
            return (a, b, c)

        with self.assertRaises(TypeError):
            ktrace(func)  # type: ignore

        tracer = ktrace()(func)
        self.assertEqual((0, 1, 2), tracer(0))
        self.assertEqual((0, 1, 2), tracer(a=0))
        self.assertEqual((0, 1, 5), tracer(a=0, c=5))

        tracer = ktrace("a[0]")(func)
        self.assertEqual((["foo", "bar"], 1, 2), tracer(["foo", "bar"]))

    def test_ktrace_capture(self) -> None:
        f = NonclosingStringIO()

        def func(a: Any, b: int = 1, c: int = 2) -> Any:
            return (a, b, c)

        with TraceOutput(file=f, pid=4, clock=lambda: 10):  # chosen by dice roll
            tracer = ktrace("a[0]")(func)
            self.assertEqual((["foo", "bar"], 1, 2), tracer(["foo", "bar"]))
            self.assertEqual(([], 1, 2), tracer([]))

            tracer = ktrace("a[0]", shortname=True)(func)
            self.assertEqual((["foo", "bar"], 1, 2), tracer(["foo", "bar"]))

            tracer = ktrace("a[0]", shortname="short")(func)
            self.assertEqual((["foo", "bar"], 1, 2), tracer(["foo", "bar"]))

        events = [ev for ev in json.loads(f.getvalue()) if ev.get("cat") != "gc"]

        # first call
        self.assertEqual(4, events[2]["pid"])
        self.assertEqual(
            "TraceOutputTest.test_ktrace_capture.<locals>.func", events[2]["name"]
        )
        self.assertEqual("dur", events[2]["cat"])
        self.assertEqual({"a[0]": "foo"}, events[2]["args"])

        # second call
        self.assertEqual(4, events[3]["pid"])
        self.assertEqual(
            "TraceOutputTest.test_ktrace_capture.<locals>.func", events[3]["name"]
        )
        self.assertEqual("dur", events[3]["cat"])
        self.assertEqual(
            {"a[0]": "IndexError('list index out of range')"}, events[3]["args"]
        )

        # third (short) call
        self.assertEqual(4, events[4]["pid"])
        self.assertEqual("func", events[4]["name"])
        self.assertEqual("dur", events[4]["cat"])
        self.assertEqual({"a[0]": "foo"}, events[4]["args"])

        # fourth (custom name) call
        self.assertEqual(4, events[5]["pid"])
        self.assertEqual("short", events[5]["name"])
        self.assertEqual("dur", events[5]["cat"])
        self.assertEqual({"a[0]": "foo"}, events[5]["args"])

    def test_bytes_raises_early(self) -> None:
        buf = io.BytesIO()
        with self.assertRaises(TypeError):
            TraceOutput(file=buf)  # type: ignore

    def test_no_close_output_file(self) -> None:
        buf = io.StringIO()
        with TraceOutput(file=buf, close_output_file=False):
            with kev("name_here", "cat_here", arg=1):
                pass
        json.loads(buf.getvalue())


@ktrace("x")
def _func_in_another_process(x) -> None:
    return x


class MultiprocessingTest(unittest.TestCase):
    def test_we_get_events_from_child(self):
        f = NonclosingStringIO()

        spawn_context = multiprocessing.get_context("spawn")
        q = spawn_context.Queue()
        my_thread = threading.Thread(
            target=partial(_consume_multiprocessing_thread, q), daemon=True
        )
        my_thread.start()

        with spawn_context.Pool(
            processes=1, initializer=partial(_setup_multiprocessing, q)
        ) as pool:
            # daemon because we don't have any way to stop once it's started yet...
            with TraceOutput(file=f, pid=4, clock=lambda: 10):  # chosen by dice roll
                result = pool.apply(_func_in_another_process, (2,))
                self.assertEqual(2, result)
                # Some sort of dummy thing to give the worker thread time to do
                # its marshalling...
                result = pool.apply(time.sleep, (0.1,))

        events = [ev for ev in json.loads(f.getvalue()) if ev.get("cat") != "gc"]
        for e in events:
            if e.get("name") == "_func_in_another_process":
                self.assertEqual(e["args"], {"x": "2"})
                break
        else:
            for e in events:
                print(e)
            self.fail("No call to _func_in_another_process found")
