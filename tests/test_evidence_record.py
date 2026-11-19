"""The evidence record of a run, its schema, and the example of handing it to another system."""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import wingbox_evidence as we  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_test_evidence import with_tests  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

SCHEMA = json.loads((ROOT / "schema" / "wingbox-evidence-2.schema.json").read_text(encoding="utf-8"))
RUN = we.Run("github", "owner/repo", "12", "7", "https://github.com/owner/repo/actions/runs/7", "v1", "2026-01-02T03:04:05Z")
TYPES = {"object": dict, "array": list, "string": str, "integer": int}


def problems(value: object, schema: dict, where: str = "$") -> list[str]:
    """What of the schema (the part of JSON Schema it uses) the value does not meet."""
    found = []
    expected = TYPES[schema["type"]]
    if not isinstance(value, expected) or isinstance(value, bool):
        return [f"{where}: not {schema['type']}"]
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if "pattern" in schema and not re.search(schema["pattern"], value):
        found.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, dict):
        found += [f"{where}: {name} is missing" for name in schema.get("required", []) if name not in value]
        for name, item in value.items():
            if name in schema.get("properties", {}):
                found += problems(item, schema["properties"][name], f"{where}.{name}")
            elif schema.get("additionalProperties") is False:
                found.append(f"{where}: {name} is not in the schema")
    if isinstance(value, list):
        for index, item in enumerate(value):
            found += problems(item, schema["items"], f"{where}[{index}]")
    return found


class RecordTest(unittest.TestCase):
    def test_the_record_says_what_was_checked_and_what_was_found(self) -> None:
        """/* REQ-ACTION-102: the evidence record names its schema, the run, the base, the head, the commit checked and how it relates to the head, the result with its reasons, the counts, each changed file with its SHA-256, each test to run with its evidence and its recorded cases, each test report and each document written beside it with its SHA-256; it meets schema/wingbox-evidence-2.schema.json, with and without test results, a tree or findings */"""
        cases = tuple(wt.parse_junit(b'<testsuite name="tests/order.test.ts"><testcase classname="x" name="a"/></testsuite>'))
        full = with_tests(tree=pr.Tree("c" * 40, "merge"), hashes=(("src/models/order.ts", "f" * 64),), test_cases=cases,
                          test_reports=(wt.TestReport("junit.xml", "e" * 64, 1),))
        record = we.build_evidence(full, RUN, {"wingbox-report.md": "report\n", "wingbox-plan.sexp": "plan\n"})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual("wingbox-evidence/2", record["schema"])
        self.assertEqual({"base": "a" * 40, "head": "b" * 40, "checked": "c" * 40, "tree": "merge", "differs": []}, record["subject"])
        self.assertEqual({"provider": "github", "repository": "owner/repo", "change": "12", "id": "7",
                          "url": "https://github.com/owner/repo/actions/runs/7"}, record["run"])
        self.assertEqual("FAIL", record["result"]["boundaries"])
        self.assertEqual([{"path": "src/models/order.ts", "sha256": "f" * 64}], record["files"])
        self.assertEqual("incomplete", record["tests"]["evidence"])
        self.assertEqual([{"test": "tests/invoice.test", "status": "no-record", "cases": 0, "records": []},
                          {"test": "tests/order.test", "status": "passed", "cases": 1, "records": [{"where": "x", "name": "a", "status": "passed"}]}],
                         record["tests"]["to-run"])
        self.assertEqual(["wingbox-plan.sexp", "wingbox-report.md"], [document["name"] for document in record["documents"]])
        plain = we.build_evidence(report(), dataclass_replace(RUN, provider="local"), {})
        self.assertEqual([], problems(plain, SCHEMA))
        self.assertEqual(("none", "unknown"), (plain["tests"]["evidence"], plain["subject"]["tree"]))
        self.assertEqual("none", we.build_evidence(with_tests(), RUN, {})["tests"]["to-run"][0]["status"])
        self.assertNotEqual([], problems({**record, "extra": 1}, SCHEMA))

    def test_the_summary_of_the_test_evidence(self) -> None:
        """/* REQ-ACTION-103: the test evidence of a run is `none` without test results, `failed` when a test to run failed, `incomplete` when one was skipped or has no record, and `complete` when every test to run has a passed record */"""
        def cases(*suites: tuple[str, str]) -> tuple:
            body = "".join(f'<testsuite name="{name}"><testcase classname="x" name="a">{inner}</testcase></testsuite>' for name, inner in suites)
            return tuple(wt.parse_junit(f"<testsuites>{body}</testsuites>".encode()))
        self.assertEqual("none", we.tests_summary(with_tests()))
        self.assertEqual("incomplete", we.tests_summary(with_tests(test_cases=cases(("tests/order.test.ts", "")))))
        self.assertEqual("failed", we.tests_summary(with_tests(test_cases=cases(("tests/order.test.ts", "<failure/>")))))
        self.assertEqual("complete", we.tests_summary(with_tests(test_cases=cases(("tests/order.test.ts", ""), ("tests/invoice.test.ts", "")))))

    def test_the_record_is_written_beside_the_report(self) -> None:
        """/* REQ-ACTION-104: the run writes the record as wingbox-evidence.json in its output folder, as JSON that reads back to the same record; action.yml has the output `evidence`, README.md documents it and the schema, and the GitLab wrapper names the job from CI_PROJECT_PATH, CI_JOB_ID and CI_JOB_URL, which the shell-runner template hands to the container */"""
        with tempfile.TemporaryDirectory() as folder:
            path = pr.write_evidence(report(), RUN, folder, {"wingbox-report.md": "x"})
            self.assertEqual(Path(folder) / "wingbox-evidence.json", Path(path))
            self.assertEqual(we.build_evidence(report(), RUN, {"wingbox-report.md": "x"}), json.loads(Path(path).read_text(encoding="utf-8")))
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(action, r"\n  evidence:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.evidence \}\}\n")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for needed in ("`evidence`", "wingbox-evidence.json", "schema/wingbox-evidence-1.schema.json", "examples/evidence-handover.yml"):
            self.assertIn(needed, readme)
        import wingbox_gitlab
        run = wingbox_gitlab.gitlab_run({"CI_JOB_ID": "5", "CI_PROJECT_PATH": "group/project", "CI_JOB_URL": "https://gl.example/j/5",
                                         "CI_MERGE_REQUEST_IID": "3", "WINGBOX_ACTION_REF": "v1"})
        self.assertEqual(("gitlab", "group/project", "3", "5", "https://gl.example/j/5", "v1"), (run.provider, run.repository, run.change,
                                                                                               run.run_id, run.url, run.action_ref))
        self.assertEqual("local", wingbox_gitlab.gitlab_run({}).provider)


