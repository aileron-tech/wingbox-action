"""Test evidence per test case: a passed case does not hide a skipped or not-run case of the same file."""
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
import wingbox_evidence as we  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_evidence_record import RUN, SCHEMA, problems  # noqa: E402
from test_test_evidence import with_tests  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

MIXED = (b'<testsuite name="pytest"><testcase classname="tests.test_auth" name="test_login"/>'
         b'<testcase classname="tests.test_auth" name="test_token_expiry"><skipped message="needs vault"/></testcase></testsuite>')
GO = (b'<testsuites><testsuite name="example.com/app/pkg/billing">'
      b'<testcase classname="example.com/app/pkg/billing" name="TestInvoice"/></testsuite></testsuites>')


def suite(name: str, *cases: tuple[str, str]) -> bytes:
    """A JUnit report of one suite: (name of the case, what the case element holds)."""
    body = "".join(f'<testcase classname="{name}" name="{case}">{inner}</testcase>' for case, inner in cases)
    return f'<testsuite name="{name}">{body}</testsuite>'.encode()


TWO_FILES = b"<testsuites>" + suite("tests/order.test.ts", ("adds", ""), ("ships", "<skipped/>")) + suite("tests/invoice.test.ts", ("bills", "")) + b"</testsuites>"


class CaseStatusTest(unittest.TestCase):
    def test_a_file_is_passed_only_when_every_recorded_case_passed(self) -> None:
        """/* REQ-ACTION-108: a test file is `passed` only when every recorded case of it passed; with a passed and a skipped case it is `partial`, with a passed and a failed case `failed`, with skipped cases only `skipped`, without a case `no-record`; the number of recorded cases is kept */"""
        self.assertEqual([("tests/test_auth.py", wt.PARTIAL, 2)],
                         [(e.test, e.status, e.cases) for e in wt.evidence(["tests/test_auth.py"], wt.parse_junit(MIXED))])

        def status(*cases: tuple[str, str]) -> tuple[str, int]:
            found = wt.evidence(["tests/test_auth.py"], wt.parse_junit(suite("tests.test_auth", *cases)))
            return found[0].status, found[0].cases
        self.assertEqual((wt.FAILED, 2), status(("a", ""), ("b", "<failure/>")))
        self.assertEqual((wt.FAILED, 3), status(("a", ""), ("b", "<skipped/>"), ("c", "<error/>")))
        self.assertEqual((wt.PARTIAL, 2), status(("a", ""), ("b", "<skipped/>")))
        self.assertEqual((wt.SKIPPED, 2), status(("a", "<skipped/>"), ("b", "<skipped/>")))
        self.assertEqual((wt.PASSED, 2), status(("a", ""), ("b", "")))
        self.assertEqual((wt.NO_RECORD, 0), status())

    def test_each_case_is_kept_with_its_identifier_and_its_status(self) -> None:
        """/* REQ-ACTION-109: a report gives the name of each case; the evidence of a test file holds every case that belongs to it with where the report puts it (the class name, else the file, else the suite), its name and its status, in the order of the reports */"""
        self.assertEqual(["test_login", "test_token_expiry"], [case.name for case in wt.parse_junit(MIXED)])
        found = wt.evidence(["tests/test_auth.py", "tests/test_other.py"], wt.parse_junit(MIXED))
        self.assertEqual((wt.CaseResult("tests.test_auth", "test_login", wt.PASSED), wt.CaseResult("tests.test_auth", "test_token_expiry", wt.SKIPPED)),
                         found[0].records)
        self.assertEqual((), found[1].records)
        jest = wt.parse_junit(b'<testsuites><testsuite name="orders" file="src/orders/orders.test.ts"><testcase name="works"/></testsuite>'
                              b'<testsuite name="src/jobs/invoice.test.ts"><testcase name="runs"><error/></testcase></testsuite></testsuites>')
        by_place = wt.evidence(["src/orders/orders.test.ts", "src/jobs/invoice.test.ts"], jest)
        self.assertEqual([(wt.CaseResult("src/orders/orders.test.ts", "works", wt.PASSED),), (wt.CaseResult("src/jobs/invoice.test.ts", "runs", wt.FAILED),)],
                         [item.records for item in by_place])


