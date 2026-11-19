"""The coverage of a run: what was analysed, what was left out, and whether the result speaks for the whole."""
import importlib.util
import json
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
import wingbox_coverage as coverage  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_facts as facts  # noqa: E402
import wingbox_pr as pr  # noqa: E402
from test_evidence_record import RUN, problems  # noqa: E402
from test_policy_run import run  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

SCHEMA = json.loads((ROOT / "schema" / "wingbox-evidence-2.schema.json").read_text(encoding="utf-8"))
HEAD = "b" * 40
STRICT = [
    '(violation-known src/a src/b :src-layer domain :tgt-layer infra :rule "domain -> infra" :known ".wingbox/baseline.sexp" "accepted")',
    "(boundaries-not-checked :part web :reason declared-rules-not-read :files (.dependency-cruiser.js) :files-in-part 120)",
    "(boundaries-summary :violations 0 :known 1 :type-only 0 :candidates 0 :checked 54)",
    "(boundaries-partial :checked 54 :not-checked 120)",
    '(boundaries-not-checked :part "-" :reason in-no-part :files-in-part 3)',
    "(boundaries-checked :part . :style layered :modules 40)",
    '(boundaries-checked :part "packages/api" :style nestjs :modules 14)',
    "(boundaries-result :status incomplete :checked 54 :not-checked 123 :violations 0 :known 1 :reason part-not-checked)",
]
GRAPH = [
    "(code-graph :schema 1 :files 5 :nodes 5 :edges 3 :violations 0)",
    f"(built-from :tree 4c3c798f0d632d7dc3de0ab6455690ba :commit {HEAD} :wingbox 0.14.0)",
    '(node sr0 ts "general module" :naming snake_case :pkg shop)',
    '(node sr1 ts "general module" :naming snake_case :pkg shop)',
    '(node sr2 tsx "component" :pkg shop)',
    '(node to0 py "general module")',
    '(node to1 py "general module")',
    '(arch-part "." :style layered :state configured :origin config :level container :kind "npm package" :files 5)',
]
FRESH = facts.Graph("FRESH", "", "4c3c798f0d632d7dc3de0ab6455690ba", HEAD, "0.14.0")


def scanned(**changes: object) -> facts.Scan:
    """A scan of a run with nothing left out."""
    values: dict = dict(languages=(("py", 2), ("ts", 3)), parts=(facts.Part(".", "layered", 5, True),), graph=FRESH)
    values.update(changes)
    return facts.Scan(**values)


def whole(**changes: object) -> "facts.Report":
    """A report of a complete run: every part checked, the graph fresh, the tree at the head."""
    values: dict = dict(introduced=(), not_checked=(), tree=pr.Tree(HEAD, "head"), scan=scanned())
    values.update(changes)
    return report(**values)


class PartsTest(unittest.TestCase):
    def test_the_parts_come_from_the_strict_account(self) -> None:
        """/* REQ-ACTION-152: from `wingbox boundaries --strict` each checked part is read with its style and modules and each part that was not checked with its reason and files, files outside every part included; without a `boundaries-result` line (a Wingbox before 0.13) the parts are not known; the files outside every part do not change the result of the boundary check, only the coverage */"""
        self.assertEqual((facts.Part("-", "", 3, False, "in-no-part"), facts.Part(".", "layered", 40, True),
                          facts.Part("packages/api", "nestjs", 14, True), facts.Part("web", "", 120, False, "declared-rules-not-read")),
                         coverage.parts(STRICT))
        self.assertIsNone(coverage.parts(STRICT[:4]))
        self.assertIsNone(coverage.parts(["(error no-match --strict)"]))
        self.assertEqual((facts.Part(".", "layered", 14, True),), coverage.parts(
            ["(boundaries-ok :checked 14)", "(boundaries-checked :part . :style layered :modules 14)",
             "(boundaries-result :status pass :checked 14 :not-checked 0 :violations 0 :known 0)"]))
        _found, not_checked = facts.findings(STRICT)
        self.assertEqual([facts.NotChecked("web", "declared-rules-not-read")], not_checked)
        outside = whole(scan=scanned(parts=(facts.Part("-", "", 3, False, "in-no-part"), facts.Part(".", "layered", 40, True))))
        self.assertEqual("PASS", pr.result(outside)[0])
        self.assertEqual((False, ("not-checked: - (in-no-part)",), 3), (coverage.complete(outside), coverage.reasons(outside), coverage.in_no_part(outside)))


