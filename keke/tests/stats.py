import time
import unittest
from unittest.mock import call, patch

from keke.stats import get_fd_count, start


class StatsTest(unittest.TestCase):
    def test_cpu(self) -> None:
        real_sleep = time.sleep
        with patch("keke.stats.time.sleep"), patch(
            "keke.stats.time.process_time", side_effect=[1, 2, 2]
        ), patch("keke.stats.time.time", side_effect=[0, 0.5, 1.0]), patch(
            "keke.stats.keke.kcount"
        ) as kc:

            start(["cpu"])

            while len(kc.mock_calls) < 2:
                real_sleep(0.01)

            kc.assert_has_calls(
                [
                    call("proc_cpu_pct", 200),
                    call("proc_cpu_pct", 0),
                ],
            )

    def test_get_fd(self) -> None:
        self.assertGreaterEqual(get_fd_count(), 3)

    def test_fd(self) -> None:
        real_sleep = time.sleep
        with patch("keke.stats.time.sleep"), patch(
            "keke.stats.get_fd_count", side_effect=[3, 4, 5]
        ), patch("keke.stats.keke.kcount") as kc:

            start(["fd"])

            while len(kc.mock_calls) < 3:
                real_sleep(0.01)

            kc.assert_has_calls(
                [
                    call("num_fds", 3),
                    call("num_fds", 4),
                    call("num_fds", 5),
                ],
            )
