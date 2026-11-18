"""Which items of a file a pull request changes: the lines of the diff and the items they fall in."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_changes as changes  # noqa: E402
from wingbox_changes import Hunk  # noqa: E402
from wingbox_facts import Item  # noqa: E402

RUST = """\
//! Session detail.

use std::fs;

const MAX_ITEMS: usize = 10;

#[derive(Debug)]
pub struct Output {
    pub text: String,
}

/// Loads the last output.
pub fn load_output(
    path: &str,
) -> Output {
    let text = fs::read_to_string(path).unwrap();
    Output { text }
}

impl Output {
    pub fn len(&self) -> usize {
        self.text.len()
    }
}

fn short_id(id: &str) -> &str {
    &id[..8]
}
""".split("\n")
RUST_ITEMS = [Item("MAX_ITEMS", "fn", 5), Item("Output", "cls", 8), Item("load_output", "fn", 13), Item("short_id", "fn", 26)]
PYTHON = """\
import os

LIMIT = 3


class Store:
    kind = "disk"

    def read(self, key):
        # look it up
        return self.data[key]

    def write(self, key, value):
        self.data[key] = value


@cached
def total(items):
    return sum(items)


print(total([1]))
""".split("\n")
PYTHON_ITEMS = [Item("LIMIT", "var", 3), Item("Store", "cls", 6), Item("read", "fn", 9), Item("write", "fn", 13), Item("total", "fn", 18)]


def touched(source: list[str], items: list[Item], path: str, *found: Hunk) -> tuple[list[str], bool]:
    hit, other = changes.touched_items(items, found, source, path)
    return [item.name for item in hit], other


class DiffTest(unittest.TestCase):
    def test_the_diff_is_read_per_file_with_the_lines_added_and_removed(self) -> None:
        """/* REQ-ACTION-034: a `git diff -U0` text is read into the hunks of each file of the head: the head line of
        the first added line (for a removal alone the head line it follows), the added lines and the removed
        lines; a line of the file that itself starts with `++` or `--` stays a line of its hunk, and a deleted
        file has no hunks. */"""
        diff = "\n".join([
            "diff --git a/src/a.rs b/src/a.rs", "index 1..2 100644", "--- a/src/a.rs", "+++ b/src/a.rs",
            "@@ -3 +3,2 @@ fn f() {", "-    old();", "+    new();", "+++ plus();",
            "@@ -9,2 +9,0 @@", "-    gone();", "--- minus();",
            "diff --git a/docs/x y.md b/docs/x y.md", "--- a/docs/x y.md", "+++ b/docs/x y.md", "@@ -0,0 +1 @@", "+hello",
            "diff --git a/src/dead.rs b/src/dead.rs", "deleted file mode 100644", "--- a/src/dead.rs", "+++ /dev/null", "@@ -1,2 +0,0 @@", "-a", "-b", ""])
        found = changes.hunks(diff)
        self.assertEqual(["src/a.rs", "docs/x y.md"], list(found))
        self.assertEqual((Hunk(3, ("    new();", "++ plus();"), ("    old();",)), Hunk(9, (), ("    gone();", "-- minus();"))), found["src/a.rs"])
        self.assertEqual((Hunk(1, ("hello",), ()),), found["docs/x y.md"])


class TouchedTest(unittest.TestCase):
    def test_a_changed_line_belongs_to_the_item_it_is_inside(self) -> None:
        """/* REQ-ACTION-035: a changed line of code belongs to the innermost item whose text it is in: from the
        attribute or decorator lines directly above the item to the line before the next line that is not
        indented deeper than its first line and opens something else. A signature over several lines, a closing
        bracket and a method inside a class belong to their item. */"""
        self.assertEqual((["load_output"], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(16, ("    let text = fs::read(path);",), ("    old",))))
        self.assertEqual((["load_output"], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(14, ("    path: &Path,",), ("    path: &str,",))))
        self.assertEqual((["Output"], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(7, ("#[derive(Debug, Clone)]",), ("#[derive(Debug)]",))))
        self.assertEqual((["short_id"], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(27, ("    &id[..8]",), ("    &id[..6]",))))
        self.assertEqual((["read"], False), touched(PYTHON, PYTHON_ITEMS, "a.py", Hunk(11, ("        return self.data[key]",), ("        return None",))))
        self.assertEqual((["Store"], False), touched(PYTHON, PYTHON_ITEMS, "a.py", Hunk(7, ('    kind = "disk"',), ('    kind = "ram"',))))
        self.assertEqual((["total"], False), touched(PYTHON, PYTHON_ITEMS, "a.py", Hunk(17, ("@cached",), ("@memo",))))
        self.assertEqual((["read", "write"], False), touched(PYTHON, PYTHON_ITEMS, "a.py", Hunk(11, ("        return 1",), ()), Hunk(14, ("        self.data[key] = value",), ())))

    def test_comments_and_blank_lines_change_no_item(self) -> None:
        """/* REQ-ACTION-036: a hunk whose added and removed lines are all blank or comments changes no item and is
        not counted as other code either: a file comment, a doc comment above an item, a comment inside it. A
        dereference such as `*count += 1;` is code, not the line of a block comment. */"""
        self.assertEqual(([], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(1, ("//! Session detail: loads things.", ""), ("//! Session detail.",))))
        self.assertEqual(([], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(12, ("/// Loads the last output of a session.",), ("/// Loads the last output.",))))
        self.assertEqual(([], False), touched(PYTHON, PYTHON_ITEMS, "a.py", Hunk(10, ("        # find it",), ("        # look it up",))))
        self.assertEqual(([], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(19, ("", " * a note", " */"), ())))
        self.assertTrue(changes.is_code("    *count += 1;", "a.rs"))
        self.assertFalse(changes.is_code("     * a note", "a.rs"))
        self.assertTrue(changes.is_code("#[derive(Debug)]", "a.rs"))
        self.assertFalse(changes.is_code("  # note", "a.py"))

    def test_code_outside_every_item_is_said_as_other_code(self) -> None:
        """/* REQ-ACTION-037: changed code that is in no item Wingbox lists (an import line, a block the graph has no
        item for such as a Rust impl, a statement at the top level, a removal between two items) is reported as
        other code of the file, never blamed on the item above it. */"""
        self.assertEqual(([], True), touched(RUST, RUST_ITEMS, "a.rs", Hunk(3, ("use std::path::Path;",), ("use std::fs;",))))
        self.assertEqual(([], True), touched(RUST, RUST_ITEMS, "a.rs", Hunk(22, ("        self.text.chars().count()",), ("        self.text.len()",))))
        self.assertEqual(([], True), touched(PYTHON, PYTHON_ITEMS, "a.py", Hunk(22, ("print(total([2]))",), ("print(total([1]))",))))
        self.assertEqual((["load_output"], True), touched(RUST, RUST_ITEMS, "a.rs", Hunk(16, ("    let t = 1;",), ()), Hunk(20, ("impl Output2 {",), ("impl Output {",))))

    def test_a_removal_alone_belongs_to_the_item_around_it(self) -> None:
        """/* REQ-ACTION-038: lines removed with nothing added belong to the item that holds both the line before
        and the line after the removal; between two items, or at the edge of one, they are other code. */"""
        self.assertEqual((["load_output"], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(16, (), ("    log(path);",))))
        self.assertEqual(([], True), touched(RUST, RUST_ITEMS, "a.rs", Hunk(19, (), ("fn removed() {", "}"))))
        self.assertEqual(([], False), touched(RUST, RUST_ITEMS, "a.rs", Hunk(19, (), ("// a note", ""))))

    def test_a_rust_test_module_inside_the_file_changes_no_item(self) -> None:
        """/* REQ-ACTION-045: in a Rust file the lines of a module marked `#[cfg(test)]` are tests, not code other
        modules can use: a change there changes no item and is not other code. */"""
        source = RUST + ["#[cfg(test)]", "mod tests {", "    use super::*;", "", "    #[test]", "    fn it_works() {", "        assert_eq!(short_id(\"abcdefghij\"), \"abcdefgh\");", "    }", "}", ""]
        first = len(RUST) + 1
        self.assertEqual(([], False), touched(source, RUST_ITEMS, "a.rs", Hunk(first + 6, ("        assert!(true);",), ("        assert!(false);",))))
        self.assertEqual(([], False), touched(source, RUST_ITEMS, "a.rs", Hunk(first + 2, ("    use super::short_id;",), ())))
        self.assertEqual((["short_id"], False), touched(source, RUST_ITEMS, "a.rs", Hunk(27, ("    &id[..6]",), ("    &id[..8]",)), Hunk(first + 6, ("        x();",), ())))

    def test_the_item_that_holds_a_line_can_be_asked_for(self) -> None:
        """/* REQ-ACTION-046: `holder` names the innermost item that holds a line of the file (the function a call
        site is in), or nothing for a line outside every item. */"""
        self.assertEqual("load_output", changes.holder(RUST_ITEMS, RUST, "a.rs", 16).name)
        self.assertEqual("read", changes.holder(PYTHON_ITEMS, PYTHON, "a.py", 11).name)
        self.assertIsNone(changes.holder(RUST_ITEMS, RUST, "a.rs", 22))
        self.assertIsNone(changes.holder(RUST_ITEMS, RUST, "a.rs", 3))


if __name__ == "__main__":
    unittest.main()
