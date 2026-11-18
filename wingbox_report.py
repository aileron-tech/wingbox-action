"""The report of the pull request check, in Markdown: the comment and the job summary.

From the data of a Report (wingbox_facts) it sorts the modules that depend on the changed files and
are not changed (direct uses, users of a facade or declaration, farther ones, tests). The report says
the thing first, in a sentence: how many modules outside the pull request use what it changes; then the
boundary findings the pull request introduces, then those modules with what each uses; the diagram
(wingbox_diagram) and the counts of everything farther away are folded in the comment. It names the
parts that were not checked, and cuts the comment to GitHub's size limit.
Pure functions only: the same report gives the same text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

import wingbox_diagram as diagram
from wingbox_facts import BLOCKING_HEADS, MARKER, ChangedFile, ChangedItem, Dependent, Report, User, folder_of

FAIL_ON = ("none", "violations", "not-checked")
LIST_LIMIT = 10    # direct dependents and findings named in the comment; the job summary names them all
ITEM_LIMIT = 5     # changed items the comment gives a block to; the job summary gives every one a block
FOLDER_LIMIT = 6   # folders named in a count
NAMES_LIMIT = 3    # names of what a dependent uses from one changed file; the others are counted
FILES_LIMIT = 2    # changed files named under one dependent in the comment
COMMENT_LIMIT = 65536      # GitHub refuses a longer comment body
SUMMARY_LIMIT = 1_000_000  # a job summary holds 1 MiB per step
MAP_ARTIFACT = "wingbox-map"
SITE = "https://wingbox.dev"
CUT_NOTICE = "This comment was cut to fit GitHub's limit; the whole report is in the job summary of the workflow run."
DIRECT, FACADE, FARTHER, TEST = "direct", "facade", "farther", "test"


@dataclass(frozen=True)
class Use:
    """One module that depends on changed files and is not changed, at its nearest depth."""
    module: str
    depth: int
    kind: str                                         # DIRECT, FACADE, FARTHER or TEST
    files: tuple[str, ...]                            # the changed files it reaches at that depth
    names: tuple[tuple[tuple[str, ...], int], ...]    # per file: a few names it uses from it, and their number
    through: tuple[str, ...]                          # below depth 1: the modules it is reached through


# ---------------------------------------------------------------------------
# Sorting the dependents
# ---------------------------------------------------------------------------

_TEST_FOLDERS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing", "e2e"})
_TEST_NAME = re.compile(r"(^test_|_test$|_tests$|\.(spec|test)$|^Test[A-Z_]|[a-z0-9]Tests?$)")
# A module ID has no extension: only a source extension is cut, never the `.spec` of `web/a.spec`.
_SOURCE_EXTENSION = re.compile(r"\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|go|java|cs|rb|rs|php|dart|pas|pp)$")
FACADE_FILES = frozenset({"mod.rs", "lib.rs", "__init__.py", "index.ts", "index.tsx", "index.js", "index.mjs"})


def is_test(path: str) -> bool:
    """A test module or file: in a test folder, or named like a test in Python, JS/TS, Go, Java, C# or Ruby.
    path is a file path or a module ID (the path without its extension)."""
    parts = _SOURCE_EXTENSION.sub("", path).split("/")
    # A module named `tests` or `test` is the test module of its folder (Rust `tests.rs`).
    return any(part in _TEST_FOLDERS for part in parts[:-1]) or parts[-1] in ("tests", "test") or bool(_TEST_NAME.search(parts[-1]))


def is_facade(path: str) -> bool:
    """A module facade: the file that declares and re-exports the files of its folder (Rust mod.rs and lib.rs,
    Python __init__.py, an index.ts). Its users reach it for many things, rarely for the line that changed."""
    return path.rsplit("/", 1)[-1] in FACADE_FILES


def is_parent(module: str, path: str) -> bool:
    """Whether module is the parent module of the file path: the module that declares it (`mod x;`), not a use."""
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    return module in (folder, f"{folder}/mod", f"{folder}/__init__", f"{folder}/index", f"{folder}/lib")


def used_names(dependent: Dependent) -> tuple[tuple[str, ...], int]:
    """(a few names, their number) of what a dependent uses from the file of its edge: a member as `Type::name`,
    a macro as `name!`."""
    shown: list[str] = []
    for route in dependent.routes:
        for name in route.names:
            shown.append(f"{route.through}::{name}" if route.way == "member" else f"{name}!" if route.way == "macro" else name)
    return tuple(shown[:NAMES_LIMIT]), sum(route.count for route in dependent.routes)


def uses(report: Report) -> tuple[Use, ...]:
    """Every dependent the pull request does not change, once, nearest first.

    Under a module only the changed files it reaches at its nearest depth count: a file reached through a
    facade at depth 2 is not something it uses directly. At depth 1 a module is a direct use when the facts
    name what it uses from the file, or when the file is an ordinary one; a module that only declares the file
    (its parent module) or imports a changed facade without a name from it is a facade use."""
    rows: dict[str, list[tuple[str, Dependent]]] = {}
    for changed in sorted(report.changed, key=lambda changed: changed.path):
        for dependent in changed.untouched:
            rows.setdefault(dependent.module, []).append((changed.path, dependent))
    found: list[Use] = []
    for module, reached in rows.items():
        depth = min(dependent.depth for _path, dependent in reached)
        nearest = [(path, dependent) for path, dependent in reached if dependent.depth == depth]
        kind = TEST if is_test(module) else FARTHER if depth > 1 else FACADE
        if kind == FACADE:
            real = [(path, dependent) for path, dependent in nearest
                    if dependent.routes or not (is_facade(path) or is_parent(module, path))]
            if real:
                kind, nearest = DIRECT, real
        through = tuple(sorted({dependent.through for _path, dependent in nearest if dependent.through}))
        found.append(Use(module, depth, kind, tuple(path for path, _d in nearest),
                         tuple(used_names(dependent) for _path, dependent in nearest), through))
    return tuple(sorted(found, key=lambda use: (use.depth, use.module)))


def untouched_count(report: Report) -> int:
    """How many different modules depend on a changed file and are not changed."""
    return len({d.module for c in report.changed for d in c.untouched})


def needs_comment(report: Report) -> bool:
    """A pull request that changes no source module of the graph other than tests gets no comment."""
    return any(c.module is not None and not is_test(c.path) for c in report.changed) or bool(report.introduced)


def exit_status(report: Report, fail_on: str) -> int:
    """1 when the fail-on condition holds, else 0."""
    if fail_on == "violations" and any(f.head in BLOCKING_HEADS for f in report.introduced):
        return 1
    if fail_on == "not-checked" and report.not_checked:
        return 1
    return 0


# ---------------------------------------------------------------------------
# The diagram
# ---------------------------------------------------------------------------

def diagram_modules(report: Report, found: tuple[Use, ...]) -> tuple[diagram.Module, ...]:
    """The changed files and the dependents as the diagram takes them: a direct use points at the changed files
    it uses; a facade use and a farther one are drawn as farther, pointing at the facade or at the modules
    they are reached through (at the changed files when the facts do not say through which)."""
    path_of = {changed.module: changed.path for changed in report.changed if changed.module is not None}
    modules = [diagram.Module(path, diagram.CHANGED) for path in sorted(path_of.values())]
    for use in found:
        near = use.kind == DIRECT or (use.kind == TEST and use.depth == 1)
        targets = use.files if near or not use.through else tuple(path_of.get(module, module) for module in use.through)
        modules.append(diagram.Module(use.module, diagram.DIRECT if near else diagram.FAR, targets, use.kind == TEST))
    return tuple(modules)


def diagram_block(report: Report, found: tuple[Use, ...]) -> list[str]:
    """The lines of the diagram section: the heading, the Mermaid block and its legend; none without a diagram."""
    drawn = diagram.blast_radius(diagram_modules(report, found))
    if not drawn.text:
        return []
    legend = ("An arrow points from a module to what it uses. Thick border: changed in this pull request. "
              f"Dashed: it reaches a changed file through a module facade, a declaration or within {report.hops} hops.")
    if drawn.collapsed:
        legend += " A box with a count stands for the modules of one folder."
    hidden = [f"{count} {what}" for count, what in ((drawn.hidden_modules, "module(s)"), (drawn.hidden_changed, "changed file(s)"),
                                                      (drawn.hidden_edges, "arrow(s)")) if count]
    if hidden:
        legend += " Not drawn: " + (", ".join(hidden[:-1]) + " and " + hidden[-1] if len(hidden) > 1 else hidden[0]) + "."
    return ["### Blast radius", "", "```mermaid", drawn.text, "```", "", f"<sub>{legend}</sub>", ""]


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------

def _code(text: str) -> str:
    """A name or a path as a code span that it cannot end."""
    return "`" + text.replace("`", "'").replace("\r", " ").replace("\n", " ") + "`"


def _folders(modules: list[str]) -> str:
    counts: dict[str, int] = {}
    for module in modules:
        counts[folder_of(module)] = counts.get(folder_of(module), 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    shown = ", ".join(f"{_code(folder)} {count}" for folder, count in ranked[:FOLDER_LIMIT])
    return shown + (f", {len(ranked) - FOLDER_LIMIT} more folder(s)" if len(ranked) > FOLDER_LIMIT else "")


def _files(paths: tuple[str, ...]) -> str:
    return ", ".join(_code(p) for p in paths[:FILES_LIMIT]) + (f" and {len(paths) - FILES_LIMIT} more" if len(paths) > FILES_LIMIT else "")


def _used(use: Use, full: bool) -> str:
    """What a direct dependent uses: per changed file the names from the facts, or the file alone."""
    limit = len(use.files) if full else FILES_LIMIT
    parts: list[str] = []
    for path, (names, count) in list(zip(use.files, use.names))[:limit]:
        if names:
            more = f" and {count - len(names)} more" if count > len(names) else ""
            parts.append(", ".join(_code(name) for name in names) + f"{more} from {_code(path)}")
        else:
            parts.append(_code(path))
    separator = "; " if any(names for names, _count_ in use.names[:limit]) else ", "
    return separator.join(parts) + (f" and {len(use.files) - limit} more" if len(use.files) > limit else "")


def _after_blank(lines: list[str], added: list[str]) -> list[str]:
    """added, after one empty line (not a second one)."""
    return added if lines[-1] == "" else ["", *added]


def _count(number: int, noun: str) -> str:
    """`1 module`, `3 modules`."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _row(use: Use, full: bool, one_file: bool) -> str:
    """The row of a direct dependent. When every row is about the same single changed file, the file is not
    repeated: the module, and what it uses from the file when the facts name it."""
    if not one_file:
        return f"- {_code(use.module)} uses {_used(use, full)}"
    names, count = use.names[0] if use.names else ((), 0)
    if not names:
        return f"- {_code(use.module)}"
    more = f" and {count - len(names)} more" if count > len(names) else ""
    return f"- {_code(use.module)} uses " + ", ".join(_code(name) for name in names) + more


