"""The check of a working directory: uncommitted and untracked files against a base, with keys on the command line."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import wingbox_changes as changes  # noqa: E402
import wingbox_pr as pr  # noqa: E402
from wingbox_changes import Hunk  # noqa: E402


class KeysTest(unittest.TestCase):
    def test_the_keys_name_the_folder_the_base_and_the_output(self) -> None:
        """/* REQ-ACTION-080: `wingbox_pr.py --root DIR --base REF [--head REF] [--out DIR]` takes the repository,
        the base and the output folder from the command line, before the environment variables and the current
        directory. With `--base` and no head anywhere the head is the working directory as it is on disk. Without
        any key the script reads its environment as before, and the head is then a commit. */"""
        self.assertEqual(("/repo", "main", pr.WORKING_TREE, "/out"), pr.settings(["--root", "/repo", "--base", "main", "--out", "/out"], {}, {}))
        self.assertEqual(("/repo", "main", "abc", "/out"), pr.settings(["--root", "/repo", "--base", "main", "--head", "abc", "--out", "/out"], {}, {}))
        env = {"WINGBOX_ROOT": "/env", "WINGBOX_BASE": "b", "WINGBOX_HEAD": "h", "RUNNER_TEMP": "/tmp/r"}
        self.assertEqual(("/env", "b", "h", "/tmp/r"), pr.settings([], env, {}))
        self.assertEqual(("/key", "k", "h", "/tmp/r"), pr.settings(["--root", "/key", "--base", "k"], env, {}))
        pull = {"base": {"sha": "pb"}, "head": {"sha": "ph"}}
        self.assertEqual((os.getcwd(), "pb", "ph"), pr.settings([], {}, pull)[:3])
        self.assertEqual("HEAD", pr.settings([], {"WINGBOX_BASE": "b"}, {})[2])

    def test_a_new_file_is_one_hunk_of_added_lines(self) -> None:
        """/* REQ-ACTION-081: an untracked file of the working directory is a change of all its lines: one hunk that
        starts at line 1 and adds every line; an empty file has no hunk. */"""
        self.assertEqual((Hunk(1, ("def a():", "    return 1"), ()),), changes.added_file("def a():\n    return 1\n"))
        self.assertEqual((), changes.added_file(""))


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class WorkingTreeTest(unittest.TestCase):
    def test_uncommitted_and_untracked_files_are_checked_without_a_commit(self) -> None:
        """/* REQ-ACTION-082: run from another directory with `--root`, `--base` and `--out` on a repository whose
        working directory holds an uncommitted edit and an untracked source file, the script reports the edit
        (the changed function and who calls it) and the new file, writes the report and the plan into the output
        folder, makes no commit, leaves the index and the working files as they were and no worktree behind. */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, out = Path(tmp) / "repo", Path(tmp) / "out"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, make_fixture.BASE, "base")
            for name, text in make_fixture.HEAD.items():
                if name.endswith(".py"):
                    (repo / name).write_text(text, encoding="utf-8", newline="\n")
            (repo / "app" / "services" / "refunds.py").write_text("from app.services.billing import bill\n\n\ndef refund_all(orders):\n    return [bill(o) for o in orders]\n",
                                                                   encoding="utf-8", newline="\n")
            before = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_")) or key == "WINGBOX_JOBS"}
            env["WINGBOX_VERSION"] = "test"
            done = subprocess.run([sys.executable, str(ROOT / "wingbox_pr.py"), "--root", str(repo), "--base", base, "--out", str(out)],
                                  cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            report = (out / "wingbox-report.md").read_text(encoding="utf-8")
            plan = (out / "wingbox-plan.sexp").read_text(encoding="utf-8").splitlines()
            self.assertIn("`bill` (function, `app/services/billing.py:4`) changed.", report)
            self.assertIn("- `app/api/admin` calls it at `app/api/admin.py:5` and in 1 more place\n", report)
            self.assertTrue(plan[0].startswith(f"(review-plan :schema 1 :result UNKNOWN :execution ok :base {base} :head working-tree "), plan[0])
            self.assertTrue(any(line.startswith('(changed-file "app/services/refunds.py" :sha256 ') for line in plan), plan)
            self.assertIn("head `working`", report)
            after = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
            self.assertEqual(before, after)
            self.assertEqual(base, subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip())
            worktrees = subprocess.run(["git", "-C", str(repo), "worktree", "list"], check=True, capture_output=True, text=True).stdout
            self.assertEqual(1, len(worktrees.strip().splitlines()))


if __name__ == "__main__":
    unittest.main()
