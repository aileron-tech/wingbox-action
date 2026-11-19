"""The report names the commit whose files were analysed, and its file hashes are of that commit."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import wingbox_pr as pr  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

A, B, M = "a" * 40, "b" * 40, "c" * 40


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
                          check=True, capture_output=True, text=True)
    return done.stdout.strip()


class RelationTest(unittest.TestCase):
    def test_the_commit_of_the_working_folder_is_the_head_its_merge_or_another_commit(self) -> None:
        """/* REQ-ACTION-090: the commit of the working folder relates to the head named as `head` (the same commit), `merge` (a merge commit with the head among its parents, what actions/checkout gives on a pull request) or `other` */"""
        self.assertEqual("head", pr.relation(B, B, (A,)))
        self.assertEqual("merge", pr.relation(M, B, (A, B)))
        self.assertEqual("other", pr.relation(M, B, (A,)))
        self.assertEqual("other", pr.relation(M, B, (A, "d" * 40)))


class ResultTest(unittest.TestCase):
    def test_another_tree_than_the_one_named_is_never_a_pass(self) -> None:
        """/* REQ-ACTION-091: a working folder at another commit than the head named, or with files that differ from its commit, makes the result UNKNOWN with the reason (also where no source module changed), and fail-on strict fails; the head itself and its merge into the base change nothing */"""
        clean = dict(introduced=(), not_checked=())
        for tree in (pr.Tree(B, "head"), pr.Tree(M, "merge"), None):
            self.assertEqual(("PASS", "ok", ()), pr.result(report(tree=tree, **clean)))
        other = report(tree=pr.Tree(M, "other"), **clean)
        self.assertEqual(("UNKNOWN", "ok", (f"tree-not-at-head: checked {M[:12]}, named {B[:12]}",)), pr.result(other))
        self.assertEqual(1, pr.exit_status(other, "strict"))
        differs = report(tree=pr.Tree(B, "head", ("app/a.py", "new.py")), **clean)
        self.assertEqual(("UNKNOWN", "ok", ("tree-differs-from-commit: 2 file(s)",)), pr.result(differs))
        nothing = report(changed=(pr.ChangedFile("README.md", None),), tree=pr.Tree(M, "other"), **clean)
        self.assertEqual("UNKNOWN", pr.result(nothing)[0])
        self.assertTrue(pr.needs_comment(nothing))

    def test_the_report_and_the_plan_name_the_commit_that_was_checked(self) -> None:
        """/* REQ-ACTION-092: the plan's head line says `:checked <commit> :tree <relation>`, each changed-file line names the commit its SHA-256 is of, a file that differs from the commit is a `(tree-differs "<path>")` line; the footer of the comment names the merge commit when that was checked, and a sentence says so when the tree is another commit or differs from its commit */"""
        hashes = (("src/models/order.ts", "f" * 64),)
        merged = report(tree=pr.Tree(M, "merge"), hashes=hashes)
        plan = pr.build_plan(merged).splitlines()
        self.assertTrue(plan[0].endswith(f" :checked {M} :tree merge)"), plan[0])
        self.assertIn(f'(changed-file "src/models/order.ts" :sha256 {"f" * 64} :commit {M})', plan)
        self.assertIn(f"head `{B[:7]}`, checked as merged into the base (commit `{M[:7]}`).", pr.build_comment(merged))
        at_head = pr.build_comment(report(tree=pr.Tree(B, "head")))
        self.assertIn(f"base `{A[:7]}`, head `{B[:7]}`. Built", at_head)
        other = pr.build_comment(report(tree=pr.Tree(M, "other")))
        self.assertIn(f"The files checked are commit `{M[:7]}`, not the head named (`{B[:7]}`)", other)
        self.assertIn("UNKNOWN, not a pass", other)
        differs = report(tree=pr.Tree(B, "head", ("app/a.py", "new.py")))
        self.assertIn("2 files of the working folder are not as in commit", pr.build_comment(differs))
        self.assertIn('(tree-differs "app/a.py")', pr.build_plan(differs))
        working = pr.build_plan(report(head=pr.WORKING_TREE, tree=pr.Tree("", pr.WORKING_TREE), hashes=hashes)).splitlines()
        self.assertTrue(working[0].endswith(" :tree working-tree)"), working[0])
        self.assertIn(f'(changed-file "src/models/order.ts" :sha256 {"f" * 64})', working)


class GitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        self.base = make_fixture.commit(self.repo, make_fixture.BASE, "base")
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.head = make_fixture.commit(self.repo, make_fixture.HEAD, "head")

    def test_the_working_folder_is_compared_with_the_head_named(self) -> None:
        """/* REQ-ACTION-093: read from git: a checkout of the head is `head` and clean; a merge of the head into a base that moved on is `merge`; another commit is `other`; a changed tracked file and an untracked source file are named as differing, while `.wingbox/`, the output folder, ignored files and an untracked file that is not source (a test report) are not */"""
        self.assertEqual(pr.Tree(self.head, "head"), pr.tree_state(str(self.repo), self.head, ()))
        git(self.repo, "checkout", "-q", "main")
        moved = make_fixture.commit(self.repo, {"app/api/extra.py": "X = 1\n"}, "base moves on")
        self.assertEqual(pr.Tree(moved, "other"), pr.tree_state(str(self.repo), self.head, ()))
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", self.head)
        merge = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(pr.Tree(merge, "merge"), pr.tree_state(str(self.repo), self.head, ()))
        (self.repo / "app" / "api" / "routes.py").write_text("changed = True\n", encoding="utf-8")
        (self.repo / "generated.py").write_text("G = 1\n", encoding="utf-8")
        (self.repo / "junit.xml").write_text("<testsuite/>\n", encoding="utf-8")
        for folder in (".wingbox", "ignored", "wingbox-out"):
            (self.repo / folder).mkdir()
            (self.repo / folder / "file.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(pr.Tree(merge, "merge", ("app/api/routes.py", "generated.py")),
                         pr.tree_state(str(self.repo), self.head, ("wingbox-out",)))

    def test_the_hashes_are_of_the_content_of_the_commit(self) -> None:
        """/* REQ-ACTION-094: the SHA-256 of a changed file is that of its content in the commit checked (`git show <commit>:<path>`), whatever the file on disk holds; a file the commit does not have is left out */"""
        (self.repo / "app" / "models" / "order.py").write_text("tampered = True\n", encoding="utf-8")
        found = pr.commit_hashes(str(self.repo), self.head, ["app/models/order.py", "docs/notes.md", "gone.py"])
        self.assertEqual([("app/models/order.py", hashlib.sha256(make_fixture.HEAD["app/models/order.py"].encode()).hexdigest()),
                          ("docs/notes.md", hashlib.sha256(make_fixture.HEAD["docs/notes.md"].encode()).hexdigest())], found)


if __name__ == "__main__":
    unittest.main()