_KINDS = {"fn": "function", "cls": "type", "enm": "enum", "typ": "type", "ifc": "interface", "var": "variable", "mod": "module"}
_VERBS = {"call": "calls it", "import": "imports it", "type": "uses it as a type"}


def _user_row(user: User) -> str:
    """The row of a module that uses a changed item: how, where, and in how many more places."""
    where = f" at {_code(f'{user.file}:{user.line}')}" if user.line else f" in {_code(user.file)}" if user.file else ""
    more = f" and in {_count(user.places - 1, 'more place')}" if user.places > 1 else ""
    return f"- {_code(user.module)} {_VERBS.get(user.kind, 'uses it')}{where}{more}"


def _real_users(item: ChangedItem) -> list[User]:
    """The users of an item that are not test modules."""
    return [user for user in item.users if not is_test(user.module)]


def changed_items(report: Report) -> list[tuple[ChangedFile, ChangedItem]]:
    """The items that changed, or that call what changed, and that modules outside the pull request use
    (test modules apart), most used first."""
    found = [(changed, item) for changed in report.changed for item in (changed.items or ()) if _real_users(item)]
    return sorted(found, key=lambda pair: (-len(_real_users(pair[1])), pair[0].path, pair[1].line))


def build_comment(report: Report, full: bool = False) -> str:
    """The pull request comment, or with full the job summary, in Markdown, starting with the marker.

    It says the thing first: a heading and a sentence with how many modules outside the pull request use what
    it changes; then the boundary findings the pull request introduces, then those modules with what they use
    (the comment at most LIST_LIMIT of them, the summary all). The diagram, the users of a facade, the farther
    dependents, the tests and the files outside the graph follow, folded in the comment and open in the
    summary. Without a direct user and without a finding the comment is one sentence. A pull request that
    changes only documents, configuration or tests gets one line."""
    footer = (f"<sub>[Wingbox]({SITE}) {report.version}, base {_code(report.base[:7])}, head {_code(report.head[:7])}. "
              "Built and checked on the runner, without a language model; no code left it.</sub>")
    if not needs_comment(report):
        return "\n".join([MARKER, "**Wingbox**: no source module outside the tests changed in this pull request; "
                          "nothing to check.", "", footer, ""])
    found = uses(report)
    # A file whose changed lines were sorted into items speaks through those items; a file that was not looked
    # at, or in which code outside every item changed too, is spoken of as a whole.
    looked_at = any(changed.items is not None for changed in report.changed)
    named = changed_items(report)
    item_users = {user.module for _changed, item in named for user in _real_users(item)}
    whole = {changed.path for changed in report.changed if changed.items is None or changed.other_code}
    direct = []
    quiet: list[str] = []      # modules that use a changed file, but not what changed in it
    for use in found:
        if use.kind != DIRECT:
            continue
        kept = [index for index, path in enumerate(use.files) if path in whole]
        if kept:
            direct.append(replace(use, files=tuple(use.files[index] for index in kept), names=tuple(use.names[index] for index in kept)))
        elif use.module not in item_users:
            quiet.append(use.module)
    through = [use for use in found if use.kind == FACADE]
    farther = [use.module for use in found if use.kind == FARTHER]
    tests = [use.module for use in found if use.kind == TEST]
    outside = [c.path for c in report.changed if c.module is None]
    limit = len(direct) if full else LIST_LIMIT

    # The thing first.
    said = []
    if report.introduced:
        said.append(_count(len(report.introduced), "new boundary finding"))
    if len(named) == 1:
        only = named[0][1]
        count = len(_real_users(only))
        said.append(f"{_code(only.name)} {'uses code that changed' if only.via else 'changed'}, and {_count(count, 'module')} "
                    f"outside this pull request use{'' if count > 1 else 's'} it")
    elif named:
        what = "items touched by this change" if any(item.via for _changed, item in named) else "changed items"
        said.append(f"{len(named)} {what} are used by {_count(len(item_users), 'module')} outside this pull request")
    elif direct:
        said.append(f"{_count(len(direct), 'module')} outside this pull request use{'' if len(direct) > 1 else 's'} what it changes")
    lines = [MARKER]
    if said:
        lines += ["## Wingbox: " + ", and ".join(said), ""]
    elif looked_at:
        lines += ["**Wingbox**: what this pull request changes is not used outside it, and it adds no boundary finding.", ""]
    else:
        lines += ["**Wingbox**: no module outside this pull request uses the files it changes directly, "
                  "and it adds no boundary finding.", ""]
    if report.introduced:
        shown_findings = report.introduced if full else report.introduced[:LIST_LIMIT]
        lines += ["### New boundary findings", ""]
        lines += [f"- {finding.head}: {finding.text}" for finding in shown_findings]
        if len(report.introduced) > len(shown_findings):
            lines.append(f"- ... and {len(report.introduced) - len(shown_findings)} more (the job summary lists them all)")
        lines.append("")
    for changed, item in named if full else named[:ITEM_LIMIT]:
        real = _real_users(item)
        several = len(real) > 1
        shown_users = real if full else real[:LIST_LIMIT]
        happened = "uses " + ", which uses ".join(_code(name) for name in item.via) + ", which changed" if item.via else "changed"
        lines += [f"{_code(item.name)} ({_KINDS.get(item.kind, item.kind)}, {_code(f'{changed.path}:{item.line}')}) {happened}. "
                  f"{'These modules use' if several else 'This module uses'} it and {'are' if several else 'is'} not in the pull request:", ""]
        lines += [_user_row(user) for user in shown_users]
        if len(real) > len(shown_users):
            lines.append(f"- ... and {len(real) - len(shown_users)} more, by folder: "
                         f"{_folders([user.module for user in real[len(shown_users):]])} (the job summary lists them all)")
        testing = len(item.users) - len(real)
        if testing:
            lines += ["", f"{_count(testing, 'test module')} use{'' if testing > 1 else 's'} it too."]
        lines.append("")
    if not full and len(named) > ITEM_LIMIT:
        lines += [f"... and {len(named) - ITEM_LIMIT} more changed items that other modules use (the job summary lists them all).", ""]
    if direct:
        files = tuple(sorted({path for use in direct for path in use.files}))
        many, one_file = len(direct) > 1, len(files) == 1
        if named:
            lines += [f"Other code of {_files(files)} changed too, outside the items Wingbox lists. "
                      f"{'These modules use' if many else 'This module uses'} {'that file' if one_file else 'those files'} "
                      f"and {'are' if many else 'is'} not in the pull request:", ""]
        else:
            lines += [f"{'These modules use' if many else 'This module uses'} {_files(files)}, which this pull request changes. "
                      f"{'They are' if many else 'It is'} not in the pull request, so a change in behaviour reaches "
                      f"{'them' if many else 'it'} without showing in the diff:", ""]
        lines += [_row(use, full, one_file) for use in direct[:limit]]
        if len(direct) > limit:
            lines.append(f"- ... and {len(direct) - limit} more, by folder: {_folders([use.module for use in direct[limit:]])} "
                         "(the job summary lists them all)")
        lines.append("")

    # The rest: folded in the comment, open in the job summary.
    drawn = diagram_block(report, found)
    more = list(drawn)
    holds = ["the picture"] if drawn else []
    if quiet:
        more += [f"Use the changed files but not the items that changed: {len(quiet)} module(s), by folder: {_folders(quiet)}.", ""]
        holds.append(f"{_count(len(quiet), 'module')} that use{'' if len(quiet) > 1 else 's'} the changed files but not what changed")
    if through:
        facades = tuple(sorted({path for use in through for path in use.files}))
        more += [f"Through {_files(facades)} (a module facade or the declaration of the file): "
                 f"{len(through)} module(s), by folder: {_folders([use.module for use in through])}. "
                 "They see the change only if it alters an item they use.", ""]
        holds.append(f"{len(through)} through a facade or declaration")
    if farther:
        more += [f"Within {report.hops} hops, by folder: {_folders(farther)}.", ""]
        holds.append(f"{len(farther)} within {report.hops} hops")
    if tests:
        more += [f"Tests that use the changed code ({len(tests)}), by folder: {_folders(tests)}.", ""]
        holds.append(_count(len(tests), "test module"))
    if outside:
        shown = outside if full else outside[:5]
        more += [f"Not in the code graph ({len(outside)}: documents, configuration or deleted files): "
                 + ", ".join(_code(p) for p in shown) + (f" and {len(outside) - len(shown)} more" if len(outside) > len(shown) else "") + ".", ""]
        holds.append(f"{_count(len(outside), 'file')} outside the code graph")
    if more and full:
        lines += more
    elif more:
        lines += ["<details>", f"<summary>More: {', '.join(holds)}</summary>", "", *more, "</details>", ""]

    if said and not report.introduced:
        lines += ["No new boundary finding.", ""]
    if report.resolved:
        lines += [f"{report.resolved} finding(s) of the base branch are gone in this pull request.", ""]
    if report.not_checked:
        named = report.not_checked if full else report.not_checked[:FOLDER_LIMIT]
        parts = "; ".join(f"part {_code(p.part)} ({_REASONS.get(p.reason, p.reason)})" for p in named)
        extra = f"; {len(report.not_checked) - len(named)} more" if len(report.not_checked) > len(named) else ""
        lines += [f"**Not checked**: {parts}{extra}. No finding is reported there, and that is not a pass.", ""]
    if report.skipped:
        lines += [f"{len(report.skipped)} changed file(s) left out by the `paths` filter.", ""]
    if report.map_run:
        lines += [f"Interactive map: the artifact `{MAP_ARTIFACT}` of [this workflow run]({report.map_run}).", ""]
    lines += [footer, ""]
    return "\n".join(lines)


