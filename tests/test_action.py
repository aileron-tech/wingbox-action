"""The edges of the action: its metadata, the map copy, and the whole script on the fixture repository."""
import http.server
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import check_fixture  # noqa: E402
import make_fixture  # noqa: E402
import wingbox_diagram  # noqa: E402
import wingbox_pr  # noqa: E402


class MapInputTest(unittest.TestCase):
    def test_the_map_is_uploaded_only_when_the_input_asks_for_it(self) -> None:
        """/* REQ-ACTION-021: action.yml has the input `upload-map`, off by default; the script learns it through WINGBOX_UPLOAD_MAP; the upload step runs only when the input is true and the script gave a map, with actions/upload-artifact and no retention of its own (the repository's setting applies); the `map` output is documented, and so is the input in README.md. */"""
        text = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r'\n  upload-map:\n    description: [^\n]+\n    default: "false"\n')
        self.assertIn("WINGBOX_UPLOAD_MAP: ${{ inputs.upload-map }}", text)
        self.assertRegex(text, r"\n  map:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.map \}\}\n")
        step = text[text.index("- name: Upload the map"):]
        self.assertRegex(step, r"if: \$\{\{ !cancelled\(\) && inputs\.upload-map == 'true' && steps\.check\.outputs\.map != '' \}\}")
        self.assertIn("uses: actions/upload-artifact@v4", step)
        self.assertIn(f"name: {wingbox_pr.MAP_ARTIFACT}\n", step)
        self.assertIn("path: ${{ steps.check.outputs.map }}", step)
        self.assertNotIn("retention-days", text)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`upload-map`", readme)
        self.assertIn("`map`", readme)

    def test_the_map_is_copied_out_of_the_hidden_folder(self) -> None:
        """/* REQ-ACTION-021: the map Wingbox built in `.wingbox/map.html` is copied to `wingbox-map/map.html` in the folder given (the runner's temporary folder), outside any hidden folder and outside the checkout; without a map nothing is copied and the path is empty. */"""
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "repo", Path(tmp) / "out"
            (root / ".wingbox").mkdir(parents=True)
            self.assertEqual("", wingbox_pr.copy_map(str(root), str(out)))
            self.assertFalse(out.exists())
            (root / ".wingbox" / "map.html").write_text("<html>map</html>", encoding="utf-8")
            copied = wingbox_pr.copy_map(str(root), str(out))
            self.assertEqual(out / "wingbox-map" / "map.html", Path(copied))
            self.assertEqual("<html>map</html>", Path(copied).read_text(encoding="utf-8"))
            self.assertEqual([".wingbox"], sorted(p.name for p in root.iterdir()))


class _Api(http.server.BaseHTTPRequestHandler):
    """A stand-in for the GitHub API of one pull request: two pages of comments, PATCH and POST recorded."""
    pages: list[list[dict]] = []
    calls: list[tuple[str, str, dict]] = []

    def _reply(self, payload: object, link: str = "") -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if link:
            self.send_header("Link", link)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        page = int(self.path.split("page=")[-1]) if "&page=" in self.path else 1
        host = f"http://{self.headers['Host']}"
        more = f'<{host}/repos/o/r/issues/5/comments?per_page=100&page={page + 1}>; rel="next"' if page < len(self.pages) else ""
        self._reply(self.pages[page - 1], more)

    def _write(self) -> None:
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append((self.command, self.path, data))
        self._reply({"id": 99})

    do_PATCH = _write
    do_POST = _write

    def log_message(self, format: str, *args: object) -> None:
        return


class OneCommentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Api)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{self.server.server_port}"
        _Api.calls = []

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_the_comment_with_the_marker_is_updated_and_no_second_one_is_created(self) -> None:
        """/* REQ-ACTION-024: a pull request keeps one comment: the action looks through all pages of its comments for the one that carries the hidden marker and updates it; only when there is none it creates one; a pull request that needs no comment gets none created. */"""
        other = [{"id": index, "body": f"review note {index}"} for index in range(3)]
        _Api.pages = [other, [{"id": 41, "body": None}, {"id": 42, "body": wingbox_pr.MARKER + "\nold report"}]]
        body = wingbox_pr.MARKER + "\nnew report"
        self.assertEqual("updated comment 42", wingbox_pr.post_comment(body, "o/r", 5, "token", self.api))
        self.assertEqual([("PATCH", "/repos/o/r/issues/comments/42", {"body": body})], _Api.calls)
        _Api.calls, _Api.pages = [], [other]
        self.assertEqual("created comment 99", wingbox_pr.post_comment(body, "o/r", 5, "token", self.api))
        self.assertEqual([("POST", "/repos/o/r/issues/5/comments", {"body": body})], _Api.calls)
        _Api.calls = []
        self.assertEqual("no comment: no source module changed", wingbox_pr.post_comment(body, "o/r", 5, "token", self.api, create=False))
        self.assertEqual([], _Api.calls)


class MarketplaceTest(unittest.TestCase):
    def test_the_description_fits_the_marketplace(self) -> None:
        """/* REQ-ACTION-007: the description in action.yml is one line of fewer than 125 characters, the length the GitHub Marketplace accepts, and says that the comment has a diagram. */"""
        text = (ROOT / "action.yml").read_text(encoding="utf-8")
        description = text.split("\ndescription: ", 1)[1].split("\n", 1)[0]
        self.assertLess(len(description), 125, description)
        self.assertIn("diagram", description)


