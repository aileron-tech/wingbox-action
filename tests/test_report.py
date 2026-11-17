"""The report of Wingbox 0.10 facts: what each dependent uses, the diagram block, the size cut."""
import dataclasses
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_facts as facts  # noqa: E402
import wingbox_report as report  # noqa: E402
from wingbox_facts import ChangedFile, Dependent, Route  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make(changed: tuple[ChangedFile, ...], **values: Any) -> facts.Report:
    return dataclasses.replace(facts.Report("0.10.1", 2, "a" * 40, "b" * 40, changed, (), 0, ()), **values)


def rust() -> facts.Report:
    lines = (FIXTURES / "impact_rust_reexports.sexp").read_text(encoding="utf-8").splitlines()
    root, found = facts.dependents(lines)
    return make((ChangedFile("src/infra/session_detail.rs", root, facts.untouched(found, {"src/infra/session_detail"})),))


def names(*shown: str, count: int = 0, way: str = "use", through: str = "") -> tuple[Route, ...]:
    return (Route(way, through, tuple(shown), count or len(shown)),)


class UsesTest(unittest.TestCase):
    def test_a_direct_dependent_says_what_it_uses_from_the_changed_file(self) -> None:
        """/* REQ-ACTION-014: under "Directly" each dependent names what it uses from each changed file, from the facts: at most NAMES_LIMIT names and the number of the others, a member as `Type::name`, a macro as `name!`; a dependent whose fact has no names is listed with the file alone. */"""
        body = report.build_comment(rust())
        self.assertIn("**6** module(s) not changed by this pull request use a changed file directly; 1 more through a module facade or declaration; 6 within 2 hops.", body)
        self.assertIn("- `src/cli` uses `LoadSessionTimelineError`, `load_session_timeline` from `src/infra/session_detail.rs`\n", body)
        self.assertIn("- `src/infra/claude` uses `LastAssistantOutput` from `src/infra/session_detail.rs`\n", body)
        self.assertIn("Through `src/infra/session_detail.rs` (a module facade or the declaration of the file): 1 module(s), by folder: `src/` 1.", body)
        changed = (
            ChangedFile("src/app/state.rs", "src/app/state", (
                Dependent("src/ui", 1, routes=names("AppModel", "DeleteConfirmDialog", "DeleteConfirmSelection", count=31)),
                Dependent("src/main", 1, routes=names("new", way="member", through="ProcessManager") + names("log_line", way="macro")),
                Dependent("src/plain", 1),
            )),
            ChangedFile("src/app/theme.rs", "src/app/theme", (Dependent("src/ui", 1, routes=names("ACCENT", way="glob", through="super")),)),
        )
        body = report.build_comment(make(changed))
        self.assertIn("- `src/ui` uses `AppModel`, `DeleteConfirmDialog`, `DeleteConfirmSelection` and 28 more from `src/app/state.rs`; `ACCENT` from `src/app/theme.rs`\n", body)
        self.assertIn("- `src/main` uses `ProcessManager::new`, `log_line!` from `src/app/state.rs`\n", body)
        self.assertIn("- `src/plain` uses `src/app/state.rs`\n", body)

    def test_names_make_a_user_of_a_facade_or_a_parent_module_a_direct_use(self) -> None:
        """/* REQ-ACTION-014: a dependent of a changed facade (mod.rs, __init__.py, an index) or the parent module of a changed file is a direct use when its fact names items it uses from that file; without names it stays counted with the facade users. */"""
        changed = (
            ChangedFile("src/app/mod.rs", "src/app", (
                Dependent("src/main", 1, routes=names("AppCommand", "AppData", "AppError", count=22)),
                Dependent("src/cli", 1),
            )),
            ChangedFile("src/ui/theme.rs", "src/ui/theme", (Dependent("src/ui", 1, routes=names("ACCENT", "BAR_BG")),)),
        )
        body = report.build_comment(make(changed))
        self.assertIn("**2** module(s) not changed by this pull request use a changed file directly; 1 more through a module facade or declaration.", body)
        self.assertIn("- `src/main` uses `AppCommand`, `AppData`, `AppError` and 19 more from `src/app/mod.rs`\n", body)
        self.assertIn("- `src/ui` uses `ACCENT`, `BAR_BG` from `src/ui/theme.rs`\n", body)
        self.assertIn("Through `src/app/mod.rs` (a module facade or the declaration of the file): 1 module(s), by folder: `src/` 1.", body)

    def test_a_name_cannot_break_the_markdown_of_the_comment(self) -> None:
        """/* REQ-ACTION-014: a backtick or a line break in a name or a path is replaced, so it cannot end the code span or start a line of its own. */"""
        changed = (ChangedFile("src/a`b.ts", "src/a`b", (Dependent("src/x\ny", 1, routes=names("f`g")),)),)
        body = report.build_comment(make(changed))
        self.assertIn("- `src/x y` uses `f'g` from `src/a'b.ts`\n", body)


