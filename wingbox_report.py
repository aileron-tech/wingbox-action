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

import json
import re
from dataclasses import dataclass, replace
from typing import Optional

import wingbox_changes as changes
import wingbox_diagram as diagram
import wingbox_tests
from wingbox_facts import BLOCKING_HEADS, MARKER, WORKING_TREE, ChangedFile, ChangedItem, Dependent, Report, Tree, User, folder_of

FAIL_ON = ("none", "violations", "not-checked", "strict")
LIST_LIMIT = 10    # direct dependents and findings named in the comment; the job summary names them all
ITEM_LIMIT = 5     # changed items the comment gives a block to; the job summary gives every one a block
TESTS_LIMIT = 5    # test files named in the comment; the job summary names them all
PLAN_LIMIT = 60    # lines of the review plan carried in the comment; the plan file and the job summary hold all
PLAN_SCHEMA = 1
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
    """A pull request that changes no source module of the graph other than tests gets no comment, unless a
    wingbox command failed or a language was left out: then the comment says what could not be checked."""
    return (any(c.module is not None and not is_test(c.path) for c in report.changed) or bool(report.introduced)
            or bool(report.tool_errors) or bool(report.skipped_languages) or bool(_tree_reasons(report)))


def _tree_reasons(report: Report) -> tuple[str, ...]:
    """Why the files analysed are not the commit the report names; empty when they are (the head, or its merge
    into the base, with no file changed since)."""
    tree = report.tree
    if tree is None or tree.relation == WORKING_TREE:
        return ()
    reasons = []
    if tree.relation == "other":
        reasons.append(f"tree-not-at-head: checked {tree.commit[:12]}, named {report.head[:12]}")
    if tree.differs:
        reasons.append(f"tree-differs-from-commit: {len(tree.differs)} file(s)")
    return tuple(reasons)


def result(report: Report) -> tuple[str, str, tuple[str, ...]]:
    """(result, execution status, reasons) of the boundary check of a pull request.

    The result never reads what was not checked as a pass. FAIL: the pull request introduces a blocking
    finding. UNKNOWN: a wingbox command ended abnormally (execution "error"), files of a language were left out of
    the graph (its parser is not installed), or a part of the repository was not checked. NOT_APPLICABLE: no source module outside the tests changed. PASS: none of these."""
    execution = "error" if report.tool_errors else "ok"
    errors = tuple(f"tool-error: {error}" for error in report.tool_errors)
    unchecked = tuple(f"not-checked: {part.part} ({part.reason})" for part in report.not_checked)
    left_out = tuple(f"skipped-language: {name} ({files} files)" for name, files in report.skipped_languages)
    blocking = sum(1 for finding in report.introduced if finding.head in BLOCKING_HEADS)
    tree = _tree_reasons(report)
    if blocking:
        return "FAIL", execution, (f"introduced-violations: {blocking}", *errors, *left_out, *tree)
    if errors or left_out or tree:
        # Before "no source module changed": the changed files may be of the language that was left out, and a
        # report about another tree than the one named says nothing about the one named.
        return "UNKNOWN", execution, (*errors, *left_out, *tree, *unchecked)
    if not needs_comment(report):
        return "NOT_APPLICABLE", execution, ("no-source-module-changed",)
    if unchecked:
        return "UNKNOWN", execution, unchecked
    return "PASS", execution, ()


def exit_status(report: Report, fail_on: str) -> int:
    """1 when the fail-on condition holds, else 0. strict: everything but PASS and NOT_APPLICABLE fails."""
    if fail_on == "violations" and any(f.head in BLOCKING_HEADS for f in report.introduced):
        return 1
    if fail_on == "not-checked" and report.not_checked:
        return 1
    if fail_on == "strict" and result(report)[0] in ("FAIL", "UNKNOWN"):
        return 1
    return 0


def changed_tests(raw: dict[str, tuple[Optional[str], tuple[Dependent, ...]]],
                  hunks: dict[str, tuple[changes.Hunk, ...]]) -> tuple[tuple[str, str], ...]:
    """(test file, source file) for every changed test that uses a changed source file directly and loses or
    replaces a line of code in this pull request: a test changed together with the code it tests.

    raw maps each changed file to its module (None outside the graph) and to all its dependents, the changed
    ones included. A test that only gains lines is a new test, not a changed one."""
    found = []
    for test_path, (test_module, _dependents) in raw.items():
        if test_module is None or not is_test(test_path) or not changes.removes_code(hunks.get(test_path, ()), test_path):
            continue
        for source_path, (source_module, dependents) in raw.items():
            if source_module is not None and not is_test(source_path) and any(d.module == test_module and d.depth == 1 for d in dependents):
                found.append((test_path, source_path))
    return tuple(sorted(found))


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


