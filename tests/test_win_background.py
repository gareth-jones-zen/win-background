"""Run with: python -m unittest discover -s tests"""

import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import win_background as wb  # noqa: E402


def other_process(code: str) -> str:
    """Run code in a second Python, with this checkout's src first on the path."""
    src = str(pathlib.Path(__file__).resolve().parent.parent / "src")
    done = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {src!r})\n" + textwrap.dedent(code)],
                          capture_output=True, text=True)
    if done.returncode:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


class Locks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = pathlib.Path(self.temp.name)
        self.name = f"win-background-test-{wb.key(self.folder)}"

    def tearDown(self):
        self.temp.cleanup()

    def test_key_ignores_case_and_is_short(self):
        self.assertEqual(wb.key(r"C:\Users\Me\settings.toml"), wb.key(r"c:\users\me\SETTINGS.toml"))
        self.assertEqual(len(wb.key("x")), 16)

    def test_single_instance_refuses_a_second_holder(self):
        held = wb.single_instance(self.name, self.folder)
        self.assertIsNotNone(held)
        refused = other_process(f"""
            import pathlib, win_background as wb
            print(wb.single_instance({self.name!r}, pathlib.Path({str(self.folder)!r})) is None)""")
        self.assertEqual(refused, "True")
        held.release()
        again = wb.single_instance(self.name, self.folder)
        self.assertIsNotNone(again)
        again.release()

    def test_exclusive_times_out_while_held(self):
        with wb.exclusive(self.name, 1, self.folder):
            said = other_process(f"""
                import pathlib, win_background as wb
                try:
                    with wb.exclusive({self.name!r}, 0.2, pathlib.Path({str(self.folder)!r}), what="sync"):
                        print("got it")
                except TimeoutError as error:
                    print(error)""")
        self.assertIn("Another sync didn't finish", said)

    def test_a_lock_dies_with_its_process(self):
        other_process(f"""
            import os, pathlib, win_background as wb
            held = wb.single_instance({self.name!r}, pathlib.Path({str(self.folder)!r}))
            os._exit(0)""")
        lock = wb.single_instance(self.name, self.folder)
        self.assertIsNotNone(lock)
        lock.release()


class Logging(unittest.TestCase):
    def test_logger_writes_and_never_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = pathlib.Path(temp)
            log = wb.logger(folder / "sub" / "tool.log", stamp_echo=False)
            printed = StringIO()
            with redirect_stdout(printed):
                log("hello")
            self.assertEqual(printed.getvalue(), "hello\n")
            self.assertTrue((folder / "sub" / "tool.log").read_text(encoding="utf-8").rstrip().endswith("  hello"))
            wb.logger(folder, echo=False)("a folder isn't a file, and that's fine")   # OSError swallowed


class Autostart(unittest.TestCase):
    def test_python_command_runs_the_module(self):
        exe, args = wb.python_command("my_tool", "--config", "x.toml", "watch")
        self.assertEqual(args, ["-m", "my_tool", "--config", "x.toml", "watch"])
        self.assertTrue(pathlib.Path(exe).exists())

    def test_a_task_in_a_folder_is_selected_by_path_and_name(self):
        # 12 Nov 2026: the whole path as a bare -TaskName found nothing
        self.assertEqual(wb.Task(r"\Claude EA\Transcript watcher")._which,
                         "-TaskName 'Transcript watcher' -TaskPath '\\Claude EA\\'")
        self.assertEqual(wb.Task("mail-triage watch")._which, "-TaskName 'mail-triage watch'")

    def test_status_parsing(self):
        task = wb.Task("t", results={"3": "another watcher was already running"})
        self.assertIsNone(task.parse_status(""))
        self.assertEqual(task.parse_status("state=Ready\nlast_run=x\nlast_result=267011\n")["last_run"], "never")
        self.assertEqual(task.parse_status("state=Running\nlast_result=267009")["last_result"], "running")
        self.assertEqual(task.parse_status("last_result=3")["last_result"], "3 (another watcher was already running)")
        self.assertEqual(task.parse_status("last_result=0")["last_result"], "0")

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_enable_needs_a_trigger(self):
        with self.assertRaises(ValueError):
            wb.Task("t").enable("x.exe", [], at_logon=False)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_every_is_for_jobs_a_minute_or_more_apart(self):
        import datetime as dt
        with self.assertRaises(ValueError):
            wb.Task("t").enable("x.exe", [], every=dt.timedelta(minutes=5))   # keep_running defaults to True
        with self.assertRaises(ValueError):
            wb.Task("t").enable("x.exe", [], every=dt.timedelta(seconds=30), keep_running=False)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_status_of_a_task_that_does_not_exist(self):
        self.assertIsNone(wb.Task("win-background test, never created").status())


if __name__ == "__main__":
    unittest.main()
