import importlib
import os
import unittest
from unittest.mock import patch


os.environ["DISABLE_BACKGROUND_THREADS"] = "1"

website_app = importlib.import_module("website_app")


class StartupPromptTestCase(unittest.TestCase):
    def test_run_startup_questions_uses_defaults_when_non_interactive(self):
        with patch("website_app.sys.stdin.isatty", return_value=False), patch(
            "website_app.sys.stdout.isatty",
            return_value=False,
        ):
            options = website_app.run_startup_questions()

        self.assertEqual(options["console_level"], "INFO")
        self.assertTrue(options["show_request_logs"])
        self.assertTrue(options["warm_cache"])
        self.assertEqual(options["host"], "0.0.0.0")
        self.assertEqual(options["port"], 5000)
        self.assertTrue(options["show_startup_summary"])

    def test_run_startup_questions_reads_interactive_answers(self):
        answers = iter(["debug", "n", "y", "y", "5050", "n"])
        with patch("website_app.sys.stdin.isatty", return_value=True), patch(
            "website_app.sys.stdout.isatty",
            return_value=True,
        ), patch("builtins.input", side_effect=lambda _prompt: next(answers)):
            options = website_app.run_startup_questions()

        self.assertEqual(options["console_level"], "DEBUG")
        self.assertFalse(options["show_request_logs"])
        self.assertTrue(options["warm_cache"])
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 5050)
        self.assertFalse(options["show_startup_summary"])

    def test_prompt_port_retries_until_valid(self):
        answers = iter(["abc", "70000", "5055"])
        with patch("builtins.input", side_effect=lambda _prompt: next(answers)):
            port = website_app._prompt_port(5000)

        self.assertEqual(port, 5055)

    def test_prompt_yes_no_uses_default_on_blank(self):
        with patch("builtins.input", return_value=""):
            self.assertTrue(website_app._prompt_yes_no("Show logs", True))

    def test_prompt_choice_retries_until_valid(self):
        answers = iter(["loud", "quiet"])
        with patch("builtins.input", side_effect=lambda _prompt: next(answers)):
            choice = website_app._prompt_choice(
                "Choose level",
                "normal",
                {"quiet": "WARNING", "normal": "INFO", "debug": "DEBUG"},
            )

        self.assertEqual(choice, "quiet")


if __name__ == "__main__":
    unittest.main()