_KINDS = {"fn": "function", "cls": "type", "enm": "enum", "typ": "type", "ifc": "interface", "var": "variable", "mod": "module",
          "method": "method"}
_VERBS = {"call": "calls it", "unresolved-call": "calls it", "import": "imports it", "type": "uses it as a type"}


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


@dataclass(frozen=True)
class TestToRun:
    """A test that uses what changed: the file (or the module when the file is not known) and what it uses,
    an item of a changed file or the changed file as a whole."""
    __test__ = False     # not a test case
    shown: str
    uses: str
    by_item: bool


def tests_to_run(report: Report) -> list[TestToRun]:
    """The tests that use what the pull request changes: the test modules that use a changed item, and for a
    file taken as a whole (not looked at, or with other code changed) the tests that use the file directly."""
    found: dict[str, TestToRun] = {}
    for changed in report.changed:
        for item in changed.items or ():
            for user in item.users:
                if is_test(user.module):
                    found.setdefault(user.module, TestToRun(user.file or user.module, item.name, True))
    whole = _whole_files(report)
    for use in uses(report):
        paths = [path for path in use.files if path in whole]
        if use.kind == TEST and use.depth == 1 and paths:
            found.setdefault(use.module, TestToRun(use.module, paths[0], False))
    return sorted(found.values(), key=lambda test: test.shown)


def test_evidence(report: Report) -> Optional[tuple[wingbox_tests.Evidence, ...]]:
    """What the test reports given to the check hold about each test to run; None when none was given."""
    if report.test_cases is None:
        return None
    return wingbox_tests.evidence([test.shown for test in tests_to_run(report)], list(report.test_cases), dict(report.test_functions))


def required_evidence(report: Report) -> Optional[tuple[wingbox_tests.Evidence, ...]]:
    """What the test reports hold about each test the manifest requires for this change, in the order of
    report.required_tests; None when no test report was given."""
    if report.test_cases is None:
        return None
    return wingbox_tests.evidence([item.test for item in report.required_tests], list(report.test_cases), dict(report.test_functions))


def holds_source(report: Report) -> bool:
    """Whether the change holds source code outside the tests: a module of the graph, or a file with the
    ending of a source file (its language may have been left out of the graph)."""
    return any((changed.module is not None or wingbox_tests.SOURCE_FILE.search(changed.path)) and not is_test(changed.path) for changed in report.changed)


def manifest_errors(report: Report) -> tuple[str, ...]:
    """Why the manifest of required tests could not be read; empty when it was, or when there is none."""
    return report.tests_manifest.errors if report.tests_manifest is not None else ()


def tests_summary(report: Report) -> str:
    """The test evidence of a run in one word. none: no test report was given. none-found: reports were
    given, the graph found no test that uses what changed and the manifest requires none: the reports say
    nothing about this change. failed: a test to run or a required test has a failed case. incomplete: one
    has a case that was skipped or did not run, or has no record, or the manifest could not be read.
    complete: every recorded case of every test to run and of every required test passed."""
    found, needed = test_evidence(report), required_evidence(report)
    if found is None or needed is None:
        return "none"
    statuses = {item.status for item in (*found, *needed)}
    if wingbox_tests.FAILED in statuses:
        return "failed"
    if statuses - {wingbox_tests.PASSED} or manifest_errors(report):
        return "incomplete"
    return "complete" if statuses else "none-found"


def evidence_counts(report: Report) -> tuple[str, str, str]:
    """(tests to run whose every recorded case passed, with a failed case, without a passing record: skipped
    in whole or in part, or no record), as the text of the outputs; three empty texts when no test report was
    given."""
    found = test_evidence(report)
    if found is None:
        return "", "", ""
    passed = sum(1 for item in found if item.status == wingbox_tests.PASSED)
    failed = sum(1 for item in found if item.status == wingbox_tests.FAILED)
    return str(passed), str(failed), str(len(found) - passed - failed)


