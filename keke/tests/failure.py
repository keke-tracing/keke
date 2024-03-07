import io
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from keke import ktrace, TraceOutput
from keke.failure import (
    _get_ctime,
    _remove_oldest,
    save_trace_on_failure,
    TraceOnFailure,
)


@ktrace()
def failing_func() -> None:
    raise Exception("fail-ure")


@ktrace()
def succeeding_func() -> None:
    return


class TraceOnFailureTest(unittest.TestCase):
    def test_basic_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)

            with TraceOnFailure(path=pd):
                succeeding_func()

            self.assertEqual([], list(pd.iterdir()))

    def test_basic_fail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)

            with self.assertRaisesRegex(Exception, "fail-ure"):
                with TraceOnFailure(path=pd):
                    failing_func()

            the_one_file = list(pd.iterdir())[0]
            self.assertIn("failing_func", (pd / the_one_file).read_text())

    def test_always_trace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)

            with TraceOnFailure(path=pd, always_trace=True):
                succeeding_func()

            the_one_file = list(pd.iterdir())[0]
            self.assertIn("succeeding_func", (pd / the_one_file).read_text())

    def test_always_trace_but_already_tracing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)

            with TraceOutput(io.StringIO()):
                with TraceOnFailure(path=pd, always_trace=True):
                    succeeding_func()

            self.assertEqual([], list(pd.iterdir()))

    def test_remove_oldest_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)

            # Just doesn't raise an exception
            _remove_oldest(pd / "x")

    def test_remove_oldest_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)

            # Empty dir is ok
            _remove_oldest(pd, keep=0)

            # one file is deterministic
            (pd / "x").touch()
            _remove_oldest(pd, keep=0)
            self.assertFalse((pd / "x").exists())

            # dirs don't cause exception
            (pd / "x").touch()
            (pd / "y").mkdir()
            _remove_oldest(pd, keep=0)
            self.assertFalse((pd / "x").exists())
            self.assertTrue((pd / "y").exists())

    def test_get_ctime_fallback(self) -> None:
        with unittest.mock.patch("time.time", return_value=4):
            self.assertEqual(4, _get_ctime(Path(), "impossible"))

    def test_save_trace_on_failure_decorator(self) -> None:
        @save_trace_on_failure
        def func() -> None:
            failing_func()

        with tempfile.TemporaryDirectory() as td:
            pd = Path(td)
            with unittest.mock.patch("keke.failure.save_trace_on_failure.path", pd):
                with self.assertRaisesRegex(Exception, "fail-ure"):
                    func()

            the_one_file = list(pd.iterdir())[0]
            self.assertIn("failing_func", (pd / the_one_file).read_text())
