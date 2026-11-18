"""The comment names the items that changed and who uses them outside the pull request."""
import dataclasses
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_facts as facts  # noqa: E402
import wingbox_report as report  # noqa: E402
from wingbox_facts import ChangedFile, ChangedItem, Dependent, Item, User  # noqa: E402

FILE = "src/infra/session_detail.rs"
MODULE = "src/infra/session_detail"
DEPENDENTS = (Dependent("src/cli", 1), Dependent("src/main", 1), Dependent("src/infra/claude", 1), Dependent("src/app", 2, through="src/cli"))
TIMELINE = ChangedItem("load_session_timeline", "fn", 97, (User("src/cli", "src/cli/mod.rs", 331, "call", 2), User("src/main", "src/main.rs", 19, "import", 1)))
OUTPUT = ChangedItem("LastAssistantOutput", "cls", 19, (User("src/infra/claude", "src/infra/claude.rs", 27, "type", 3),))
PRIVATE = ChangedItem("short_id", "fn", 271, ())
# A public function that calls a private one that changed.
REACHED = ChangedItem("load_session_timeline", "fn", 97, (User("src/cli", "src/cli/mod.rs", 331, "call", 2),), ("detect_log_format", "looks_like_claude_jsonl"))


def make(*items: ChangedItem, other_code: bool = False, **values: Any) -> facts.Report:
    changed = (ChangedFile(FILE, MODULE, DEPENDENTS, tuple(items), other_code),)
    return dataclasses.replace(facts.Report("0.10.2", 2, "a" * 40, "b" * 40, changed, (), 0, ()), **values)


class FactsTest(unittest.TestCase):
    def test_the_items_of_a_module_are_read_with_their_lines(self) -> None:
        """/* REQ-ACTION-039: `wingbox module <id>` gives the items of a file: each function, type, enum and
        constant with the line it starts at; the node, import and reference facts are not items. */"""
        lines = ['(node src/infra/session_detail :lang rs "infrastructure adapter")', "(fn src/infra/session_detail MAX_TIMELINE_ITEMS 10 :lang rs)",
                 "(enm src/infra/session_detail LoadSessionTimelineError 13 :lang rs (:deco derive) (:methods OpenFile))",
                 "(cls src/infra/session_detail LastAssistantOutput 19 :lang rs (:deco derive))", "(fn src/infra/session_detail load_session_timeline 97 :lang rs)",
                 "(imports src/infra/session_detail src/domain)", "(refs src/infra/session_detail src/domain/timeline::ParsedLogLine)"]
        self.assertEqual([Item("MAX_TIMELINE_ITEMS", "fn", 10), Item("LoadSessionTimelineError", "enm", 13), Item("LastAssistantOutput", "cls", 19),
                          Item("load_session_timeline", "fn", 97)], facts.items(lines))

    def test_the_users_of_an_item_are_the_modules_outside_the_pull_request(self) -> None:
        """/* REQ-ACTION-040: from `wingbox usages <module>::<name> --sexp` each module that uses the item is named
        once, with the place to point at (a call before a type use before an import) and the number of its
        places; the module of the item and the modules the pull request changes are left out. An answer about
        another item, an ambiguous name or no match gives None: who uses it is then not known. */"""
        lines = ["(usages-target src/infra/session_detail::load_session_timeline :kind fn :file src/infra/session_detail.rs :line 97)",
                 "(usage src/cli 2 46 :kind import :file src/cli/mod.rs)", "(usage src/cli 331 28 :kind call :file src/cli/mod.rs)",
                 "(usage src/infra/opencode 992 38 :kind call :file src/infra/opencode.rs)", "(usage src/infra/session_detail 397 24 :kind call :file src/infra/session_detail.rs)",
                 "(usage src/main 19 5 :kind import :file src/main.rs)", "(usage-file src/legacy :kind reference :file src/legacy.py)",
                 "(usages-summary src/infra/session_detail::load_session_timeline :uses 6 :files 5 :unresolved 0)"]
        target = "src/infra/session_detail::load_session_timeline"
        self.assertEqual((User("src/cli", "src/cli/mod.rs", 331, "call", 2), User("src/legacy", "src/legacy.py", 0, "reference", 1),
                          User("src/main", "src/main.rs", 19, "import", 1)), facts.users(lines, target, {"src/infra/opencode"}))
        self.assertIsNone(facts.users(["(usages-ambiguous short_id :definitions 3)", "(usages-candidate src/cli::short_id :kind fn :file src/cli/mod.rs :line 497)"], "src/cli::short_id", set()))
        self.assertIsNone(facts.users(["(error no-match nothing)"], "src/a::nothing", set()))
        self.assertIsNone(facts.users(lines, "src/other::load_session_timeline", set()))


