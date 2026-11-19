"""The review plan: the tests to run, a test changed with the code it tests, the strict result, the plan for an agent."""
import dataclasses
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_changes as changes  # noqa: E402
import wingbox_facts as facts  # noqa: E402
import wingbox_report as report  # noqa: E402
from wingbox_changes import Hunk  # noqa: E402
from wingbox_facts import ChangedFile, ChangedItem, Dependent, Finding, NotChecked, User  # noqa: E402

FILE, MODULE = "src/billing/invoice.py", "src/billing/invoice"
ITEM = ChangedItem("total", "fn", 12, (
    User("src/api/orders", "src/api/orders.py", 40, "call", 2),
    User("tests/test_invoice", "tests/test_invoice.py", 9, "call", 4),
    User("tests/api/test_orders", "tests/api/test_orders.py", 31, "call", 1),
))
DEPENDENTS = (Dependent("src/api/orders", 1), Dependent("tests/test_invoice", 1), Dependent("tests/test_legacy", 1), Dependent("src/web/page", 2, through="src/api/orders"))
VIOLATION = Finding("violation", ("violation", "src/domain/a", "src/infra/b", "domain -> infra"), "`src/domain/a` -> `src/infra/b` (domain -> infra)")
CANDIDATE = Finding("violation-candidate", ("violation-candidate", "x", "y", "r"), "`x` -> `y` (r)")


def make(*items: ChangedItem, looked_at: bool = True, other_code: bool = False, **values: Any) -> facts.Report:
    changed = (ChangedFile(FILE, MODULE, DEPENDENTS, tuple(items) if looked_at else None, other_code),)
    return dataclasses.replace(facts.Report("0.11.0", 2, "a" * 40, "b" * 40, changed, (), 0, ()), **values)


class TestsToRunTest(unittest.TestCase):
    def test_the_comment_names_the_tests_that_use_what_changed(self) -> None:
        """/* REQ-ACTION-052: the comment names the test files that use a changed item, in one line after the
        modules: the number, at most TESTS_LIMIT files and a count of the rest; the job summary names all. A
        test module is not a row of an item. For a file taken as a whole (not looked at, or with other code
        changed) the tests that use the file directly are named too. */"""
        body = report.build_comment(make(ITEM))
        self.assertIn("\nTests that use what changed (2): `tests/api/test_orders.py`, `tests/test_invoice.py`.\n", body)
        self.assertNotIn("- `tests/", body.split("<details>")[0])
        self.assertNotIn("test modules use it too", body)
        self.assertLess(body.index("- `src/api/orders` calls it"), body.index("Tests that use what changed"))
        self.assertLess(body.index("Tests that use what changed"), body.index("<details>"))
        whole = report.build_comment(make(looked_at=False))
        self.assertIn("\nTests that use what changed (2): `tests/test_invoice`, `tests/test_legacy`.\n", whole)
        both = report.build_comment(make(ITEM, other_code=True))
        self.assertIn("Tests that use what changed (3): `tests/api/test_orders.py`, `tests/test_invoice.py`, `tests/test_legacy`.", both)
        many = ChangedItem("total", "fn", 12, tuple(User(f"tests/test_m{index:02d}", f"tests/test_m{index:02d}.py", 3, "call", 1) for index in range(report.TESTS_LIMIT + 3)))
        self.assertIn(f"Tests that use what changed ({report.TESTS_LIMIT + 3}): `tests/test_m00.py`, `tests/test_m01.py`, `tests/test_m02.py`, `tests/test_m03.py`, `tests/test_m04.py` and 3 more "
                      "(the job summary lists them all).", report.build_comment(make(many)))
        self.assertIn("`tests/test_m07.py`.", report.build_comment(make(many), full=True))

    def test_a_change_only_tests_use_is_one_sentence_with_its_tests(self) -> None:
        """/* REQ-ACTION-053: a changed item that only tests use gives the one sentence, followed by the tests to
        run; a change of comments names no test. */"""
        only = ChangedItem("helper", "fn", 3, (User("tests/test_helper", "tests/test_helper.py", 4, "call", 1),))
        body = report.build_comment(make(only))
        self.assertIn("**Wingbox**: what this pull request changes is not used outside it, and it adds no boundary finding.\n\nTests that use what changed (1): `tests/test_helper.py`.\n", body)
        self.assertNotIn("Tests that use what changed", report.build_comment(make()))