class GoTest(unittest.TestCase):
    def test_a_package_record_counts_for_the_file_that_holds_the_function(self) -> None:
        """/* REQ-ACTION-110: a record of a Go package is matched to a test file of that folder by the test functions of the file: of two test files of one package where one function ran, the file that holds it is `passed` and the other is `no-record`; a Go test file whose functions are not known gets no package record at all */"""
        tests = ["pkg/billing/invoice_test.go", "pkg/billing/refund_test.go"]
        functions = {"pkg/billing/invoice_test.go": ("TestInvoice",), "pkg/billing/refund_test.go": ("TestRefund",)}
        found = wt.evidence(tests, wt.parse_junit(GO), functions)
        self.assertEqual([("pkg/billing/invoice_test.go", wt.PASSED, 1), ("pkg/billing/refund_test.go", wt.NO_RECORD, 0)],
                         [(e.test, e.status, e.cases) for e in found])
        self.assertEqual((wt.CaseResult("example.com/app/pkg/billing", "TestInvoice", wt.PASSED),), found[0].records)
        self.assertEqual((wt.CaseResult("pkg/billing", "TestRefund", wt.NO_RECORD),), found[1].records)
        self.assertEqual([wt.NO_RECORD, wt.NO_RECORD], [e.status for e in wt.evidence(tests, wt.parse_junit(GO))])
        self.assertEqual([wt.NO_RECORD, wt.NO_RECORD], [e.status for e in wt.evidence(tests, wt.parse_junit(GO), {"pkg/other/x_test.go": ("TestInvoice",)})])

    def test_a_test_function_of_the_file_without_a_record_is_named(self) -> None:
        """/* REQ-ACTION-111: a subtest (`TestTax/zero`) belongs to the file that holds its function; a test function of a Go file that no report holds is kept as a case `no-record` and the file is then `partial`, never `passed`; `TestMain`, benchmarks and helpers are not test functions; a Go test named as a module (no file ending) is matched the same way; a function of the same name in another package does not count */"""
        report = (b'<testsuites><testsuite name="example.com/app/pkg/billing">'
                  b'<testcase classname="example.com/app/pkg/billing" name="TestTax/zero"/>'
                  b'<testcase classname="example.com/app/pkg/billing" name="TestTax"/></testsuite>'
                  b'<testsuite name="example.com/app/pkg/store"><testcase classname="example.com/app/pkg/store" name="TestSave"/></testsuite>'
                  b'<testsuite name="example.com/app/other/billing2"><testcase classname="example.com/app/other/billing2" name="TestTotal"/></testsuite></testsuites>')
        functions = {"pkg/billing/tax_test.go": ("TestMain", "TestTax", "TestTotal", "BenchmarkTax", "helper", "Testify"),
                     "pkg/store/store_ext_test": ("TestSave",)}
        tax, store = wt.evidence(["pkg/billing/tax_test.go", "pkg/store/store_ext_test"], wt.parse_junit(report), functions)
        self.assertEqual((wt.PARTIAL, 2), (tax.status, tax.cases))
        self.assertEqual((wt.CaseResult("example.com/app/pkg/billing", "TestTax/zero", wt.PASSED), wt.CaseResult("example.com/app/pkg/billing", "TestTax", wt.PASSED),
                          wt.CaseResult("pkg/billing", "TestTotal", wt.NO_RECORD)), tax.records)
        self.assertEqual((wt.PASSED, 1), (store.status, store.cases))

    def test_the_functions_of_a_go_test_file_are_read_from_wingbox(self) -> None:
        """/* REQ-ACTION-112: the functions of a Go test file are the `fn` lines of `wingbox module <id>` when its node line says `:lang go`; for a module of another language or an unknown one there is no answer (None). Only a Go test of the tests to run whose folder is the end of a package a report names is asked about */"""
        lines = ["(node pkg/billing/invoice_test :lang go test :file /w/pkg/billing/invoice_test.go)",
                 "(fn pkg/billing/invoice_test TestInvoice 5 :lang go)", "(fn pkg/billing/invoice_test helper 15 :lang go)",
                 "(typ pkg/billing/invoice_test fixture 20 :lang go)", "(imports pkg/billing/invoice_test pkg/billing/invoice)"]
        self.assertEqual(("TestInvoice", "helper"), pr.go_functions(lines))
        self.assertIsNone(pr.go_functions(["(node tests/billing_test :lang python test :file /w/tests/billing_test.py)", "(fn tests/billing_test test_bill 4 :lang python)"]))
        self.assertIsNone(pr.go_functions(["(error no-match pkg/billing/nothing :suggestions pkg/billing/invoice)"]))
        self.assertIsNone(pr.go_functions([]))
        cases = wt.parse_junit(GO)
        self.assertEqual(["pkg/billing/invoice_test.go", "pkg/billing/refund_test"],
                         wt.go_candidates(["pkg/billing/invoice_test.go", "pkg/billing/refund_test", "pkg/store/store_test.go", "tests/test_billing.py",
                                           "billing/x.test.ts", "main_test.go"], cases))
        self.assertEqual([], wt.go_candidates(["pkg/billing/invoice_test.go"], []))


