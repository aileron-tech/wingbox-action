"""The form of the pull request comment: the thing first, in plain words; findings before lists; the rest folded."""
import dataclasses
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_facts as facts  # noqa: E402
import wingbox_report as report  # noqa: E402
from wingbox_facts import ChangedFile, Dependent, Finding, NotChecked, Route  # noqa: E402


def make(changed: tuple[ChangedFile, ...], **values: Any) -> facts.Report:
    return dataclasses.replace(facts.Report("0.10.1", 2, "a" * 40, "b" * 40, changed, (), 0, ()), **values)


def names(*shown: str, count: int = 0) -> tuple[Route, ...]:
    return (Route("use", "", tuple(shown), count or len(shown)),)


ONE_FILE = (ChangedFile("src/infra/session_detail.rs", "src/infra/session_detail", (
    Dependent("src/cli", 1, routes=names("LoadSessionTimelineError", "load_session_timeline")),
    Dependent("src/main", 1, routes=names("load_session_timeline")),
    Dependent("src/plain", 1),
    Dependent("src/infra", 1),                       # the parent module: it only declares the file
    Dependent("src/app", 2, through="src/infra"),
    Dependent("tests/test_detail", 1),
)), ChangedFile("README.md", None))
FINDING = Finding("violation", ("violation", "src/domain/a", "src/infra/b", "domain -> infra"), "`src/domain/a` -> `src/infra/b` (domain -> infra)")


class HeadlineTest(unittest.TestCase):
    def test_the_comment_opens_with_what_the_reviewer_must_know(self) -> None:
        """/* REQ-ACTION-026: the comment opens with a heading that says, in a full sentence, how many modules outside
        the pull request use what it changes (singular and plural agree), and a first paragraph that names the
        changed file these modules use, says that they are not in the pull request, and why that matters; no
        line of counts joined by semicolons. */"""
        body = report.build_comment(make(ONE_FILE))
        lines = body.split("\n")
        self.assertEqual(report.MARKER, lines[0])
        self.assertEqual("## Wingbox: 3 modules outside this pull request use what it changes", lines[1])
        self.assertEqual("These modules use `src/infra/session_detail.rs`, which this pull request changes. They are not in the pull request, "
                         "so a change in behaviour reaches them without showing in the diff:", lines[3])
        self.assertNotIn("module(s) not changed by this pull request", body)
        one = report.build_comment(make((ChangedFile("src/a.ts", "src/a", (Dependent("src/b", 1),)),)))
        self.assertIn("## Wingbox: 1 module outside this pull request uses what it changes\n", one)
        self.assertIn("This module uses `src/a.ts`, which this pull request changes. It is not in the pull request, "
                      "so a change in behaviour reaches it without showing in the diff:", one)
        two = report.build_comment(make((ChangedFile("src/a.ts", "src/a", (Dependent("src/b", 1),)), ChangedFile("src/c.ts", "src/c", (Dependent("src/d", 1),)))))
        self.assertIn("These modules use `src/a.ts`, `src/c.ts`, which this pull request changes. They are not in the pull request", two)

    def test_one_changed_file_is_not_repeated_on_every_row(self) -> None:
        """/* REQ-ACTION-027: when every listed module uses the same single changed file, a row names the module and
        what it uses from the file, without the file again; a module with no names known is a row of its own
        name. With more than one changed file each row says from which file. */"""
        body = report.build_comment(make(ONE_FILE))
        self.assertIn("\n- `src/cli` uses `LoadSessionTimelineError`, `load_session_timeline`\n", body)
        self.assertIn("\n- `src/main` uses `load_session_timeline`\n", body)
        self.assertIn("\n- `src/plain`\n", body)
        self.assertNotIn("from `src/infra/session_detail.rs`", body)
        two = report.build_comment(make((ChangedFile("src/a.rs", "src/a", (Dependent("src/x", 1, routes=names("f")),)),
                                         ChangedFile("src/c.rs", "src/c", (Dependent("src/x", 1, routes=names("g")), Dependent("src/y", 1))))))
        self.assertIn("\n- `src/x` uses `f` from `src/a.rs`; `g` from `src/c.rs`\n", two)
        self.assertIn("\n- `src/y` uses `src/c.rs`\n", two)

    def test_nothing_to_say_is_one_sentence(self) -> None:
        """/* REQ-ACTION-028: when no module outside the pull request uses a changed file directly and the pull
        request adds no boundary finding, the comment has no heading and no list: one sentence says so. What
        reaches the files farther away stays available, folded. */"""
        leaf = report.build_comment(make((ChangedFile("src/leaf.ts", "src/leaf", ()),)))
        self.assertNotIn("## ", leaf)
        self.assertNotIn("\n- ", leaf)
        self.assertNotIn("<details>", leaf)
        self.assertIn("**Wingbox**: no module outside this pull request uses the files it changes directly, and it adds no boundary finding.", leaf)
        far = report.build_comment(make((ChangedFile("src/core.py", "src/core", (Dependent("src/far/b", 2, through="src/mid"),)),)))
        self.assertNotIn("## ", far)
        self.assertIn("**Wingbox**: no module outside this pull request uses the files it changes directly, and it adds no boundary finding.", far)
        self.assertIn("<summary>More: 1 within 2 hops</summary>", far)