class TestChangedTest(unittest.TestCase):
    def test_a_test_that_loses_lines_together_with_the_code_it_tests_is_flagged(self) -> None:
        """/* REQ-ACTION-054: a changed test file that uses a changed source file directly, and in which the pull
        request removes or replaces a line of code, is paired with that source file; a test that only gains lines
        (new tests), a test of a file the pull request does not change, and a source file are not. */"""
        raw = {
            "src/billing/invoice.py": ("src/billing/invoice", (Dependent("tests/test_invoice", 1), Dependent("tests/test_new", 1), Dependent("src/api/orders", 1), Dependent("tests/test_far", 2))),
            "tests/test_invoice.py": ("tests/test_invoice", ()), "tests/test_new.py": ("tests/test_new", ()), "tests/test_other.py": ("tests/test_other", ()),
            "tests/test_far.py": ("tests/test_far", ()), "src/api/orders.py": ("src/api/orders", ()), "README.md": (None, ()),
        }
        hunks = {
            "tests/test_invoice.py": (Hunk(10, ("    assert total(1) == 3",), ("    assert total(1) == 2",)),),
            "tests/test_new.py": (Hunk(1, ("def test_more():", "    assert True"), ()),),
            "tests/test_other.py": (Hunk(5, (), ("    assert legacy()",)),),
            "tests/test_far.py": (Hunk(5, (), ("    assert far()",)),),
            "src/api/orders.py": (Hunk(2, ("x = 1",), ("x = 0",)),),
        }
        self.assertTrue(changes.removes_code(hunks["tests/test_invoice.py"], "tests/test_invoice.py"))
        self.assertFalse(changes.removes_code(hunks["tests/test_new.py"], "tests/test_new.py"))
        self.assertFalse(changes.removes_code((Hunk(3, ("# new note",), ("# old note",)),), "tests/t.py"))
        self.assertEqual((("tests/test_invoice.py", "src/billing/invoice.py"),), report.changed_tests(raw, hunks))

    def test_the_comment_says_which_tests_changed_with_their_code(self) -> None:
        """/* REQ-ACTION-055: the comment says in one line which tests changed together with the code they test,
        before the folded block; without such a test it says nothing about it. */"""
        body = report.build_comment(make(ITEM, tests_changed=(("tests/test_invoice.py", FILE), ("tests/api/test_orders.py", FILE))))
        line = ("Tests changed together with the code they test: `tests/api/test_orders.py` (tests `src/billing/invoice.py`); "
                "`tests/test_invoice.py` (tests `src/billing/invoice.py`). Look at what they no longer check.")
        self.assertIn("\n" + line + "\n", body)
        self.assertLess(body.index(line), body.index("<details>"))
        self.assertNotIn("Tests changed together", report.build_comment(make(ITEM)))


class ResultTest(unittest.TestCase):
    def test_the_result_never_reads_an_unchecked_part_or_a_tool_error_as_a_pass(self) -> None:
        """/* REQ-ACTION-056: the result of the boundary check is one of PASS, FAIL, UNKNOWN, NOT_APPLICABLE with a
        separate execution status and reasons. FAIL: the pull request introduces a blocking finding (a candidate
        is not one). UNKNOWN: a part was not checked, or a wingbox command ended abnormally (execution error).
        NOT_APPLICABLE: no source module outside the tests changed. PASS only when nothing of that holds. */"""
        self.assertEqual(("PASS", "ok", ()), report.result(make(ITEM)))
        self.assertEqual(("PASS", "ok", ()), report.result(make(ITEM, introduced=(CANDIDATE,))))
        self.assertEqual(("FAIL", "ok", ("introduced-violations: 1",)), report.result(make(ITEM, introduced=(VIOLATION, CANDIDATE))))
        self.assertEqual(("UNKNOWN", "ok", ("not-checked: web (too-few-files)",)), report.result(make(ITEM, not_checked=(NotChecked("web", "too-few-files"),))))
        self.assertEqual(("UNKNOWN", "error", ("tool-error: wingbox boundaries exited 3",)), report.result(make(ITEM, tool_errors=("wingbox boundaries exited 3",))))
        self.assertEqual(("FAIL", "error", ("introduced-violations: 1", "tool-error: wingbox usages exited 70")),
                         report.result(make(ITEM, introduced=(VIOLATION,), tool_errors=("wingbox usages exited 70",))))
        docs = dataclasses.replace(make(), changed=(ChangedFile("README.md", None),))
        self.assertEqual(("NOT_APPLICABLE", "ok", ("no-source-module-changed",)), report.result(docs))

    def test_the_strict_gate_fails_on_everything_but_a_pass(self) -> None:
        """/* REQ-ACTION-057: with fail-on strict the action fails when the result is FAIL or UNKNOWN and passes on
        PASS and NOT_APPLICABLE; the job summary states the result, the execution status and the reasons. */"""
        unknown = make(ITEM, not_checked=(NotChecked("web", "too-few-files"),))
        self.assertIn("strict", report.FAIL_ON)
        self.assertEqual([0, 1, 1, 0], [report.exit_status(r, "strict") for r in (
            make(ITEM), unknown, make(ITEM, introduced=(VIOLATION,)), dataclasses.replace(make(), changed=(ChangedFile("README.md", None),)))])
        self.assertEqual(0, report.exit_status(unknown, "none"))
        summary = report.build_comment(unknown, full=True)
        self.assertIn("\nResult of the boundary check: **UNKNOWN** (execution ok). Reasons: not-checked: web (too-few-files).\n", summary)
        self.assertIn("\nResult of the boundary check: **PASS** (execution ok).\n", report.build_comment(make(ITEM), full=True))