_REASONS = {
    "declared-rules-not-read": "its declared rules could not be read",
    "style-undetermined": "its architecture style could not be determined",
    "no-layer-style": "its style has no layers",
    "too-few-files": "it has too few files to judge",
}


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def cut(body: str, limit: int) -> str:
    """body, or when it is over limit (in UTF-8 bytes) its first whole lines that fit, a notice and its footer.

    The first line (the marker the comment is found by) and the last line (the footer) always stay. The cut
    never falls inside a code block: a block that does not fit whole is left out, with the heading above it."""
    if _size(body) <= limit:
        return body
    lines = body.split("\n")
    last = max(index for index, line in enumerate(lines) if line.strip())
    tail = f"\n{CUT_NOTICE}\n\n{lines[last]}\n"
    budget = limit - _size(tail) - _size("\n\n</details>")
    kept = [lines[0]]
    size = _size(lines[0]) + 1
    for line in lines[1:last]:
        size += _size(line) + 1
        if size > budget:
            break
        kept.append(line)
    opened = -1
    for index, line in enumerate(kept):
        if line.startswith("```"):
            opened = index if opened < 0 else -1
    if opened >= 0:
        kept = kept[:opened]
    while len(kept) > 1 and (not kept[-1].strip() or kept[-1].startswith("#") or kept[-1].startswith("<summary>") or kept[-1] == "<details>"):
        kept.pop()
    folded = kept.count("<details>") > kept.count("</details>")   # the notice and the footer must not be hidden in it
    return "\n".join(kept) + ("\n\n</details>" if folded else "") + "\n" + tail
