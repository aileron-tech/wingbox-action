"""The GitLab CI wrapper: which commits it compares, the merge request note, the variables it hands on."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("wingbox_gitlab", ROOT / "wingbox_gitlab.py")
gitlab = importlib.util.module_from_spec(_spec)
sys.modules["wingbox_gitlab"] = gitlab
_spec.loader.exec_module(gitlab)

ZERO = "0" * 40


class CommitsTest(unittest.TestCase):
    def test_a_merge_request_pipeline_compares_the_diff_base_with_the_commit(self) -> None:
        """/* REQ-ACTION-060: in a merge request pipeline the base is CI_MERGE_REQUEST_DIFF_BASE_SHA and the head is CI_COMMIT_SHA */"""
        env = {"CI_PIPELINE_SOURCE": "merge_request_event", "CI_MERGE_REQUEST_DIFF_BASE_SHA": "b" * 40, "CI_COMMIT_SHA": "c" * 40,
               "CI_COMMIT_BEFORE_SHA": ZERO}
        self.assertEqual(gitlab.Compare("b" * 40, "c" * 40), gitlab.commits(env))

    def test_a_push_pipeline_compares_the_commit_before_the_push_with_the_commit(self) -> None:
        """/* REQ-ACTION-061: in a push pipeline the base is CI_COMMIT_BEFORE_SHA; a first push of a branch (all zeros) and a pipeline without these variables are skipped with the reason said */"""
        self.assertEqual(gitlab.Compare("a" * 40, "c" * 40),
                         gitlab.commits({"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BEFORE_SHA": "a" * 40, "CI_COMMIT_SHA": "c" * 40}))
        first = gitlab.commits({"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BEFORE_SHA": ZERO, "CI_COMMIT_SHA": "c" * 40})
        self.assertIsInstance(first, gitlab.Skip)
        self.assertIn("first push", first.reason)
        self.assertIsInstance(gitlab.commits({}), gitlab.Skip)

    def test_explicit_commits_win(self) -> None:
        """/* REQ-ACTION-062: WINGBOX_BASE and WINGBOX_HEAD, when set, are the commits compared, whatever the pipeline */"""
        env = {"WINGBOX_BASE": "1" * 40, "WINGBOX_HEAD": "2" * 40, "CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BEFORE_SHA": "a" * 40,
               "CI_COMMIT_SHA": "c" * 40}
        self.assertEqual(gitlab.Compare("1" * 40, "2" * 40), gitlab.commits(env))


class NoteTest(unittest.TestCase):
    def _calls(self, pages: list[list[dict]]):
        made: list[tuple[str, str, object]] = []

        def call(method: str, url: str, body: object = None):
            made.append((method, url, body))
            if method == "GET":
                page = int(url.rsplit("page=", 1)[1])
                return pages[page - 1], (str(page + 1) if page < len(pages) else "")
            return {"id": 77}, ""
        return made, call

    def test_the_note_with_the_marker_is_updated(self) -> None:
        """/* REQ-ACTION-063: the merge request has one Wingbox note: the note that holds the marker is updated, also when it is on a later page of notes */"""
        made, call = self._calls([[{"id": 1, "body": "hello"}], [{"id": 9, "body": gitlab.MARKER + "\nold"}]])
        said = gitlab.post_note("new body", "https://gl.example/api/v4", "42", "7", call)
        self.assertEqual("updated note 9", said)
        self.assertEqual(("PUT", "https://gl.example/api/v4/projects/42/merge_requests/7/notes/9", {"body": "new body"}), made[-1])

    def test_a_note_is_created_only_when_there_is_something_to_say(self) -> None:
        """/* REQ-ACTION-064: with no Wingbox note yet, one is created when the report has something to say and none otherwise */"""
        made, call = self._calls([[{"id": 1, "body": "hello"}]])
        self.assertEqual("created note 77", gitlab.post_note("body", "https://gl.example/api/v4", "42", "7", call))
        self.assertEqual("POST", made[-1][0])
        made, call = self._calls([[]])
        self.assertEqual("no note: no source module changed", gitlab.post_note("body", "https://gl.example/api/v4", "42", "7", call, create=False))
        self.assertEqual(["GET"], [method for method, _, _ in made])

    def test_a_project_path_is_encoded(self) -> None:
        """/* REQ-ACTION-065: a project given by its path is written into the address with its slashes encoded */"""
        made, call = self._calls([[]])
        gitlab.post_note("body", "https://gl.example/api/v4", "group/sub/repo", "7", call)
        self.assertIn("/projects/group%2Fsub%2Frepo/merge_requests/7/notes", made[0][1])


class HandOnTest(unittest.TestCase):
    def test_the_results_are_written_as_variables_for_later_jobs(self) -> None:
        """/* REQ-ACTION-066: the result, its reasons and the counts are written as a dotenv file with WINGBOX_ names a later job can read; a value keeps to one line */"""
        text = gitlab.dotenv({"result": "UNKNOWN", "execution": "ok", "reasons": "not-checked: . (no-layer-style)", "tests-to-run": "3",
                              "introduced-findings": "0", "untouched-dependents": "5", "not-checked-parts": "1"})
        lines = text.splitlines()
        self.assertIn("WINGBOX_RESULT=UNKNOWN", lines)
        self.assertIn("WINGBOX_TESTS_TO_RUN=3", lines)
        self.assertIn('WINGBOX_REASONS="not-checked: . (no-layer-style)"', lines)
        self.assertEqual("WINGBOX_REASONS=\"a b\"\n", gitlab.dotenv({"reasons": "a\nb"}))

    def test_the_template_runs_the_wrapper_on_merge_requests_and_the_default_branch(self) -> None:
        """/* REQ-ACTION-067: the CI template fetches the whole history, installs the pinned wingbox release, runs wingbox_gitlab.py from a pinned tag of this repository, keeps the report as an artifact and runs on merge requests and on the default branch */"""
        text = (ROOT / "gitlab" / "wingbox.gitlab-ci.yml").read_text(encoding="utf-8")
        for needed in ('GIT_DEPTH: "0"', 'pip install', '"wingbox==$WINGBOX_VERSION"', "wingbox_gitlab.py", 'WINGBOX_ACTION_REF: "v1"',
                       "merge_request_event", "$CI_DEFAULT_BRANCH", "wingbox-out/", "dotenv: wingbox-out/wingbox.env"):
            self.assertIn(needed, text)
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        import re
        default = re.search(r'version:\n(?:.*\n)*?\s+default: "([0-9.]+)"', action).group(1)
        self.assertIn(f'WINGBOX_VERSION: "{default}"', text)

    def test_a_pipeline_with_nothing_to_compare_passes_and_says_so(self) -> None:
        """/* REQ-ACTION-068: a pipeline with nothing to compare (the first push of a branch) ends with status 0, says why and still writes wingbox.env with the result NOT_APPLICABLE, so the job's dotenv report has its file */"""
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            env = {key: value for key, value in os.environ.items() if not key.startswith(("CI_", "WINGBOX_", "GITHUB_"))}
            env.update(CI_PROJECT_DIR=directory, CI_PIPELINE_SOURCE="push", CI_COMMIT_SHA="c" * 40, CI_COMMIT_BEFORE_SHA=ZERO)
            done = subprocess.run([sys.executable, str(ROOT / "wingbox_gitlab.py")], cwd=directory, env=env, capture_output=True, text=True,
                                  encoding="utf-8", timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertIn("first push", done.stdout)
            written = (Path(directory) / "wingbox-out" / "wingbox.env").read_text(encoding="utf-8")
            self.assertIn("WINGBOX_RESULT=NOT_APPLICABLE", written)


class BuildFailureTest(unittest.TestCase):
    def test_a_build_that_fails_is_a_tool_error(self) -> None:
        """/* REQ-ACTION-069: a `wingbox build` that does not end with status 0 (wingbox not installed for this Python, a locked or unreadable project) is recorded as a tool error, so the result is UNKNOWN and never "nothing to check"; `wingbox boundaries` may still end with 1 (it found violations) */"""
        import subprocess
        pr = gitlab.pr
        answers = {"build": 1, "boundaries": 1}

        def fake_run(argv: list[str], cwd: str):
            command = argv[3] if argv[1:3] == ["-m", "wingbox"] else argv[0]
            return subprocess.CompletedProcess(argv, answers.get(command, 0), "", "No module named wingbox")
        real_run, pr._run = pr._run, fake_run
        try:
            del pr.TOOL_ERRORS[:]
            pr._boundaries("/nowhere")
            self.assertEqual(["wingbox build exited 1"], pr.TOOL_ERRORS)
            answers["build"] = 0
            del pr.TOOL_ERRORS[:]
            pr._boundaries("/nowhere")
            self.assertEqual([], pr.TOOL_ERRORS)
        finally:
            pr._run = real_run
            del pr.TOOL_ERRORS[:]

    def test_a_tool_error_is_said_in_the_comment(self) -> None:
        """/* REQ-ACTION-070: when a wingbox command failed, the comment says the change could not be checked and names the error, instead of "nothing to check", and the pull request gets that comment */"""
        import dataclasses
        pr = gitlab.pr
        report = dataclasses.replace(pr.Report("0.11.0", 2, "a" * 40, "b" * 40, (pr.ChangedFile("src/a.rs", None, ()),), (), 0, ()),
                                     tool_errors=("wingbox build exited 1",))
        comment = pr.build_comment(report)
        self.assertNotIn("nothing to check", comment)
        self.assertIn("could not check this change", comment)
        self.assertIn("wingbox build exited 1", comment)
        self.assertTrue(pr.needs_comment(report))
        self.assertEqual("UNKNOWN", pr.result(report)[0])
        quiet = dataclasses.replace(report, tool_errors=())
        self.assertIn("nothing to check", pr.build_comment(quiet))
        self.assertFalse(pr.needs_comment(quiet))


if __name__ == "__main__":
    unittest.main()
