"""Evidence that the tests to run did run: JUnit reports matched with the tests the check names."""
import hashlib
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

import make_fixture  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_wingbox_pr import IMPACT, report  # noqa: E402,F401

PYTEST = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.test_billing.BillingTest" name="test_total" time="0.01"/>
<testcase classname="tests.test_billing.BillingTest" name="test_discount"><failure message="x">trace</failure></testcase>
<testcase classname="tests.test_orders" name="test_new"/>
<testcase classname="tests.test_refunds" name="test_all"><skipped message="later"/></testcase>
</testsuite></testsuites>"""
JEST = b"""<testsuites><testsuite name="orders" file="/work/repo/src/orders/orders.test.ts">
<testcase classname="orders create" name="works"/></testsuite>
<testsuite name="src/jobs/invoice.test.ts"><testcase classname="src/jobs/invoice.test.ts" name="runs"><error/></testcase></testsuite></testsuites>"""
JAVA = b"""<testsuite name="com.shop.OrderTest"><testcase classname="com.shop.OrderTest" name="total"/></testsuite>"""
GO = b"""<testsuites><testsuite name="github.com/shop/app/pkg/billing"><testcase classname="github.com/shop/app/pkg/billing" name="TestBill"/></testsuite></testsuites>"""


class JUnitTest(unittest.TestCase):
    def test_the_cases_of_a_report_are_read_with_their_place_and_their_end(self) -> None:
        """/* REQ-ACTION-095: a JUnit XML report gives one case per testcase element, in nested suites too, with the file of the case or of its suite, its class name, its suite and whether it passed, failed (a failure or an error element) or was skipped */"""
        cases = wt.parse_junit(PYTEST)
        self.assertEqual([wt.PASSED, wt.FAILED, wt.PASSED, wt.SKIPPED], [case.status for case in cases])
        self.assertEqual("tests.test_billing.BillingTest", cases[0].classname)
        jest = wt.parse_junit(JEST)
        self.assertEqual(("/work/repo/src/orders/orders.test.ts", wt.PASSED), (jest[0].file, jest[0].status))
        self.assertEqual(("src/jobs/invoice.test.ts", wt.FAILED), (jest[1].suite, jest[1].status))

    def test_a_file_that_is_not_a_plain_report_is_refused(self) -> None:
        """/* REQ-ACTION-096: a report that declares a document type or an entity, is not XML, has another root element or is over the size limit is refused with the reason, before or instead of being parsed */"""
        for data, reason in ((b'<!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite/>', "document type"), (b"<testsuite>", "not XML"),
                             (b"<html/>", "not a JUnit report"), (b"<testsuite>" + b" " * wt.SIZE_LIMIT + b"</testsuite>", "larger than")):
            with self.assertRaises(ValueError) as caught:
                wt.parse_junit(data)
            self.assertIn(reason, str(caught.exception))


class MatchTest(unittest.TestCase):
    def test_a_test_to_run_gets_the_cases_recorded_for_its_file(self) -> None:
        """/* REQ-ACTION-097: each test to run is matched with the recorded cases of its file: by the dotted class name of a Python test, by the file of a suite or a class name that is a path (JavaScript), by the end of a Java class name, by the Go package of a _test.go file together with the test functions of that file; it is failed when one of its cases failed, passed when every one of them passed, skipped when all were skipped, and without a record when it has none. A test named as a module (no file ending) matches the same way, and a name that only starts alike does not match */"""
        cases = wt.parse_junit(PYTEST) + wt.parse_junit(JEST) + wt.parse_junit(JAVA) + wt.parse_junit(GO)
        found = wt.evidence(["tests/test_billing.py", "tests/test_orders", "tests/test_refunds.py", "tests/test_order.py",
                             "src/orders/orders.test.ts", "src/jobs/invoice.test", "src/test/java/com/shop/OrderTest.java",
                             "pkg/billing/billing_test.go", "pkg/other/other_test.go"], cases,
                            {"pkg/billing/billing_test.go": ("TestBill",), "pkg/other/other_test.go": ("TestOther",)})
        self.assertEqual([("tests/test_billing.py", wt.FAILED, 2), ("tests/test_orders", wt.PASSED, 1), ("tests/test_refunds.py", wt.SKIPPED, 1),
                          ("tests/test_order.py", wt.NO_RECORD, 0), ("src/orders/orders.test.ts", wt.PASSED, 1),
                          ("src/jobs/invoice.test", wt.FAILED, 1), ("src/test/java/com/shop/OrderTest.java", wt.PASSED, 1),
                          ("pkg/billing/billing_test.go", wt.PASSED, 1), ("pkg/other/other_test.go", wt.NO_RECORD, 0)],
                         [(e.test, e.status, e.cases) for e in found])


def with_tests(**changes) -> "pr.Report":
    """A report whose change is used by two tests."""
    lines = IMPACT + ["(impact src/models/order tests/order.test :lang ts :via imports)",
                      "(impact src/models/order tests/invoice.test :lang ts :via imports)"]
    root, found = pr.dependents(lines)
    return report(changed=(pr.ChangedFile("src/models/order.ts", root, pr.untouched(found, {"src/models/order"})),), **changes)


class ReportTest(unittest.TestCase):
    def test_without_a_test_report_the_plan_says_there_is_no_evidence(self) -> None:
        """/* REQ-ACTION-098: without test results every `run-test` line of the plan says `:evidence none`, the comment names the tests as before, the counts of evidence are empty and `require-tests` fails the job as soon as there is a test to run */"""
        plain = with_tests()
        self.assertEqual(["tests/invoice.test", "tests/order.test"], [test.shown for test in pr.tests_to_run(plain)])
        plan = pr.build_plan(plain)
        self.assertIn('(run-test "tests/order.test" :uses "src/models/order.ts" :evidence none)', plan)
        self.assertIn("Tests that use what changed (2): `tests/invoice.test`, `tests/order.test`.\n", pr.build_comment(plain))
        self.assertIsNone(pr.test_evidence(plain))
        self.assertEqual(("", "", ""), pr.evidence_counts(plain))
        self.assertEqual(1, pr.tests_status(plain, True))
        self.assertEqual(0, pr.tests_status(plain, False))
        self.assertEqual(0, pr.tests_status(report(), True))

    def test_with_a_test_report_each_test_to_run_carries_what_was_recorded(self) -> None:
        """/* REQ-ACTION-099: with test results each `run-test` line says `:evidence passed|failed|skipped|no-record :cases N`, each report file is a `(test-report "<path>" :sha256 <digest> :cases N)` line, a file that could not be read is a `(test-report-error ...)` line, the comment counts the passed, the failed and those without a passing record and names the latter, and `require-tests` fails the job unless every test to run passed */"""
        cases = tuple(wt.parse_junit(b'<testsuite name="tests/order.test.ts"><testcase classname="tests/order.test.ts" name="a"/></testsuite>'))
        some = with_tests(test_cases=cases, test_reports=(wt.TestReport("junit.xml", "e" * 64, 1),), test_report_errors=(("bad.xml", "not XML: x"),))
        self.assertEqual([("tests/invoice.test", wt.NO_RECORD), ("tests/order.test", wt.PASSED)],
                         [(e.test, e.status) for e in pr.test_evidence(some)])
        plan = pr.build_plan(some)
        self.assertIn('(run-test "tests/order.test" :uses "src/models/order.ts" :evidence passed :cases 1)', plan)
        self.assertIn('(run-test "tests/invoice.test" :uses "src/models/order.ts" :evidence no-record :cases 0)', plan)
        self.assertIn(f'(test-report "junit.xml" :sha256 {"e" * 64} :cases 1)', plan)
        self.assertIn('(test-report-error "bad.xml" :reason "not XML: x")', plan)
        comment = pr.build_comment(some)
        self.assertIn("Tests that use what changed (2): 1 passed, 1 without a record in the test report (`tests/invoice.test`).", comment)
        self.assertEqual(("1", "0", "1"), pr.evidence_counts(some))
        self.assertEqual(1, pr.tests_status(some, True))
        both = tuple(wt.parse_junit(b'<testsuites><testsuite name="tests/order.test.ts"><testcase classname="x" name="a"/></testsuite>'
                                    b'<testsuite name="tests/invoice.test.ts"><testcase classname="y" name="b"/></testsuite></testsuites>'))
        self.assertEqual(0, pr.tests_status(with_tests(test_cases=both), True))
        failed = tuple(wt.parse_junit(b'<testsuite name="tests/order.test.ts"><testcase classname="x" name="a"><failure/></testcase></testsuite>'))
        self.assertIn("1 FAILED (`tests/order.test`)", pr.build_comment(with_tests(test_cases=failed)))


class FilesTest(unittest.TestCase):
    def test_the_report_files_are_found_by_pattern_and_named_with_their_digest(self) -> None:
        """/* REQ-ACTION-100: the patterns of `test-results` are globs from the given folder (** crosses folders); every file found is read once, named by its path from that folder with the SHA-256 of its bytes and the number of its cases; a file that is refused and a pattern that finds no file are reported, and no pattern at all means no evidence was given (None) */"""
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "out" / "deep").mkdir(parents=True)
            (base / "out" / "a.xml").write_bytes(PYTEST)
            (base / "out" / "deep" / "b.xml").write_bytes(JAVA)
            (base / "out" / "bad.xml").write_bytes(b"<html/>")
            cases, reports, errors = pr.read_test_results(folder, ["out/**/*.xml", "out/a.xml", "missing/*.xml"])
            self.assertEqual(5, len(cases))
            self.assertEqual([wt.TestReport("out/a.xml", hashlib.sha256(PYTEST).hexdigest(), 4),
                              wt.TestReport("out/deep/b.xml", hashlib.sha256(JAVA).hexdigest(), 1)], list(reports))
            self.assertEqual([("missing/*.xml", "no file matches this pattern"), ("out/bad.xml", "not a JUnit report: its root is <html>")],
                             sorted(errors))
            self.assertEqual((None, (), ()), pr.read_test_results(folder, []))

    def test_the_inputs_and_outputs_are_documented(self) -> None:
        """/* REQ-ACTION-101: action.yml has the inputs `test-results` and `require-tests` (off by default) and the outputs `tests-passed`, `tests-failed`, `tests-without-record`, hands the inputs to the script, and README.md documents them; the shell-runner GitLab template hands the container the two variables and the folder the reports are in */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  test-results:\n    description: [^\n]+\n    default: ""\n')
        self.assertRegex(action, r'\n  require-tests:\n    description: [^\n]+\n    default: "false"\n')
        for name in ("WINGBOX_TEST_RESULTS: ${{ inputs.test-results }}", "WINGBOX_REQUIRE_TESTS: ${{ inputs.require-tests }}"):
            self.assertIn(name, action)
        for name in ("tests-passed", "tests-failed", "tests-without-record"):
            self.assertIn(f"\n  {name}:\n", action)
            self.assertIn(f"`{name}`", readme)
        for name in ("`test-results`", "`require-tests`", "WINGBOX_TEST_RESULTS", "WINGBOX_REQUIRE_TESTS"):
            self.assertIn(name, readme)
        shell = (ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8")
        for name in ("--env WINGBOX_TEST_RESULTS ", "--env WINGBOX_REQUIRE_TESTS", "--env WINGBOX_TEST_RESULTS_ROOT=/repo"):
            self.assertIn(name, shell)


TESTED = {"tests/__init__.py": "", "tests/test_billing.py": "from app.services.billing import bill\n\n\ndef test_bill():\n    assert bill(None) is None\n"}
JUNIT = '<testsuites><testsuite name="pytest"><testcase classname="{name}" name="test_bill"/></testsuite></testsuites>'


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class EndToEndTest(unittest.TestCase):
    def run_check(self, repo: Path, out: Path, base: str, head: str, require: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_")) or key == "WINGBOX_JOBS"}
        env.update(WINGBOX_VERSION="test", WINGBOX_TEST_RESULTS="test-results/*.xml", WINGBOX_REQUIRE_TESTS=require,
                   GITHUB_OUTPUT=str(out / "output.txt"))
        return subprocess.run([sys.executable, str(ROOT / "wingbox_pr.py"), "--root", str(repo), "--base", base, "--head", head, "--out", str(out)],
                              cwd=out, env=env, capture_output=True, text=True)

    def test_the_whole_script_takes_the_test_reports_of_the_job(self) -> None:
        """/* REQ-ACTION-107: run as a job would run it, on a repository whose test file uses the changed function and whose test step left a JUnit report in the checkout (an untracked file that is not source, so the checkout still counts as its commit): the plan says `:evidence passed`, the outputs count one passed test, the evidence record says `complete` and names the report with its SHA-256, and `require-tests` lets the job pass; with a report that holds no case of that test the same run says `no-record`, `incomplete`, and `require-tests` fails the job while the result of the boundary check is the same */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, out = Path(tmp) / "repo", Path(tmp) / "out"
            repo.mkdir()
            out.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, {**make_fixture.BASE, **TESTED}, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            (repo / "test-results").mkdir()
            report_file = repo / "test-results" / "junit.xml"
            report_file.write_text(JUNIT.format(name="tests.test_billing"), encoding="utf-8")
            done = self.run_check(repo, out, base, head, "true")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            plan = (out / "wingbox-plan.sexp").read_text(encoding="utf-8")
            self.assertIn(":tree head)", plan.splitlines()[0])
            self.assertRegex(plan, r'\(run-test "tests/test_billing(\.py)?" :uses \S+ :evidence passed :cases 1\)')
            self.assertIn(f'(test-report "test-results/junit.xml" :sha256 {hashlib.sha256(report_file.read_bytes()).hexdigest()} :cases 1)', plan)
            outputs = dict(line.split("=", 1) for line in (out / "output.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual(("1", "1", "0", "0"), (outputs["tests-to-run"], outputs["tests-passed"], outputs["tests-failed"], outputs["tests-without-record"]))
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(("complete", "head", head), (record["tests"]["evidence"], record["subject"]["tree"], record["subject"]["checked"]))
            self.assertNotIn("tree-differs", plan)
            first_result = outputs["result"]

            (out / "output.txt").unlink()
            report_file.write_text(JUNIT.format(name="tests.test_other"), encoding="utf-8")
            missing = self.run_check(repo, out, base, head, "true")
            self.assertEqual(1, missing.returncode, missing.stdout + missing.stderr)
            self.assertIn("require-tests: 1 test(s) that use what changed have no passing record", missing.stdout)
            self.assertIn(":evidence no-record :cases 0)", (out / "wingbox-plan.sexp").read_text(encoding="utf-8"))
            again = dict(line.split("=", 1) for line in (out / "output.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual((first_result, "0", "1"), (again["result"], again["tests-passed"], again["tests-without-record"]))
            self.assertEqual("incomplete", json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))["tests"]["evidence"])
            self.assertEqual(0, self.run_check(repo, out, base, head, "false").returncode)


if __name__ == "__main__":
    unittest.main()
