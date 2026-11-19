"""What a pull request changes inside a file: the lines of the diff and the items they fall in.

The graph of Wingbox names the items of a file (functions, types, constants) with the line each one
starts at. From a `git diff -U0` and the text of the file at the head this module says which items
hold a changed line of code, and whether code outside every item changed too. It is deliberately
careful: a comment or a blank line changes no item, and a line that is in no item is reported as
other code of the file, never blamed on the item above it. Pure functions only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from wingbox_facts import Item


@dataclass(frozen=True)
class Hunk:
    """One change of a file. start is the head line of the first added line; for a removal with nothing
    added, the head line the removed lines followed (0: they were the first lines of the file)."""
    start: int
    added: tuple[str, ...]
    removed: tuple[str, ...]


_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def hunks(diff: str) -> dict[str, tuple[Hunk, ...]]:
    """The hunks of a `git diff -U0` text for each file of the head, in the order of the diff.

    The numbers of a hunk header say how many removed and added lines follow, so a line of the file that
    starts with `++` or `--` is never taken for a file header. A deleted file has no entry."""
    found: dict[str, list[Hunk]] = {}
    path: Optional[str] = None
    lines = diff.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path is not None:
                found.setdefault(path.rstrip("\t"), [])
            continue
        header = _HEADER.match(line)
        if header is None:
            continue
        old_count = 1 if header.group(1) is None else int(header.group(1))
        start = int(header.group(2))
        new_count = 1 if header.group(3) is None else int(header.group(3))
        removed: list[str] = []
        added: list[str] = []
        while index < len(lines) and (len(removed) < old_count or len(added) < new_count):
            body = lines[index]
            if body.startswith("-") and len(removed) < old_count:
                removed.append(body[1:])
            elif body.startswith("+") and len(added) < new_count:
                added.append(body[1:])
            elif not body.startswith("\\"):   # "\ No newline at end of file"
                break
            index += 1
        if path is not None:
            found[path.rstrip("\t")].append(Hunk(start, tuple(added), tuple(removed)))
    return {name: tuple(value) for name, value in found.items()}


# Languages whose comments start with `#`; the others the graph knows use `//` and `/* */`.
_HASH_COMMENTS = (".py", ".pyi", ".rb", ".rake")


def is_code(line: str, path: str) -> bool:
    """Whether a line of a source file is code: not blank and not a comment line. An attribute (`#[derive]`),
    a decorator and a dereference (`*count += 1;`) are code; `* text` and `*/` are lines of a block comment."""
    text = line.strip()
    if not text:
        return False
    if path.endswith(_HASH_COMMENTS):
        return not text.startswith("#")
    if text.startswith(("//", "/*", "*/")) or text == "*" or text.startswith("* "):
        return False
    return not (path.endswith(".php") and text.startswith("#") and not text.startswith("#["))


def added_file(text: str) -> tuple[Hunk, ...]:
    """The hunks of a file that is new as a whole (an untracked file): one hunk that adds every line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return (Hunk(1, tuple(lines), ()),) if lines else ()


def removes_code(changed: tuple["Hunk", ...], path: str) -> bool:
    """Whether the hunks of a file remove or replace a line of code (not only add lines, not only comments)."""
    return any(is_code(text, path) for hunk in changed for text in hunk.removed)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _continues(text: str) -> bool:
    """A line that goes on with the item above it although it is not indented deeper: a closing bracket,
    an opening brace on its own line, a `where` clause."""
    return text[0] in ")]}{" or text == "where" or text.startswith("where ")


def _leads(text: str) -> bool:
    """A line directly above an item that belongs to it: an attribute or a decorator."""
    return text.startswith(("#[", "#![", "@"))