def tests_status(report: Report, require: bool, strict: bool = False) -> int:
    """1 when a passing record is required and missing: a test to run or a required test has none (no test
    report was given, or the test failed, was skipped in whole or in part, or is not in the reports), or the
    manifest of required tests could not be read. A change no test was found for passes, unless strict and
    the change holds source code outside the tests. Else 0."""
    if not require:
        return 0
    if manifest_errors(report):
        return 1
    if not tests_to_run(report) and not report.required_tests:
        return 1 if strict and holds_source(report) else 0
    found, needed = test_evidence(report), required_evidence(report)
    return 0 if found is not None and needed is not None and all(item.status == wingbox_tests.PASSED for item in (*found, *needed)) else 1


def outputs(report: Report) -> dict[str, str]:
    """The outputs of a run that are read from the report alone, by their names."""
    outcome, execution, reasons = result(report)
    passed, failed, without = evidence_counts(report)
    needed = required_evidence(report)
    return {"result": outcome, "execution": execution, "reasons": "; ".join(reasons),
            "untouched-dependents": str(untouched_count(report)), "introduced-findings": str(len(report.introduced)),
            "not-checked-parts": str(len(report.not_checked)), "tests-to-run": str(len(tests_to_run(report))),
            "tests-passed": passed, "tests-failed": failed, "tests-without-record": without,
            "tests-evidence": tests_summary(report), "tests-required": str(len(report.required_tests)),
            "tests-required-passed": "" if needed is None else str(sum(1 for item in needed if item.status == wingbox_tests.PASSED)),
            "test-reports-refused": "" if report.test_cases is None else str(sum(1 for read in report.test_reports if read.refused))}


def _evidence_words(recorded: tuple[wingbox_tests.Evidence, ...], full: bool) -> str:
    """How the tests to run ended by the test reports: the passed counted, the others counted and named."""
    def named(status: str) -> list[str]:
        return [item.test for item in recorded if item.status == status]

    def listed(names: list[str]) -> str:
        shown = names if full else names[:TESTS_LIMIT]
        return ", ".join(_code(name) for name in shown) + (f" and {len(names) - len(shown)} more" if len(names) > len(shown) else "")

    def in_part(names: list[str]) -> str:
        """The tests with their cases counted: a passed case there does not make the test passed."""
        cases = {item.test: item.records for item in recorded}
        shown = names if full else names[:TESTS_LIMIT]
        return (", ".join(f"{_code(name)}: {sum(1 for case in cases[name] if case.status == wingbox_tests.PASSED)} of {len(cases[name])} cases passed"
                          for name in shown) + (f" and {len(names) - len(shown)} more" if len(names) > len(shown) else ""))

    said = []
    passed, failed, partial, skipped, none = (named(status) for status in (wingbox_tests.PASSED, wingbox_tests.FAILED, wingbox_tests.PARTIAL,
                                                                           wingbox_tests.SKIPPED, wingbox_tests.NO_RECORD))
    if passed:
        said.append(f"{len(passed)} passed")
    if failed:
        said.append(f"{len(failed)} FAILED ({listed(failed)})")
    if partial:
        said.append(f"{len(partial)} with a case skipped or not run ({in_part(partial)})")
    if skipped:
        said.append(f"{len(skipped)} skipped ({listed(skipped)})")
    if none:
        said.append(f"{len(none)} without a record in the test report ({listed(none)})")
    return ", ".join(said)


def _required_lines(report: Report, full: bool) -> list[str]:
    """The lines of the comment about the tests the manifest requires for this change, and about a manifest
    that could not be read."""
    read = report.tests_manifest
    if read is None:
        return []
    lines = []
    if read.errors:
        lines += [f"The manifest of required tests {_code(read.path)} could not be read ({'; '.join(read.errors)}): "
                  "which tests this change must pass is not known.", ""]
    if report.required_tests:
        needed = required_evidence(report)
        names = [item.test for item in report.required_tests]
        shown = names if full else names[:TESTS_LIMIT]
        said = (", ".join(_code(name) for name in shown) + (f" and {len(names) - len(shown)} more" if len(names) > len(shown) else "")
                if needed is None else _evidence_words(needed, full))
        where = "the base" if read.source == "base" else "the checked files"
        lines += [f"Required tests ({len(names)}, by {_code(read.path)} of {where}): {said}.", ""]
    return lines