class PlanTest(unittest.TestCase):
    def test_the_plan_lists_what_to_look_at_in_order_with_file_and_line(self) -> None:
        """/* REQ-ACTION-058: the review plan is a list of S-expression lines an agent can read: a head line with the
        schema, the result, the execution status, the two commits and the wingbox version; then, in the order to
        check: the findings, each changed item, a `look` line for every place outside the pull request that uses
        it (file, line, how), the files with other code changed and the modules that use them, the tests to
        run, the tests changed with their code, the parts not checked, and the content hash of each changed
        file. The same report gives the same plan. */"""
        data = make(ITEM, other_code=True, introduced=(VIOLATION,), not_checked=(NotChecked("web", "too-few-files"),),
                    tests_changed=(("tests/test_invoice.py", FILE),), hashes=((FILE, "ab" * 32),))
        plan = report.build_plan(data).split("\n")
        self.assertEqual(f'(review-plan :schema 1 :result FAIL :execution ok :base {"a" * 40} :head {"b" * 40} :wingbox "0.11.0" :hops 2)', plan[0])
        self.assertEqual([
            '(reason "introduced-violations: 1")',
            '(finding violation "`src/domain/a` -> `src/infra/b` (domain -> infra)")',
            '(changed-item "src/billing/invoice.py" total :kind fn :line 12 :how changed)',
            '(look "src/api/orders.py" 40 :module src/api/orders :why call :item total :places 2)',
            '(other-code "src/billing/invoice.py")',
            '(look-module src/api/orders :why uses :file "src/billing/invoice.py")',
            '(run-test "tests/api/test_orders.py" :uses total :evidence none)',
            '(run-test "tests/test_invoice.py" :uses total :evidence none)',
            '(run-test "tests/test_legacy" :uses "src/billing/invoice.py" :evidence none)',
            '(test-changed "tests/test_invoice.py" :tests "src/billing/invoice.py")',
            '(not-checked "web" :reason too-few-files)',
            f'(changed-file "src/billing/invoice.py" :sha256 {"ab" * 32})',
        ], plan[1:])
        self.assertEqual(report.build_plan(data), report.build_plan(data))
        via = ChangedItem("outer", "fn", 1, (User("src/x", "src/x.py", 9, "call", 1),), ("helper", "inner"))
        self.assertIn('(changed-item "src/billing/invoice.py" outer :kind fn :line 1 :how calls :via (helper inner))', report.build_plan(make(via)))
        odd = ChangedItem("op`+", "fn", 1, (User("src/x", "src/a b.py", 0, "import", 1),))
        self.assertIn('(look "src/a b.py" 0 :module src/x :why import :item "op`+" :places 1)', report.build_plan(make(odd)))

    def test_the_comment_carries_the_plan_folded_when_there_is_something_to_review(self) -> None:
        """/* REQ-ACTION-059: the comment carries the plan in a folded block of its own, as a code block, at most
        PLAN_LIMIT lines with a line that counts the rest; the job summary shows it whole under a heading. A
        comment with nothing to review (no look, no test, no finding) carries no plan block. */"""
        body = report.build_comment(make(ITEM))
        self.assertIn("<details>\n<summary>Review plan for an agent</summary>\n\n```lisp\n(review-plan :schema 1 :result PASS", body)
        self.assertIn('(look "src/api/orders.py" 40 :module src/api/orders :why call :item total :places 2)\n', body)
        self.assertEqual(body.count("<details>"), body.count("</details>"))
        self.assertLess(body.index("Review plan for an agent"), body.index("<sub>[Wingbox]"))
        self.assertNotIn("Review plan", report.build_comment(make()))
        self.assertNotIn("Review plan", report.build_comment(dataclasses.replace(make(), changed=(ChangedFile("README.md", None),))))
        summary = report.build_comment(make(ITEM), full=True)
        self.assertIn("### Review plan\n\n```lisp\n(review-plan :schema 1 :result PASS", summary)
        self.assertNotIn("<details>", summary)
        many = ChangedItem("total", "fn", 12, tuple(User(f"src/m{index:03d}", f"src/m{index:03d}.py", 3, "call", 1) for index in range(report.PLAN_LIMIT + 20)))
        cut_plan = report.build_comment(make(many)).split("```lisp\n")[1].split("\n```")[0].split("\n")
        self.assertEqual(report.PLAN_LIMIT + 1, len(cut_plan))
        self.assertRegex(cut_plan[-1], r"^\(plan-more :hidden \d+\)$")
        self.assertNotIn("plan-more", report.build_comment(make(many), full=True))


if __name__ == "__main__":
    unittest.main()