class OrderTest(unittest.TestCase):
    def test_new_boundary_findings_come_first_and_an_empty_section_is_not_printed(self) -> None:
        """/* REQ-ACTION-029: findings the pull request introduces are named in the heading and listed before the
        modules; without any, the comment says "No new boundary finding." in one line after the lists, and
        prints no section for them. */"""
        body = report.build_comment(make(ONE_FILE, introduced=(FINDING,)))
        self.assertIn("## Wingbox: 1 new boundary finding, and 3 modules outside this pull request use what it changes\n", body)
        self.assertLess(body.index("### New boundary findings"), body.index("- `src/cli` uses"))
        self.assertIn("- violation: `src/domain/a` -> `src/infra/b` (domain -> infra)\n", body)
        self.assertNotIn("No new boundary finding.", body)
        only = report.build_comment(make((ChangedFile("src/leaf.ts", "src/leaf", ()),), introduced=(FINDING, FINDING)))
        self.assertIn("## Wingbox: 2 new boundary findings\n", only)
        clean = report.build_comment(make(ONE_FILE))
        self.assertNotIn("### New boundary findings", clean)
        self.assertNotIn("None.", clean)
        self.assertIn("\nNo new boundary finding.\n", clean)
        self.assertLess(clean.index("</details>"), clean.index("No new boundary finding."))

    def test_the_picture_and_the_counts_are_folded_in_the_comment_and_open_in_the_summary(self) -> None:
        """/* REQ-ACTION-030: in the comment the diagram, the users of a facade or declaration, the modules farther
        away, the tests and the files outside the code graph sit in one folded block whose summary line says
        what it holds; the listed modules stay above it. The job summary (full) shows the same content unfolded. */"""
        body = report.build_comment(make(ONE_FILE))
        self.assertEqual((1, 1), (body.count("<details>"), body.count("</details>")))
        self.assertIn("<summary>More: the picture, 1 through a facade or declaration, 1 within 2 hops, 1 test module, 1 file outside the code graph</summary>\n\n", body)
        opened, closed = body.index("<details>"), body.index("</details>")
        self.assertLess(body.index("- `src/plain`"), opened)
        for piece in ("```mermaid\n", "Through `src/infra/session_detail.rs`", "Within 2 hops, by folder:", "Tests that use the changed code (1)", "Not in the code graph (1"):
            self.assertTrue(opened < body.index(piece) < closed, piece)
        self.assertIn("\n\n</details>\n", body)
        summary = report.build_comment(make(ONE_FILE), full=True)
        self.assertNotIn("<details>", summary)
        for piece in ("## Wingbox: 3 modules outside this pull request use what it changes", "```mermaid\n", "Within 2 hops, by folder:", "- `src/plain`"):
            self.assertIn(piece, summary)

    def test_what_was_not_checked_is_still_said(self) -> None:
        """/* REQ-ACTION-031: the parts that were not checked are named outside the folded block, with the sentence
        that this is not a pass, also when the comment is the one-sentence form. */"""
        missing = (NotChecked("web", "too-few-files"),)
        for changed in (ONE_FILE, (ChangedFile("src/leaf.ts", "src/leaf", ()),)):
            body = report.build_comment(make(changed, not_checked=missing))
            line = "**Not checked**: part `web` (it has too few files to judge). No finding is reported there, and that is not a pass."
            self.assertIn(line, body)
            if "</details>" in body:
                self.assertLess(body.index("</details>"), body.index(line))


class CutTest(unittest.TestCase):
    def test_a_cut_comment_closes_its_folded_block(self) -> None:
        """/* REQ-ACTION-032: a comment cut to the size limit inside the folded block closes the block before the
        notice, so the notice and the footer are not hidden in it. */"""
        many = tuple(Dependent(f"pkg{index:03d}/sub/mod", 2, through="src/mid") for index in range(400))
        body = report.build_comment(make((ChangedFile("src/core.py", "src/core", (Dependent("src/a", 1),) + many),)))
        self.assertIn("<details>", body)
        limit = body.index("Within 2 hops") + 40
        short = report.cut(body, limit)
        self.assertLessEqual(len(short.encode("utf-8")), limit)
        self.assertEqual(short.count("<details>"), short.count("</details>"))
        self.assertLess(short.index("</details>"), short.index(report.CUT_NOTICE))


if __name__ == "__main__":
    unittest.main()
