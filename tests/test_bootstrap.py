"""bin/roles on an old Python: re-exec on a newer one, or explain how to get one (agent shells often get /usr/bin/python3)."""
import os
import subprocess
import sys
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "roles")


def _old_python():
    """A local Python < 3.11 to start bin/roles with (macOS ships 3.9 at /usr/bin/python3), else None."""
    for python in ("/usr/bin/python3", "python3.9", "python3.10"):
        try:
            out = subprocess.run([python, "-c", "import sys; print(sys.version_info < (3, 11))"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.stdout.strip() == "True":
            return python
    return None


OLD = _old_python()


def run(python, *args, **env):
    full = {k: v for k, v in os.environ.items() if not k.startswith("ROLES_PYTHON") and k != "ROLES_REEXEC"}
    full.update(env)
    return subprocess.run([python, BIN, *args], capture_output=True, text=True, env=full, timeout=60)


class BootstrapTests(unittest.TestCase):
    def test_new_python_runs_directly(self):
        r = run(sys.executable, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("team-up", r.stdout)


@unittest.skipUnless(OLD, "no Python < 3.11 on this machine")
class OldPythonTests(unittest.TestCase):
    def test_reexecs_on_a_newer_one(self):
        r = run(OLD, "--help", ROLES_PYTHON_CANDIDATES=sys.executable)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("team-up", r.stdout)

    def test_without_a_newer_one_explains_the_fix(self):
        r = run(OLD, "--help", ROLES_PYTHON_CANDIDATES="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Python 3.11+", r.stderr)
        self.assertIn("brew shellenv", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_candidate_that_is_also_old_is_skipped(self):
        r = run(OLD, "--help", ROLES_PYTHON_CANDIDATES=OLD)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Python 3.11+", r.stderr)

    def test_guard_stops_a_reexec_loop(self):
        r = run(OLD, "--help", ROLES_PYTHON_CANDIDATES=sys.executable, ROLES_REEXEC="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Python 3.11+", r.stderr)


if __name__ == "__main__":
    unittest.main()