class GraphTest(unittest.TestCase):
    def test_the_languages_are_counted_from_the_graph(self) -> None:
        """/* REQ-ACTION-153: the files analysed per language are the `node` facts of the graph by their language; a language the build left out is listed with the files it skipped and none analysed */"""
        self.assertEqual((("py", 2), ("ts", 2), ("tsx", 1)), coverage.languages(GRAPH))
        self.assertEqual((), coverage.languages([]))
        record = coverage.record(whole(scan=scanned(languages=coverage.languages(GRAPH)), skipped_languages=(("csharp", 214),)), "owner/repo")
        self.assertEqual([{"language": "csharp", "analysed": 0, "skipped": 214}, {"language": "py", "analysed": 2, "skipped": 0},
                          {"language": "ts", "analysed": 2, "skipped": 0}, {"language": "tsx", "analysed": 1, "skipped": 0}], record["languages"])

    def test_a_stale_graph_and_an_unknown_state_are_their_own_values(self) -> None:
        """/* REQ-ACTION-154: the state of the graph is FRESH (built by this run from the commit checked, no command found a changed file), STALE (a Wingbox command says the graph misses changed files, or it was built from another commit) or UNKNOWN (the build failed, a command could not confirm the graph, or the graph has no `built-from` fact, as before Wingbox 0.13); the notes are read from what the commands print */"""
        said = coverage.notes("[wingbox] WARNING: (wingbox-graph-stale :reason auto-refresh-off :changed-files 2)\n(boundaries-ok :checked 14)\n"
                              '[wingbox] WARNING: (wingbox-graph-unconfirmed :reason not-built-here :hint "rebuild: wingbox build .")\n')
        self.assertEqual((("stale", "auto-refresh-off", 2), ("unconfirmed", "not-built-here", 0)), said)
        self.assertEqual((), coverage.notes("(boundaries-ok :checked 14)\n"))
        self.assertEqual(FRESH, coverage.graph_state(GRAPH, (), HEAD, False))
        self.assertEqual("FRESH", coverage.graph_state(GRAPH, (), "", False).state)
        without_commit = [GRAPH[1].replace(f" :commit {HEAD}", "")]
        self.assertEqual(facts.Graph("FRESH", "", "4c3c798f0d632d7dc3de0ab6455690ba", "", "0.14.0"), coverage.graph_state(without_commit, (), HEAD, False))
        stale = coverage.graph_state(GRAPH, said[:1], HEAD, False)
        self.assertEqual(("STALE", "auto-refresh-off", 2, HEAD), (stale.state, stale.reason, stale.stale_files, stale.commit))
        self.assertEqual(("STALE", "built-from-another-commit"), (lambda g: (g.state, g.reason))(coverage.graph_state(GRAPH, (), "c" * 40, False)))
        self.assertEqual(("UNKNOWN", "not-built-here"), (lambda g: (g.state, g.reason))(coverage.graph_state(GRAPH, said[1:], HEAD, False)))
        self.assertEqual(facts.Graph("UNKNOWN", "no-built-from"), coverage.graph_state(GRAPH[:1] + GRAPH[2:], (), HEAD, False))
        self.assertEqual(("UNKNOWN", "build-failed"), (lambda g: (g.state, g.reason))(coverage.graph_state(GRAPH, (), HEAD, True)))
        for graph in (stale, facts.Graph("UNKNOWN", "no-built-from")):
            partial = whole(scan=scanned(graph=graph))
            self.assertFalse(coverage.complete(partial))
            self.assertEqual(graph.state, coverage.record(partial, "")["graph"]["state"])
        self.assertEqual(("graph: STALE (auto-refresh-off, 2 changed file(s))",), coverage.reasons(whole(scan=scanned(graph=stale))))
        self.assertEqual(("graph: UNKNOWN (no-built-from)",), coverage.reasons(whole(scan=scanned(graph=facts.Graph("UNKNOWN", "no-built-from")))))


