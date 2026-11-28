from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from server import ServerSettings, __main__ as launcher


class ServerLauncherTests(unittest.TestCase):
    def _run_launcher(self, *arguments: str) -> tuple[MagicMock, MagicMock]:
        timer = MagicMock()
        with (
            patch.object(sys, "argv", ["quorune-server", *arguments]),
            patch.object(launcher, "_prepare_browser"),
            patch.object(launcher.uvicorn, "run"),
            patch.object(launcher.threading, "Timer", return_value=timer) as timer_factory,
        ):
            launcher.main()
        return timer, timer_factory

    def test_default_startup_does_not_open_browser(self) -> None:
        timer, timer_factory = self._run_launcher("--no-build-browser", "--offline")

        timer_factory.assert_not_called()
        timer.start.assert_not_called()

    def test_engine_managed_empty_priority_requires_an_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self._run_launcher("--no-build-browser", "--offline")
            self.assertFalse(ServerSettings.from_environment().auto_pass_empty_priority)
            self._run_launcher("--no-build-browser", "--offline", "--auto-pass-empty-priority")
            self.assertTrue(ServerSettings.from_environment().auto_pass_empty_priority)

    def test_open_flag_explicitly_schedules_browser(self) -> None:
        timer, timer_factory = self._run_launcher(
            "--no-build-browser",
            "--offline",
            "--open",
        )

        timer_factory.assert_called_once()
        timer.start.assert_called_once_with()

    def test_legacy_no_open_flag_remains_non_opening(self) -> None:
        _, timer_factory = self._run_launcher(
            "--no-build-browser",
            "--offline",
            "--no-open",
        )

        timer_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