class ReportTest(unittest.TestCase):
    def test_a_test_with_a_skipped_case_is_not_counted_as_passed(self) -> None:
        """/* REQ-ACTION-113: a test to run with a passed and a skipped case is `partial` in its `run-test` line, which then counts its cases by status; every case is a `(test-case "<test>" "<where>" "<name>" :status <status>)` line of the plan; the comment counts the test apart from the passed ones and names it; the outputs count it among the tests without a passing record; `require-tests` fails on it */"""
        cases = tuple(wt.parse_junit(TWO_FILES))
        some = with_tests(test_cases=cases)
        self.assertEqual([("tests/invoice.test", wt.PASSED), ("tests/order.test", wt.PARTIAL)], [(e.test, e.status) for e in pr.test_evidence(some)])
        plan = pr.build_plan(some).split("\n")
        self.assertIn('(run-test "tests/order.test" :uses "src/models/order.ts" :evidence partial :cases 2 :passed 1 :skipped 1)', plan)
        self.assertIn('(run-test "tests/invoice.test" :uses "src/models/order.ts" :evidence passed :cases 1)', plan)
        self.assertIn('(test-case "tests/order.test" "tests/order.test.ts" "adds" :status passed)', plan)
        self.assertIn('(test-case "tests/order.test" "tests/order.test.ts" "ships" :status skipped)', plan)
        self.assertIn('(test-case "tests/invoice.test" "tests/invoice.test.ts" "bills" :status passed)', plan)
        self.assertLess(max(index for index, line in enumerate(plan) if line.startswith("(run-test ")),
                        min(index for index, line in enumerate(plan) if line.startswith("(test-case ")))
        self.assertIn("Tests that use what changed (2): 1 passed, 1 with a case skipped or not run (`tests/order.test`: 1 of 2 cases passed).", pr.build_comment(some))
        self.assertEqual(("1", "0", "1"), pr.evidence_counts(some))
        self.assertEqual(1, pr.tests_status(some, True))
        self.assertEqual(0, pr.tests_status(some, False))

    def test_the_functions_of_a_go_test_reach_the_evidence_of_the_report(self) -> None:
        """/* REQ-ACTION-114: the report carries the functions Wingbox lists for the Go tests to run, and its test evidence uses them: the same package record marks one of two test files of a folder `passed` and the other `no-record` */"""
        lines = ["(impact-root pkg/store/store :lang go :file pkg/store/store.go)",
                 "(impact pkg/store/store pkg/store/store_ext_test :lang go :via imports :depth 1)",
                 "(impact pkg/store/store pkg/store/other_ext_test :lang go :via imports :depth 1)"]
        root, found = pr.dependents(lines)
        cases = tuple(wt.parse_junit(b'<testsuite name="example.com/app/pkg/store"><testcase classname="example.com/app/pkg/store" name="TestSave"/></testsuite>'))
        changed = (pr.ChangedFile("pkg/store/store.go", root, pr.untouched(found, {"pkg/store/store"})),)
        with_go = report(changed=changed, test_cases=cases,
                         test_functions=(("pkg/store/store_ext_test", ("TestSave",)), ("pkg/store/other_ext_test", ("TestOther",))))
        self.assertEqual([("pkg/store/other_ext_test", wt.NO_RECORD), ("pkg/store/store_ext_test", wt.PASSED)],
                         [(e.test, e.status) for e in pr.test_evidence(with_go)])
        self.assertEqual([wt.NO_RECORD, wt.NO_RECORD], [e.status for e in pr.test_evidence(report(changed=changed, test_cases=cases))])
        self.assertIn('(test-case "pkg/store/other_ext_test" "pkg/store" "TestOther" :status no-record)', pr.build_plan(with_go))

    def test_the_record_keeps_every_case(self) -> None:
        """/* REQ-ACTION-115: the evidence record is `wingbox-evidence/2`: each test to run carries its cases as `records` (where, name, status), its status may be `partial`, and a test with a case that did not pass makes the test evidence `incomplete`; the record meets schema/wingbox-evidence-2.schema.json */"""
        cases = tuple(wt.parse_junit(TWO_FILES))
        record = we.build_evidence(with_tests(test_cases=cases), RUN, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual("wingbox-evidence/2", record["schema"])
        self.assertEqual("https://github.com/aileron-tech/wingbox-action/blob/main/schema/wingbox-evidence-2.schema.json", SCHEMA["$id"])
        self.assertEqual("incomplete", record["tests"]["evidence"])
        self.assertEqual([{"test": "tests/invoice.test", "status": "passed", "cases": 1,
                           "records": [{"where": "tests/invoice.test.ts", "name": "bills", "status": "passed"}]},
                          {"test": "tests/order.test", "status": "partial", "cases": 2,
                           "records": [{"where": "tests/order.test.ts", "name": "adds", "status": "passed"},
                                       {"where": "tests/order.test.ts", "name": "ships", "status": "skipped"}]}], record["tests"]["to-run"])
        self.assertEqual([], we.build_evidence(with_tests(), RUN, {})["tests"]["to-run"][0]["records"])
        self.assertIn("schema/wingbox-evidence-2.schema.json", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("schema/wingbox-evidence-2.schema.json", (ROOT / "action.yml").read_text(encoding="utf-8"))


GO_BASE = {
    "go.mod": "module example.com/app\n\ngo 1.22\n",
    "pkg/store/store.go": 'package store\n\nimport "strings"\n\nfunc Save(name string) string { return strings.TrimSpace(name) }\n',
    "pkg/store/store_ext_test.go": ('package store_test\n\nimport (\n\t"testing"\n\n\t"example.com/app/pkg/store"\n)\n\n'
                                    'func TestSave(t *testing.T) {\n\tif store.Save(" a ") != "a" {\n\t\tt.Fatal("no")\n\t}\n}\n'),
    "pkg/store/other_ext_test.go": ('package store_test\n\nimport (\n\t"testing"\n\n\t"example.com/app/pkg/store"\n)\n\n'
                                    'func TestOther(t *testing.T) {\n\tif store.Save("b") != "b" {\n\t\tt.Fatal("no")\n\t}\n}\n'),
}
GO_HEAD = {"pkg/store/store.go": 'package store\n\nimport (\n\t"strings"\n)\n\nfunc Save(name string) string { return strings.TrimSpace(name) }\n'}
GO_REPORT = ('<testsuites><testsuite name="example.com/app/pkg/store">'
             '<testcase classname="example.com/app/pkg/store" name="TestSave"/></testsuite></testsuites>')


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class EndToEndTest(unittest.TestCase):
    def test_the_whole_script_matches_a_go_package_record_by_function(self) -> None:
        """/* REQ-ACTION-116: run as a job would run it on a Go repository whose changed file is used by two test files of one package, with a report that holds one test function of that package: the test file that holds the function is `passed`, the other is `no-record` with its function named, the record says `incomplete` and keeps the cases, and `require-tests` fails the job */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, out = Path(tmp) / "repo", Path(tmp) / "out"
            repo.mkdir()
            out.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, GO_BASE, "base")
            head = make_fixture.commit(repo, GO_HEAD, "head")
            (repo / "test-results").mkdir()
            (repo / "test-results" / "junit.xml").write_text(GO_REPORT, encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_")) or key == "WINGBOX_JOBS"}
            env.update(WINGBOX_VERSION="test", WINGBOX_TEST_RESULTS="test-results/*.xml", WINGBOX_REQUIRE_TESTS="true")
            done = subprocess.run([sys.executable, str(ROOT / "wingbox_pr.py"), "--root", str(repo), "--base", base, "--head", head, "--out", str(out)],
                                  cwd=out, env=env, capture_output=True, text=True)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            plan = (out / "wingbox-plan.sexp").read_text(encoding="utf-8").split("\n")
            self.assertIn('(run-test "pkg/store/store_ext_test" :uses "pkg/store/store.go" :evidence passed :cases 1)', plan)
            self.assertIn('(run-test "pkg/store/other_ext_test" :uses "pkg/store/store.go" :evidence no-record :cases 0)', plan)
            self.assertIn('(test-case "pkg/store/store_ext_test" "example.com/app/pkg/store" "TestSave" :status passed)', plan)
            self.assertIn('(test-case "pkg/store/other_ext_test" "pkg/store" "TestOther" :status no-record)', plan)
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, SCHEMA))
            self.assertEqual("incomplete", record["tests"]["evidence"])
            self.assertEqual({"pkg/store/store_ext_test": "passed", "pkg/store/other_ext_test": "no-record"},
                             {item["test"]: item["status"] for item in record["tests"]["to-run"]})


if __name__ == "__main__":
    unittest.main()
