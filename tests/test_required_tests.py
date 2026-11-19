"""No test found is not complete evidence; the tests a change must pass are declared in a manifest."""
import dataclasses
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
import wingbox_evidence as we  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_evidence_record import RUN, SCHEMA, problems  # noqa: E402
from test_test_cases import suite  # noqa: E402
from test_test_evidence import PYTEST, with_tests  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

MANIFEST = """# The tests a change of these paths must pass.
billing:
  paths:
    - "src/models/**"
    - src/billing/invoice.py
  tests:
    - tests/test_billing.py
    - tests/test_orders.py::test_new

docs:
  paths:
  - docs/
  tests:
  - 'tests/test_refunds.py'
"""
GROUPS = (wt.Group("billing", ("src/models/**", "src/billing/invoice.py"), ("tests/test_billing.py", "tests/test_orders.py::test_new")),
          wt.Group("docs", ("docs/",), ("tests/test_refunds.py",)))


def manifest(groups: tuple = GROUPS, errors: tuple = ()) -> wt.Manifest:
    return wt.Manifest(".wingbox/required-tests.yaml", "base", "d" * 40, "9" * 64, groups, errors)


def required(*tests: str, group: str = "billing") -> tuple:
    return tuple(wt.Required(group, test) for test in tests)


def cases(*suites: tuple) -> tuple:
    """The cases of a report of several suites: (suite and class name, (case name, what the case element holds)...)."""
    return tuple(wt.parse_junit(b"<testsuites>" + b"".join(suite(name, *inside) for name, *inside in suites) + b"</testsuites>"))


class NoneFoundTest(unittest.TestCase):
    def test_no_test_to_run_is_not_complete_evidence(self) -> None:
        """/* REQ-ACTION-117: with test results given and no test to run, the test evidence is `none-found`, never `complete`: for a change no test uses, and for test results that hold no case; without test results it stays `none`; the output `tests-evidence` carries the same word */"""
        without_tests = report(test_cases=tuple(wt.parse_junit(PYTEST)))
        self.assertEqual(0, len(pr.tests_to_run(without_tests)))
        self.assertEqual("none-found", we.tests_summary(without_tests))
        self.assertEqual("none-found", we.tests_summary(report(test_cases=())))
        self.assertEqual("none", we.tests_summary(report()))
        self.assertEqual("none-found", pr.outputs(without_tests)["tests-evidence"])
        self.assertEqual("none", pr.outputs(report())["tests-evidence"])
        self.assertEqual("none-found", we.build_evidence(without_tests, RUN, {})["tests"]["evidence"])
        self.assertIn("Tests that use what changed: none found. The test reports say nothing about this change.", pr.build_comment(without_tests))
        self.assertNotIn("none found", pr.build_comment(report()))
        self.assertIn("(tests :evidence none-found)", pr.build_plan(without_tests).split("\n"))

    def test_require_tests_reports_a_change_without_a_test_and_fails_only_when_strict(self) -> None:
        """/* REQ-ACTION-118: `require-tests: true` passes a change no test uses and says so; `require-tests: strict` fails it when the change holds source code outside the tests, with or without test results, and passes a change of documents only */"""
        source = report(test_cases=())
        self.assertEqual(0, pr.tests_status(source, True))
        self.assertEqual(1, pr.tests_status(source, True, True))
        self.assertEqual(1, pr.tests_status(report(), True, True))
        self.assertEqual(0, pr.tests_status(source, False, True))
        documents = report(changed=(pr.ChangedFile("README.md", None), pr.ChangedFile("docs/notes.md", None)), test_cases=())
        self.assertEqual(0, pr.tests_status(documents, True, True))
        self.assertEqual("none-found", we.tests_summary(documents))
        said: list[str] = []
        self.assertEqual(0, pr.tests_failed(source, {"WINGBOX_REQUIRE_TESTS": "true"}, said.append))
        self.assertEqual(["Wingbox: require-tests: no test that uses what changed was found"], said)
        said.clear()
        self.assertEqual(1, pr.tests_failed(source, {"WINGBOX_REQUIRE_TESTS": "strict"}, said.append))
        self.assertEqual(["Wingbox: require-tests: no test that uses what changed was found, and the change holds source code (require-tests is strict)"], said)
        said.clear()
        self.assertEqual(0, pr.tests_failed(source, {}, said.append))
        self.assertEqual([], said)


