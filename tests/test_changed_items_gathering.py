"""Gathering what changed in a file: the items of the graph, the diff, and the users of each item."""
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_pr as pr  # noqa: E402
from wingbox_changes import Hunk  # noqa: E402
from wingbox_facts import ChangedFile, ChangedItem, User  # noqa: E402

SOURCE = "pub fn outer() -> u8 {\n    helper()\n}\n\nfn helper() -> u8 {\n    1\n}\n\npub fn apart() -> u8 {\n    7\n}\n"
MODULE = ["(node src/a :lang rs)", "(fn src/a outer 1 :lang rs)", "(fn src/a helper 5 :lang rs)", "(fn src/a apart 9 :lang rs)"]
USAGES = {
    "src/a::helper": ["(usages-target src/a::helper :kind fn :file src/a.rs :line 5)", "(usage src/a 2 5 :kind call :file src/a.rs)"],
    "src/a::outer": ["(usages-target src/a::outer :kind fn :file src/a.rs :line 1)", "(usage src/x 9 3 :kind call :file src/x.rs)",
                     "(usage src/changed 4 1 :kind call :file src/changed.rs)"],
    "src/a::apart": ["(usages-ambiguous apart :definitions 2)"],
}


class GatheringTest(unittest.TestCase):
    def gather(self, hunks: tuple[Hunk, ...], budget: int = 10) -> tuple:
        asked: list[str] = []

        def wingbox(_root: str, *args: str) -> list[str]:
            asked.append(" ".join(args))
            return MODULE if args[0] == "module" else USAGES[args[1]]

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "src").mkdir()
            (Path(tmp) / "src" / "a.rs").write_text(SOURCE, encoding="utf-8")
            with unittest.mock.patch.object(pr, "_wingbox", wingbox):
                found = pr.changed_items(tmp, ChangedFile("src/a.rs", "src/a"), hunks, {"src/a", "src/changed"}, budget)
        return (*found, asked)

    def test_a_changed_private_item_reaches_the_item_that_calls_it(self) -> None:
        """/* REQ-ACTION-049: for a changed file the action asks Wingbox for the items of its module and for the
        users of each item that holds a changed line; an item of the same file that calls a changed item is
        asked about too and carries the name it goes through. Users in modules the pull request changes are
        left out. Every query counts against the budget. */"""
        items, other, left, asked = self.gather((Hunk(6, ("    2",), ("    1",)),))
        self.assertEqual((ChangedItem("outer", "fn", 1, (User("src/x", "src/x.rs", 9, "call", 1),), ("helper",)), ChangedItem("helper", "fn", 5, (), ())), items)
        self.assertFalse(other)
        self.assertEqual(8, left)
        self.assertEqual(["module src/a", "usages src/a::helper --sexp --all", "usages src/a::outer --sexp --all"], asked)

    def test_only_a_call_carries_a_change_to_the_item_that_makes_it(self) -> None:
        """/* REQ-ACTION-051: a change is followed from an item to the items of the file that call it; an item that
        only names a changed type or imports the item is not followed: its users are not touched through it. */"""
        typed = dict(USAGES, **{"src/a::helper": ["(usages-target src/a::helper :kind fn :file src/a.rs :line 5)", "(usage src/a 2 5 :kind type :file src/a.rs)"]})
        with unittest.mock.patch.dict(USAGES, typed):
            items, other, _left, asked = self.gather((Hunk(6, ("    2",), ("    1",)),))
        self.assertEqual((ChangedItem("helper", "fn", 5, (), ()),), items)
        self.assertEqual(["module src/a", "usages src/a::helper --sexp --all"], asked)

    def test_what_wingbox_cannot_tell_keeps_the_file_spoken_of_as_a_whole(self) -> None:
        """/* REQ-ACTION-050: an item whose users Wingbox cannot tell (an ambiguous name) counts as other code of
        the file, so the modules that use the file are still listed; when the budget of queries is spent, or
        the file cannot be read, the file is not looked at and the report speaks of it as a whole. */"""
        items, other, _left, _asked = self.gather((Hunk(10, ("    8",), ("    7",)),))
        self.assertEqual(((), True), (items, other))
        items, other, left, _asked = self.gather((Hunk(6, ("    2",), ("    1",)),), budget=1)
        self.assertEqual((None, False, 0), (items, other, left))
        with unittest.mock.patch.object(pr, "_wingbox", lambda _root, *args: MODULE):
            self.assertEqual((None, False, 5), pr.changed_items("/nonexistent-folder", ChangedFile("src/a.rs", "src/a"), (), set(), 5))


if __name__ == "__main__":
    unittest.main()