class ReportTest(unittest.TestCase):
    def test_a_changed_item_is_named_with_the_modules_that_use_it(self) -> None:
        """/* REQ-ACTION-041: when a changed line of code is in an item that modules outside the pull request use,
        the heading names the item and the number of those modules, and the comment lists each module with how
        it uses the item (calls, imports, uses as a type) and the place. Modules that use the file but not what
        changed are not listed: they are counted in the folded block. */"""
        body = report.build_comment(make(TIMELINE))
        self.assertIn("## Wingbox: `load_session_timeline` changed, and 2 modules outside this pull request use it\n", body)
        self.assertIn("`load_session_timeline` (function, `src/infra/session_detail.rs:97`) changed. These modules use it and are not in the pull request:\n", body)
        self.assertIn("\n- `src/cli` calls it at `src/cli/mod.rs:331` and in 1 more place\n", body)
        self.assertIn("\n- `src/main` imports it at `src/main.rs:19`\n", body)
        self.assertNotIn("- `src/infra/claude`", body)
        self.assertIn("1 module that uses the changed files but not what changed", body.split("<summary>")[1].split("</summary>")[0])
        self.assertIn("Use the changed files but not the items that changed: 1 module(s), by folder: `src/infra/` 1.", body)
        one = report.build_comment(make(OUTPUT))
        self.assertIn("## Wingbox: `LastAssistantOutput` changed, and 1 module outside this pull request uses it\n", one)
        self.assertIn("`LastAssistantOutput` (type, `src/infra/session_detail.rs:19`) changed. This module uses it and is not in the pull request:\n", one)
        self.assertIn("\n- `src/infra/claude` uses it as a type at `src/infra/claude.rs:27` and in 2 more places\n", one)

    def test_an_item_that_calls_what_changed_is_named_with_the_way_to_it(self) -> None:
        """/* REQ-ACTION-047: when the changed code is in an item nobody outside uses, but an item of the same file
        that others use calls it (directly or through other items of the file), the comment names that item,
        says through what it reaches the change, and lists its users: a changed private helper is not reported
        as used by nobody. */"""
        body = report.build_comment(make(ChangedItem("looks_like_claude_jsonl", "fn", 293, ()), REACHED))
        self.assertIn("## Wingbox: `load_session_timeline` calls code that changed, and 1 module outside this pull request uses it\n", body)
        self.assertIn("`load_session_timeline` (function, `src/infra/session_detail.rs:97`) calls `detect_log_format`, which calls `looks_like_claude_jsonl`, which changed. "
                      "This module uses it and is not in the pull request:\n", body)
        self.assertIn("\n- `src/cli` calls it at `src/cli/mod.rs:331` and in 1 more place\n", body)

    def test_test_modules_that_use_a_changed_item_are_counted(self) -> None:
        """/* REQ-ACTION-048: test modules that use a changed item are not rows: they are counted after the rows;
        an item that only tests use is not one that modules outside the pull request use. A module named
        `tests` (Rust `src/cli/tests.rs`) is a test module. */"""
        self.assertTrue(report.is_test("src/cli/tests") and report.is_test("src/cli/tests.rs") and not report.is_test("src/cli/testing_tools"))
        item = ChangedItem("load_session_timeline", "fn", 97, (User("src/cli", "src/cli/mod.rs", 331, "call", 1), User("tests/test_timeline", "tests/test_timeline.rs", 9, "call", 4),
                                                              User("src/cli/tests", "src/cli/tests.rs", 3, "call", 1)))
        body = report.build_comment(make(item))
        self.assertIn("## Wingbox: `load_session_timeline` changed, and 1 module outside this pull request uses it\n", body)
        self.assertNotIn("- `tests/test_timeline`", body)
        self.assertIn("\n- `src/cli` calls it at `src/cli/mod.rs:331`\n\n2 test modules use it too.\n", body)
        only_tests = report.build_comment(make(ChangedItem("short_id", "fn", 271, (User("tests/test_ids", "tests/test_ids.rs", 4, "call", 1),))))
        self.assertIn("**Wingbox**: what this pull request changes is not used outside it, and it adds no boundary finding.\n", only_tests)

    def test_several_changed_items_each_get_their_users(self) -> None:
        """/* REQ-ACTION-042: with several changed items that are used outside the pull request the heading counts
        the items and the modules, and each item has its own sentence and rows, most used first. */"""
        body = report.build_comment(make(OUTPUT, TIMELINE, PRIVATE))
        self.assertIn("## Wingbox: 2 changed items are used by 3 modules outside this pull request\n", body)
        self.assertLess(body.index("`load_session_timeline` (function"), body.index("`LastAssistantOutput` (type"))
        self.assertNotIn("short_id", body)

    def test_a_change_nobody_outside_uses_is_one_sentence(self) -> None:
        """/* REQ-ACTION-043: when the changed lines are in items no module outside the pull request uses, or are
        comments and blank lines alone, the comment is one sentence without a heading and without a list, even
        though other modules import the file. */"""
        for report_data in (make(PRIVATE), make()):
            body = report.build_comment(report_data)
            self.assertNotIn("## ", body.split("<details>")[0])
            self.assertNotIn("\n- ", body.split("<details>")[0])
            self.assertIn("**Wingbox**: what this pull request changes is not used outside it, and it adds no boundary finding.\n", body)
            self.assertIn("3 modules that use the changed files but not what changed", body)

    def test_code_outside_the_listed_items_falls_back_to_the_file(self) -> None:
        """/* REQ-ACTION-044: when code outside every item Wingbox lists changed too, the modules that use the file
        are listed as before, after the changed items when there are any, with a sentence that says why. A file
        that was not looked at is spoken of as a whole, as before. */"""
        alone = report.build_comment(make(other_code=True))
        self.assertIn("## Wingbox: 3 modules outside this pull request use what it changes\n", alone)
        self.assertIn("\n- `src/cli`\n", alone)
        both = report.build_comment(make(TIMELINE, other_code=True))
        self.assertIn("## Wingbox: `load_session_timeline` changed, and 2 modules outside this pull request use it\n", both)
        self.assertIn("Other code of `src/infra/session_detail.rs` changed too, outside the items Wingbox lists. These modules use that file and are not in the pull request:\n", both)
        self.assertLess(both.index("- `src/cli` calls it"), both.index("Other code of"))
        self.assertIn("\n- `src/infra/claude`\n", both)
        whole = dataclasses.replace(make(), changed=(ChangedFile(FILE, MODULE, DEPENDENTS),))
        self.assertIn("## Wingbox: 3 modules outside this pull request use what it changes\n", report.build_comment(whole))


if __name__ == "__main__":
    unittest.main()
