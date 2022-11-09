import io
import json
import unittest

from keke import kev, TraceOutput


class TraceOutputTest(unittest.TestCase):
    def test_basic(self) -> None:
        n = 123.0

        def clock() -> float:
            return float(n)

        class NonclosingStringIO(io.StringIO):
            def close(self) -> None:
                pass

        f = NonclosingStringIO()

        with TraceOutput(file=f, pid=4, clock=clock):  # chosen by dice roll
            with kev("name_here", "cat_here", arg=1):
                n = 125.0

        events = json.loads(f.getvalue())

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
