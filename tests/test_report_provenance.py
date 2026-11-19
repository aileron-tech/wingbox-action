"""Where a test report comes from: what it states, what the job knows, and when it is refused."""
import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_gitlab  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_evidence_record import RUN, SCHEMA, problems  # noqa: E402
from test_test_evidence import with_tests  # noqa: E402

COMMIT = "c" * 40
# The report of the issue: written long ago, on another machine, for another commit.
OLD = (b'<testsuites><testsuite name="tests/order.test.ts" timestamp="2019-01-01T00:00:00" hostname="laptop">'
       b'<properties><property name="commit" value="0000000"/></properties>'
       b'<testcase classname="tests/order.test.ts" name="adds"/></testsuite></testsuites>')
FRESH = b'<testsuite name="tests/order.test.ts" timestamp="2026-03-04T10:00:30Z"><testcase classname="tests/order.test.ts" name="adds"/></testsuite>'
ORIGIN = wt.Origin(commit=COMMIT, started="2026-03-04T10:00:00Z", run_id="77", attempt="2")
NOTHING = wt.Stated((), ())


def refusals(stated: wt.Stated = NOTHING, modified: str = "2026-03-04T10:00:40Z", tracked: str = "untracked", digest: str = "5" * 64,
             sidecar: "wt.Sidecar | None" = None, sidecar_error: str = "", origin: wt.Origin = ORIGIN) -> tuple:
    return wt.refusals(stated, modified, tracked, digest, sidecar, sidecar_error, origin)