class ManifestTest(unittest.TestCase):
    def test_the_manifest_is_read_as_groups_of_paths_and_tests(self) -> None:
        """/* REQ-ACTION-119: the manifest is a YAML file of named groups, each with a list `paths` and a list `tests`, one item per line, plain or quoted; comments on their own lines and blank lines are skipped; list items may be indented as deep as their key or deeper; the reader needs the standard library only */"""
        self.assertEqual((GROUPS, ()), wt.parse_manifest(MANIFEST))
        self.assertEqual((GROUPS, ()), wt.parse_manifest(MANIFEST.replace("\n", "\r\n")))
        self.assertEqual(((), ()), wt.parse_manifest("# nothing yet\n\n"))

    def test_a_manifest_that_is_not_in_the_subset_is_refused_with_the_line(self) -> None:
        """/* REQ-ACTION-120: a manifest with anything outside the subset is refused as a whole, with the line and the reason: a tab, a key other than `paths` and `tests`, a value after a key, a list in brackets, an unquoted value that starts with `*` or is a YAML word, a comment after a value, a group named twice, a list given twice, a group without tests, an item outside a list */"""
        for text, reason in (
                ("billing:\n\tpaths:\n", "line 2: a tab"),
                ("billing:\n  paths:\n    - src/**\n  owners:\n    - me\n", "line 4: unknown key `owners`"),
                ("billing:\n  paths: src/**\n", "line 2: write the items of `paths` on the lines below"),
                ("billing:\n  paths: [a, b]\n", "line 2: write the items of `paths` on the lines below"),
                ("billing:\n  paths:\n    - **/*.py\n  tests:\n    - tests/a.py\n", "line 3: quote this value"),
                ("billing:\n  paths:\n    - src/a.py\n  tests:\n    - true\n", "line 5: quote this value"),
                ("billing:\n  paths:\n    - src/a.py # all\n  tests:\n    - tests/a.py\n", "line 3: a comment must be on a line of its own"),
                ("billing:\n  paths:\n    - src/a.py\n  tests:\n    - tests/a.py\nbilling:\n  paths:\n    - b\n  tests:\n    - c\n", "line 6: group `billing` is named twice"),
                ("billing:\n  paths:\n    - a/b\n  paths:\n    - c/d\n  tests:\n    - t/e\n", "line 4: `paths` is given twice"),
                ("billing:\n  paths:\n    - src/a.py\n", "line 1: group `billing` has no tests"),
                ("billing:\n  tests:\n    - tests/a.py\n", "line 1: group `billing` has no paths"),
                ("billing:\n  - src/a.py\n", "line 2: an item outside `paths` and `tests`"),
                ("  paths:\n    - src/a.py\n", "line 1: a key outside a group"),
                ("billing\n", "line 1: not a group, a key or an item"),
                ("billing:\n  paths:\n    - \"src/a.py\n  tests:\n    - t/a.py\n", "line 3: a quote is not closed"),
                ("billing:\n  paths:\n    - \"src\\\\a.py\"\n  tests:\n    - t/a.py\n", "line 3: no backslash in a quoted value"),
        ):
            groups, errors = wt.parse_manifest(text)
            self.assertEqual((), groups, text)
            self.assertTrue(any(error.startswith(reason) for error in errors), (text, errors))

    def test_the_groups_whose_paths_the_change_touches_give_the_required_tests(self) -> None:
        """/* REQ-ACTION-121: a group applies when a changed file matches one of its paths: a glob (`*` and `**` cross folders), a folder ending in `/`, or a module (the file without its ending); the required tests of the groups that apply are listed once each, with their group, in the order of the manifest */"""
        self.assertEqual(required("tests/test_billing.py", "tests/test_orders.py::test_new"), wt.required_for(GROUPS, ["src/models/order.ts", "README.md"]))
        self.assertEqual(required("tests/test_billing.py", "tests/test_orders.py::test_new"), wt.required_for(GROUPS, ["src/billing/invoice.py"]))
        self.assertEqual(required("tests/test_refunds.py", group="docs"), wt.required_for(GROUPS, ["docs/guide/a.md"]))
        self.assertEqual((), wt.required_for(GROUPS, ["src/billing/other.py", "docs.md"]))
        both = wt.required_for(GROUPS + (wt.Group("again", ("src/models/order",), ("tests/test_billing.py", "tests/extra.py")),), ["src/models/order.ts", "docs/a.md"])
        self.assertEqual([("billing", "tests/test_billing.py"), ("billing", "tests/test_orders.py::test_new"), ("docs", "tests/test_refunds.py"),
                          ("again", "tests/extra.py")], [(item.group, item.test) for item in both])

    def test_a_required_test_is_a_file_or_one_case(self) -> None:
        """/* REQ-ACTION-122: a required test named as a file gets the cases of that file like a test to run; named `<file or class or Go folder>::<case>` it gets the cases of that name there (with their parameters `[..]` and subtests `/..`); it is passed only when every such case passed, and without a record when the reports hold none */"""
        found = cases(("tests.test_billing", ("test_total", ""), ("test_tax", "<skipped/>")),
                      ("tests.test_orders", ("test_new[1]", ""), ("test_new[2]", ""), ("test_newer", "<failure/>")),
                      ("example.com/app/pkg/billing", ("TestInvoice/zero", ""), ("TestRefund", "<failure/>")))
        got = wt.evidence(["tests/test_billing.py", "tests/test_orders.py::test_new", "tests.test_orders::test_newer", "tests/test_orders.py::test_old",
                           "pkg/billing::TestInvoice", "example.com/app/pkg/billing::TestRefund", "tests/test_billing.py::test_tax"], list(found))
        self.assertEqual([("tests/test_billing.py", wt.PARTIAL, 2), ("tests/test_orders.py::test_new", wt.PASSED, 2), ("tests.test_orders::test_newer", wt.FAILED, 1),
                          ("tests/test_orders.py::test_old", wt.NO_RECORD, 0), ("pkg/billing::TestInvoice", wt.PASSED, 1),
                          ("example.com/app/pkg/billing::TestRefund", wt.FAILED, 1), ("tests/test_billing.py::test_tax", wt.SKIPPED, 1)],
                         [(item.test, item.status, item.cases) for item in got])
        self.assertEqual((wt.CaseResult("tests.test_orders", "test_new[1]", wt.PASSED), wt.CaseResult("tests.test_orders", "test_new[2]", wt.PASSED)), got[1].records)


