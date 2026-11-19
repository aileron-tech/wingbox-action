"""The policy of the base on real repositories: the files the judging build sees, and the whole script."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import layers_fixture as layers  # noqa: E402
import make_fixture  # noqa: E402
import wingbox_pr as pr  # noqa: E402

git = layers.git


class Repository(unittest.TestCase):
    """A test with the layered project in a repository of its own."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.repo, self.base = layers.repository(self.folder)


class JudgeTreeTest(Repository):
    def test_the_judging_build_sees_the_files_of_the_change_with_the_policy_files_of_the_base(self) -> None:
        """/* REQ-ACTION-147: the tree the findings are judged in is a temporary git worktree of the commit checked: every file is the head's, but each policy file the change edits is as the base has it (absent when the base has none); the checkout of the user keeps the head's files, and no worktree is left when it is removed */"""
        head = layers.branch(self.repo, "weaken", {**layers.WITHOUT_TERMS, layers.CONFIG: "parts: {}\n"})
        git(self.repo, "checkout", "-q", "main")
        both = layers.commit(self.repo, {layers.CONFIG: "parts:\n  .:\n    style: layered\n"}, "the base states its rules")
        git(self.repo, "checkout", "-q", "weaken")
        git(self.repo, "merge", "-q", "-X", "ours", "-m", "the base merged in", both)
        head = git(self.repo, "rev-parse", "HEAD")
        judge = self.folder / "judge"
        pr._judge_tree(str(self.repo), str(judge), [], [layers.BASELINE, layers.CONFIG], both)
        try:
            self.assertEqual(layers.BREAK["src/models/models0.ts"], (judge / "src/models/models0.ts").read_text(encoding="utf-8"))
            self.assertFalse((judge / layers.BASELINE).exists())
            self.assertEqual("parts:\n  .:\n    style: layered\n", (judge / layers.CONFIG).read_bytes().decode("utf-8"))
            self.assertEqual(head, git(judge, "rev-parse", "HEAD"))
        finally:
            pr._remove_worktree(str(self.repo), str(judge))
        self.assertFalse(judge.exists())
        self.assertEqual(1, len(git(self.repo, "worktree", "list").splitlines()))
        self.assertEqual("", git(self.repo, "status", "--porcelain"))
        self.assertEqual("parts: {}\n", (self.repo / layers.CONFIG).read_text(encoding="utf-8"))
        self.assertTrue((self.repo / layers.BASELINE).is_file())

    def test_a_working_directory_is_judged_with_its_files_on_disk(self) -> None:
        """/* REQ-ACTION-147: for a working directory the judging tree takes the changed and the new files from disk (a deleted one is absent) and the policy files from the base; nothing is committed and the working files stay as they were */"""
        layers.write(self.repo, {**layers.WITHOUT_TERMS, "src/routes/routes9.ts": "export const routes9 = 9;\n"})
        (self.repo / "src/services/services3.ts").unlink()
        before = git(self.repo, "status", "--porcelain")
        judge = self.folder / "judge"
        names = ["src/models/models0.ts", "src/routes/routes9.ts", "src/services/services3.ts", layers.BASELINE]
        pr._judge_tree(str(self.repo), str(judge), names, [layers.BASELINE], self.base)
        try:
            self.assertEqual(layers.BREAK["src/models/models0.ts"], (judge / "src/models/models0.ts").read_text(encoding="utf-8"))
            self.assertTrue((judge / "src/routes/routes9.ts").is_file())
            self.assertFalse((judge / "src/services/services3.ts").exists())
            self.assertFalse((judge / layers.BASELINE).exists())
        finally:
            pr._remove_worktree(str(self.repo), str(judge))
        self.assertEqual(before, git(self.repo, "status", "--porcelain"))
        self.assertEqual(self.base, git(self.repo, "rev-parse", "HEAD"))

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_a_link_out_of_the_tree_is_not_written_through(self) -> None:
        """/* REQ-ACTION-147: when the head makes the folder of a policy file a symbolic link to a folder outside the repository, the judging tree does not write (or remove) the base's file through it: the file outside stays as it was and the run records a tool error, so the result is UNKNOWN */"""
        with_rules = layers.commit(self.repo, {layers.CONFIG: "parts: {}\n"}, "the base states its rules")
        outside = self.folder / "outside"
        outside.mkdir()
        (outside / "config.yaml").write_text("mine\n", encoding="utf-8")
        git(self.repo, "checkout", "-q", "-b", "link")
        git(self.repo, "rm", "-q", "-r", ".wingbox")
        os.symlink(str(outside), str(self.repo / ".wingbox"))
        git(self.repo, "add", ".wingbox")
        git(self.repo, "commit", "-q", "-m", "a link")
        judge = self.folder / "judge"
        errors = list(pr.TOOL_ERRORS)
        try:
            pr._judge_tree(str(self.repo), str(judge), [], [layers.CONFIG, layers.BASELINE], with_rules)
            self.assertEqual(["a link leads .wingbox/config.yaml out of the checkout", "a link leads .wingbox/baseline.sexp out of the checkout"],
                             pr.TOOL_ERRORS[len(errors):])
        finally:
            pr.TOOL_ERRORS[:] = errors
            pr._remove_worktree(str(self.repo), str(judge))
        self.assertEqual("mine\n", (outside / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(["config.yaml"], sorted(path.name for path in outside.iterdir()))

    def test_the_policy_of_the_base_is_the_base_as_merged(self) -> None:
        """/* REQ-ACTION-147: the commit whose policy files judge the findings is the merge base; when the checkout is the merge of the head into the base (what actions/checkout gives on a pull request) it is the base as merged, the other parent of that merge, so a policy change the base made meanwhile is kept */"""
        head = layers.branch(self.repo, "feature", layers.WITHOUT_TERMS)
        self.assertEqual(self.base, pr.policy_base(str(self.repo), self.base, head, pr.tree_state(str(self.repo), head, ())))
        git(self.repo, "checkout", "-q", "main")
        moved = layers.commit(self.repo, {layers.CONFIG: "parts: {}\n"}, "the base moves on")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", head)
        tree = pr.tree_state(str(self.repo), head, ())
        self.assertEqual("merge", tree.relation)
        self.assertEqual(moved, pr.policy_base(str(self.repo), self.base, head, tree))
        self.assertEqual(self.base, pr.policy_base(str(self.repo), self.base, pr.WORKING_TREE, pr.Tree("", pr.WORKING_TREE)))


class PolicyOnDiskTest(Repository):
    def test_a_policy_file_that_is_not_as_in_the_commit_is_named(self) -> None:
        """/* REQ-ACTION-148: a policy file of `.wingbox/` on disk that is not as in the commit checked (edited, new, or new and ignored by git) is named among the files that differ, so the result is UNKNOWN: the check would be judged by a file that is in no commit; the other files of `.wingbox/` still do not count */"""
        head = layers.branch(self.repo, "accepted", layers.WITH_TERMS)
        self.assertEqual(pr.Tree(head, "head"), pr.tree_state(str(self.repo), head, ()))
        (self.repo / layers.BASELINE).write_text(f"{layers.ENTRY})\n", encoding="utf-8")
        (self.repo / ".wingbox" / "index.sexp").write_text("(code-graph)\n", encoding="utf-8")
        self.assertEqual(pr.Tree(head, "head", (layers.BASELINE,)), pr.tree_state(str(self.repo), head, ()))
        git(self.repo, "checkout", "-q", "--", layers.BASELINE)
        (self.repo / ".wingbox" / ".gitignore").write_text("*\n", encoding="utf-8")
        (self.repo / layers.CONFIG).write_text("parts: {}\n", encoding="utf-8")
        self.assertEqual((layers.CONFIG,), pr.tree_state(str(self.repo), head, ()).differs)
        self.assertEqual("UNKNOWN", pr.result(pr.Report("0.14.0", 2, self.base, head, (pr.ChangedFile("src/models/models0.ts", "src/models/models0"),),
                                                        (), 0, (), tree=pr.tree_state(str(self.repo), head, ())))[0])


def run(repo: Path, out: Path, base: str, head: str, **settings: str) -> tuple[subprocess.CompletedProcess, list[str], dict, str]:
    """The script on two commits of repo, as a job runs it, or with an empty head on its working directory:
    (the finished process, the plan, the record, the report)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_")) or key == "WINGBOX_JOBS"}
    env.update(WINGBOX_VERSION="test", **settings)
    commits = ["--base", base, "--head", head] if head else ["--base", base]
    done = subprocess.run([sys.executable, "-P", str(ROOT / "wingbox_pr.py"), "--root", str(repo), *commits, "--out", str(out)],
                          cwd=str(out.parent), env=env, capture_output=True, text=True, encoding="utf-8")
    plan = (out / "wingbox-plan.sexp").read_text(encoding="utf-8").splitlines()
    record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
    return done, plan, record, (out / "wingbox-report.md").read_text(encoding="utf-8")


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class OwnAcceptanceTest(Repository):
    def test_a_change_cannot_accept_its_own_break(self) -> None:
        """/* REQ-ACTION-145: the scenario of issue 18, the whole script with a real Wingbox: a change that makes a domain module import the API layer and adds a baseline entry for that break is FAIL, and fails the job with `fail-on: strict`; the report, the plan and the record name the baseline as a policy file the change edits, and the finding with the entry that alone accepts it; the checkout keeps its files, and no worktree is left */"""
        head = layers.branch(self.repo, "weaken", layers.WITHOUT_TERMS)
        done, plan, record, report = run(self.repo, self.folder / "out", self.base, head, WINGBOX_FAIL_ON="strict")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(plan[0].startswith("(review-plan :schema 1 :result FAIL :execution ok "), plan[0])
        self.assertIn('(reason "introduced-violations: 1")', plan)
        self.assertIn('(reason "accepted-only-by-this-change: 1")', plan)
        self.assertIn(f'(finding violation {json.dumps(layers.VIOLATION)} :accepted-only-by ".wingbox/baseline.sexp" :missing (owner reason expires))', plan)
        self.assertIn('(policy-changed ".wingbox/baseline.sexp" :kind baseline :how added)', plan)
        self.assertIn(f"(policy-of-base {self.base})", plan)
        self.assertIn('(baseline-entry "." src/models/models0 src/routes/routes1 :rule "domain -> api" :missing (owner reason expires))', plan)
        self.assertIn("## Wingbox: 1 new boundary finding", report)
        self.assertIn(f"- violation: {layers.VIOLATION}. Only this change accepts it: `.wingbox/baseline.sexp`", report)
        self.assertIn("### This change edits what it is checked by\n\n- `.wingbox/baseline.sexp` (added): the breaks of the rules that are accepted\n", report)
        self.assertIn("Result of the boundary check: **FAIL** (execution ok).", report)
        self.assertEqual({"boundaries": "FAIL", "execution": "ok", "reasons": ["introduced-violations: 1", "accepted-only-by-this-change: 1"]},
                         record["result"])
        self.assertEqual(("fail", [{"path": ".wingbox/baseline.sexp", "kind": "baseline", "change": "added"}], self.base),
                         (record["policy"]["mode"], record["policy"]["changed"], record["policy"]["judged-with"]))
        self.assertEqual(1, record["counts"]["introduced-findings"])
        self.assertIn("policy-change: this change edits 1 file it is checked by", done.stdout)
        self.assertEqual("", git(self.repo, "status", "--porcelain"))
        self.assertEqual(1, len(git(self.repo, "worktree", "list").splitlines()))
        self.assertEqual(layers.WITHOUT_TERMS[layers.BASELINE], (self.repo / layers.BASELINE).read_text(encoding="utf-8"))

    def test_an_edit_of_the_rules_does_not_hide_the_break(self) -> None:
        """/* REQ-ACTION-146: a change that breaks a rule and edits `.wingbox/config.yaml` so that the rule no longer applies is FAIL: the finding the rules of the base give is introduced and says that only the rules of the change do not report it; what the head reports under its own rules stays in the list */"""
        head = layers.branch(self.repo, "rules", layers.RULES_EDIT)
        done, plan, record, report = run(self.repo, self.folder / "out", self.base, head, WINGBOX_FAIL_ON="strict")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(plan[0].startswith("(review-plan :schema 1 :result FAIL :execution ok "), plan[0])
        self.assertIn(f"(finding violation {json.dumps(layers.VIOLATION)} :accepted-only-by rules)", plan)
        self.assertIn('(policy-changed ".wingbox/config.yaml" :kind rules :how added)', plan)
        self.assertEqual(["violation-candidate", "violation"], [line.split()[1] for line in plan if line.startswith("(finding ")])
        self.assertIn(f"- violation: {layers.VIOLATION}. Only the rules as this change edits them do not report it.", report)
        self.assertIn("- `.wingbox/config.yaml` (added): the boundary rules\n", report)
        self.assertEqual([{"finding": "violation", "text": layers.VIOLATION, "by": "rules", "owner": "", "reason": "", "expires": "",
                           "accepts": False}], record["policy"]["self-accepted"])

    def test_an_entry_with_its_terms_accepts_and_the_policy_change_is_the_reviewers(self) -> None:
        """/* REQ-ACTION-146: the same break with a baseline entry that states an owner, a reason and an end date is accepted: the result is PASS and the report lists the break as accepted by the change itself; with `fail-on: strict` the job still fails for the policy change (`policy-change` is then `fail`), and with `policy-change: report` it passes */"""
        head = layers.branch(self.repo, "accepted", layers.WITH_TERMS)
        done, plan, record, report = run(self.repo, self.folder / "out", self.base, head, WINGBOX_FAIL_ON="strict")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(plan[0].startswith("(review-plan :schema 1 :result PASS :execution ok "), plan[0])
        self.assertIn("policy-change: this change edits 1 file it is checked by (.wingbox/baseline.sexp)", done.stdout)
        self.assertEqual("fail", record["policy"]["mode"])
        done, plan, record, report = run(self.repo, self.folder / "out2", self.base, head, WINGBOX_FAIL_ON="strict", WINGBOX_POLICY_CHANGE="report")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(plan[0].startswith("(review-plan :schema 1 :result PASS :execution ok "), plan[0])
        self.assertIn(f'(accepted-by-change violation {json.dumps(layers.VIOLATION)} :known ".wingbox/baseline.sexp" :owner "lead" '
                      ':reason "moves with the next release" :expires 2099-01-01)', plan)
        self.assertIn(f"Accepted by this change itself (1):\n\n- violation: {layers.VIOLATION}, accepted in `.wingbox/baseline.sexp` by lead "
                      'until 2099-01-01 ("moves with the next release")', report)
        self.assertEqual(("report", 0, True), (record["policy"]["mode"], record["counts"]["introduced-findings"],
                                               record["policy"]["self-accepted"][0]["accepts"]))
        self.assertNotIn("policy-change:", done.stdout)
        self.assertEqual("", git(self.repo, "status", "--porcelain"))
        wrong = subprocess.run([sys.executable, "-P", str(ROOT / "wingbox_pr.py"), "--root", str(self.repo), "--base", self.base, "--head", head],
                               env={**os.environ, "WINGBOX_POLICY_CHANGE": "approve"}, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(2, wrong.returncode)
        self.assertIn("policy-change must be one of fail, report", wrong.stdout)


    def test_a_working_directory_cannot_accept_its_own_break_either(self) -> None:
        """/* REQ-ACTION-151: the check of a working directory before a commit judges the same way: an uncommitted break with a baseline entry on disk, also one git ignores, is FAIL, the baseline is named as a policy file that was added, nothing is committed and the working files stay as they were */"""
        layers.write(self.repo, {**layers.WITHOUT_TERMS, ".wingbox/.gitignore": "*\n"})
        before = git(self.repo, "status", "--porcelain", "--ignored")
        done, plan, record, _report = run(self.repo, self.folder / "out", self.base, "", WINGBOX_FAIL_ON="violations")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(plan[0].startswith(f"(review-plan :schema 1 :result FAIL :execution ok :base {self.base} :head working-tree "), plan[0])
        self.assertIn(f'(finding violation {json.dumps(layers.VIOLATION)} :accepted-only-by ".wingbox/baseline.sexp" :missing (owner reason expires))', plan)
        self.assertIn('(policy-changed ".wingbox/baseline.sexp" :kind baseline :how added)', plan)
        self.assertEqual("report", record["policy"]["mode"])
        self.assertEqual(self.base, git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(layers.WITHOUT_TERMS[layers.BASELINE], (self.repo / layers.BASELINE).read_text(encoding="utf-8"))
        self.assertEqual(1, len(git(self.repo, "worktree", "list").splitlines()))
        after = [line for line in git(self.repo, "status", "--porcelain", "--ignored").splitlines() if not line.startswith("!! .wingbox/")]
        self.assertEqual([line for line in before.splitlines() if not line.startswith("!! .wingbox/")], after)


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class DeletedTestRunTest(unittest.TestCase):
    def test_a_test_deleted_with_the_code_it_tested_is_reported(self) -> None:
        """/* REQ-ACTION-149: the whole script on a change that edits a function and deletes the test file that used it: the report says that the test was deleted together with the code it tested, and the plan names the pair */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            test = "from app.services.billing import bill\n\n\ndef test_bill():\n    assert bill(None) == 0\n"
            base = make_fixture.commit(repo, {**make_fixture.BASE, "tests/__init__.py": "", "tests/test_billing.py": test}, "base")
            git(repo, "rm", "-q", "tests/test_billing.py")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            done, plan, _record, report = run(repo, Path(tmp) / "out", base, head)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertIn('(test-deleted "tests/test_billing.py" :tested "app/services/billing.py")', plan)
            self.assertIn("Tests deleted together with the code they tested: `tests/test_billing.py` (tested `app/services/billing.py`). "
                          "What they checked is no longer checked.", report)


if __name__ == "__main__":
    unittest.main()
