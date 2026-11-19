"""The action's pure report functions, with fixtures, its metadata and the version bump."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("wingbox_pr", ROOT / "wingbox_pr.py")
pr = importlib.util.module_from_spec(_spec)
sys.modules["wingbox_pr"] = pr   # dataclasses look their module up
_spec.loader.exec_module(pr)

BASE_BOUNDARIES = [
    '(violation-candidate src/utils/access src/repositories/access.repository :src-layer shared :tgt-layer infra :via imports :lang ts :part . :style nestjs :rule "shared -> infra" :src-file /w/src/utils/access.ts :tgt-file /w/src/repositories/access.repository.ts "utility depends on infrastructure; move it to services or invert")',
    '(violation-known src/a src/b :src-layer domain :tgt-layer infra :known ".wingbox/baseline.sexp" "accepted")',
    "(boundaries-summary :violations 0 :known 1 :type-only 0 :candidates 1 :checked 40)",
]
HEAD_BOUNDARIES = BASE_BOUNDARIES[:2] + [
    '(violation src/dtos/order.dto src/services/order.service :src-layer domain :tgt-layer service :via imports :lang ts :part . :style nestjs :rule "domain -> service" :src-file /w/src/dtos/order.dto.ts :tgt-file /w/src/services/order.service.ts "an entity or DTO imports application code")',
    '(violation-type-only src/dtos/x src/repositories/y :src-layer domain :tgt-layer infra :rule "domain -> infra" "types only")',
    "(capability-violation src/services/order.service.ts :capability db-client :layer service :allowed (infra module) :line 2 :evidence \"import { Insertable } from 'kysely';\" :part . :style nestjs :fix \"keep the ORM client in repositories\")",
    "(pattern-violation src/api/orders.controller.ts :pattern controller-skips-service :target src/repositories/order.repository.ts :part . :style nestjs \"a controller reads a repository\")",
    "(boundaries-not-checked :part web :reason declared-rules-not-read :files (.dependency-cruiser.js) :files-in-part 120)",
    "(boundaries-summary :violations 1 :known 1 :type-only 1 :candidates 1 :checked 41)",
]
IMPACT = [
    "(impact-root src/models/order :lang ts :file src/models/order.ts)",
    "(impact src/models/order src/services/order.service :lang ts :via imports :depth 1)",
    "(impact src/models/order src/api/orders.controller :lang ts :via imports :depth 2)",
    "(impact src/models/order src/services/order.service :lang ts :via calls :depth 2)",
    "(impact src/models/order src/jobs/invoice.job :lang ts :via imports :depth 2)",
]


def report(**changes) -> "pr.Report":
    root, found = pr.dependents(IMPACT)
    head, not_checked = pr.findings(HEAD_BOUNDARIES)
    base, _ = pr.findings(BASE_BOUNDARIES)
    new, resolved = pr.introduced(base, head)
    values = dict(
        version="0.7.2", hops=2, base="a" * 40, head="b" * 40,
        changed=(
            pr.ChangedFile("src/models/order.ts", root, pr.untouched(found, {"src/models/order", "src/services/order.service"})),
            pr.ChangedFile("README.md", None),
        ),
        introduced=tuple(new), resolved=resolved, not_checked=tuple(not_checked),
    )
    values.update(changes)
    return pr.Report(**values)


class ParseTest(unittest.TestCase):
    def test_lines_of_wingbox_output_are_read_into_head_arguments_and_keywords(self) -> None:
        """/* REQ-ACTION-001: one S-expression line of wingbox output is read into its head, positional arguments and keywords; quoted strings lose their quotes and escapes, a list stays one value, and a line that is not an S-expression gives nothing. */"""
        head, args, keys = pr.parse_line(HEAD_BOUNDARIES[4])
        self.assertEqual(("capability-violation", ["src/services/order.service.ts"]), (head, args))
        self.assertEqual("(infra module)", keys["allowed"])
        self.assertEqual("import { Insertable } from 'kysely';", keys["evidence"])
        self.assertIsNone(pr.parse_line("Impact of changing src/x.ts"))


class FindingsTest(unittest.TestCase):
    def test_the_findings_a_pull_request_introduces_are_those_the_base_has_not(self) -> None:
        """/* REQ-ACTION-002: from `wingbox boundaries` of base and head, the report keeps violations, candidates, capability and pattern violations that the head has and the base has not (known and type-only imports are not findings), counts the base findings that are gone, and lists every part that was not checked with its reason. */"""
        head, not_checked = pr.findings(HEAD_BOUNDARIES)
        base, _ = pr.findings(BASE_BOUNDARIES)
        new, resolved = pr.introduced(base, head)
        self.assertEqual(["violation", "capability-violation", "pattern-violation"], [f.head for f in new])
        self.assertEqual(0, resolved)
        self.assertEqual([pr.NotChecked("web", "declared-rules-not-read")], not_checked)
        self.assertEqual((["violation"], 0), ([f.head for f in pr.introduced(base, head)[0][:1]], pr.introduced(head, head)[1]))
        self.assertEqual(3, pr.introduced(head, base)[1])


class DependentsTest(unittest.TestCase):
    def test_dependents_the_pull_request_does_not_change_come_nearest_first_and_once(self) -> None:
        """/* REQ-ACTION-003: the dependents of a changed file within N hops, from `wingbox impact --sexp`, leave out the modules the pull request changes, keep each module once at its nearest depth, and come nearest first; the paths filter keeps glob matches and leaves out `!pattern` matches. */"""
        root, found = pr.dependents(IMPACT)
        self.assertEqual("src/models/order", root)
        kept = pr.untouched(found, {"src/models/order"})
        self.assertEqual(
            (pr.Dependent("src/services/order.service", 1), pr.Dependent("src/api/orders.controller", 2), pr.Dependent("src/jobs/invoice.job", 2)),
            kept,
        )
        self.assertEqual(
            (["src/a.ts", "src/b.py"], ["docs/x.md", "src/gen/c.ts"]),
            pr.selected(["src/a.ts", "docs/x.md", "src/b.py", "src/gen/c.ts"], ["src/*", "!src/gen/*"]),
        )
        self.assertEqual(("src/a", "lib/x"), (pr.module_of("src/a.ts"), pr.module_of("lib/x.py")))


class CommentTest(unittest.TestCase):
    def test_the_comment_names_forgotten_dependents_new_findings_and_what_was_not_checked(self) -> None:
        """/* REQ-ACTION-004: the comment starts with its HTML marker (the action finds and updates its own comment by it), names each changed file with the dependents it does not change, the files outside the graph, every introduced finding, and says in one line which parts were not checked and that no finding there is not a pass; it ends with the version and the commits. */"""
        body = pr.build_comment(report())
        self.assertTrue(body.startswith(pr.MARKER))
        self.assertIn("## Wingbox: 3 new boundary findings\n", body)
        self.assertLess(body.index("### New boundary findings"), body.index("<details>"))
        self.assertIn("\n\nWithin 2 hops, by folder: `src/api/` 1, `src/jobs/` 1.", body)
        self.assertIn("Not in the code graph (1: documents, configuration or deleted files): `README.md`.", body)
        self.assertIn('- violation: `src/dtos/order.dto` -> `src/services/order.service` (domain -> service)', body)
        self.assertIn("- capability-violation: `src/services/order.service.ts:2` uses db-client in layer service", body)
        self.assertIn("**Not checked**: part `web` (its declared rules could not be read). No finding is reported there, and that is not a pass.", body)
        self.assertIn("[Wingbox](https://wingbox.dev) 0.7.2, base `aaaaaaa`, head `bbbbbbb`", body)

    def test_a_clean_pull_request_says_none_and_long_lists_are_cut(self) -> None:
        """/* REQ-ACTION-005: the comment leads with the counts; it names at most LIST_LIMIT direct dependents (each module once, with the changed files it uses) and counts the rest by folder; farther dependents are counted by folder and tests apart; a clean pull request says Nothing and None; a pull request that changes no source module outside the tests gets one line, and the action creates no comment for it (it only updates one it wrote before). */"""
        many = tuple(pr.Dependent(f"src/m{index:02d}", 1) for index in range(pr.LIST_LIMIT + 4)) + (
            pr.Dependent("docs_src/body/tutorial1", 2), pr.Dependent("docs_src/body/tutorial2", 2), pr.Dependent("tests/test_core", 1))
        body = pr.build_comment(report(changed=(pr.ChangedFile("src/core.ts", "src/core", many), pr.ChangedFile("src/util.ts", "src/util", many[:1])),
                                       introduced=(), not_checked=()))
        self.assertIn("## Wingbox: 14 modules outside this pull request use what it changes\n", body)
        self.assertIn("- `src/m00` uses `src/core.ts`, `src/util.ts`", body)
        self.assertIn("- ... and 4 more, by folder: `src/` 4", body)
        self.assertIn("Within 2 hops, by folder: `docs_src/body/` 2.", body)
        self.assertIn("Tests that use the changed code (1), by folder: `tests/` 1.", body)
        self.assertNotIn("Not checked", body)
        clean = pr.build_comment(report(changed=(pr.ChangedFile("src/leaf.ts", "src/leaf", ()),), introduced=(), not_checked=()))
        self.assertIn("**Wingbox**: no module outside this pull request uses the files it changes directly, and it adds no boundary finding.", clean)
        self.assertNotIn("None.", clean)
        docs = report(changed=(pr.ChangedFile("README.md", None), pr.ChangedFile("tests/test_x.py", "tests/test_x", ())), introduced=(), not_checked=())
        self.assertFalse(pr.needs_comment(docs))
        self.assertEqual(4, pr.build_comment(docs).count("\n"))
        self.assertIn("nothing to check", pr.build_comment(docs))

    def test_a_module_facade_or_a_declaration_is_counted_not_listed(self) -> None:
        """/* REQ-ACTION-011: a module that only declares a changed file (its parent module: Rust mod.rs or folder.rs, Python __init__, an index) and the users of a changed facade (mod.rs, lib.rs, __init__.py, index.ts) are counted by folder, not listed as direct uses; the headline counts only the direct uses of ordinary files. */"""
        changed = (
            pr.ChangedFile("src/infra/session_detail.rs", "src/infra/session_detail", (pr.Dependent("src/infra", 1), pr.Dependent("src/app", 2))),
            pr.ChangedFile("src/domain/mod.rs", "src/domain", (pr.Dependent("src/app/fork", 1), pr.Dependent("src/infra/claude", 1))),
            pr.ChangedFile("src/domain/stats.rs", "src/domain/stats", (pr.Dependent("src/ui", 1),)),
        )
        body = pr.build_comment(report(changed=changed, introduced=(), not_checked=()))
        self.assertIn("## Wingbox: 1 module outside this pull request uses what it changes\n", body)
        self.assertIn("This module uses `src/domain/stats.rs`, which this pull request changes.", body)
        self.assertIn("\n- `src/ui`\n", body)
        self.assertIn("3 through a facade or declaration, 1 within 2 hops", body)
        self.assertIn("Through `src/domain/mod.rs`, `src/infra/session_detail.rs` (a module facade or the declaration of the file): 3 module(s)", body)
        self.assertTrue(pr.is_parent("src/infra", "src/infra/session_detail.rs") and pr.is_parent("pkg/__init__", "pkg/x.py"))
        self.assertFalse(pr.is_parent("src/app", "src/infra/session_detail.rs"))

    def test_a_facade_and_its_children_changed_together_list_only_depth_one_uses(self) -> None:
        """/* REQ-ACTION-012: when a pull request changes a facade (src/domain/mod.rs) and children it re-exports (gemini.rs, skill_span.rs), a module that imports the facade is a direct user of the facade only: under it the comment names only the changed files it uses at depth 1, never a child it reaches through the facade at depth 2, and such a module is counted with the facade users, not as a direct use. */"""
        changed = (
            pr.ChangedFile("src/domain/mod.rs", "src/domain", (pr.Dependent("src/app/fork", 1), pr.Dependent("src/infra/claude", 1))),
            pr.ChangedFile("src/domain/gemini.rs", "src/domain/gemini", (pr.Dependent("src/app/fork", 2), pr.Dependent("src/infra/claude", 2))),
            pr.ChangedFile("src/domain/skill_span.rs", "src/domain/skill_span", (pr.Dependent("src/app/fork", 2), pr.Dependent("src/ui", 1))),
        )
        body = pr.build_comment(report(changed=changed, introduced=(), not_checked=()))
        self.assertIn("## Wingbox: 1 module outside this pull request uses what it changes\n", body)
        self.assertIn("This module uses `src/domain/skill_span.rs`, which this pull request changes.", body)
        self.assertIn("\n- `src/ui`\n", body)
        self.assertNotIn("`src/app/fork` uses", body)
        self.assertIn("Through `src/domain/mod.rs` (a module facade or the declaration of the file): 2 module(s)", body)

    def test_test_files_are_known_by_folder_and_name(self) -> None:
        """/* REQ-ACTION-010: a test is a module in a test folder (test, tests, __tests__, spec, e2e) or named like a test in Python, JavaScript and TypeScript, Go, Java and C#; other modules are not. */"""
        for path in ("tests/x.py", "src/__tests__/a.ts", "pkg/test_orders.py", "pkg/orders_test.go", "web/a.spec.ts", "web/a.test.tsx",
                     "src/OrderTest.java", "Shop.Tests/OrderTests.cs"):
            self.assertTrue(pr.is_test(path), path)
        for path in ("src/contest.py", "src/latest.ts", "app/testimony/view.py", "src/Testimonial.cs"):
            self.assertFalse(pr.is_test(path), path)


class ExitStatusTest(unittest.TestCase):
    def test_fail_on_decides_the_exit_status(self) -> None:
        """/* REQ-ACTION-006: fail-on none never fails; violations fails on an introduced violation, capability or pattern violation (not on a candidate); not-checked fails when a part was not checked. */"""
        self.assertEqual(0, pr.exit_status(report(), "none"))
        self.assertEqual(1, pr.exit_status(report(), "violations"))
        self.assertEqual(1, pr.exit_status(report(), "not-checked"))
        candidate = (pr.Finding("violation-candidate", ("k",), "x"),)
        self.assertEqual(0, pr.exit_status(report(introduced=candidate, not_checked=()), "violations"))
        self.assertEqual(0, pr.exit_status(report(not_checked=()), "not-checked"))


class MetadataTest(unittest.TestCase):
    def test_the_metadata_documents_every_input_and_runs_the_script_from_the_action(self) -> None:
        """/* REQ-ACTION-007: action.yml has the name, description, author and branding the Marketplace asks for, documents every input the script reads, runs the script from the action's own folder, and README.md names the same default wingbox release as action.yml. */"""
        text = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn("name: Wingbox Pull Request Check\n", text)
        self.assertRegex(text, r"branding:\n  icon: [a-z-]+\n  color: [a-z]+\n")
        for name in ("version", "hops", "fail-on", "comment", "paths", "base", "head", "working-directory", "github-token"):
            self.assertIn(f"\n  {name}:\n", text)
            self.assertIn(f"`{name}`", (ROOT / "README.md").read_text(encoding="utf-8"), name)
        self.assertIn('"$GITHUB_ACTION_PATH/wingbox_pr.py"', text)
        default = re.search(r'  version:\n    description: [^\n]+\n    default: "([^"]+)"', text).group(1)
        self.assertIn(f"wingbox {default} from PyPI", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_the_bump_script_rewrites_the_default_release(self) -> None:
        """/* REQ-ACTION-008: scripts/bump_version.py sets the default of the version input in action.yml and the release named in README.md, and refuses an action.yml without that default; `--show` prints the current default, and the unit job of the test workflow installs that release so the end-to-end tests run there. */"""
        _bump = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump_version.py")
        bump = importlib.util.module_from_spec(_bump)
        _bump.loader.exec_module(bump)
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        new_action, new_readme = bump.with_version(action, readme, "9.8.7")
        self.assertIn('    default: "9.8.7"', new_action)
        self.assertIn("wingbox 9.8.7 from PyPI", new_readme)
        with self.assertRaises(ValueError):
            bump.with_version("name: x\n", readme, "9.8.7")
        self.assertEqual("9.8.7", bump.current(new_action))
        self.assertRegex(bump.current(action), r"^\d+\.\d+\.\d+$")
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn('"wingbox[all]==$(python -P scripts/bump_version.py --show)"', workflow)

    def test_no_local_path_or_private_name_in_the_repository(self) -> None:
        """/* REQ-ACTION-009: the repository holds English text only, no local path of a machine and no non-ASCII character in its text files. */"""
        for path in ROOT.rglob("*"):
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts and path != Path(__file__).resolve():
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.isascii(), path)
                self.assertNotRegex(text, r"/Users/[a-z]|/home/[a-z]+/|[A-Z]:\\\\Users\\\\|/private/var/", path)


if __name__ == "__main__":
    unittest.main()