class RequiredEvidenceTest(unittest.TestCase):
    def with_required(self, found: tuple, **changes) -> "pr.Report":
        return report(test_cases=found, tests_manifest=manifest(), required_tests=required("tests/test_billing.py", "tests/test_orders.py::test_new"), **changes)

    def test_a_required_test_without_a_passed_record_makes_the_evidence_incomplete(self) -> None:
        """/* REQ-ACTION-123: whatever the graph found, a required test that is not in the reports or was skipped makes the test evidence `incomplete`, a failed one makes it `failed`, and with every required test passed it is `complete`, also when the graph found no test to run; without test results it is `none`; a manifest that could not be read makes it `incomplete` */"""
        self.assertEqual(0, len(pr.tests_to_run(self.with_required(()))))
        missing = self.with_required(cases(("tests.test_billing", ("test_total", ""))))
        self.assertEqual("incomplete", we.tests_summary(missing))
        self.assertEqual([wt.PASSED, wt.NO_RECORD], [item.status for item in pr.required_evidence(missing)])
        skipped = self.with_required(cases(("tests.test_billing", ("test_total", "")), ("tests.test_orders", ("test_new", "<skipped/>"))))
        self.assertEqual("incomplete", we.tests_summary(skipped))
        failed = self.with_required(cases(("tests.test_billing", ("test_total", "<error/>")), ("tests.test_orders", ("test_new", ""))))
        self.assertEqual("failed", we.tests_summary(failed))
        present = self.with_required(cases(("tests.test_billing", ("test_total", "")), ("tests.test_orders", ("test_new", ""))))
        self.assertEqual("complete", we.tests_summary(present))
        self.assertEqual("incomplete", we.tests_summary(self.with_required(())))
        self.assertEqual("none", we.tests_summary(dataclasses.replace(present, test_cases=None)))
        self.assertIsNone(pr.required_evidence(dataclasses.replace(present, test_cases=None)))
        unread = report(test_cases=(), tests_manifest=manifest((), ("line 2: a tab in the indentation",)))
        self.assertEqual("incomplete", we.tests_summary(unread))
        graph_passed = with_tests(test_cases=cases(("tests/order.test.ts", ("a", "")), ("tests/invoice.test.ts", ("b", ""))),
                                  tests_manifest=manifest(), required_tests=required("tests/test_billing.py"))
        self.assertEqual(["passed", "passed"], [item.status for item in pr.test_evidence(graph_passed)])
        self.assertEqual("incomplete", we.tests_summary(graph_passed))

    def test_require_tests_fails_on_a_required_test_that_did_not_pass(self) -> None:
        """/* REQ-ACTION-124: `require-tests` fails the job when a required test has no passed record (not in the reports, skipped, failed, or no test results at all) or when the manifest could not be read, says how many, and passes when every required test passed */"""
        present = self.with_required(cases(("tests.test_billing", ("test_total", "")), ("tests.test_orders", ("test_new", ""))))
        missing = self.with_required(cases(("tests.test_billing", ("test_total", ""))))
        self.assertEqual(0, pr.tests_status(present, True))
        self.assertEqual(0, pr.tests_status(present, True, True))
        self.assertEqual(1, pr.tests_status(missing, True))
        self.assertEqual(0, pr.tests_status(missing, False))
        self.assertEqual(1, pr.tests_status(dataclasses.replace(present, test_cases=None), True))
        self.assertEqual(1, pr.tests_status(report(test_cases=(), tests_manifest=manifest((), ("line 1: not a group, a key or an item",))), True))
        said: list[str] = []
        self.assertEqual(1, pr.tests_failed(missing, {"WINGBOX_REQUIRE_TESTS": "true"}, said.append))
        self.assertEqual(["Wingbox: require-tests: 1 required test(s) of .wingbox/required-tests.yaml have no passing record in the test reports given"], said)

    def test_the_plan_the_comment_and_the_outputs_name_the_required_tests(self) -> None:
        """/* REQ-ACTION-125: each required test is a `(required-test "<test>" :group <group> :evidence <status> :cases N)` line of the plan with its cases as `test-case` lines, the manifest is a `(required-tests "<path>" :from base|head :commit <sha> :sha256 <digest>)` line and each of its errors a `(required-tests-error ...)` line; the comment counts the required tests and names those that did not pass, also for a change of documents only; the outputs `tests-required` and `tests-required-passed` count them */"""
        missing = self.with_required(cases(("tests.test_billing", ("test_total", ""))))
        plan = pr.build_plan(missing).split("\n")
        self.assertIn('(required-test "tests/test_billing.py" :group billing :evidence passed :cases 1)', plan)
        self.assertIn('(required-test "tests/test_orders.py::test_new" :group billing :evidence no-record :cases 0)', plan)
        self.assertIn('(test-case "tests/test_billing.py" "tests.test_billing" "test_total" :status passed)', plan)
        self.assertIn(f'(required-tests ".wingbox/required-tests.yaml" :from base :commit {"d" * 40} :sha256 {"9" * 64})', plan)
        self.assertIn("(tests :evidence incomplete)", plan)
        comment = pr.build_comment(missing)
        self.assertIn("Required tests (2, by `.wingbox/required-tests.yaml` of the base): 1 passed, 1 without a record in the test report "
                      "(`tests/test_orders.py::test_new`).", comment)
        values = pr.outputs(missing)
        self.assertEqual(("2", "1", "incomplete"), (values["tests-required"], values["tests-required-passed"], values["tests-evidence"]))
        self.assertEqual(("0", ""), (pr.outputs(report())["tests-required"], pr.outputs(report())["tests-required-passed"]))
        no_results = dataclasses.replace(missing, test_cases=None)
        self.assertIn('(required-test "tests/test_billing.py" :group billing :evidence none)', pr.build_plan(no_results).split("\n"))
        self.assertIn("Required tests (2, by `.wingbox/required-tests.yaml` of the base): `tests/test_billing.py`, `tests/test_orders.py::test_new`.",
                      pr.build_comment(no_results))
        documents = dataclasses.replace(missing, changed=(pr.ChangedFile("docs/guide.md", None),), introduced=(), required_tests=required("tests/test_refunds.py", group="docs"))
        self.assertIn("nothing to check.\n\nRequired tests (1, by `.wingbox/required-tests.yaml` of the base): 1 without a record in the test report "
                      "(`tests/test_refunds.py`).", pr.build_comment(documents))
        unread = report(test_cases=(), tests_manifest=manifest((), ("line 2: a tab in the indentation",)))
        self.assertIn('(required-tests-error ".wingbox/required-tests.yaml" :reason "line 2: a tab in the indentation")', pr.build_plan(unread).split("\n"))
        self.assertIn("The manifest of required tests `.wingbox/required-tests.yaml` could not be read (line 2: a tab in the indentation)",
                      pr.build_comment(unread))

    def test_the_record_lists_each_required_test_with_its_group_and_its_status(self) -> None:
        """/* REQ-ACTION-126: the evidence record lists each required test with its group, its status, the number of its cases and the cases, and names the manifest: its path, where it was read (the base commit or the checked files), the commit, its SHA-256 and its errors; the record meets schema/wingbox-evidence-2.schema.json, with and without a manifest */"""
        missing = self.with_required(cases(("tests.test_billing", ("test_total", ""))))
        record = we.build_evidence(missing, RUN, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual([{"group": "billing", "test": "tests/test_billing.py", "status": "passed", "cases": 1,
                           "records": [{"where": "tests.test_billing", "name": "test_total", "status": "passed"}]},
                          {"group": "billing", "test": "tests/test_orders.py::test_new", "status": "no-record", "cases": 0, "records": []}],
                         record["tests"]["required"])
        self.assertEqual({"path": ".wingbox/required-tests.yaml", "source": "base", "commit": "d" * 40, "sha256": "9" * 64, "errors": []},
                         record["tests"]["manifest"])
        self.assertEqual("incomplete", record["tests"]["evidence"])
        plain = we.build_evidence(report(), RUN, {})
        self.assertEqual([], problems(plain, SCHEMA))
        self.assertEqual(([], {"path": "", "source": "none", "commit": "", "sha256": "", "errors": []}), (plain["tests"]["required"], plain["tests"]["manifest"]))
        self.assertEqual("none", we.build_evidence(dataclasses.replace(missing, test_cases=None), RUN, {})["tests"]["required"][0]["status"])


class ManifestFileTest(unittest.TestCase):
    def test_the_manifest_of_the_base_commit_is_the_one_that_counts(self) -> None:
        """/* REQ-ACTION-127: the manifest is read from the base commit when that commit holds it, so a change cannot drop a requirement it is judged by; a manifest only the checked files hold is read from them; without a manifest at the default path there is none; a path given as input that no commit and no file holds, or that leaves the repository, is an error of the manifest */"""
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            self.assertIsNone(pr.read_manifest(str(repo), "", ""))
            base = make_fixture.commit(repo, {"src/a.py": "A = 1\n", ".wingbox/required-tests.yaml": MANIFEST}, "base")
            weaker = "billing:\n  paths:\n    - src/none/**\n  tests:\n    - tests/test_billing.py\n"
            make_fixture.commit(repo, {"src/a.py": "A = 2\n", ".wingbox/required-tests.yaml": weaker, "ci/tests.yaml": weaker}, "head")
            read = pr.read_manifest(str(repo), "", base)
            self.assertEqual((".wingbox/required-tests.yaml", "base", base, hashlib.sha256(MANIFEST.encode()).hexdigest(), GROUPS, ()),
                             (read.path, read.source, read.commit, read.sha256, read.groups, read.errors))
            added = pr.read_manifest(str(repo), "ci/tests.yaml", base)
            self.assertEqual(("ci/tests.yaml", "head", "", hashlib.sha256(weaker.encode()).hexdigest(), 1, ()),
                             (added.path, added.source, added.commit, added.sha256, len(added.groups), added.errors))
            absent = pr.read_manifest(str(repo), "ci/missing.yaml", base)
            self.assertEqual(("ci/missing.yaml", "none", (), ("the file is not in the base commit and not in the checkout",)),
                             (absent.path, absent.source, absent.groups, absent.errors))
            outside = pr.read_manifest(str(repo), "../elsewhere.yaml", base)
            self.assertEqual(("none", ("the path leaves the repository",)), (outside.source, outside.errors))

    def test_the_inputs_and_outputs_are_documented(self) -> None:
        """/* REQ-ACTION-128: action.yml has the input `required-tests` (the path of the manifest, default `.wingbox/required-tests.yaml`) and hands it to the script; `require-tests` takes `strict`; the outputs `tests-evidence`, `tests-required` and `tests-required-passed` exist; README.md documents them, the format of the manifest and `none-found`; the shell-runner GitLab template hands the variable to the container */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  required-tests:\n    description: [^\n]+\n    default: ""\n')
        self.assertIn("WINGBOX_REQUIRED_TESTS: ${{ inputs.required-tests }}", action)
        self.assertIn("strict", action.split("\n  require-tests:\n")[1].split("\n")[0])
        for name in ("tests-evidence", "tests-required", "tests-required-passed"):
            self.assertRegex(action, rf"\n  {name}:\n    description: [^\n]+\n    value: \$\{{\{{ steps\.check\.outputs\.{name} \}}\}}\n")
            self.assertIn(f"`{name}`", readme)
        for needed in ("`required-tests`", ".wingbox/required-tests.yaml", "`none-found`", "require-tests: strict", "WINGBOX_REQUIRED_TESTS",
                       "WINGBOX_TESTS_EVIDENCE"):
            self.assertIn(needed, readme)
        self.assertEqual(wt.MANIFEST_PATH, ".wingbox/required-tests.yaml")
        self.assertIn("--env WINGBOX_REQUIRED_TESTS", (ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8"))
        example = readme.split("```yaml\n# .wingbox/required-tests.yaml\n")[1].split("```")[0]
        groups, errors = wt.parse_manifest(example)
        self.assertEqual(((), True), (errors, len(groups) > 0))


TESTED = {"tests/__init__.py": "", "tests/test_billing.py": "from app.services.billing import bill\n\n\ndef test_bill():\n    assert bill(None) is None\n",
          ".wingbox/required-tests.yaml": "billing:\n  paths:\n    - app/services/**\n  tests:\n    - tests/test_billing.py\n    - tests/test_refund.py::test_refund\n"}
JUNIT = ('<testsuites><testsuite name="pytest"><testcase classname="tests.test_billing" name="test_bill"/>{more}</testsuite></testsuites>')


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class EndToEndTest(unittest.TestCase):
    def test_the_whole_script_holds_a_change_to_the_tests_its_base_requires(self) -> None:
        """/* REQ-ACTION-129: run as a job would run it on a repository whose base commit holds a manifest: with a report that lacks one required test the record says `incomplete`, lists the required tests with their group and status, names the manifest as read from the base, and `require-tests` fails the job; with the missing test in the report the same run says `complete` and passes */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, out = Path(tmp) / "repo", Path(tmp) / "out"
            repo.mkdir()
            out.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, {**make_fixture.BASE, **TESTED}, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            (repo / "test-results").mkdir()
            report_file = repo / "test-results" / "junit.xml"
            report_file.write_text(JUNIT.format(more=""), encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_")) or key == "WINGBOX_JOBS"}
            env.update(WINGBOX_VERSION="test", WINGBOX_TEST_RESULTS="test-results/*.xml", WINGBOX_REQUIRE_TESTS="true", GITHUB_OUTPUT=str(out / "output.txt"))
            command = [sys.executable, str(ROOT / "wingbox_pr.py"), "--root", str(repo), "--base", base, "--head", head, "--out", str(out)]
            done = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            self.assertIn("require-tests: 1 required test(s) of .wingbox/required-tests.yaml have no passing record", done.stdout)
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, SCHEMA))
            self.assertEqual("incomplete", record["tests"]["evidence"])
            self.assertEqual([("billing", "tests/test_billing.py", "passed"), ("billing", "tests/test_refund.py::test_refund", "no-record")],
                             [(item["group"], item["test"], item["status"]) for item in record["tests"]["required"]])
            self.assertEqual((".wingbox/required-tests.yaml", "base", base), tuple(record["tests"]["manifest"][key] for key in ("path", "source", "commit")))
            self.assertEqual("head", record["subject"]["tree"])
            (out / "output.txt").unlink()
            report_file.write_text(JUNIT.format(more='<testcase classname="tests.test_refund" name="test_refund"/>'), encoding="utf-8")
            again = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True)
            self.assertEqual(0, again.returncode, again.stdout + again.stderr)
            outputs = dict(line.split("=", 1) for line in (out / "output.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual(("complete", "2", "2"), (outputs["tests-evidence"], outputs["tests-required"], outputs["tests-required-passed"]))


if __name__ == "__main__":
    unittest.main()
