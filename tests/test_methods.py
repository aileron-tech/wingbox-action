"""A change inside one method of a class is reported as that method, with the modules that call it."""
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_changes as changes  # noqa: E402
import wingbox_facts as facts  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_report as report  # noqa: E402
from wingbox_changes import Hunk  # noqa: E402
from wingbox_facts import ChangedFile, ChangedItem, Item, User  # noqa: E402

TS = """\
import { Injectable } from '@nestjs/common';

@Injectable()
export class AlbumService extends BaseService {
  private limit = 10;

  constructor(private repo: AlbumRepository) {
    super();
  }

  @Get(':id')
  async get(auth: AuthDto, id: string): Promise<Album> {
    const album = await this.repo.find(id);
    return album;
  }

  getAll = async (auth: AuthDto) => {
    return this.repo.all(auth.user.id);
  };

  private findOrFail<T>(
    id: string,
  ): Promise<T> {
    return this.repo.findOrFail(id);
  }
}

export function helper(): number {
  return 1;
}
""".split("\n")
TS_ITEMS = [Item("AlbumService", "cls", 4, ("get", "getAll", "findOrFail")), Item("helper", "fn", 28)]
PY = """\
class Store:
    kind = "disk"

    def __init__(self, data):
        self.data = data

    @cached
    def read(self, key):
        return self.data[key]

    async def write(self, key, value):
        self.data[key] = value


def total(items):
    return sum(items)
""".split("\n")
PY_ITEMS = [Item("Store", "cls", 1, ("__init__", "read", "write")), Item("total", "fn", 15)]


def touched(source: list[str], items: list[Item], path: str, *found: Hunk) -> list[tuple[str, str, int]]:
    hit, _other = changes.touched_items(items, found, source, path)
    return [(item.name, item.kind, item.line) for item in hit]


class FactsTest(unittest.TestCase):
    def test_the_methods_of_a_class_are_read_from_its_fact(self) -> None:
        """/* REQ-ACTION-073: the item of a class carries the names of its methods, from the `(:methods ...)` list of
        its fact; an item without that list has none. */"""
        lines = ["(cls src/services/album.service AlbumService 35 :lang ts (:bases BaseService) (:deco Injectable) (:methods getStatistics getAll get))",
                 "(fn src/services/album.service mapAlbumMetadata 26 :lang ts)"]
        self.assertEqual([Item("AlbumService", "cls", 35, ("getStatistics", "getAll", "get")), Item("mapAlbumMetadata", "fn", 26)], facts.items(lines))


class LayoutTest(unittest.TestCase):
    def test_a_changed_line_inside_a_method_belongs_to_the_method(self) -> None:
        """/* REQ-ACTION-074: inside a class whose fact names its methods, a changed line of code belongs to the
        method that holds it, named `Class.method` with the line of its definition: a plain method, an arrow
        property, a signature over several lines, the decorator above it, in TypeScript and in Python. A line of
        the class outside every listed method (a field, the constructor) belongs to the class. */"""
        self.assertEqual([("AlbumService.get", "method", 12)], touched(TS, TS_ITEMS, "a.ts", Hunk(13, ("    const album = await this.repo.get(id);",), ("    old",))))
        self.assertEqual([("AlbumService.get", "method", 12)], touched(TS, TS_ITEMS, "a.ts", Hunk(11, ("  @Get(':albumId')",), ("  @Get(':id')",))))
        self.assertEqual([("AlbumService.getAll", "method", 17)], touched(TS, TS_ITEMS, "a.ts", Hunk(18, ("    return [];",), ("    old",))))
        self.assertEqual([("AlbumService.findOrFail", "method", 21)], touched(TS, TS_ITEMS, "a.ts", Hunk(22, ("    id: number,",), ("    id: string,",))))
        self.assertEqual([("AlbumService", "cls", 4)], touched(TS, TS_ITEMS, "a.ts", Hunk(5, ("  private limit = 20;",), ("  private limit = 10;",))))
        self.assertEqual([("AlbumService", "cls", 4)], touched(TS, TS_ITEMS, "a.ts", Hunk(8, ("    super(repo);",), ("    super();",))))
        self.assertEqual([("helper", "fn", 28)], touched(TS, TS_ITEMS, "a.ts", Hunk(29, ("  return 2;",), ("  return 1;",))))
        self.assertEqual([("Store.read", "method", 8)], touched(PY, PY_ITEMS, "a.py", Hunk(9, ("        return self.data.get(key)",), ("        old",))))
        self.assertEqual([("Store.read", "method", 8)], touched(PY, PY_ITEMS, "a.py", Hunk(7, ("    @memo",), ("    @cached",))))
        self.assertEqual([("Store.write", "method", 11)], touched(PY, PY_ITEMS, "a.py", Hunk(12, ("        self.data[key] = [value]",), ("        old",))))
        self.assertEqual([("Store.__init__", "method", 4)], touched(PY, PY_ITEMS, "a.py", Hunk(5, ("        self.data = dict(data)",), ("        old",))))
        self.assertEqual([("Store", "cls", 1)], touched(PY, PY_ITEMS, "a.py", Hunk(2, ('    kind = "ram"',), ('    kind = "disk"',))))
        self.assertEqual([("AlbumService.get", "method", 12), ("AlbumService.getAll", "method", 17)],
                         touched(TS, TS_ITEMS, "a.ts", Hunk(14, ("    return null;",), ("    old",)), Hunk(18, ("    return [];",), ("    old",))))

    def test_a_call_of_a_method_name_is_not_its_definition(self) -> None:
        """/* REQ-ACTION-075: only a line at the level of the class's members can define a method: a call of the
        same name inside another method, or a decorator of that name, is not a definition. */"""
        source = ["class A {", "  run() {", "    this.stop();", "    stop(1);", "  }", "", "  @stop()", "  stop() {", "    return 1;", "  }", "}", ""]
        items = [Item("A", "cls", 1, ("run", "stop"))]
        self.assertEqual([("A.run", "method", 2)], touched(source, items, "a.ts", Hunk(3, ("    this.stop(2);",), ("    this.stop();",))))
        self.assertEqual([("A.stop", "method", 8)], touched(source, items, "a.ts", Hunk(9, ("    return 2;",), ("    return 1;",))))