class TestModulesTest(unittest.TestCase):
    def test_a_module_id_named_like_a_test_is_a_test(self) -> None:
        """/* REQ-ACTION-010: Wingbox names a dependent by its module ID, the path without the extension: `web/a.spec` and `web/a.test` are tests like the files `web/a.spec.ts` and `web/a.test.tsx`, and a dotted name that is no test (`src/order.service`) is not. */"""
        for module in ("web/a.spec", "web/a.test", "pkg/orders_test", "tests/unit/orders", "src/OrderTest"):
            self.assertTrue(report.is_test(module), module)
        for module in ("src/order.service", "src/app.module", "src/latest", "lib/contest.io"):
            self.assertFalse(report.is_test(module), module)
        changed = (ChangedFile("src/order.ts", "src/order", (Dependent("src/order.spec", 1), Dependent("src/order.service", 1))),)
        self.assertIn("**1** module(s) not changed by this pull request use a changed file directly; 1 test module(s).", report.build_comment(make(changed)))


class DiagramBlockTest(unittest.TestCase):
    def test_the_comment_carries_the_blast_radius_as_a_mermaid_block(self) -> None:
        """/* REQ-ACTION-019: after the counts the comment has one ```mermaid block: the changed file, the direct dependents with a solid arrow to it, the module that only declares it and the farther ones dashed, each farther one linked to the module it is reached through; a legend line follows; a pull request without dependents, or without source changes, has no diagram. */"""
        body = report.build_comment(rust())
        self.assertEqual(1, body.count("```mermaid\n"))
        self.assertLess(body.index("**6** module(s)"), body.index("### Blast radius"))
        self.assertLess(body.index("### Blast radius"), body.index("### Not changed, but uses what changed"))
        block = body.split("```mermaid\n")[1].split("\n```\n")[0]
        self.assertIn('    c0["src/infra/session_detail.rs"]\n', block)
        self.assertIn('  subgraph g0["src/"]\n    m0("cli")\n    m1("main")\n    m2("app")\n    m3("infra")\n  end\n', block)
        self.assertIn("  m0 --> c0\n", block)
        self.assertIn("  m2 -.-> m3\n", block)
        self.assertIn("  m3 -.-> c0\n", block)
        self.assertIn('  subgraph g2["src/infra/"]\n    m5("claude")\n', block)
        self.assertTrue(block.endswith("  class m2,m3,m4,m9,m10,m11,m12 far"), block)
        self.assertIn("<sub>An arrow points from a module to what it uses. Thick border: changed in this pull request. "
                      "Dashed: it reaches a changed file through a module facade, a declaration or within 2 hops.</sub>", body)
        clean = report.build_comment(make((ChangedFile("src/leaf.ts", "src/leaf", ()),)))
        self.assertNotIn("mermaid", clean)
        docs = report.build_comment(make((ChangedFile("README.md", None),)))
        self.assertNotIn("mermaid", docs)

    def test_a_farther_dependent_without_a_known_path_points_at_the_changed_file(self) -> None:
        """/* REQ-ACTION-019: when the facts do not say through which module a farther dependent is reached (a Wingbox before 0.10), its dashed arrow goes to the changed file. */"""
        changed = (ChangedFile("src/core.py", "src/core", (Dependent("src/a", 1), Dependent("src/far/b", 2))),)
        block = report.build_comment(make(changed)).split("```mermaid\n")[1].split("\n```\n")[0]
        self.assertIn('    m1("b")\n', block)
        self.assertIn("  m0 --> c0\n  m1 -.-> c0\n", block)

    def test_a_large_pull_request_gets_a_collapsed_diagram_and_says_so(self) -> None:
        """/* REQ-ACTION-019: over the caps the legend says that a box with a count stands for the modules of one folder and how many modules, changed files and arrows are not drawn; tests are counted boxes. */"""
        many = tuple(Dependent(f"p{index % 90:02d}/m{index:03d}", 1, routes=names("f")) for index in range(400))
        tests = tuple(Dependent(f"tests/test_{index}", 1) for index in range(12))
        body = report.build_comment(make((ChangedFile("core/a.py", "core/a", many + tests),)))
        legend = next(line for line in body.splitlines() if line.startswith("<sub>An arrow"))
        self.assertIn(" A box with a count stands for the modules of one folder.", legend)
        self.assertRegex(legend, r" Not drawn: \d+ module\(s\) and \d+ arrow\(s\)\.</sub>$")
        self.assertIn('[["12 test modules"]]', body)
        small = report.build_comment(make((ChangedFile("core/a.py", "core/a", many[:3]),)))
        self.assertNotIn("Not drawn", small)
        self.assertNotIn("A box with a count", small)

    def test_the_same_report_gives_the_same_comment(self) -> None:
        """/* REQ-ACTION-018: the comment is a function of the report alone: built twice, or from the changed files and their dependents in another order, it is the same text byte for byte. */"""
        first = rust()
        again = make(tuple(ChangedFile(c.path, c.module, tuple(reversed(c.untouched))) for c in reversed(first.changed)))
        self.assertEqual(report.build_comment(first), report.build_comment(first))
        self.assertEqual(report.build_comment(first), report.build_comment(again))


