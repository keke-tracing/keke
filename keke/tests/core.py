import io
import json
import time
import unittest
from typing import Any, Generator

from keke import kev, ktrace, TraceOutput


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

        events = json.loads(f.getvalue())
        events = [
            e
            for e in events
            if e.get("name") not in ("collect", "thread_name", "thread_sort_index")
        ]

        # first call
        self.assertEqual(4, events[0]["pid"])
        self.assertEqual(
            "TraceOutputTest.test_ktrace_capture.<locals>.func", events[0]["name"]
        )
        self.assertEqual("dur", events[0]["cat"])
        self.assertEqual({"a[0]": "foo"}, events[0]["args"])

        # second call
        self.assertEqual(4, events[1]["pid"])
        self.assertEqual(
            "TraceOutputTest.test_ktrace_capture.<locals>.func", events[1]["name"]
        )
        self.assertEqual("dur", events[1]["cat"])
        self.assertEqual(
            {"a[0]": "IndexError('list index out of range')"}, events[1]["args"]
        )

        # third (short) call
        self.assertEqual(4, events[2]["pid"])
        self.assertEqual("func", events[2]["name"])
        self.assertEqual("dur", events[2]["cat"])
        self.assertEqual({"a[0]": "foo"}, events[2]["args"])

        # fourth (custom name) call
        self.assertEqual(4, events[3]["pid"])
        self.assertEqual("short", events[3]["name"])
        self.assertEqual("dur", events[3]["cat"])
        self.assertEqual({"a[0]": "foo"}, events[3]["args"])

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

    def test_ktrace_generator(self) -> None:
        @ktrace()
        def _uniquely_named_generator() -> Generator[int, None, None]:
            yield 1
            yield 2

        f = NonclosingStringIO()

        with TraceOutput(file=f, pid=4, clock=lambda: 10):  # chosen by dice roll
            g = _uniquely_named_generator()
            next(g)
            next(g)
            time.sleep(0.1)  # :(
            assert "_uniquely_named_generator" not in f.getvalue()

            with self.assertRaises(StopIteration):
                next(g)
            time.sleep(0.1)  # :(
            assert "_uniquely_named_generator" in f.getvalue()

        events = [ev for ev in json.loads(f.getvalue()) if ev.get("cat") != "gc"]
        for e in events:
            if (
                e.get("name")
                == "TraceOutputTest.test_ktrace_generator.<locals>._uniquely_named_generator"
            ):
                break
        else:
            for e in events:
                print(e)
            self.fail()