def dataclass_replace(run: we.Run, **changes: str) -> we.Run:
    import dataclasses
    return dataclasses.replace(run, **changes)


class HandOverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / "examples" / "evidence-handover.yml").read_text(encoding="utf-8")
        self.check, self.hand_over = self.text.split("\n  hand-over:\n")

    def test_the_secret_of_the_receiver_is_not_in_the_job_that_reads_the_code(self) -> None:
        """/* REQ-ACTION-105: in the example workflow the job that checks out the pull request and runs the check names no secret, and the job that holds the secrets of the receiving system has no permission, no checkout and no step that runs code of the repository: it downloads the record and sends it */"""
        self.assertIn("actions/checkout@", self.check)
        self.assertNotIn("secrets.", self.check)
        self.assertIn("permissions: {}", self.hand_over)
        self.assertNotIn("actions/checkout", self.hand_over)
        self.assertEqual(["actions/download-artifact@v4"], re.findall(r"uses: (\S+)", self.hand_over))
        self.assertEqual(1, self.hand_over.count("run: |"))
        self.assertIn("secrets.EVIDENCE_TOKEN", self.hand_over)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("curl") and os.name != "nt", "needs bash and curl")
    def test_the_hand_over_step_sends_the_record_to_the_endpoint(self) -> None:
        """/* REQ-ACTION-106: the command of the hand-over step, run as written against a local stand-in server, posts the bytes of wingbox-evidence.json as JSON with the bearer token, and fails when the server refuses */"""
        command = self.hand_over.split("run: |\n", 1)[1].strip()
        seen: list[tuple[str, str, bytes]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                seen.append((self.headers.get("Authorization", ""), self.headers.get("Content-Type", ""), body))
                self.send_response(200 if self.path == "/evidence" else 403)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                pr.write_evidence(report(), RUN, folder, {})
                sent = (Path(folder) / "wingbox-evidence.json").read_bytes()
                env = {**os.environ, "EVIDENCE_TOKEN": "t0ken", "EVIDENCE_URL": f"http://127.0.0.1:{server.server_port}/evidence"}
                done = subprocess.run(["bash", "-c", command], cwd=folder, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual([("Bearer t0ken", "application/json", sent)], seen)
                env["EVIDENCE_URL"] = f"http://127.0.0.1:{server.server_port}/elsewhere"
                refused = subprocess.run(["bash", "-c", command], cwd=folder, env=env, capture_output=True, text=True, timeout=120)
                self.assertNotEqual(0, refused.returncode)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