class SummaryTest(unittest.TestCase):
    def test_the_job_summary_lists_everything_and_the_comment_points_to_it(self) -> None:
        """/* REQ-ACTION-020: the comment names at most LIST_LIMIT direct dependents and findings and says the job summary lists them all; the job summary (full) lists every one, with the same counts and the same diagram. */"""
        many = tuple(Dependent(f"src/m{index:02d}", 1, routes=names(f"item{index}")) for index in range(report.LIST_LIMIT + 5))
        found = tuple(facts.Finding("violation", (str(index),), f"`a{index}` -> `b`") for index in range(report.LIST_LIMIT + 2))
        data = make((ChangedFile("src/core.ts", "src/core", many),), introduced=found)
        comment, summary = report.build_comment(data), report.build_comment(data, full=True)
        self.assertIn("- ... and 5 more, by folder: `src/` 5 (the job summary lists them all)\n", comment)
        self.assertNotIn("`src/m14` uses", comment)
        self.assertIn("- `src/m14` uses `item14` from `src/core.ts`\n", summary)
        self.assertIn("- violation: `a11` -> `b`\n", summary)
        self.assertNotIn("more", summary.split("### Not changed")[1])
        self.assertEqual(comment.split("```mermaid\n")[1].split("\n```\n")[0], summary.split("```mermaid\n")[1].split("\n```\n")[0])
        self.assertEqual(comment.splitlines()[:4], summary.splitlines()[:4])

    def test_the_map_artifact_is_named_when_it_is_uploaded(self) -> None:
        """/* REQ-ACTION-021: when the interactive map is uploaded as a workflow artifact, the report says under which name and links the workflow run; otherwise it does not mention a map. */"""
        with_map = report.build_comment(make(rust().changed, map_run="https://github.com/o/r/actions/runs/7"))
        self.assertIn("Interactive map: the artifact `wingbox-map` of [this workflow run](https://github.com/o/r/actions/runs/7).", with_map)
        self.assertNotIn("Interactive map", report.build_comment(rust()))


class CutTest(unittest.TestCase):
    def test_a_comment_within_the_limit_is_left_alone(self) -> None:
        """/* REQ-ACTION-022: a comment within GitHub's limit of 65,536 characters is not changed. */"""
        body = report.build_comment(rust())
        self.assertEqual(65536, report.COMMENT_LIMIT)
        self.assertEqual(body, report.cut(body, report.COMMENT_LIMIT))

    def test_a_comment_over_the_limit_is_cut_at_a_line_and_keeps_its_marker_and_footer(self) -> None:
        """/* REQ-ACTION-022: a comment over the limit (measured in UTF-8 bytes, never less than characters) keeps its first lines whole up to the limit, never ends inside a code block, says that it was cut and where the whole report is, and ends with its footer; the cut is the same every time. */"""
        long_path = "src/" + "very_long_folder_name/" * 400 + "x.ts"
        many = tuple(Dependent(f"{long_path[:-5]}/m{index:02d}", 1) for index in range(9))
        body = report.build_comment(make((ChangedFile(long_path, long_path[:-3], many),)))
        self.assertGreater(len(body), report.COMMENT_LIMIT)
        cut = report.cut(body, report.COMMENT_LIMIT)
        self.assertLessEqual(len(cut.encode("utf-8")), report.COMMENT_LIMIT)
        self.assertGreater(len(cut), report.COMMENT_LIMIT - 12_000)
        self.assertTrue(cut.startswith(facts.MARKER + "\n## Wingbox: what this pull request touches\n"))
        self.assertTrue(cut.endswith(body.splitlines()[-1] + "\n"))
        self.assertIn("\nThis comment was cut to fit GitHub's limit; the whole report is in the job summary of the workflow run.\n", cut)
        self.assertEqual(0, cut.count("```") % 2)
        self.assertEqual(cut, report.cut(body, report.COMMENT_LIMIT))
        kept = cut.split("\nThis comment was cut")[0]
        self.assertTrue(body.startswith(kept))
        self.assertTrue(kept.endswith("\n") or body[len(kept)] == "\n")

    def test_a_cut_never_leaves_an_open_code_block(self) -> None:
        """/* REQ-ACTION-022: when the limit falls inside the diagram, the whole block is left out; characters outside ASCII count by their bytes. */"""
        body = report.build_comment(rust())
        start, end = body.index("```mermaid\n"), body.index("\n```\n")
        for limit in (start + 400, end + 2, end + 350):
            cut = report.cut(body, limit)
            self.assertLessEqual(len(cut.encode("utf-8")), limit)
            self.assertEqual(0, cut.count("```") % 2, limit)
            self.assertIn("This comment was cut", cut)
        self.assertNotIn("mermaid", report.cut(body, start + 400))
        wide = facts.MARKER + "\n" + "\u00e9" * 100 + "\nline\n" * 30 + "\n<sub>footer</sub>\n"
        self.assertLessEqual(len(report.cut(wide, 300).encode("utf-8")), 300)


if __name__ == "__main__":
    unittest.main()