class LeftOutTest(unittest.TestCase):
    def test_what_git_and_wingbox_leave_out_is_counted(self) -> None:
        """/* REQ-ACTION-155: tracked source files in a folder Wingbox leaves out by its name (vendor, node_modules, dist, build, target and the like) are counted per folder name; what git ignores on disk is counted as source files and named as folders, `.wingbox/` apart; the method calls without a visible receiver type are summed from the use file when it records positions, and are not counted otherwise */"""
        tracked = ["src/a.ts", "vendor/lib/x.go", "vendor/lib/y.go", "vendor/README.md", "web/node_modules/p/index.js", "tools/build/gen.py",
                   "docs/build.md", "buildings/a.py"]
        self.assertEqual((("build", 1), ("node_modules", 1), ("vendor", 2)), coverage.left_out(tracked))
        self.assertEqual((), coverage.left_out(["src/a.ts", "README.md"]))
        self.assertEqual((2, ("node_modules/", "out/")), coverage.ignored(
            ["node_modules/", "out/", "gen/client.ts", "gen/schema.json", "local_settings.py", ".wingbox/", ".wingbox/index.sexp", ""]))
        uses = ["(code-graph-uses :schema 1 :key aa :positions yes :graph bb)", "(unresolved cc0 :method-calls 1216)",
                "(unresolved cc1 :method-calls 9)", "(unresolved-call cc0 backspace 3002:45)", "(use cc0 cc1::x)"]
        self.assertEqual(1225, coverage.unresolved_calls(uses))
        self.assertEqual(0, coverage.unresolved_calls(uses[:1]))
        self.assertIsNone(coverage.unresolved_calls(["(code-graph-uses :schema 1 :key aa :positions no :graph bb)"]))
        self.assertIsNone(coverage.unresolved_calls([]))