SOURCE = "export class Store {\n  read(key) {\n    return 1;\n  }\n\n  write(key) {\n    return 2;\n  }\n}\n"
MODULE = ["(node src/a :lang ts)", "(cls src/a Store 1 :lang ts (:methods read write))"]
USAGES = {
    "src/a::Store.read": ["(usages-target src/a::Store.read :kind cls :file src/a.ts :line 1)",
                          "(usage src/api 9 3 :kind unresolved-call :file src/api.ts)", "(usage src/api 20 3 :kind unresolved-call :file src/api.ts)",
                          "(usage src/other 4 1 :kind unresolved-call :file src/other.ts)", "(usage tests/store.spec 7 2 :kind unresolved-call :file tests/store.spec.ts)",
                          "(usage src/changed 2 2 :kind call :file src/changed.ts)"],
    "src/a::Store": ["(usages-target src/a::Store :kind cls :file src/a.ts :line 1)", "(usage src/api 1 10 :kind import :file src/api.ts)",
                     "(usage src/jobs 1 10 :kind import :file src/jobs.ts)", "(usage tests/store.spec 1 10 :kind import :file tests/store.spec.ts)",
                     "(usage src/changed 1 1 :kind import :file src/changed.ts)"],
}


class GatheringTest(unittest.TestCase):
    def gather(self, hunks: tuple[Hunk, ...], usages: dict[str, list[str]] = USAGES) -> tuple:
        asked: list[str] = []

        def wingbox(_root: str, *args: str) -> list[str]:
            asked.append(" ".join(args))
            return MODULE if args[0] == "module" else usages.get(args[1], ["(error no-match x)"])

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "src").mkdir()
            (Path(tmp) / "src" / "a.ts").write_text(SOURCE, encoding="utf-8")
            with unittest.mock.patch.object(pr, "_wingbox", wingbox):
                found = pr.changed_items(tmp, ChangedFile("src/a.ts", "src/a"), hunks, {"src/a", "src/changed"}, 10)
        return (*found, asked)

    def test_the_users_of_a_method_use_the_class_and_call_the_method(self) -> None:
        """/* REQ-ACTION-076: the users of a changed method are the modules outside the pull request that use the
        class and have a call of that method's name; a module that calls a method of the same name on something
        else (it does not use the class) and a module that uses the class but not the method are not its users.
        The place shown is the call. */"""
        items, other, left, asked = self.gather((Hunk(3, ("    return 3;",), ("    return 1;",)),))
        self.assertEqual((ChangedItem("Store.read", "method", 2, (User("src/api", "src/api.ts", 9, "unresolved-call", 2),
                                                                  User("tests/store.spec", "tests/store.spec.ts", 7, "unresolved-call", 1))),), items)
        self.assertFalse(other)
        self.assertEqual(["module src/a", "usages src/a::Store.read --sexp --all", "usages src/a::Store --sexp --all"], asked)
        self.assertEqual(8, left)

    def test_a_method_wingbox_cannot_tell_falls_back_to_its_class(self) -> None:
        """/* REQ-ACTION-077: when Wingbox has no answer for the method, the change is reported as a change of the
        class, with the users of the class, as before. */"""
        only_class = {"src/a::Store": USAGES["src/a::Store"]}
        items, other, _left, asked = self.gather((Hunk(3, ("    return 3;",), ("    return 1;",)),), only_class)
        self.assertEqual(["Store"], [item.name for item in items])
        self.assertEqual(["src/api", "src/jobs", "tests/store.spec"], [user.module for user in items[0].users])
        self.assertFalse(other)


