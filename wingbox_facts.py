"""The facts the pull request check reads from Wingbox, and the data of its report.

Wingbox prints its answers as flat S-expression lines (`wingbox impact FILE N --sexp`,
`wingbox boundaries`). This module reads those lines into typed values; nothing here scrapes the
text Wingbox prints for people. Pure functions only: no file, process or network use.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

MARKER = "<!-- wingbox-pull-request-report -->"
# The head of a check of a working directory: the files as they are on disk, not a commit.
WORKING_TREE = "working-tree"
# Findings of `wingbox boundaries` that count: a known (baselined) or type-only import is not one.
FINDING_HEADS = ("violation", "violation-candidate", "capability-violation", "capability-candidate", "pattern-violation")
BLOCKING_HEADS = ("violation", "capability-violation", "pattern-violation")
# Name lists of an import edge whose first value is not a name: the facade, the glob module or the type.
ROUTES_WITH_THROUGH = frozenset({"reexport", "glob", "member"})


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    """Names of a dependent that Wingbox followed to the file it depends on, and how they arrive.

    way: use (an item of the imported module), reexport (an item a facade re-exports; through is the
    facade as written), glob (a name used after a glob import; through is the module of that import),
    member (a member an impl block of the file defines; through is the type), macro. names holds a few
    of them, count their number."""
    way: str
    through: str
    names: tuple[str, ...]
    count: int


@dataclass(frozen=True)
class Dependent:
    module: str
    depth: int
    via: str = "imports"             # the relation of its edge
    through: str = ""                # below depth 1: the module of the previous depth it is reached through
    more: int = 0                    # how many other modules of that depth it also depends on
    routes: tuple[Route, ...] = ()   # the names of its edge; at depth 1 what it uses from the changed file


@dataclass(frozen=True)
class Item:
    """An item of a module as the graph lists it: a function, a type, a constant, with the line it starts at."""
    name: str
    kind: str     # fn, cls, enm, typ, ifc, var, mod; "method" for a method found in the layout of its class
    line: int
    methods: tuple[str, ...] = ()   # of a class: the names of its methods, as the graph lists them


@dataclass(frozen=True)
class User:
    """A module that uses a changed item and is not changed by the pull request: one place of use to point at
    (a call before a type use before an import) and how many places it has."""
    module: str
    file: str
    line: int
    kind: str     # call, type, import, ...
    places: int


@dataclass(frozen=True)
class ChangedItem:
    """An item that holds a changed line of code, or that calls one inside the same file, with the modules
    outside the pull request that use it. via is empty for an item that changed itself; otherwise the names
    it goes through to the change, nearest first, the changed item last."""
    name: str
    kind: str
    line: int
    users: tuple[User, ...] = ()
    via: tuple[str, ...] = ()


@dataclass(frozen=True)
class Site:
    """One place that uses an item (line 0 when the language has no use positions)."""
    module: str
    file: str
    line: int
    kind: str


@dataclass(frozen=True)
class ChangedFile:
    path: str
    module: Optional[str]                 # None: not a module of the graph (docs, config, a deleted file)
    untouched: tuple[Dependent, ...] = ()  # dependents within N hops that the pull request does not change
    # What changed inside the file. None: not looked at (the report then speaks of the file as a whole).
    items: Optional[tuple[ChangedItem, ...]] = None
    other_code: bool = False               # code outside every item changed too


@dataclass(frozen=True)
class Finding:
    head: str
    key: tuple[str, ...]   # what makes the finding the same on base and head
    text: str              # one line for people


@dataclass(frozen=True)
class NotChecked:
    part: str
    reason: str


@dataclass(frozen=True)
class Report:
    version: str
    hops: int
    base: str
    head: str
    changed: tuple[ChangedFile, ...]
    introduced: tuple[Finding, ...]
    resolved: int
    not_checked: tuple[NotChecked, ...]
    skipped: tuple[str, ...] = field(default=())   # changed files left out by the paths filter
    map_run: str = ""                               # the workflow run that holds the map artifact, when uploaded
    tests_changed: tuple[tuple[str, str], ...] = field(default=())   # (test file, source file it tests), both changed
    tool_errors: tuple[str, ...] = field(default=())                 # wingbox commands that ended abnormally
    hashes: tuple[tuple[str, str], ...] = field(default=())          # (changed file, SHA-256 of its content at the head)
    checked_modules: Optional[int] = None                            # modules the boundary rules were checked on, when known


# ---------------------------------------------------------------------------
# S-expression lines
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')


def _unquote(token: str) -> str:
    return json.loads(token) if token.startswith('"') else token


def _list_text(tokens: list[str]) -> str:
    """The tokens of a nested list as one text: `(:use A "b c")`."""
    text = ""
    for token in tokens:
        text += token if token == ")" or text.endswith("(") or not text else " " + token
    return text


def parse_line(line: str) -> Optional[tuple[str, list[str], dict[str, str]]]:
    """(head, positional arguments, keywords) of one flat S-expression line; lists become their text."""
    line = line.strip()
    if not line.startswith("(") or not line.endswith(")"):
        return None
    values: list[str] = []
    depth = 0
    buffer: list[str] = []
    for token in _TOKEN.findall(line[1:-1]):
        if token == "(":
            depth += 1
            buffer.append(token)
        elif token == ")":
            depth -= 1
            buffer.append(token)
            if depth == 0:
                values.append(_list_text(buffer))
                buffer = []
        elif depth:
            buffer.append(token)
        else:
            values.append(_unquote(token))
    if not values:
        return None
    head, rest = values[0], values[1:]
    args: list[str] = []
    keywords: dict[str, str] = {}
    index = 0
    while index < len(rest):
        if rest[index].startswith(":") and index + 1 < len(rest):
            keywords[rest[index][1:]] = rest[index + 1]
            index += 2
        else:
            args.append(rest[index])
            index += 1
    return head, args, keywords


def parse_route(token: str) -> Optional[Route]:
    """A `(:use A B)` or `(:glob "super" A B C :of 5)` list of an impact fact; None for any other token."""
    if not (token.startswith("(:") and token.endswith(")")):
        return None
    parts = [_unquote(part) for part in _TOKEN.findall(token[1:-1])]
    way, values = parts[0][1:], parts[1:]
    through = values.pop(0) if way in ROUTES_WITH_THROUGH and values else ""
    count = len(values)
    if len(values) >= 2 and values[-2] == ":of" and values[-1].isdigit():
        count, values = int(values[-1]), values[:-2]
    return Route(way, through, tuple(values), count) if way and values else None


def _number(text: str, default: int) -> int:
    return int(text) if text.isdigit() else default


def dependents(lines: list[str]) -> tuple[Optional[str], list[Dependent]]:
    """(the module of the root, its dependents) from `wingbox impact --sexp`: each dependent with its depth,
    the module it is reached through and the names of its edge, when the facts carry them (Wingbox 0.10)."""
    root: Optional[str] = None
    found: list[Dependent] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        head, args, keys = parsed
        if head == "impact-root" and args:
            root = args[0]
        elif head == "impact" and len(args) >= 2:
            routes = tuple(route for route in map(parse_route, args[2:]) if route is not None)
            found.append(Dependent(args[1], _number(keys.get("depth", ""), 1), keys.get("via", "imports"),
                                   keys.get("through", ""), _number(keys.get("more", ""), 0), routes))
    return root, found


ITEM_HEADS = frozenset({"fn", "cls", "enm", "typ", "ifc", "var", "mod"})


def items(lines: list[str]) -> list[Item]:
    """The items of a module from `wingbox module <id>`: its functions, types and constants with their lines."""
    found: list[Item] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        head, args, _keys = parsed
        if head in ITEM_HEADS and len(args) >= 3 and args[2].isdigit():
            methods: tuple[str, ...] = ()
            for extra in args[3:]:
                if extra.startswith("(:methods ") and extra.endswith(")"):
                    methods = tuple(_unquote(token) for token in _TOKEN.findall(extra[1:-1])[1:])
            found.append(Item(args[1], head, int(args[2]), methods))
    return found


# The place of use to point at: a call before a use as a type before an import. An unresolved call is a call
# of that method name whose receiver Wingbox could not type.
_USE_ORDER = {"call": 0, "unresolved-call": 1, "type": 2, "ref": 3, "import": 5}


def use_sites(lines: list[str], target: str) -> Optional[tuple[Site, ...]]:
    """Every place that uses one item, from `wingbox usages <module>::<name> --sexp`; target is
    `<module>::<name>`. None when the answer is not about that item (no match, an ambiguous name): who uses
    it is then not known."""
    answered = False
    found: list[Site] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        head, args, keys = parsed
        if head == "usages-target":
            answered = bool(args) and args[0] == target
        elif answered and head in ("usage", "usage-file") and args:
            number = int(args[1]) if head == "usage" and len(args) > 1 and args[1].isdigit() else 0
            found.append(Site(args[0], keys.get("file", ""), number, keys.get("kind", "use")))
    return tuple(found) if answered else None


def users(lines: list[str], target: str, changed_modules: set[str]) -> Optional[tuple[User, ...]]:
    """The modules outside the pull request that use one item: each module once, with the place to point at
    (a call before a type use before an import) and the number of its places. The module of the item itself
    and the modules the pull request changes are left out. None when `use_sites` cannot tell."""
    sites = use_sites(lines, target)
    if sites is None:
        return None
    own = target.split("::", 1)[0]
    places: dict[str, list[tuple[int, int, str, str]]] = {}
    for site in sites:
        if site.module != own and site.module not in changed_modules:
            places.setdefault(site.module, []).append((_USE_ORDER.get(site.kind, 4), site.line, site.kind, site.file))
    found = []
    for module, of_module in sorted(places.items()):
        _rank, number, kind, file_path = min(of_module)
        found.append(User(module, file_path, number, kind, len(of_module)))
    return tuple(found)


def checked_modules(lines: list[str]) -> Optional[int]:
    """On how many modules `wingbox boundaries` checked the rules: `:checked N` of its partial line, else of
    its summary line; None when it prints neither."""
    found: dict[str, int] = {}
    for line in lines:
        parsed = parse_line(line)
        if parsed and parsed[0] in ("boundaries-partial", "boundaries-summary") and parsed[2].get("checked", "").isdigit():
            found.setdefault(parsed[0], int(parsed[2]["checked"]))
    return found.get("boundaries-partial", found.get("boundaries-summary"))


def findings(lines: list[str]) -> tuple[list[Finding], list[NotChecked]]:
    """The findings and the parts not checked in the output of `wingbox boundaries`."""
    found: list[Finding] = []
    not_checked: list[NotChecked] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        head, args, keys = parsed
        if head == "boundaries-not-checked":
            not_checked.append(NotChecked(keys.get("part", "."), keys.get("reason", "unknown")))
        elif head in ("violation", "violation-candidate") and len(args) >= 2:
            text = f"`{args[0]}` -> `{args[1]}` ({keys.get('rule', '')})"
            found.append(Finding(head, (head, args[0], args[1], keys.get("rule", "")), text))
        elif head in ("capability-violation", "capability-candidate") and args:
            where = f"{args[0]}:{keys['line']}" if "line" in keys else args[0]
            text = f"`{where}` uses {keys.get('capability', '?')} in layer {keys.get('layer', '?')}"
            found.append(Finding(head, (head, args[0], keys.get("capability", ""), keys.get("pattern", "")), text))
        elif head == "pattern-violation" and args:
            text = f"`{args[0]}`: {keys.get('pattern', '?')}" + (f" (`{keys['target']}`)" if "target" in keys else "")
            found.append(Finding(head, (head, args[0], keys.get("pattern", ""), keys.get("target", "")), text))
    return found, not_checked


# ---------------------------------------------------------------------------
# What a pull request changes
# ---------------------------------------------------------------------------

def introduced(base: list[Finding], head: list[Finding]) -> tuple[list[Finding], int]:
    """Findings of head that base has not, and how many findings of base head no longer has."""
    base_keys = {finding.key for finding in base}
    head_keys = {finding.key for finding in head}
    return [finding for finding in head if finding.key not in base_keys], len(base_keys - head_keys)


def module_of(path: str) -> str:
    """The module ID a source file most likely has: its path without the extension."""
    stem, _ext = os.path.splitext(path)
    return stem


def folder_of(module: str, depth: int = 2) -> str:
    """The folder a module is counted under: its first two folders ("docs_src/body/x" -> "docs_src/body/")."""
    parts = module.split("/")[:-1]
    return "/".join(parts[:depth]) + "/" if parts else "(root)"


def selected(paths: list[str], patterns: list[str]) -> tuple[list[str], list[str]]:
    """(kept, left out) by the paths filter: glob patterns, `!pattern` excludes; no pattern keeps all."""
    include = [p for p in patterns if not p.startswith("!")]
    exclude = [p[1:] for p in patterns if p.startswith("!")]
    kept: list[str] = []
    left: list[str] = []
    for path in paths:
        wanted = (not include or any(fnmatch.fnmatch(path, p) for p in include)) and not any(fnmatch.fnmatch(path, p) for p in exclude)
        (kept if wanted else left).append(path)
    return kept, left


def untouched(found: list[Dependent], changed_modules: set[str]) -> tuple[Dependent, ...]:
    """Dependents the pull request does not change, nearest first, each module once: the row of its nearest depth."""
    best: dict[str, Dependent] = {}
    for dependent in found:
        if dependent.module not in changed_modules and (dependent.module not in best or dependent.depth < best[dependent.module].depth):
            best[dependent.module] = dependent
    return tuple(sorted(best.values(), key=lambda dependent: (dependent.depth, dependent.module)))