class RecordTest(unittest.TestCase):
    def test_a_complete_run_says_so_with_the_numbers(self) -> None:
        """/* REQ-ACTION-156: the evidence record has a `coverage` object: the repository, the commit checked and how it relates to the head, the languages with the files analysed and skipped, the parts with style, modules and whether they were checked, what was excluded (by `paths` with its patterns, by git's ignore rules, by the folders Wingbox leaves out), the state of the graph with what it was built from, and the fixed list of what a static graph does not see with the count Wingbox has; a run that left nothing out is `complete: true` with no reason; the record meets the version 2 schema */"""
        full = whole(scan=scanned(patterns=("src/*",), ignored_files=2, ignored_folders=("node_modules/",), folders=(("vendor", 2),),
                                  unresolved_calls=37))
        record = we.build_evidence(full, RUN, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual({
            "complete": True,
            "reasons": [],
            "repository": "owner/repo",
            "commit": HEAD,
            "tree": "head",
            "languages": [{"language": "py", "analysed": 2, "skipped": 0}, {"language": "ts", "analysed": 3, "skipped": 0}],
            "parts": [{"part": ".", "style": "layered", "modules": 5, "checked": True, "reason": ""}],
            "excluded": {"paths": {"patterns": ["src/*"], "changed-files": []},
                         "gitignore": {"files": 2, "folders": ["node_modules/"]},
                         "folders": [{"folder": "vendor", "files": 2}]},
            "graph": {"state": "FRESH", "reason": "", "tree": "4c3c798f0d632d7dc3de0ab6455690ba", "commit": HEAD, "wingbox": "0.14.0",
                      "stale-files": 0},
            "not-seen": [{"what": "reflection", "counted": False, "sites": 0}, {"what": "imports built from strings", "counted": False, "sites": 0},
                         {"what": "calls through an untyped receiver", "counted": True, "sites": 37}],
        }, record["coverage"])
        self.assertEqual("true", pr.outputs(full)["coverage-complete"])
        self.assertIn("(coverage :complete true :graph FRESH)", pr.build_plan(full).splitlines())

    def test_each_kind_of_partial_run_has_its_entry(self) -> None:
        """/* REQ-ACTION-156: a skipped language, a part that was not checked, changed files left out by `paths`, a checkout that is not the commit named, files that differ from the commit, a tool error, a graph that is stale or of unknown state, and a Wingbox that does not name the parts it checked each make `complete` false with their reason and their entry in the record; a report without a scan is never complete; the output `coverage-complete` says the same, and the plan of a run that looked has a `coverage` line and a `coverage-gap` line per reason */"""
        kinds = {
            "skipped-language: csharp (214 files)": whole(skipped_languages=(("csharp", 214),)),
            "not-checked: web (declared-rules-not-read)": whole(scan=scanned(parts=(facts.Part(".", "layered", 5, True),
                                                                                    facts.Part("web", "", 120, False, "declared-rules-not-read")))),
            "paths-filter: 2 changed file(s) left out": whole(skipped=("docs/a.md", "gen/b.ts"), scan=scanned(patterns=("src/*", "!gen/*"))),
            f"tree-not-at-head: checked {'c' * 12}, named {'b' * 12}": whole(tree=pr.Tree("c" * 40, "other")),
            "tree-differs-from-commit: 1 file(s)": whole(tree=pr.Tree(HEAD, "head", ("src/a.ts",))),
            "tool-error: wingbox usages exited 70": whole(tool_errors=("wingbox usages exited 70",)),
            "graph: STALE (refresh-failed, 3 changed file(s))": whole(scan=scanned(graph=facts.Graph("STALE", "refresh-failed", stale_files=3))),
            "parts: UNKNOWN (wingbox-names-no-checked-part)": whole(scan=scanned(parts=None)),
            "graph: UNKNOWN (not-read)": whole(scan=None),
        }
        for reason, partial in kinds.items():
            record = we.build_evidence(partial, RUN, {})
            self.assertEqual([], problems(record, SCHEMA), reason)
            self.assertEqual((False, [reason]), (record["coverage"]["complete"], record["coverage"]["reasons"]), reason)
            self.assertEqual("false", pr.outputs(partial)["coverage-complete"], reason)
        record = we.build_evidence(kinds["paths-filter: 2 changed file(s) left out"], RUN, {})
        self.assertEqual({"patterns": ["src/*", "!gen/*"], "changed-files": ["docs/a.md", "gen/b.ts"]}, record["coverage"]["excluded"]["paths"])
        old = whole(scan=scanned(parts=None, graph=facts.Graph("UNKNOWN", "no-built-from")), not_checked=(facts.NotChecked("web", "no-layer-style"),))
        self.assertEqual([{"part": "web", "style": "", "modules": 0, "checked": False, "reason": "no-layer-style"}],
                         coverage.record(old, "")["parts"])
        self.assertEqual(("not-checked: web (no-layer-style)", "graph: UNKNOWN (no-built-from)", "parts: UNKNOWN (wingbox-names-no-checked-part)"),
                         coverage.reasons(old))
        plan = pr.build_plan(kinds["graph: STALE (refresh-failed, 3 changed file(s))"]).splitlines()
        self.assertIn("(coverage :complete false :graph STALE)", plan)
        self.assertIn('(coverage-gap "graph: STALE (refresh-failed, 3 changed file(s))")', plan)
        self.assertFalse([line for line in pr.build_plan(report()).splitlines() if line.startswith("(coverage")])


class RunTest(unittest.TestCase):
    def test_the_boundaries_are_asked_in_strict_mode_and_as_before_of_an_older_wingbox(self) -> None:
        """/* REQ-ACTION-157: the check asks `wingbox boundaries --strict`; a Wingbox before 0.13 takes the word for a module name and prints no `boundaries-result` line, and is then asked as before, so its findings are read all the same; what a command prints about a stale graph is kept for the folder it ran in */"""
        asked: list[list[str]] = []
        answers = {("boundaries", "--strict"): "(error no-match --strict)\n",
                   ("boundaries",): '(violation src/a src/b :rule "domain -> api" "x")\n(boundaries-summary :violations 1 :known 0 :checked 9)\n'}

        def old_wingbox(argv: list[str], cwd: str) -> subprocess.CompletedProcess:
            asked.append(argv[4:])
            return subprocess.CompletedProcess(argv, 0, answers.get(tuple(argv[4:]), ""), "")

        def new_wingbox(argv: list[str], cwd: str) -> subprocess.CompletedProcess:
            asked.append(argv[4:])
            stderr = "[wingbox] WARNING: (wingbox-graph-stale :reason auto-refresh-off :changed-files 1)\n" if argv[4] == "boundaries" else ""
            return subprocess.CompletedProcess(argv, 0, "\n".join(STRICT) + "\n" if argv[4] == "boundaries" else "", stderr)

        real_run, errors = pr._run, list(pr.TOOL_ERRORS)
        try:
            pr._run = old_wingbox
            found, _not_checked = facts.findings(pr._boundaries("/old"))
            self.assertEqual(["violation"], [finding.head for finding in found])
            self.assertEqual([["build", "/old"], ["boundaries", "--strict"], ["boundaries"]], asked)
            self.assertNotIn("/old", pr.GRAPH_NOTES)
            del asked[:]
            pr._run = new_wingbox
            self.assertEqual(STRICT, pr._boundaries("/new"))
            self.assertEqual([["build", "/new"], ["boundaries", "--strict"]], asked)
            self.assertEqual([("stale", "auto-refresh-off", 1)], pr.GRAPH_NOTES["/new"])
        finally:
            pr._run = real_run
            pr.TOOL_ERRORS[:] = errors
            pr.GRAPH_NOTES.clear()


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class WholeScriptTest(unittest.TestCase):
    def test_a_complete_run_on_a_real_repository(self) -> None:
        """/* REQ-ACTION-158: the whole script with a real Wingbox on the layered project, a change that edits one module and no policy file: the record says `coverage.complete: true`, with the fourteen TypeScript files analysed, the one part checked with its style, the graph FRESH and built from the head commit, and nothing excluded */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = layers.repository(Path(tmp))
            head = layers.branch(repo, "feature", layers.UNRELATED)
            done, plan, record, _report = run(repo, Path(tmp) / "out", base, head, WINGBOX_FAIL_ON="strict")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            found = record["coverage"]
            self.assertEqual((True, [], head, "head"), (found["complete"], found["reasons"], found["commit"], found["tree"]))
            self.assertEqual([{"language": "ts", "analysed": 14, "skipped": 0}], found["languages"])
            self.assertEqual([{"part": ".", "style": "layered", "modules": 14, "checked": True, "reason": ""}], found["parts"])
            self.assertEqual(("FRESH", "", head, 0), (found["graph"]["state"], found["graph"]["reason"], found["graph"]["commit"], found["graph"]["stale-files"]))
            self.assertRegex(found["graph"]["tree"], r"^[0-9a-f]{32}$")
            self.assertEqual({"paths": {"patterns": [], "changed-files": []}, "gitignore": {"files": 0, "folders": []}, "folders": []}, found["excluded"])
            self.assertIn("(coverage :complete true :graph FRESH)", plan)
            self.assertEqual([], problems(record, SCHEMA))

    def test_a_partial_run_on_a_real_repository(self) -> None:
        """/* REQ-ACTION-158: the whole script on the Python project whose one part is too small to judge, with a `paths` input that leaves a changed file out, a vendored folder and a folder git ignores: the record says `coverage.complete: false` with both reasons, names the part as not checked, the file and the patterns, and counts the vendored and the ignored files */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            base = make_fixture.commit(repo, {**make_fixture.BASE, "vendor/lib/tool.py": "X = 1\n", ".gitignore": "generated/\n"}, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            layers.write(repo, {"generated/client.py": "Y = 2\n"})
            done, _plan, record, _report = run(repo, Path(tmp) / "out", base, head, WINGBOX_PATHS="app/*\n")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            found = record["coverage"]
            self.assertFalse(found["complete"])
            self.assertEqual(["not-checked: . (too-few-files)", "paths-filter: 1 changed file(s) left out"], found["reasons"])
            self.assertEqual([{"part": ".", "style": "", "modules": 8, "checked": False, "reason": "too-few-files"}], found["parts"])
            self.assertEqual({"paths": {"patterns": ["app/*"], "changed-files": ["docs/notes.md"]},
                              "gitignore": {"files": 0, "folders": ["generated/"]}, "folders": [{"folder": "vendor", "files": 1}]}, found["excluded"])
            self.assertEqual([{"language": "py", "analysed": 8, "skipped": 0}], found["languages"])
            self.assertEqual("FRESH", found["graph"]["state"])


class DocumentsTest(unittest.TestCase):
    def test_the_coverage_is_documented(self) -> None:
        """/* REQ-ACTION-159: action.yml has the output `coverage-complete`; README.md documents the `coverage` object of the record with its parts (languages, parts, excluded, graph, not-seen), the three states of the graph, the output and the plan line, and the schema describes the object */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn("\n  coverage-complete:\n    description: ", action)
        self.assertIn("value: ${{ steps.check.outputs.coverage-complete }}", action)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for piece in ("`coverage`", "`coverage.complete`", "`languages`", "`parts`", "`excluded`", "`graph`", "`not-seen`", "`FRESH`", "`STALE`",
                      "`UNKNOWN`", "`coverage-complete`", "WINGBOX_COVERAGE_COMPLETE", "(coverage :complete "):
            self.assertIn(piece, readme, piece)
        self.assertEqual(["complete", "reasons", "repository", "commit", "tree", "languages", "parts", "excluded", "graph", "not-seen"],
                         SCHEMA["properties"]["coverage"]["required"])
        self.assertIn("coverage", SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()