def _definition(names: tuple[str, ...]) -> "re.Pattern[str]":
    """A line that defines one of the methods: `def name`, `async name(`, `name = (`, `name<T>(`, `Type name(`."""
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w.@$])({alternatives})(?:\s*<[^>()]*>)?\s*(?:\(|=(?!=)|:)|^def\s+({alternatives})\b")


def methods_of(item: Item, last: int, source: list[str], path: str) -> list[Item]:
    """The methods of a class as items of their own, found in the layout of the class: a line at the level of
    the class's members that defines one of the names the graph lists for it. The item is named
    `Class.method` and starts at the line of the definition. A call of the same name deeper in a body, a
    decorator or a comment is not a definition."""
    if not item.methods or not 1 <= item.line <= len(source):
        return []
    base = _indent(source[item.line - 1])
    body = [number for number in range(item.line + 1, min(last, len(source)) + 1)
            if is_code(source[number - 1], path) and _indent(source[number - 1]) > base]
    if not body:
        return []
    level = min(_indent(source[number - 1]) for number in body)
    pattern = _definition(item.methods)
    found: list[Item] = []
    for number in body:
        text = source[number - 1]
        if _indent(text) != level or text.strip().startswith(("@", "#[")):
            continue
        match = pattern.search(text.strip())
        if match:
            found.append(Item(f"{item.name}.{match.group(1) or match.group(2)}", "method", number))
    return found


def spans(items: list[Item], source: list[str], path: str) -> list[tuple[Item, int, int]]:
    """(item, first line, last line), lines counted from 1, for every item whose start line is in the file,
    and for the methods of every class whose fact names them.

    An item runs from the attribute and decorator lines directly above its start to the line before the next
    line of code that is not indented deeper than its start and does not continue it."""
    found = _spans(items, source, path)
    methods = [method for item, _first, last in found for method in methods_of(item, last, source, path)]
    return found + _spans(methods, source, path) if methods else found


def _spans(items: list[Item], source: list[str], path: str) -> list[tuple[Item, int, int]]:
    found: list[tuple[Item, int, int]] = []
    total = len(source)
    for item in sorted(items, key=lambda item: item.line):
        if not 1 <= item.line <= total or not source[item.line - 1].strip():
            continue
        base = _indent(source[item.line - 1])
        last = total
        for number in range(item.line + 1, total + 1):
            text = source[number - 1]
            if is_code(text, path) and _indent(text) <= base and not _continues(text.strip()):
                last = number - 1
                break
        first = item.line
        while first > 1 and _leads(source[first - 2].strip()) and _indent(source[first - 2]) == base:
            first -= 1
        found.append((item, first, last))
    return found


def _holder(found: list[tuple[Item, int, int]], line: int) -> Optional[Item]:
    """The innermost item whose text holds the line."""
    inside = [(first, item) for item, first, last in found if first <= line <= last]
    return max(inside, key=lambda pair: pair[0])[1] if inside else None


def holder(items: list[Item], source: list[str], path: str, line: int) -> Optional[Item]:
    """The innermost item that holds a line of the file: the function a place of use is in."""
    return _holder(spans(items, source, path), line)


_TEST_MARK = re.compile(r"^#\[cfg\(test\)\]$")


def test_lines(source: list[str], path: str) -> set[int]:
    """The lines of a Rust file that belong to a module marked `#[cfg(test)]`: its tests, kept in the file.
    Other languages keep tests in files of their own, which the graph knows as test modules."""
    found: set[int] = set()
    if not path.endswith(".rs"):
        return found
    number = 1
    while number <= len(source):
        if _TEST_MARK.match(source[number - 1].strip()):
            base = _indent(source[number - 1])
            opener = number + 1
            while opener <= len(source) and not is_code(source[opener - 1], path):
                opener += 1
            if opener <= len(source) and re.match(r"(pub(\([a-z]+\))? )?mod \w+", source[opener - 1].strip()):
                last = len(source)
                for later in range(opener + 1, len(source) + 1):
                    text = source[later - 1]
                    if is_code(text, path) and _indent(text) <= base and not _continues(text.strip()):
                        last = later - 1
                        break
                found.update(range(number, last + 1))
                number = last
        number += 1
    return found


def touched_items(items: list[Item], changed: tuple[Hunk, ...], source: list[str], path: str) -> tuple[list[Item], bool]:
    """(the items that hold a changed line of code, in the order of the file; whether code outside every item
    changed too).

    An added line of code belongs to the innermost item that holds it. Lines removed with nothing added in
    their place belong to the item that holds both the line before and the line after them. A hunk of blank
    lines and comments alone changes nothing."""
    found = spans(items, source, path)
    tests = test_lines(source, path)
    hit: dict[str, Item] = {}
    other = False

    def note(item: Optional[Item]) -> None:
        nonlocal other
        if item is None:
            other = True
        else:
            hit.setdefault(f"{item.line}:{item.name}", item)

    for hunk in changed:
        if hunk.start in tests or (not hunk.added and hunk.start + 1 in tests):
            continue                             # a change of the tests kept in the file
        code_lines = [hunk.start + offset for offset, text in enumerate(hunk.added) if is_code(text, path)]
        removed_code = any(is_code(text, path) for text in hunk.removed)
        for line in code_lines:
            note(_holder(found, line))
        if removed_code and not code_lines:
            if hunk.added:                       # code replaced by comments or blank lines: where those now stand
                note(_holder(found, hunk.start))
            else:
                before, after = _holder(found, hunk.start), _holder(found, hunk.start + 1)
                note(before if before is not None and before == after else None)
    return sorted(hit.values(), key=lambda item: item.line), other