class WordingTest(unittest.TestCase):
    def test_the_comment_names_the_method_and_who_calls_it(self) -> None:
        """/* REQ-ACTION-078: the comment names a changed method as `Class.method` (method, file:line) and each of
        its users with the place of the call. */"""
        import dataclasses
        item = ChangedItem("Store.read", "method", 2, (User("src/api", "src/api.ts", 9, "unresolved-call", 2),))
        changed = (ChangedFile("src/a.ts", "src/a", (facts.Dependent("src/api", 1), facts.Dependent("src/jobs", 1)), (item,), False),)
        body = report.build_comment(dataclasses.replace(facts.Report("0.11.0", 2, "a" * 40, "b" * 40, changed, (), 0, ())))
        self.assertIn("## Wingbox: `Store.read` changed, and 1 module outside this pull request uses it\n", body)
        self.assertIn("`Store.read` (method, `src/a.ts:2`) changed. This module uses it and is not in the pull request:\n\n- `src/api` calls it at `src/api.ts:9` and in 1 more place\n", body)
        self.assertIn("1 module that uses the changed files but not what changed", body)
        self.assertIn('(look "src/api.ts" 9 :module src/api :why unresolved-call :item Store.read :places 2)', report.build_plan(dataclasses.replace(facts.Report("0.11.0", 2, "a" * 40, "b" * 40, changed, (), 0, ()))))


if __name__ == "__main__":
    unittest.main()


class CheckedPartsTest(unittest.TestCase):
    def test_the_report_says_on_how_many_modules_the_rules_were_checked(self) -> None:
        """/* REQ-ACTION-079: next to the parts that were not checked the report says on how many modules the
        boundary rules were checked (from the summary line of `wingbox boundaries`), or that none could be; the
        plan carries the same number. Without that line of Wingbox nothing is added. */"""
        import dataclasses
        self.assertEqual(707, facts.checked_modules(["(boundaries-summary :violations 0 :known 0 :type-only 8 :candidates 15 :checked 707)"]))
        self.assertEqual(0, facts.checked_modules(["(boundaries-not-checked :part a :reason no-layer-style)", "(boundaries-partial :checked 0 :not-checked 54)"]))
        self.assertEqual(2950, facts.checked_modules(["(boundaries-partial :checked 2950 :not-checked 416)", "(boundaries-summary :violations 1 :checked 2950)"]))
        self.assertIsNone(facts.checked_modules(["(violation src/a src/b :rule x)"]))
        changed = (ChangedFile("src/a.ts", "src/a", (facts.Dependent("src/api", 1),)),)
        base = facts.Report("0.11.0", 2, "a" * 40, "b" * 40, changed, (), 0, (facts.NotChecked("web", "no-layer-style"),))
        some = report.build_comment(dataclasses.replace(base, checked_modules=2950))
        self.assertIn("**Not checked**: part `web` (its style has no layers). No finding is reported there, and that is not a pass. "
                      "The rules were checked on 2,950 other modules.\n", some)
        none = report.build_comment(dataclasses.replace(base, checked_modules=0))
        self.assertIn("that is not a pass. No part of this repository could be checked against boundary rules.\n", none)
        self.assertNotIn("The rules were checked", report.build_comment(base))
        self.assertIn("(checked :modules 2950)", report.build_plan(dataclasses.replace(base, checked_modules=2950)).split("\n"))
        self.assertNotIn("(checked", report.build_plan(base))