def _whole_files(report: Report) -> set[str]:
    """The changed files spoken of as a whole: not looked at, or with code outside every item changed."""
    return {changed.path for changed in report.changed if changed.module is not None and (changed.items is None or changed.other_code)}


def _direct_uses(report: Report, found: tuple[Use, ...], item_users: set[str]) -> tuple[list[Use], list[str]]:
    """(the direct users of the files spoken of as a whole, each with only those files; the modules that use a
    changed file but not what changed in it)."""
    whole = _whole_files(report)
    direct: list[Use] = []
    quiet: list[str] = []
    for use in found:
        if use.kind != DIRECT:
            continue
        kept = [index for index, path in enumerate(use.files) if path in whole]
        if kept:
            direct.append(replace(use, files=tuple(use.files[index] for index in kept), names=tuple(use.names[index] for index in kept)))
        elif use.module not in item_users:
            quiet.append(use.module)
    return direct, quiet


_ATOM = re.compile(r"^[A-Za-z0-9_.:/!<>=+*-]+$")


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _atom(text: str) -> str:
    """A name as a bare word of an S-expression, or quoted when it holds anything else."""
    return text if _ATOM.match(text) else _q(text)


def build_plan(report: Report) -> str:
    """The review plan of a pull request as S-expression lines, in the order to check.

    A head line (schema, result, execution status, commits, wingbox version), the reasons of the result, the
    findings, then for each changed item the places outside the pull request that use it (file, line, how),
    the files whose other code changed with the modules that use them, the tests to run, the tests changed
    with their code, the parts not checked and the content hash of each changed file. For a reviewing agent:
    every `look` line is a place to open, every `run-test` line a test to run, every `required-test` line a
    test the manifest of the repository requires for this change, every `test-case` line a case the test
    reports hold of such a test (or a test function of it that they do not hold). A `test-report-refused`
    line names a report that does not count as evidence, with the reasons."""
    outcome, execution, reasons = result(report)
    lines = [f"(review-plan :schema {PLAN_SCHEMA} :result {outcome} :execution {execution} :base {report.base} :head {report.head} "
             f":wingbox {_q(report.version)} :hops {report.hops}{_tree_keys(report.tree)})"]
    lines += [f"(reason {_q(reason)})" for reason in reasons]
    lines += [f"(finding {finding.head} {_q(finding.text)})" for finding in report.introduced]
    named = changed_items(report)
    for changed, item in named:
        how = f"calls :via ({' '.join(_atom(name) for name in item.via)})" if item.via else "changed"
        lines.append(f"(changed-item {_q(changed.path)} {_atom(item.name)} :kind {item.kind} :line {item.line} :how {how})")
        lines += [f"(look {_q(user.file)} {user.line} :module {_atom(user.module)} :why {_atom(user.kind)} :item {_atom(item.name)} :places {user.places})"
                  for user in _real_users(item)]
    item_users = {user.module for _changed, item in named for user in _real_users(item)}
    direct, _quiet = _direct_uses(report, uses(report) if needs_comment(report) else (), item_users)
    for changed in report.changed:
        if changed.module is not None and changed.items is None:
            lines.append(f"(whole-file {_q(changed.path)})")
        elif changed.other_code:
            lines.append(f"(other-code {_q(changed.path)})")
    lines += [f"(look-module {_atom(use.module)} :why uses :file {_q(path)})" for use in direct for path in use.files]
    recorded = {item.test: item for item in test_evidence(report) or ()}
    for test in tests_to_run(report):
        known = recorded.get(test.shown)
        proof = "none" if report.test_cases is None or known is None else f"{known.status} :cases {known.cases}{_case_counts(known)}"
        lines.append(f"(run-test {_q(test.shown)} :uses {_atom(test.uses) if test.by_item else _q(test.uses)} :evidence {proof})")
    needed = required_evidence(report)
    for index, wanted in enumerate(report.required_tests):
        proof = "none" if needed is None else f"{needed[index].status} :cases {needed[index].cases}{_case_counts(needed[index])}"
        lines.append(f"(required-test {_q(wanted.test)} :group {_atom(wanted.group)} :evidence {proof})")
    if report.tests_manifest is not None:
        read = report.tests_manifest
        if read.source != "none":
            lines.append(f"(required-tests {_q(read.path)} :from {read.source}{f' :commit {read.commit}' if read.commit else ''} :sha256 {read.sha256})")
        lines += [f"(required-tests-error {_q(read.path)} :reason {_q(error)})" for error in read.errors]
    if report.test_cases is not None:
        lines.append(f"(tests :evidence {tests_summary(report)})")
    case_lines = [f"(test-case {_q(item.test)} {_q(case.where)} {_q(case.name)} :status {case.status})"
                  for item in (*(recorded[test.shown] for test in tests_to_run(report) if test.shown in recorded), *(needed or ()))
                  for case in item.records]
    lines += list(dict.fromkeys(case_lines))     # a test that is to run and required has its cases once
    if report.test_origin is not None:
        known = report.test_origin
        keys = [("commit", known.commit), ("started", known.started), ("run", known.run_id), ("attempt", known.attempt)]
        lines.append("(test-provenance" + "".join(f" :{key} {_atom(value)}" for key, value in keys if value)
                     + f" :sidecar {'required' if known.require_sidecar else 'optional'})")
        if known.command:
            lines.append(f"(test-command {_q(known.command)})")
    for read in report.test_reports:
        lines.append(f"(test-report-refused {_q(read.path)} :sha256 {read.sha256} :cases {read.cases} :reason {_q('; '.join(read.refused))})"
                     if read.refused else f"(test-report {_q(read.path)} :sha256 {read.sha256} :cases {read.cases})")
        if read.modified:
            lines.append(f"(test-report-origin {_q(read.path)} :modified {read.modified}" + (f" :timestamp {_q(read.timestamp)}" if read.timestamp else "")
                         + f" :tracked {read.tracked} :sidecar {read.sidecar}"
                         + (f" :commits ({' '.join(_atom(commit) for commit in read.commits)})" if read.commits else "") + ")")
    lines += [f"(test-report-error {_q(path)} :reason {_q(reason)})" for path, reason in report.test_report_errors]
    lines += [f"(test-changed {_q(test)} :tests {_q(source)})" for test, source in report.tests_changed]
    lines += [f"(not-checked {_q(part.part)} :reason {_atom(part.reason)})" for part in report.not_checked]
    lines += [f"(skipped-language {_atom(name)} :files {files})" for name, files in report.skipped_languages]
    if report.checked_modules is not None:
        lines.append(f"(checked :modules {report.checked_modules})")
    commit = f" :commit {report.tree.commit}" if report.tree is not None and report.tree.commit else ""
    lines += [f"(changed-file {_q(path)} :sha256 {digest}{commit})" for path, digest in report.hashes]
    if report.tree is not None:
        lines += [f"(tree-differs {_q(path)})" for path in report.tree.differs[:DIFFERS_LIMIT]]
        if len(report.tree.differs) > DIFFERS_LIMIT:
            lines.append(f"(tree-differs-more :hidden {len(report.tree.differs) - DIFFERS_LIMIT})")
    return "\n".join(lines)


