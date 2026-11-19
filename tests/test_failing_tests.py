"""When the test step fails the check still runs, and a record is written and handed over."""
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import check_evidence  # noqa: E402
import make_fixture  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_evidence_record import RUN, SCHEMA, problems  # noqa: E402
from test_test_cases import suite  # noqa: E402
from test_test_evidence import with_tests  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

ACTION = (ROOT / "action.yml").read_text(encoding="utf-8")
EXAMPLE = (ROOT / "examples" / "evidence-handover.yml").read_text(encoding="utf-8")
PASSED = tuple(wt.parse_junit(b"<testsuites>" + suite("tests/order.test.ts", ("a", "")) + suite("tests/invoice.test.ts", ("b", "")) + b"</testsuites>"))
MINIMAL = {"boundaries": "UNKNOWN", "execution": "error", "reasons": ["check-did-not-run"]}
CAN_HAND_OVER = bool(shutil.which("bash") and shutil.which("curl")) and os.name != "nt"


def clean_env(**values: str) -> dict:
    """The environment of this process without the variables of a CI system, and with the given ones."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_", "RUNNER_")) or key == "WINGBOX_JOBS"}
    return {**env, **values}


class OutcomeTest(unittest.TestCase):
    def test_a_failed_test_step_makes_the_evidence_failed_whatever_the_reports_hold(self) -> None:
        """/* REQ-ACTION-135: the outcome of the test step (`test-step-outcome`) is recorded; `failure` makes the test evidence `failed` when the reports hold passed cases only, when no report was given and when the report was cut short; `cancelled` and `skipped` make evidence that would be complete `incomplete`; `success` and no outcome change nothing; `require-tests` fails on an outcome other than success; the plan, the comment and the output say it */"""
        self.assertEqual("complete", we.tests_summary(with_tests(test_cases=PASSED)))
        self.assertEqual("complete", we.tests_summary(with_tests(test_cases=PASSED, test_step_outcome="success")))
        failed = with_tests(test_cases=PASSED, test_step_outcome="failure")
        self.assertEqual("failed", we.tests_summary(failed))
        self.assertEqual("failed", we.tests_summary(with_tests(test_step_outcome="failure")))
        self.assertEqual("failed", we.tests_summary(report(test_step_outcome="failure")))
        cut_short = with_tests(test_cases=(), test_report_errors=(("test-results/junit.xml", "not XML: no element found: line 1, column 11"),),
                               test_step_outcome="failure")
        self.assertEqual("failed", we.tests_summary(cut_short))
        for outcome in ("cancelled", "skipped"):
            self.assertEqual("incomplete", we.tests_summary(with_tests(test_cases=PASSED, test_step_outcome=outcome)))
            self.assertEqual("incomplete", we.tests_summary(report(test_cases=(), test_step_outcome=outcome)))
            self.assertEqual("none", we.tests_summary(with_tests(test_step_outcome=outcome)))
            self.assertEqual(1, pr.tests_status(with_tests(test_cases=PASSED, test_step_outcome=outcome), True))
        self.assertEqual((1, 0, 0), (pr.tests_status(failed, True), pr.tests_status(failed, False),
                                     pr.tests_status(with_tests(test_cases=PASSED, test_step_outcome="success"), True)))
        self.assertEqual(1, pr.tests_status(report(test_step_outcome="failure"), True))
        said: list[str] = []
        self.assertEqual(1, pr.tests_failed(failed, {"WINGBOX_REQUIRE_TESTS": "true"}, said.append))
        self.assertEqual(["Wingbox: require-tests: the test step of the job ended with failure"], said)
        record = we.build_evidence(failed, RUN, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual(("failed", "failure"), (record["tests"]["evidence"], record["tests"]["step-outcome"]))
        self.assertEqual("", we.build_evidence(with_tests(), RUN, {})["tests"]["step-outcome"])
        plan = pr.build_plan(with_tests(test_step_outcome="failure")).split("\n")
        self.assertIn("(test-step :outcome failure)", plan)
        self.assertIn("(tests :evidence failed)", plan)
        self.assertNotIn("test-step", pr.build_plan(with_tests()))
        self.assertIn("The test step of the job ended with `failure`: the test evidence is `failed`, whatever the reports hold.", pr.build_comment(failed))
        self.assertIn("The test step of the job ended with `skipped`: the test evidence is not complete.",
                      pr.build_comment(with_tests(test_cases=PASSED, test_step_outcome="skipped")))
        self.assertNotIn("The test step", pr.build_comment(with_tests(test_cases=PASSED, test_step_outcome="success")))
        self.assertEqual("failed", pr.outputs(failed)["tests-evidence"])

    def test_the_outcome_is_an_input_with_four_values(self) -> None:
        """/* REQ-ACTION-135: `test-step-outcome` is an input of action.yml (empty by default) handed to the script as WINGBOX_TEST_STEP_OUTCOME; it is `success`, `failure`, `cancelled` or `skipped` (what `steps.<id>.outcome` gives), and another value ends the run with status 2 before anything is checked; README.md documents it */"""
        self.assertRegex(ACTION, r'\n  test-step-outcome:\n    description: [^\n]+\n    default: ""\n')
        self.assertIn("WINGBOX_TEST_STEP_OUTCOME: ${{ inputs.test-step-outcome }}", ACTION)
        self.assertEqual(("failure", "", "success"), (pr.step_outcome({"WINGBOX_TEST_STEP_OUTCOME": "failure"}), pr.step_outcome({}),
                                                      pr.step_outcome({"WINGBOX_TEST_STEP_OUTCOME": " success "})))
        self.assertIsNone(pr.step_outcome({"WINGBOX_TEST_STEP_OUTCOME": "red"}))
        with tempfile.TemporaryDirectory() as folder:
            for script in ("wingbox_pr.py", "wingbox_gitlab.py"):
                done = subprocess.run([sys.executable, "-P", str(ROOT / script), "--root", folder, "--base", "main", "--out", folder],
                                      cwd=folder, env=clean_env(WINGBOX_TEST_STEP_OUTCOME="red"), capture_output=True, text=True)
                self.assertEqual(2, done.returncode, done.stdout + done.stderr)
                self.assertIn("test-step-outcome must be one of success, failure, cancelled, skipped", done.stdout)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for needed in ("`test-step-outcome`", "WINGBOX_TEST_STEP_OUTCOME", "steps.tests.outcome"):
            self.assertIn(needed, readme)


class NotRunTest(unittest.TestCase):
    def test_a_check_that_did_not_run_still_leaves_a_record(self) -> None:
        """/* REQ-ACTION-136: the record of a check that did not run says the result UNKNOWN, the execution `error` and the one reason `check-did-not-run`, names the run, the base and the head as far as they are known, carries the outcome of the test step and the test command, holds no file and no test, and meets the schema; its test evidence is `failed` when the test step failed, else `none` */"""
        env = {"WINGBOX_VERSION": "0.14.0", "WINGBOX_TEST_STEP_OUTCOME": "failure", "WINGBOX_TEST_COMMAND": "pytest -q"}
        empty = pr.not_run_report("a" * 40, "b" * 40, env)
        self.assertEqual(("UNKNOWN", "error", ("check-did-not-run",)), pr.result(empty))
        record = we.build_evidence(empty, RUN, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual(MINIMAL, record["result"])
        self.assertEqual(("a" * 40, "b" * 40, "", "unknown"), tuple(record["subject"][key] for key in ("base", "head", "checked", "tree")))
        self.assertEqual(("owner/repo", "7"), (record["run"]["repository"], record["run"]["id"]))
        self.assertEqual(("failed", "failure", "pytest -q", [], [], []), (record["tests"]["evidence"], record["tests"]["step-outcome"],
                                                                         record["tests"]["provenance"]["command"], record["tests"]["to-run"],
                                                                         record["tests"]["reports"], record["files"]))
        self.assertEqual("none", we.build_evidence(pr.not_run_report("", "", {}), RUN, {})["tests"]["evidence"])
        self.assertEqual(1, pr.exit_status(empty, "strict"))

    def test_both_scripts_write_that_record_when_asked(self) -> None:
        """/* REQ-ACTION-136: `wingbox_pr.py --did-not-run` writes the record into the output folder and the outputs `evidence`, `result`, `execution` and `reasons`, and `wingbox_gitlab.py --did-not-run` writes it with wingbox.env into wingbox-out/; neither needs Wingbox, git or a repository, and both end with status 0 (the step that failed has made the job fail) */"""
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "runner"
            out.mkdir()
            env = clean_env(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="owner/repo", GITHUB_RUN_ID="77", GITHUB_RUN_ATTEMPT="2", GITHUB_JOB="check",
                            RUNNER_TEMP=str(out), GITHUB_OUTPUT=str(out / "output.txt"), WINGBOX_BASE="a" * 40, WINGBOX_HEAD="b" * 40,
                            WINGBOX_TEST_STEP_OUTCOME="failure", WINGBOX_VERSION="0.0.0")
            done = subprocess.run([sys.executable, "-P", str(ROOT / "wingbox_pr.py"), "--did-not-run"], cwd=folder, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            outputs = dict(line.split("=", 1) for line in (out / "output.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual((str(out / "wingbox-evidence.json"), "UNKNOWN", "error", "check-did-not-run", "failed"),
                             (outputs["evidence"], outputs["result"], outputs["execution"], outputs["reasons"], outputs["tests-evidence"]))
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, SCHEMA))
            self.assertEqual((MINIMAL, "github", "77", "2", "a" * 40, "b" * 40),
                             (record["result"], record["run"]["provider"], record["run"]["id"], record["run"]["attempt"], record["subject"]["base"], record["subject"]["head"]))
            self.assertEqual([], check_evidence.problems(record, "failed", "failure", "check-did-not-run"))

            project = Path(folder) / "project"
            project.mkdir()
            env = clean_env(CI_PROJECT_DIR=str(project), CI_JOB_ID="5", CI_PROJECT_PATH="group/project", CI_COMMIT_SHA="c" * 40,
                            CI_MERGE_REQUEST_DIFF_BASE_SHA="d" * 40, CI_MERGE_REQUEST_IID="3")
            done = subprocess.run([sys.executable, "-P", str(ROOT / "wingbox_gitlab.py"), "--did-not-run"], cwd=folder, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            record = json.loads((project / "wingbox-out" / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, SCHEMA))
            self.assertEqual((MINIMAL, "gitlab", "group/project", "3", "d" * 40, "c" * 40, "none"),
                             (record["result"], record["run"]["provider"], record["run"]["repository"], record["run"]["change"], record["subject"]["base"],
                              record["subject"]["head"], record["tests"]["evidence"]))
            dotenv = (project / "wingbox-out" / "wingbox.env").read_text(encoding="utf-8").splitlines()
            self.assertIn("WINGBOX_RESULT=UNKNOWN", dotenv)
            self.assertIn("WINGBOX_REASONS=check-did-not-run", dotenv)

    def test_the_action_writes_that_record_in_a_last_step_that_always_runs(self) -> None:
        """/* REQ-ACTION-136: the last step of action.yml runs when the check step left no record (`always()`), also after a failed install, starts the script with `--did-not-run` by the Python the action set up or else the one of the runner, and the outputs `evidence`, `result`, `execution` and `reasons` of the action fall back on it */"""
        last = ACTION.split("\n    - name: ")[-1]
        self.assertTrue(last.startswith("Record that the check did not run\n      id: did-not-run\n"), last)
        self.assertIn("      if: ${{ always() && steps.check.outputs.evidence == '' }}\n", last)
        self.assertIn('-P "$GITHUB_ACTION_PATH/wingbox_pr.py" --did-not-run', last)
        self.assertIn("command -v python3 || command -v python", last)
        for name in ("WINGBOX_BASE: ${{ inputs.base }}", "WINGBOX_HEAD: ${{ inputs.head }}", "WINGBOX_TEST_STEP_OUTCOME: ${{ inputs.test-step-outcome }}",
                     "WINGBOX_TEST_COMMAND: ${{ inputs.test-command }}", "WINGBOX_VERSION: ${{ inputs.version }}"):
            self.assertIn(name, last)
        self.assertNotIn("working-directory", last)
        for name in ("evidence", "result", "execution", "reasons"):
            self.assertRegex(ACTION, rf"\n  {name}:\n    description: [^\n]+\n    value: \$\{{\{{ steps\.check\.outputs\.{name} \|\| steps\.did-not-run\.outputs\.{name} \}}\}}\n")


class ExampleTest(unittest.TestCase):
    def test_the_example_keeps_the_job_red_and_still_runs_the_check_and_the_upload(self) -> None:
        """/* REQ-ACTION-137: in the example workflow the test step has an id and `continue-on-error: true`; the check step runs unless the job was cancelled and gets the outcome of the test step and the test command; the upload step runs unless the job was cancelled and uploads the record, the report and the plan; a last step fails the job when the test step did not succeed; README.md shows the same four steps */"""
        check = EXAMPLE.split("\n  hand-over:\n")[0]
        steps = check.split("\n      - ")[1:]
        self.assertEqual(5, len(steps), [step.split("\n")[0] for step in steps])
        checkout, tests, wingbox, upload, last = steps
        self.assertTrue(checkout.startswith("uses: actions/checkout@v4"))
        self.assertIn("id: tests\n", tests)
        self.assertIn("\n        continue-on-error: true", tests)
        command = re.search(r"\n        run: (.+)\n", tests).group(1)
        self.assertTrue(wingbox.startswith("uses: aileron-tech/wingbox-action@v1\n"))
        self.assertIn("        if: ${{ !cancelled() }}\n", wingbox)
        self.assertEqual({"fail-on": "strict", "test-results": "test-results/*.xml", "test-step-outcome": "${{ steps.tests.outcome }}", "test-command": command},
                         example_inputs())
        self.assertTrue(upload.startswith("uses: actions/upload-artifact@v4\n        if: ${{ !cancelled() }}\n"), upload)
        for name in ("evidence", "report", "plan"):
            self.assertIn(f"            ${{{{ steps.wingbox.outputs.{name} }}}}\n", upload)
        self.assertIn("        if: ${{ steps.tests.outcome != 'success' }}\n        run: exit 1", last)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        shown = readme.split("### Evidence that the tests ran")[1].split("```yaml\n")[1].split("```")[0]
        for needed in ("id: tests", "continue-on-error: true", "if: ${{ !cancelled() }}", "test-step-outcome: ${{ steps.tests.outcome }}",
                       "if: ${{ steps.tests.outcome != 'success' }}", "run: exit 1"):
            self.assertIn(needed, shown)


def example_inputs() -> dict:
    """The inputs the example gives the action."""
    step = EXAMPLE.split("      - uses: aileron-tech/wingbox-action@v1\n")[1].split("\n      - ")[0]
    return dict(re.findall(r"^          ([a-z-]+): (.+)$", step, re.M))


def action_env(inputs: dict, outcome: str) -> dict:
    """The variables the check step of action.yml gets for these inputs, the outcome of the test step put in."""
    names = {name: variable for variable, name in re.findall(r"(WINGBOX_[A-Z_]+): \$\{\{ inputs\.([a-z-]+) \}\}", ACTION)}
    return {names[name]: value.replace("${{ steps.tests.outcome }}", outcome) for name, value in inputs.items()}


def script_of(template: str) -> str:
    """The commands of the `script` of a GitLab template as the shell of a runner gets them: one after another,
    in one shell that stops at the first command that fails."""
    body = (ROOT / "gitlab" / template).read_text(encoding="utf-8").split("\n  script:\n", 1)[1].split("\n  artifacts:\n", 1)[0]
    commands: list[str] = []
    folded: "list[str] | None" = None
    for line in body.split("\n"):
        if line.startswith("    #") or not line.strip():
            continue
        if line.startswith("    - "):
            if folded is not None:
                commands.append(" ".join(folded))
            folded = [] if line[6:].strip() == ">" else None
            if folded is None:
                commands.append(line[6:])
        elif folded is not None:
            folded.append(line.strip())
    if folded is not None:
        commands.append(" ".join(folded))
    return "set -e\n" + "\n".join(commands) + "\n"


STUBS = {
    "pip": 'echo "pip $*" >> "$STUB_LOG"\nexit "${STUB_PIP:-0}"\n',
    "git": 'echo "git $*" >> "$STUB_LOG"\nexit 0\n',
    "python": ('echo "python $* outcome=${WINGBOX_TEST_STEP_OUTCOME-unset} command=${WINGBOX_TEST_COMMAND-unset}" >> "$STUB_LOG"\n'
               'case "$*" in\n'
               '  *--did-not-run*) [ "${STUB_NOT_RUN:-0}" = 0 ] || exit "$STUB_NOT_RUN"; mkdir -p wingbox-out; echo not-run > wingbox-out/wingbox-evidence.json; exit 0;;\n'
               '  *wingbox_gitlab.py*) [ "${STUB_WRITES:-1}" = 0 ] || { mkdir -p wingbox-out; echo checked > wingbox-out/wingbox-evidence.json; }; exit "${STUB_CHECK:-0}";;\n'
               'esac\n'),
    "docker": ('echo "docker outcome=${WINGBOX_TEST_STEP_OUTCOME-unset} $*" | tr "\\n" " " >> "$STUB_LOG"\necho >> "$STUB_LOG"\n'
               '[ "${STUB_WRITES:-1}" = 0 ] || echo checked > wingbox-out/wingbox-evidence.json\nexit "${STUB_CHECK:-0}"\n'),
}


@unittest.skipUnless(shutil.which("sh") and os.name != "nt", "needs a POSIX shell")
class GitLabTest(unittest.TestCase):
    def run_script(self, template: str, **settings: str) -> tuple[int, str, str]:
        """(the status the job's script ends with, what the stand-in commands were called with, the record left)."""
        with tempfile.TemporaryDirectory() as folder:
            tools, work = Path(folder) / "bin", Path(folder) / "work"
            tools.mkdir()
            work.mkdir()
            for name, body in STUBS.items():
                (tools / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
                (tools / name).chmod(stat.S_IRWXU)
            (Path(folder) / "job.sh").write_text(script_of(template), encoding="utf-8")
            env = clean_env(PATH=f"{tools}{os.pathsep}{os.environ['PATH']}", STUB_LOG=str(Path(folder) / "log.txt"), CI_PROJECT_DIR=str(work),
                            WINGBOX_VERSION="0.14.0", WINGBOX_ACTION_REF="v1", WINGBOX_IMAGE="python:3.12", **settings)
            done = subprocess.run(["sh", str(Path(folder) / "job.sh")], cwd=work, env=env, capture_output=True, text=True, timeout=120)
            log = Path(folder) / "log.txt"
            record = work / "wingbox-out" / "wingbox-evidence.json"
            return (done.returncode, log.read_text(encoding="utf-8") if log.is_file() else "",
                    record.read_text(encoding="utf-8").strip() if record.is_file() else "")

    def test_the_job_runs_the_tests_then_the_check_and_ends_with_the_tests_status(self) -> None:
        """/* REQ-ACTION-138: in the GitLab template the tests named by WINGBOX_TEST_SCRIPT run inside `script` with their status kept; the check runs whatever they say and learns their outcome and their command; the job ends with the tests' status, else with the status of the check; without a test script nothing is said of a test step; when the install fails the script is asked for the record of a check that did not run, and when that cannot run either the smallest record is written by the shell */"""
        status, log, record = self.run_script("wingbox.gitlab-ci.yml", WINGBOX_TEST_SCRIPT="echo ran > tests-ran; exit 3")
        self.assertEqual((3, "checked"), (status, record), log)
        self.assertRegex(log, r"python \S+/action/wingbox_gitlab\.py outcome=failure command=echo ran > tests-ran; exit 3\n")
        status, log, record = self.run_script("wingbox.gitlab-ci.yml", WINGBOX_TEST_SCRIPT="true", STUB_CHECK="1")
        self.assertEqual((1, "checked"), (status, record), log)
        self.assertIn("wingbox_gitlab.py outcome=success command=true\n", log)
        self.assertEqual(3, self.run_script("wingbox.gitlab-ci.yml", WINGBOX_TEST_SCRIPT="exit 3", STUB_CHECK="1")[0])
        status, log, record = self.run_script("wingbox.gitlab-ci.yml")
        self.assertEqual((0, "checked"), (status, record), log)
        self.assertIn("wingbox_gitlab.py outcome=unset command=unset\n", log)
        status, log, record = self.run_script("wingbox.gitlab-ci.yml", STUB_WRITES="0")
        self.assertEqual((0, ""), (status, record), log)
        self.assertNotIn("--did-not-run", log)
        status, log, record = self.run_script("wingbox.gitlab-ci.yml", WINGBOX_TEST_SCRIPT="exit 3", STUB_PIP="1")
        self.assertEqual((3, "not-run"), (status, record), log)
        self.assertRegex(log, r"python -P \S+/action/wingbox_gitlab\.py --did-not-run outcome=failure ")
        status, log, record = self.run_script("wingbox.gitlab-ci.yml", STUB_PIP="1", STUB_NOT_RUN="2")
        self.assertEqual(1, status, log)
        self.assertEqual({"result": MINIMAL}, json.loads(record))

    def test_the_shell_runner_job_does_the_same_around_its_container(self) -> None:
        """/* REQ-ACTION-138: in the template for shell runners the tests run on the runner before the container, the container gets their outcome and their command, the job ends with the tests' status, else with that of the container; inside the container the script is asked for the record of a check that did not run, and when the container left none the shell writes the smallest record; both templates keep their artifacts whatever the status */"""
        status, log, record = self.run_script("wingbox-shell.gitlab-ci.yml", WINGBOX_TEST_SCRIPT="exit 3")
        self.assertEqual((3, "checked"), (status, record), log)
        self.assertIn("docker outcome=failure run --rm ", log)
        for name in ("--env WINGBOX_TEST_STEP_OUTCOME", "--env WINGBOX_TEST_COMMAND"):
            self.assertIn(name, log)
        self.assertEqual(0, self.run_script("wingbox-shell.gitlab-ci.yml")[0])
        status, log, record = self.run_script("wingbox-shell.gitlab-ci.yml", STUB_CHECK="125", STUB_WRITES="0")
        self.assertEqual(125, status, log)
        self.assertEqual({"result": MINIMAL}, json.loads(record))
        shell = (ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -P /tmp/action/wingbox_gitlab.py --did-not-run", shell)
        for name in ("wingbox.gitlab-ci.yml", "wingbox-shell.gitlab-ci.yml"):
            text = (ROOT / "gitlab" / name).read_text(encoding="utf-8")
            self.assertIn("\n  artifacts:\n    when: always\n", text)
            self.assertIn('WINGBOX_TEST_SCRIPT: ""', text)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("WINGBOX_TEST_SCRIPT", readme)
        self.assertNotIn("before_script", readme)


class EndToEndTest(unittest.TestCase):
    @unittest.skipUnless(CAN_HAND_OVER, "needs bash and curl")
    def test_the_record_of_a_check_that_did_not_run_is_handed_over(self) -> None:
        """/* REQ-ACTION-139: after an install that failed, the last step of the action run as written (by the Python of the runner) leaves the record, and the command of the hand-over step of the example, run as written against a stand-in server, sends it */"""
        command = ACTION.split("\n    - name: Record that the check did not run\n")[1].split("      run: |\n")[1].split("\n      env:\n")[0]
        with tempfile.TemporaryDirectory() as folder:
            env = clean_env(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="owner/repo", GITHUB_RUN_ID="77", RUNNER_TEMP=folder, GITHUB_OUTPUT=str(Path(folder) / "output.txt"),
                            GITHUB_ACTION_PATH=str(ROOT), WINGBOX_PYTHON="", WINGBOX_TEST_STEP_OUTCOME="failure",
                            PATH=os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]))
            done = subprocess.run(["bash", "-c", "\n".join(line.strip() for line in command.split("\n"))], cwd=folder, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            record = json.loads((Path(folder) / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], check_evidence.problems(record, "failed", "failure", "check-did-not-run"))
            self.assertEqual([], check_evidence.hand_over(folder))
            self.assertEqual(0, check_evidence.main([str(Path(folder) / "wingbox-evidence.json"), "--evidence", "failed", "--reason", "check-did-not-run", "--hand-over"]))
            self.assertEqual(1, check_evidence.main([str(Path(folder) / "wingbox-evidence.json"), "--evidence", "complete"]))

    @unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
    def test_the_example_run_with_a_test_step_that_fails_leaves_a_record_that_says_failed(self) -> None:
        """/* REQ-ACTION-139: the check job of the example, run as far as that goes without GitHub: a test step that fails after a report cut short, then the script with the variables action.yml gives it for the inputs of the example and the outcome `failure`, on a pull request event: the files the upload step names exist, the record says the tests failed and why the job is red (the last step of the example), and the hand-over command sends the record */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, temp = Path(tmp) / "repo", Path(tmp) / "runner"
            repo.mkdir()
            temp.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, make_fixture.BASE, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            (temp / "event.json").write_text(json.dumps({"pull_request": {"number": 5, "base": {"sha": base}, "head": {"sha": head}}}), encoding="utf-8")
            # The test step: it writes the start of a report and fails.
            (repo / "test-results").mkdir()
            (repo / "test-results" / "junit.xml").write_text('<testsuites><testsuite name="pytest"><testcase classname="tests.test_billing"', encoding="utf-8")
            outcome = "failure"
            inputs = example_inputs()
            env = clean_env(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="owner/repo", GITHUB_RUN_ID="77", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="check",
                            GITHUB_EVENT_PATH=str(temp / "event.json"), RUNNER_TEMP=str(temp), GITHUB_OUTPUT=str(temp / "output.txt"),
                            WINGBOX_VERSION="test", **action_env(inputs, outcome), WINGBOX_COMMENT="false")
            done = subprocess.run([sys.executable, str(ROOT / "wingbox_pr.py")], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)     # fail-on: strict, and a fixture whose rules cannot be checked
            outputs = dict(line.split("=", 1) for line in (temp / "output.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
            for name in re.findall(r"\$\{\{ steps\.wingbox\.outputs\.([a-z]+) \}\}", EXAMPLE.split("actions/upload-artifact@v4")[1].split("\n      - ")[0]):
                self.assertTrue(Path(outputs[name]).is_file(), name)
            record = json.loads(Path(outputs["evidence"]).read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, SCHEMA))
            self.assertEqual([], check_evidence.problems(record, "failed", "failure"))
            self.assertEqual(("github", "5", inputs["test-command"], head), (record["run"]["provider"], record["run"]["change"],
                                                                            record["tests"]["provenance"]["command"], record["subject"]["head"]))
            self.assertEqual("failed", outputs["tests-evidence"])
            self.assertIn("The test step of the job ended with `failure`", Path(outputs["report"]).read_text(encoding="utf-8"))
            self.assertIn("if: ${{ steps.tests.outcome != 'success' }}\n        run: exit 1", EXAMPLE)     # with this outcome the last step fails the job
            if CAN_HAND_OVER:
                self.assertEqual([], check_evidence.hand_over(str(Path(outputs["evidence"]).parent)))


class WorkflowTest(unittest.TestCase):
    def test_the_test_workflow_runs_the_example_with_a_failing_test_step_on_github(self) -> None:
        """/* REQ-ACTION-139: the test workflow of this repository has a job that runs the action as the example does (a test step that fails with `continue-on-error`, the action and the upload unless cancelled, the outcome handed over) and checks the record and the condition of the example's last step; a job that downloads the artifact and hands the record over with the command of the example; and a job whose install fails and that checks the record of a check that did not run */"""
        text = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        job = text.split("\n  evidence:\n")[1].split("\n  evidence-received:\n")[0]
        for needed in ("        id: tests\n        continue-on-error: true\n", "        if: ${{ !cancelled() }}\n        uses: ./\n",
                       "          test-step-outcome: ${{ steps.tests.outcome }}\n", "      - uses: actions/upload-artifact@v4\n        if: ${{ !cancelled() }}\n",
                       'python -P tests/check_evidence.py "$EVIDENCE" --evidence failed --outcome failure\n',
                       "        if: ${{ steps.tests.outcome != 'success' }}\n"):
            self.assertIn(needed, job)
        for name in ("evidence", "report", "plan"):
            self.assertIn(f"            ${{{{ steps.wingbox.outputs.{name} }}}}\n", job)
        received = text.split("\n  evidence-received:\n")[1].split("\n  not-run:\n")[0]
        self.assertIn("    needs: evidence\n    if: ${{ !cancelled() }}\n", received)
        self.assertIn("uses: actions/download-artifact@v4", received)
        self.assertIn("python -P tests/check_evidence.py received/wingbox-evidence.json --evidence failed --outcome failure --hand-over", received)
        not_run = text.split("\n  not-run:\n")[1]
        for needed in ("        continue-on-error: true\n        uses: ./\n", "          version: not-a-release\n",
                       'python -P tests/check_evidence.py "$EVIDENCE" --evidence failed --outcome failure --reason check-did-not-run\n',
                       'test "$OUTCOME" = failure\n', 'test "$REASONS" = check-did-not-run\n'):
            self.assertIn(needed, not_run)


if __name__ == "__main__":
    unittest.main()