class ReadmeTest(unittest.TestCase):
    def test_the_readme_states_the_limits_the_code_has(self) -> None:
        """/* REQ-ACTION-025: README.md names the caps of the diagram, the number of direct dependents the comment lists and GitHub's comment limit as the code has them, the two permissions the action needs, and a workflow example of ten lines. */"""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{wingbox_diagram.NODE_CAP} boxes and {wingbox_diagram.EDGE_CAP} arrows", readme)
        self.assertEqual((10, 65536), (wingbox_pr.LIST_LIMIT, wingbox_pr.COMMENT_LIMIT))
        self.assertIn("at most ten direct dependents", readme)
        self.assertIn("65,536 characters", readme)
        self.assertIn("contents: read", readme)
        self.assertIn("pull-requests: write", readme)
        usage = readme.split("## Usage\n\n```yaml\n")[1].split("```")[0]
        self.assertEqual(10, len(usage.splitlines()))
        self.assertIn("fetch-depth: 0", usage)
        self.assertIn("uses: aileron-tech/wingbox-action@v1", usage)


class WorkflowTest(unittest.TestCase):
    def test_the_test_workflow_checks_the_report_with_the_same_expectations(self) -> None:
        """/* REQ-ACTION-023: the workflow that runs the action on the fixture repository checks its report with tests/check_fixture.py, the expectations the local end-to-end test uses, and asks for the map upload in one job only (an artifact name is taken once per workflow run). */"""
        text = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn('python tests/check_fixture.py "$REPORT" "$UNTOUCHED" "$NOT_CHECKED"', text)
        self.assertIn("upload-map: ${{ matrix.os == 'ubuntu-latest' }}", text)
        self.assertEqual(["missing"], [name for name in ("missing",) if callable(getattr(check_fixture, name, None))])


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class FixtureTest(unittest.TestCase):
    def test_the_script_reports_on_the_fixture_repository_and_leaves_it_clean(self) -> None:
        """/* REQ-ACTION-023: run as on a runner on the fixture repository (a base and a head commit), the script writes the report with its diagram to the job summary and to the report file, the outputs (report, map, counts), copies the map when asked and names the artifact in the report; it leaves the checkout clean for git (Wingbox writes only into `.wingbox/`, which ignores itself), removes its worktree of the base, and exits 0. */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, temp = Path(tmp) / "repo", Path(tmp) / "runner"
            temp.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, make_fixture.BASE, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_"))}
            env.update(WINGBOX_BASE=base, WINGBOX_HEAD=head, WINGBOX_VERSION="test", WINGBOX_COMMENT="false", WINGBOX_UPLOAD_MAP="true",
                       GITHUB_RUN_ID="7", GITHUB_REPOSITORY="owner/repo", RUNNER_TEMP=str(temp),
                       GITHUB_STEP_SUMMARY=str(temp / "summary.md"), GITHUB_OUTPUT=str(temp / "output.txt"))
            done = subprocess.run([sys.executable, str(ROOT / "wingbox_pr.py")], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            outputs = dict(line.split("=", 1) for line in (temp / "output.txt").read_text(encoding="utf-8").splitlines())
            report = Path(outputs["report"]).read_text(encoding="utf-8")
            self.assertEqual([], check_fixture.missing(report))
            self.assertEqual(("2", "0", "1"), (outputs["untouched-dependents"], outputs["introduced-findings"], outputs["not-checked-parts"]))
            self.assertEqual(report + "\n", (temp / "summary.md").read_text(encoding="utf-8"))
            self.assertIn(report.strip(), done.stdout)
            self.assertTrue(Path(outputs["map"]).is_file())
            self.assertEqual(temp / "wingbox-map" / "map.html", Path(outputs["map"]))
            self.assertIn("Interactive map: the artifact `wingbox-map` of [this workflow run](https://github.com/owner/repo/actions/runs/7).", report)
            status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
            self.assertEqual("", status)
            worktrees = subprocess.run(["git", "-C", str(repo), "worktree", "list"], check=True, capture_output=True, text=True).stdout
            self.assertEqual(1, len(worktrees.splitlines()))
            self.assertEqual({".git", ".wingbox", "app", "docs"}, {p.name for p in repo.iterdir()})

    def test_without_the_input_no_map_leaves_the_checkout(self) -> None:
        """/* REQ-ACTION-021: without `upload-map` the script copies no map, gives an empty `map` output and the report does not mention one. */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, temp = Path(tmp) / "repo", Path(tmp) / "runner"
            temp.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, make_fixture.BASE, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_"))}
            env.update(WINGBOX_BASE=base, WINGBOX_HEAD=head, WINGBOX_COMMENT="false", RUNNER_TEMP=str(temp), GITHUB_OUTPUT=str(temp / "output.txt"))
            done = subprocess.run([sys.executable, str(ROOT / "wingbox_pr.py")], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertRegex((temp / "output.txt").read_text(encoding="utf-8"), re.compile(r"^map=$", re.M))
            self.assertEqual(["output.txt", "wingbox-report.md"], sorted(p.name for p in temp.iterdir()))
            self.assertNotIn("Interactive map", done.stdout)


if __name__ == "__main__":
    unittest.main()