class StatedTest(unittest.TestCase):
    def test_what_a_report_states_of_itself_is_read(self) -> None:
        """/* REQ-ACTION-130: a report gives, beside its cases, the timestamps of its suites as written and the commits it names: the value of a property or of an attribute of a suite whose name means a commit (`commit`, `git.commit`, `GITHUB_SHA`, `CI_COMMIT_SHA`, `revision`, `sha`), when it is 7 to 64 hexadecimal digits */"""
        cases, stated = wt.parse_report(OLD)
        self.assertEqual((1, ("2019-01-01T00:00:00",), ("0000000",)), (len(cases), stated.timestamps, stated.commits))
        self.assertEqual(cases, wt.parse_junit(OLD))
        many = (b'<testsuites timestamp="2026-03-04T10:00:00+02:00" GITHUB_SHA="' + b"a" * 40 + b'"><testsuite name="a" timestamp="2026-03-04T08:00:05Z" revision="42">'
                b'<properties><property name="git.commit" value="abcdef1"/><property name="python" value="3.12"/><property name="build" value="deadbeef"/></properties>'
                b'</testsuite><testsuite name="b"><properties><property name="CI_COMMIT_SHA" value="abcdef1"/></properties></testsuite></testsuites>')
        self.assertEqual(wt.Stated(("2026-03-04T10:00:00+02:00", "2026-03-04T08:00:05Z"), ("a" * 40, "abcdef1")), wt.parse_report(many)[1])
        self.assertEqual(NOTHING, wt.parse_report(b'<testsuite name="x"><testcase name="a"/></testsuite>')[1])

    def test_the_record_and_the_plan_state_what_the_job_knows_of_each_report(self) -> None:
        """/* REQ-ACTION-130: the record names the run with its attempt and its job, and under `tests.provenance` the commit checked, the start of the run, the run and the attempt a sidecar must name, the test command verbatim and whether a sidecar is required; each report carries its status, the reasons of a refusal, its timestamp, its modification time, whether git tracks it, the commits it names and the state of its sidecar; the plan has a `test-provenance` line, a `test-command` line and a `test-report-origin` line per report; the record meets the schema */"""
        command = 'python -P -m pytest -k "billing or orders" --junitxml=test-results/junit.xml'
        accepted = wt.TestReport("test-results/junit.xml", "e" * 64, 1, (), "2026-03-04T10:00:30Z", "2026-03-04T10:00:40Z", "untracked", (COMMIT,), "matches", command)
        refused = wt.TestReport("old.xml", "f" * 64, 1, ("it is a file tracked by git", "its timestamp 2019-01-01T00:00:00 is before the start of the run 2026-03-04T10:00:00Z"),
                                "2019-01-01T00:00:00", "2026-03-04T09:00:00Z", "tracked", ("0000000",))
        some = with_tests(test_cases=tuple(wt.parse_junit(FRESH)), test_reports=(accepted, refused),
                          test_origin=dataclasses.replace(ORIGIN, command=command, require_sidecar=True))
        run = dataclasses.replace(RUN, attempt="2", job="check")
        record = we.build_evidence(some, run, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual(("7", "2", "check"), (record["run"]["id"], record["run"]["attempt"], record["run"]["job"]))
        self.assertEqual({"commit": COMMIT, "started": "2026-03-04T10:00:00Z", "run": "77", "attempt": "2", "command": command, "sidecar-required": True},
                         record["tests"]["provenance"])
        self.assertEqual([{"path": "test-results/junit.xml", "sha256": "e" * 64, "cases": 1, "status": "accepted", "reasons": [],
                           "timestamp": "2026-03-04T10:00:30Z", "modified": "2026-03-04T10:00:40Z", "tracked": "untracked", "commits": [COMMIT],
                           "sidecar": "matches", "sidecar-command": command},
                          {"path": "old.xml", "sha256": "f" * 64, "cases": 1, "status": "refused",
                           "reasons": ["it is a file tracked by git", "its timestamp 2019-01-01T00:00:00 is before the start of the run 2026-03-04T10:00:00Z"],
                           "timestamp": "2019-01-01T00:00:00", "modified": "2026-03-04T09:00:00Z", "tracked": "tracked", "commits": ["0000000"],
                           "sidecar": "none", "sidecar-command": ""}], record["tests"]["reports"])
        plan = pr.build_plan(some).split("\n")
        self.assertIn(f"(test-provenance :commit {COMMIT} :started 2026-03-04T10:00:00Z :run 77 :attempt 2 :sidecar required)", plan)
        self.assertIn('(test-command "python -P -m pytest -k \\"billing or orders\\" --junitxml=test-results/junit.xml")', plan)
        self.assertIn(f'(test-report "test-results/junit.xml" :sha256 {"e" * 64} :cases 1)', plan)
        self.assertIn(f'(test-report-origin "test-results/junit.xml" :modified 2026-03-04T10:00:40Z :timestamp "2026-03-04T10:00:30Z" :tracked untracked :sidecar matches :commits ({COMMIT}))', plan)
        self.assertIn(f'(test-report-refused "old.xml" :sha256 {"f" * 64} :cases 1 :reason "it is a file tracked by git; its timestamp 2019-01-01T00:00:00 is before the start of the run 2026-03-04T10:00:00Z")', plan)
        plain = we.build_evidence(with_tests(), RUN, {})
        self.assertEqual([], problems(plain, SCHEMA))
        self.assertEqual({"commit": "", "started": "", "run": "", "attempt": "", "command": "", "sidecar-required": False}, plain["tests"]["provenance"])
        self.assertNotIn("test-provenance", pr.build_plan(with_tests()))
        github = pr.github_run({"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_JOB": "check"}, "5")
        self.assertEqual(("77", "2", "check"), (github.run_id, github.attempt, github.job))
        gitlab = wingbox_gitlab.gitlab_run({"CI_JOB_ID": "5", "CI_JOB_NAME": "wingbox", "CI_PROJECT_PATH": "g/p"})
        self.assertEqual(("5", "", "wingbox"), (gitlab.run_id, gitlab.attempt, gitlab.job))


class RefusalTest(unittest.TestCase):
    def test_a_stale_report_a_tracked_report_and_a_report_of_another_commit_are_refused(self) -> None:
        """/* REQ-ACTION-131: a report is refused, with the reason, when git tracks it, when its modification time or the timestamp of one of its suites is before the start of the run, or when it names a commit other than the commit checked; a report that matches is accepted. A timestamp without a zone is refused only when it is before the start both as UTC and as the local time of the machine; a start dated by another machine allows its tolerance; without a known start or a commit checked those checks are not made */"""
        self.assertEqual((), refusals(wt.Stated(("2026-03-04T10:00:30Z",), (COMMIT, "ccccccc"))))
        self.assertEqual(("it is a file tracked by git",), refusals(tracked="tracked"))
        self.assertEqual((), refusals(tracked="unknown"))
        self.assertEqual(("it was last modified 2026-03-04T09:59:59Z, before the start of the run 2026-03-04T10:00:00Z",), refusals(modified="2026-03-04T09:59:59Z"))
        self.assertEqual(("its timestamp 2019-01-01T00:00:00 is before the start of the run 2026-03-04T10:00:00Z",), refusals(wt.Stated(("2026-03-04T10:00:30Z", "2019-01-01T00:00:00"), ())))
        self.assertEqual(("its timestamp 2026-03-04T11:59:59+02:00 is before the start of the run 2026-03-04T10:00:00Z",), refusals(wt.Stated(("2026-03-04T11:59:59+02:00",), ())))
        self.assertEqual((), refusals(wt.Stated(("2026-03-04T12:00:01+02:00", "2026-03-04T10:00:00.250000", "not a time"), ())))
        local = dataclasses.replace(ORIGIN, utc_offset=-8 * 3600)
        self.assertEqual((), refusals(wt.Stated(("2026-03-04T02:00:30",), ()), origin=local))
        self.assertEqual(1, len(refusals(wt.Stated(("2026-03-04T01:59:30",), ()), origin=local)))
        self.assertEqual(1, len(refusals(wt.Stated(("2026-03-04T02:00:30",), ()))))
        skewed = dataclasses.replace(ORIGIN, tolerance=300)
        self.assertEqual((), refusals(wt.Stated(("2026-03-04T09:56:00Z",), ()), modified="2026-03-04T09:55:01Z", origin=skewed))
        self.assertEqual(2, len(refusals(wt.Stated(("2026-03-04T09:54:00Z",), ()), modified="2026-03-04T09:54:59Z", origin=skewed)))
        self.assertEqual((f"it names commit 0000000, and the commit checked is {COMMIT}",), refusals(wt.Stated((), ("0000000", COMMIT))))
        self.assertEqual((f"it names commit {'d' * 40}, and the commit checked is {COMMIT}",), refusals(wt.Stated((), ("d" * 40,))))
        by_hand = wt.Origin()
        self.assertEqual((), refusals(wt.parse_report(OLD)[1], modified="2019-01-01T00:00:10Z", origin=by_hand))
        self.assertEqual(("its timestamp 2019-01-01T00:00:00 is before the start of the run 2026-03-04T10:00:00Z",
                          f"it names commit 0000000, and the commit checked is {COMMIT}"), refusals(wt.parse_report(OLD)[1]))

    def test_a_refused_report_counts_as_no_evidence_and_is_named(self) -> None:
        """/* REQ-ACTION-131: the cases of a refused report are not counted: a test to run that only such a report holds has no record; the report is read with its SHA-256, its cases and the reasons, the comment names it with them, the output `test-reports-refused` counts it, and a report that matches gives its evidence */"""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "old.xml").write_bytes(OLD)
            origin = dataclasses.replace(ORIGIN, started=pr.utc(time.time() - 60), run_id="", attempt="")
            cases, reports, errors = pr.read_test_results(folder, ["*.xml"], origin)
            self.assertEqual(((), ()), (cases, errors))
            self.assertEqual(("old.xml", hashlib.sha256(OLD).hexdigest(), 1, "2019-01-01T00:00:00", ("0000000",), "none"),
                             (reports[0].path, reports[0].sha256, reports[0].cases, reports[0].timestamp, reports[0].commits, reports[0].sidecar))
            self.assertEqual((f"its timestamp 2019-01-01T00:00:00 is before the start of the run {origin.started}", f"it names commit 0000000, and the commit checked is {COMMIT}"),
                             reports[0].refused)
            self.assertRegex(reports[0].modified, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            refused = with_tests(test_cases=cases, test_reports=reports, test_origin=origin)
            self.assertEqual([wt.NO_RECORD, wt.NO_RECORD], [item.status for item in pr.test_evidence(refused)])
            self.assertEqual(("incomplete", "1"), (pr.outputs(refused)["tests-evidence"], pr.outputs(refused)["test-reports-refused"]))
            self.assertIn(f"Test reports refused, which do not count as evidence: `old.xml` (its timestamp 2019-01-01T00:00:00 is before the start of the run {origin.started}; "
                          f"it names commit 0000000, and the commit checked is {COMMIT}).", pr.build_comment(refused))
            (Path(folder) / "fresh.xml").write_bytes(FRESH.replace(b"2026-03-04T10:00:30Z", pr.utc(time.time()).encode()))
            cases, reports, errors = pr.read_test_results(folder, ["*.xml"], origin)
            self.assertEqual([("fresh.xml", ()), ("old.xml", 2)], [(read.path, read.refused if not read.refused else len(read.refused)) for read in reports])
            self.assertEqual(["adds"], [case.name for case in cases])
            accepted = with_tests(test_cases=cases, test_reports=reports, test_origin=origin)
            self.assertEqual([wt.NO_RECORD, wt.PASSED], [item.status for item in pr.test_evidence(accepted)])
            self.assertEqual("", pr.outputs(with_tests())["test-reports-refused"])
            unjudged = pr.read_test_results(folder, ["old.xml"])
            self.assertEqual((1, [wt.TestReport("old.xml", hashlib.sha256(OLD).hexdigest(), 1)]), (len(unjudged[0]), list(unjudged[1])))


class SidecarTest(unittest.TestCase):
    def sidecar(self, **changes: str) -> wt.Sidecar:
        return dataclasses.replace(wt.Sidecar(COMMIT, "77", "2", "pytest", "5" * 64), **changes)

    def test_a_sidecar_is_a_small_json_file_of_five_values(self) -> None:
        """/* REQ-ACTION-132: the sidecar of a report (`<report>.run.json`) is a JSON object with `commit`, `run-id`, `attempt`, `command` and `sha256`, texts or whole numbers; a file that is not such an object is not read, with the reason */"""
        text = json.dumps({"commit": COMMIT, "run-id": 77, "attempt": "2", "command": "pytest", "sha256": "5" * 64}).encode()
        self.assertEqual((self.sidecar(), ""), wt.parse_sidecar(text))
        for data, reason in ((b"{", "not JSON"), (b"[]", "not a JSON object"), (b'{"commit": "c"}', "`run-id` is missing"),
                             (json.dumps({"commit": COMMIT, "run-id": 7.5, "attempt": "2", "command": "x", "sha256": "5" * 64}).encode(), "`run-id` is not a text or a whole number"),
                             (b" " * (wt.SIDECAR_LIMIT + 1), "larger than")):
            read, error = wt.parse_sidecar(data)
            self.assertIsNone(read)
            self.assertIn(reason, error)
        self.assertEqual("junit.xml.run.json", wt.sidecar_name("junit.xml"))

    def test_a_report_without_a_matching_sidecar_is_refused_when_provenance_is_required(self) -> None:
        """/* REQ-ACTION-132: a sidecar matches when its SHA-256 is that of the report, its commit is the commit checked and its run and attempt are those of this run (as far as the job knows them); a report with a sidecar that differs or cannot be read is refused in any case, and with `require-test-provenance` a report without a sidecar is refused too; a sidecar that matches and names this run stands in for the comparison of times, so a report of an earlier job of the same run is accepted */"""
        strict = dataclasses.replace(ORIGIN, require_sidecar=True)
        self.assertEqual(("matches", ()), wt.sidecar_state("5" * 64, self.sidecar(), "", ORIGIN))
        self.assertEqual((), refusals(sidecar=self.sidecar(), origin=strict))
        self.assertEqual(("it has no sidecar (`<report>.run.json`), which require-test-provenance asks for",), refusals(origin=strict))
        self.assertEqual((), refusals())
        self.assertEqual(("none", ()), wt.sidecar_state("5" * 64, None, "", strict))
        for changed, reason in (({"sha256": "6" * 64}, "its sidecar holds the SHA-256 of another file"),
                                ({"commit": "d" * 40}, f"its sidecar names commit {'d' * 40}, and the commit checked is {COMMIT}"),
                                ({"run_id": "76"}, "its sidecar names run 76 attempt 2, and this is run 77 attempt 2"),
                                ({"attempt": "1"}, "its sidecar names run 77 attempt 1, and this is run 77 attempt 2")):
            self.assertEqual(("differs", (reason,)), wt.sidecar_state("5" * 64, self.sidecar(**changed), "", ORIGIN))
            self.assertEqual((reason,), refusals(sidecar=self.sidecar(**changed)))
        self.assertEqual(("unreadable", ("its sidecar could not be read: not JSON",)), wt.sidecar_state("5" * 64, None, "not JSON", ORIGIN))
        self.assertEqual(("its sidecar could not be read: not JSON",), refusals(sidecar_error="not JSON"))
        earlier_job = dict(stated=wt.Stated(("2026-03-04T09:50:00Z",), (COMMIT,)), modified="2026-03-04T09:50:10Z")
        self.assertEqual(2, len(refusals(**earlier_job)))
        self.assertEqual((), refusals(sidecar=self.sidecar(), **earlier_job))
        by_hand = wt.Origin(commit=COMMIT, started="2026-03-04T10:00:00Z")
        self.assertEqual(("matches", ()), wt.sidecar_state("5" * 64, self.sidecar(), "", by_hand))
        self.assertEqual(2, len(refusals(sidecar=self.sidecar(), origin=by_hand, **earlier_job)))
        self.assertEqual(("matches", ()), wt.sidecar_state("5" * 64, self.sidecar(commit="ccccccc"), "", ORIGIN))


class FilesTest(unittest.TestCase):
    def test_the_job_dates_the_run_and_asks_git_and_the_file_system_about_each_report(self) -> None:
        """/* REQ-ACTION-133: the start of the run is the time the runner wrote the event file of the job (GitHub), or the creation of the pipeline with a tolerance for the other clock (GitLab), and not known by hand; the origin carries the commit checked, the run and the attempt, the test command verbatim and `require-test-provenance`; a report git tracks is refused and an untracked one beside it is not; in a folder outside git the state is `unknown`; the sidecar beside a report is read, and a pattern never takes a sidecar for a report */"""
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / "event.json"
            event.write_text("{}", encoding="utf-8")
            os.utime(event, (1772618400, 1772618400))     # 2026-03-04T10:00:00Z
            self.assertEqual(("2026-03-04T10:00:00Z", 0), pr.run_started({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(event)}))
            self.assertEqual(("2026-03-04T09:00:00Z", 300), pr.run_started({"CI_JOB_ID": "5", "CI_PIPELINE_CREATED_AT": "2026-03-04T10:00:00+01:00",
                                                                           "CI_JOB_STARTED_AT": "2026-03-04T09:30:00Z"}))
            self.assertEqual(("2026-03-04T09:30:00Z", 300), pr.run_started({"CI_JOB_ID": "5", "CI_JOB_STARTED_AT": "2026-03-04T09:30:00Z"}))
            self.assertEqual(("", 0), pr.run_started({}))
            self.assertEqual(("", 0), pr.run_started({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(Path(folder) / "none.json")}))
            checked = with_tests(tree=pr.Tree(COMMIT, "head"))
            origin = pr.test_origin({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(event), "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2",
                                     "WINGBOX_TEST_COMMAND": "go test ./...  -run 'Test.*'", "WINGBOX_REQUIRE_TEST_PROVENANCE": "true"}, checked)
            self.assertEqual((COMMIT, "2026-03-04T10:00:00Z", 0, "77", "2", "go test ./...  -run 'Test.*'", True),
                             (origin.commit, origin.started, origin.tolerance, origin.run_id, origin.attempt, origin.command, origin.require_sidecar))
            pipeline = pr.test_origin({"CI_JOB_ID": "5", "CI_PIPELINE_ID": "900", "CI_PIPELINE_CREATED_AT": "2026-03-04T10:00:00Z"}, with_tests())
            self.assertEqual(("", "2026-03-04T10:00:00Z", 300, "900", "", "", False),
                             (pipeline.commit, pipeline.started, pipeline.tolerance, pipeline.run_id, pipeline.attempt, pipeline.command, pipeline.require_sidecar))

            outside = Path(folder) / "plain"
            outside.mkdir()
            (outside / "junit.xml").write_bytes(FRESH)
            self.assertEqual(["unknown"], [read.tracked for read in pr.read_test_results(str(outside), ["*.xml"], wt.Origin())[1]])

            repo = Path(folder) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            make_fixture.commit(repo, {"reports/committed.xml": FRESH.decode(), "src/a.py": "A = 1\n"}, "base")
            (repo / "reports" / "written.xml").write_bytes(FRESH)
            digest = hashlib.sha256(FRESH).hexdigest()
            (repo / "reports" / "written.xml.run.json").write_text(json.dumps({"commit": COMMIT, "run-id": "77", "attempt": 2, "command": "pytest", "sha256": digest}),
                                                                   encoding="utf-8")
            (repo / "reports" / "committed.xml.run.json").write_text("{", encoding="utf-8")
            cases, reports, errors = pr.read_test_results(str(repo), ["reports/*"], wt.Origin(commit=COMMIT, run_id="77", attempt="2"))
            self.assertEqual([], list(errors))
            self.assertEqual([("reports/committed.xml", "tracked", "unreadable", 2), ("reports/written.xml", "untracked", "matches", 0)],
                             [(read.path, read.tracked, read.sidecar, len(read.refused)) for read in reports])
            self.assertEqual("it is a file tracked by git", reports[0].refused[0])
            self.assertEqual(("pytest", 1), (reports[1].sidecar_command, len(cases)))

    def test_the_inputs_and_outputs_are_documented(self) -> None:
        """/* REQ-ACTION-133: action.yml has the inputs `test-command` and `require-test-provenance` (off by default) and the output `test-reports-refused`, and hands the inputs to the script; README.md documents them, the refusals and the sidecar; the shell-runner GitLab template hands the container the variables of the pipeline and of the two settings */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  test-command:\n    description: [^\n]+\n    default: ""\n')
        self.assertRegex(action, r'\n  require-test-provenance:\n    description: [^\n]+\n    default: "false"\n')
        for name in ("WINGBOX_TEST_COMMAND: ${{ inputs.test-command }}", "WINGBOX_REQUIRE_TEST_PROVENANCE: ${{ inputs.require-test-provenance }}"):
            self.assertIn(name, action)
        self.assertRegex(action, r"\n  test-reports-refused:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.test-reports-refused \}\}\n")
        for needed in ("`test-command`", "`require-test-provenance`", "`test-reports-refused`", ".run.json", "WINGBOX_TEST_COMMAND", "WINGBOX_REQUIRE_TEST_PROVENANCE",
                       "tracked by git", "before the start of the run", "WINGBOX_TEST_REPORTS_REFUSED"):
            self.assertIn(needed, readme)
        shell = (ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8")
        for name in ("--env WINGBOX_TEST_COMMAND", "--env WINGBOX_REQUIRE_TEST_PROVENANCE", "--env CI_PIPELINE_ID", "--env CI_PIPELINE_CREATED_AT",
                     "--env CI_JOB_STARTED_AT", "--env CI_JOB_NAME"):
            self.assertIn(name, shell)
        sidecar = readme.split("```json\n")[1].split("```")[0]
        self.assertEqual("", wt.parse_sidecar(sidecar.replace("<sha>", COMMIT).replace("<digest>", "5" * 64).encode())[1])


TESTED = {"tests/__init__.py": "", "tests/test_billing.py": "from app.services.billing import bill\n\n\ndef test_bill():\n    assert bill(None) is None\n"}
JUNIT = ('<testsuites><testsuite name="pytest" timestamp="{timestamp}" hostname="laptop"><properties><property name="commit" value="{commit}"/></properties>'
         '<testcase classname="tests.test_billing" name="test_bill"/></testsuite></testsuites>')


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class EndToEndTest(unittest.TestCase):
    def test_the_whole_script_refuses_the_report_of_another_time_and_commit(self) -> None:
        """/* REQ-ACTION-134: run as a job of GitHub Actions would run it, with a report that states a time long before the job and another commit: the report is refused with both reasons in the plan and in the record, the test it holds has no record, the record names the run, the attempt, the job, the commit checked, the start and the test command, and `require-tests` fails the job; with a report written in the job for the commit checked the same run accepts it and passes */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, out = Path(tmp) / "repo", Path(tmp) / "out"
            repo.mkdir()
            out.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, {**make_fixture.BASE, **TESTED}, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            event = out / "event.json"
            event.write_text("{}", encoding="utf-8")
            started = int(time.time()) - 120
            os.utime(event, (started, started))
            (repo / "test-results").mkdir()
            report_file = repo / "test-results" / "junit.xml"
            report_file.write_text(JUNIT.format(timestamp="2019-01-01T00:00:00", commit="0000000"), encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_")) or key == "WINGBOX_JOBS"}
            env.update(WINGBOX_VERSION="test", WINGBOX_TEST_RESULTS="test-results/*.xml", WINGBOX_REQUIRE_TESTS="true", WINGBOX_COMMENT="false",
                       WINGBOX_TEST_COMMAND="python -P -m pytest --junitxml=test-results/junit.xml", GITHUB_ACTIONS="true", GITHUB_EVENT_PATH=str(event),
                       GITHUB_REPOSITORY="owner/repo", GITHUB_RUN_ID="77", GITHUB_RUN_ATTEMPT="2", GITHUB_JOB="check", GITHUB_OUTPUT=str(out / "output.txt"))
            command = [sys.executable, str(ROOT / "wingbox_pr.py"), "--root", str(repo), "--base", base, "--head", head, "--out", str(out)]
            done = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            start = pr.utc(started)
            reasons = f"its timestamp 2019-01-01T00:00:00 is before the start of the run {start}; it names commit 0000000, and the commit checked is {head}"
            plan = (out / "wingbox-plan.sexp").read_text(encoding="utf-8").split("\n")
            digest = hashlib.sha256(report_file.read_bytes()).hexdigest()
            self.assertIn(f'(test-report-refused "test-results/junit.xml" :sha256 {digest} :cases 1 :reason "{reasons}")', plan)
            self.assertIn(f"(test-provenance :commit {head} :started {start} :run 77 :attempt 2 :sidecar optional)", plan)
            self.assertTrue(any(line.startswith('(run-test "tests/test_billing') and line.endswith(":evidence no-record :cases 0)") for line in plan), plan)
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, SCHEMA))
            self.assertEqual(("github", "77", "2", "check"), tuple(record["run"][key] for key in ("provider", "id", "attempt", "job")))
            self.assertEqual({"commit": head, "started": start, "run": "77", "attempt": "2", "command": "python -P -m pytest --junitxml=test-results/junit.xml",
                              "sidecar-required": False}, record["tests"]["provenance"])
            read = record["tests"]["reports"][0]
            self.assertEqual(("refused", reasons.split("; "), "2019-01-01T00:00:00", "untracked", ["0000000"]),
                             (read["status"], read["reasons"], read["timestamp"], read["tracked"], read["commits"]))
            self.assertEqual("incomplete", record["tests"]["evidence"])
            outputs = dict(line.split("=", 1) for line in (out / "output.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual(("1", "0"), (outputs["test-reports-refused"], outputs["tests-passed"]))

            (out / "output.txt").unlink()
            report_file.write_text(JUNIT.format(timestamp=pr.utc(time.time()), commit=head), encoding="utf-8")
            again = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True)
            self.assertEqual(0, again.returncode, again.stdout + again.stderr)
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(("complete", "accepted", []), (record["tests"]["evidence"], record["tests"]["reports"][0]["status"], record["tests"]["reports"][0]["reasons"]))


if __name__ == "__main__":
    unittest.main()