def _case_counts(known: wingbox_tests.Evidence) -> str:
    """The cases of a test to run counted by status, for its `run-test` line; nothing when they all ended alike."""
    counted = [(status, sum(1 for case in known.records if case.status == status)) for status in wingbox_tests.STATUSES]
    return "".join(f" :{status} {number}" for status, number in counted if number) if sum(1 for _status, number in counted if number) > 1 else ""


DIFFERS_LIMIT = 50   # differing files named in the plan; the reason of the result holds the count of them all


def _tree_keys(tree: Optional[Tree]) -> str:
    """The keys of the plan's head line that say which files were analysed."""
    if tree is None:
        return ""
    return f" :tree {tree.relation}" if not tree.commit else f" :checked {tree.commit} :tree {tree.relation}"


_PLAN_WORK = ("(finding ", "(look ", "(look-module ", "(run-test ", "(required-test ", "(test-changed ")


def _build_failed(report: Report) -> bool:
    """Whether a `wingbox build` of this run failed: then there is no graph to answer from."""
    return any(error.startswith("wingbox build ") for error in report.tool_errors)


def build_comment(report: Report, full: bool = False) -> str:
    """The pull request comment, or with full the job summary, in Markdown, starting with the marker.

    It says the thing first: a heading and a sentence with how many modules outside the pull request use what
    it changes; then the boundary findings the pull request introduces, then those modules with what they use
    (the comment at most LIST_LIMIT of them, the summary all). The diagram, the users of a facade, the farther
    dependents, the tests and the files outside the graph follow, folded in the comment and open in the
    summary. Without a direct user and without a finding the comment is one sentence. A pull request that
    changes only documents, configuration or tests gets one line."""
    head = ("head: the working directory (uncommitted and untracked files included)" if report.head == WORKING_TREE
            else f"head {_code(report.head[:7])}")
    tree = report.tree
    if tree is not None and tree.relation == "merge":
        head += f", checked as merged into the base (commit {_code(tree.commit[:7])})"
    footer = (f"<sub>[Wingbox]({SITE}) {report.version}, base {_code(report.base[:7])}, {head}. "
              "Built and checked on the runner, without a language model; no code left it.</sub>")
    if not needs_comment(report):
        return "\n".join([MARKER, "**Wingbox**: no source module outside the tests changed in this pull request; "
                          "nothing to check.", "", *_required_lines(report, full), footer, ""])
    problem = ""
    if report.tool_errors:
        # A failed command leaves answers empty, and an empty answer must not read as "nothing uses this".
        problem = (f"**Wingbox** could not check this change{'' if _build_failed(report) else ' completely'}: "
                   f"{', '.join(_code(error) for error in report.tool_errors)}. "
                   "The result is UNKNOWN, not a pass; the job log has the tool's message.")
    nothing_else = not any(c.module is not None and not is_test(c.path) for c in report.changed) and not report.introduced
    if report.skipped_languages:
        # Files left out of the graph have no users and no findings in it: that must not read as "nothing uses this".
        counts = [f"{files:,} {_LANGUAGES.get(name, name)} file{'' if files == 1 else 's'}" for name, files in report.skipped_languages]
        listed = counts[0] if len(counts) == 1 else f"{', '.join(counts[:-1])} and {counts[-1]}"
        one = len(counts) == 1 and report.skipped_languages[0][1] == 1
        left_out = (f"**Not analysed**: {listed} {'was' if one else 'were'} not analysed, because the parser of that language "
                    "is not installed in this run (install `wingbox[all]`). What uses them, and what they use, is not in "
                    "this report. The result is UNKNOWN, not a pass.")
        problem = f"{problem}\n\n{left_out}" if problem else left_out
    if _tree_reasons(report) and tree is not None:
        said_tree = []
        if tree.relation == "other":
            said_tree.append(f"The files checked are commit {_code(tree.commit[:7])}, not the head named ({_code(report.head[:7])}).")
        if tree.differs:
            shown = ", ".join(_code(path) for path in tree.differs[:3])
            more = f" and {len(tree.differs) - 3} more" if len(tree.differs) > 3 else ""
            count = f"{len(tree.differs):,} file{'' if len(tree.differs) == 1 else 's'}"
            said_tree.append(f"{count} of the working folder {'is' if len(tree.differs) == 1 else 'are'} not as in commit "
                             f"{_code(tree.commit[:7])} ({shown}{more}).")
        other_tree = (f"**Wingbox** did not check the commit this report names. {' '.join(said_tree)} "
                      "The result is UNKNOWN, not a pass.")
        problem = f"{problem}\n\n{other_tree}" if problem else other_tree
    if problem and (_build_failed(report) or nothing_else):
        # Without a graph, or without a changed module in it, the usual report would be made of empty answers:
        # say only what was not checked.
        return "\n".join([MARKER, problem, "", footer, ""])
    found = uses(report)
    # A file whose changed lines were sorted into items speaks through those items; a file that was not looked
    # at, or in which code outside every item changed too, is spoken of as a whole.
    looked_at = any(changed.items is not None for changed in report.changed)
    named = changed_items(report)
    item_users = {user.module for _changed, item in named for user in _real_users(item)}
    direct, quiet = _direct_uses(report, found, item_users)
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
        said.append(f"{_code(only.name)} {'calls code that changed' if only.via else 'changed'}, and {_count(count, 'module')} "
                    f"outside this pull request use{'' if count > 1 else 's'} it")
    elif named:
        what = "items touched by this change" if any(item.via for _changed, item in named) else "changed items"
        said.append(f"{len(named)} {what} are used by {_count(len(item_users), 'module')} outside this pull request")
    elif direct:
        said.append(f"{_count(len(direct), 'module')} outside this pull request use{'' if len(direct) > 1 else 's'} what it changes")
    lines = [MARKER, problem, ""] if problem else [MARKER]
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
        happened = "calls " + ", which calls ".join(_code(name) for name in item.via) + ", which changed" if item.via else "changed"
        lines += [f"{_code(item.name)} ({_KINDS.get(item.kind, item.kind)}, {_code(f'{changed.path}:{item.line}')}) {happened}. "
                  f"{'These modules use' if several else 'This module uses'} it and {'are' if several else 'is'} not in the pull request:", ""]
        lines += [_user_row(user) for user in shown_users]
        if len(real) > len(shown_users):
            lines.append(f"- ... and {len(real) - len(shown_users)} more, by folder: "
                         f"{_folders([user.module for user in real[len(shown_users):]])} (the job summary lists them all)")
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

    to_run = tests_to_run(report)
    if to_run:
        shown_tests = to_run if full else to_run[:TESTS_LIMIT]
        rest = f" and {len(to_run) - len(shown_tests)} more (the job summary lists them all)" if len(to_run) > len(shown_tests) else ""
        recorded = test_evidence(report)
        if recorded is None:
            lines += [f"Tests that use what changed ({len(to_run)}): " + ", ".join(_code(test.shown) for test in shown_tests) + rest + ".", ""]
        else:
            lines += [f"Tests that use what changed ({len(to_run)}): " + _evidence_words(recorded, full) + ".", ""]
            if report.test_report_errors:
                lines += ["Test reports that gave no cases: " + "; ".join(f"{_code(path)} ({reason})" for path, reason in report.test_report_errors)
                          + ".", ""]
    elif report.test_cases is not None:
        lines += ["Tests that use what changed: none found." + ("" if report.required_tests else " The test reports say nothing about this change."), ""]
    refused = [read for read in report.test_reports if read.refused]
    if refused:
        shown = refused if full else refused[:TESTS_LIMIT]
        lines += ["Test reports refused, which do not count as evidence: " + "; ".join(f"{_code(read.path)} ({'; '.join(read.refused)})" for read in shown)
                  + (f"; and {len(refused) - len(shown)} more" if len(refused) > len(shown) else "") + ".", ""]
    lines += _required_lines(report, full)
    if report.tests_changed:
        pairs = sorted(report.tests_changed) if full else sorted(report.tests_changed)[:LIST_LIMIT]
        rest = f"; {len(report.tests_changed) - len(pairs)} more" if len(report.tests_changed) > len(pairs) else ""
        lines += ["Tests changed together with the code they test: " + "; ".join(f"{_code(test)} (tests {_code(source)})" for test, source in pairs)
                  + rest + ". Look at what they no longer check.", ""]

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
        scope = ("" if report.checked_modules is None else
                 f" The rules were checked on {report.checked_modules:,} other modules." if report.checked_modules else
                 " No part of this repository could be checked against boundary rules.")
        lines += [f"**Not checked**: {parts}{extra}. No finding is reported there, and that is not a pass.{scope}", ""]
    if report.skipped:
        lines += [f"{len(report.skipped)} changed file(s) left out by the `paths` filter.", ""]
    if report.map_run:
        lines += [f"Interactive map: the artifact `{MAP_ARTIFACT}` of [this workflow run]({report.map_run}).", ""]
    plan = build_plan(report).split("\n")
    if full:
        outcome, execution, reasons = result(report)
        lines += [f"Result of the boundary check: **{outcome}** (execution {execution})." + (f" Reasons: {'; '.join(reasons)}." if reasons else ""), ""]
        lines += ["### Review plan", "", "```lisp", *plan, "```", ""]
    elif any(line.startswith(_PLAN_WORK) for line in plan):
        if len(plan) > PLAN_LIMIT:
            plan = [*plan[:PLAN_LIMIT], f"(plan-more :hidden {len(plan) - PLAN_LIMIT})"]
        lines += ["<details>", "<summary>Review plan for an agent</summary>", "", "```lisp", *plan, "```", "", "</details>", ""]
    lines += [footer, ""]
    return "\n".join(lines)


_LANGUAGES = {"csharp": "C#", "java": "Java", "go": "Go", "ruby": "Ruby", "php": "PHP", "dart": "Dart"}
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
