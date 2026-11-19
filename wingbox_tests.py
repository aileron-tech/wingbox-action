"""Evidence that the tests to run did run: the test cases of JUnit XML reports, matched with test files.

The check never runs a test. A job that ran its tests before the check hands over the report files the test
runner wrote (the `test-results` input); this module reads their test cases and says, for each test the
check names, which cases the reports hold of it and how each ended. A test is passed only when every case
recorded for it passed. Pure, standard library only: the files are read by wingbox_pr.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Mapping, Optional

PASSED, FAILED, SKIPPED, NO_RECORD = "passed", "failed", "skipped", "no-record"
PARTIAL = "partial"                # of a test file: a case passed, and another was skipped or did not run
STATUSES = (PASSED, FAILED, SKIPPED, NO_RECORD)    # of a case, in the order they are counted
SIZE_LIMIT = 64 * 1024 * 1024      # bytes of one report file; a larger one is refused
# A file Wingbox reads as source code, by its ending.
SOURCE_FILE = re.compile(r"\.(?:py|pyi|ts|tsx|mts|cts|js|jsx|mjs|cjs|rs|cs|java|kt|go|rb|php|dart|pas|pp)$")


@dataclass(frozen=True)
class Case:
    """One recorded test case: where the report says it is, and how it ended."""
    file: str         # the file attribute of the case or of its suite; "" when the report has none
    classname: str
    suite: str        # the name of the suite that holds it
    status: str       # PASSED, FAILED or SKIPPED
    name: str = ""    # the name of the case


@dataclass(frozen=True)
class TestReport:
    """A report file that was read: its path as given, the SHA-256 of its bytes, the cases it holds."""
    __test__ = False
    path: str
    sha256: str
    cases: int


@dataclass(frozen=True)
class CaseResult:
    """One case of a test to run: where the report puts it (its class name, else its file, else its suite),
    its name and how it ended. NO_RECORD: a test function the file holds and no report names."""
    where: str
    name: str
    status: str       # PASSED, FAILED, SKIPPED or NO_RECORD


@dataclass(frozen=True)
class Evidence:
    """What the reports hold about one test to run."""
    test: str
    status: str       # PASSED, FAILED, SKIPPED, PARTIAL or NO_RECORD
    cases: int        # the recorded cases that belong to it
    records: tuple[CaseResult, ...] = ()   # each of those cases, then the test functions of the file without a record


def parse_junit(data: bytes) -> list[Case]:
    """The test cases of one JUnit XML report. ValueError when the file is too large, declares a document
    type or entities (none is needed, and they are how an XML file grows on reading), or is not such a report."""
    if len(data) > SIZE_LIMIT:
        raise ValueError(f"larger than {SIZE_LIMIT // (1024 * 1024)} MB")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("declares a document type")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError(f"not XML: {error}") from error
    if _local(root.tag) not in ("testsuites", "testsuite"):
        raise ValueError(f"not a JUnit report: its root is <{_local(root.tag)}>")
    cases: list[Case] = []

    def walk(node: ElementTree.Element, suite: str, file: str) -> None:
        for child in node:
            tag = _local(child.tag)
            if tag == "testsuite":
                walk(child, child.get("name", "") or suite, child.get("file", "") or file)
            elif tag == "testcase":
                kinds = {_local(result.tag) for result in child}
                status = FAILED if kinds & {"failure", "error"} else SKIPPED if "skipped" in kinds else PASSED
                cases.append(Case(_path(child.get("file", "") or file), child.get("classname", ""), suite, status, child.get("name", "")))

    walk(root, root.get("name", "") if _local(root.tag) == "testsuite" else "", root.get("file", "") if _local(root.tag) == "testsuite" else "")
    return cases


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _path(text: str) -> str:
    text = text.replace("\\", "/")
    return text[2:] if text.startswith("./") else text


def _forms(test: str) -> tuple[str, ...]:
    """A test as the check names it (a file, or a module: the file without its ending), with and without ending."""
    test = _path(test)
    bare = SOURCE_FILE.sub("", test)
    return (test,) if bare == test else (test, bare)


def _same_file(test: str, place: str) -> bool:
    """Whether a place a report names (a file, absolute or from another folder) is the test file."""
    if not place or "/" not in place and "." not in place:
        return False
    for mine in _forms(test):
        for theirs in _forms(place):
            if mine == theirs or theirs.endswith("/" + mine) or mine.endswith("/" + theirs):
                return True
    return False


def belongs(test: str, case: Case) -> bool:
    """Whether a recorded case is a case of this test file, by what the report says of the case alone.

    By file: the file of the case or of its suite, a class name or a suite name that is a path (JavaScript
    runners). By class name: a dotted name that is the file's path (Python: `tests.test_x.TestCase`) or its
    end (Java, C#: `com.shop.OrderTest` for `src/test/java/com/shop/OrderTest.java`). A record of a Go
    package names no file: see in_go_file."""
    if any(_same_file(test, place) for place in (case.file, _path(case.classname), _path(case.suite))):
        return True
    name = case.classname
    if name and "/" not in name:
        for form in _forms(test):
            dotted = form.replace("/", ".")
            if name == dotted or name.startswith(dotted + ".") or dotted.endswith("." + name):
                return True
    return False


_GO_TEST = re.compile(r"_test(?:\.go)?$")
_GO_TEST_FUNCTION = re.compile(r"^Test(?:[^a-z]|$)")   # what `go test` runs as a test: TestXxx, Xxx not starting in lower case


def _folder(test: str) -> str:
    test = _path(test)
    return test.rsplit("/", 1)[0] if "/" in test else ""


def _in_package(folder: str, case: Case) -> bool:
    """Whether the package a Go record names (its class name or its suite: an import path) is this folder."""
    return bool(folder) and any(place == folder or place.endswith("/" + folder) for place in (_path(case.classname), _path(case.suite)) if place)


def in_go_file(test: str, functions: tuple[str, ...], case: Case) -> bool:
    """Whether a record of a Go package is a case of this test file: the package is the folder of the file
    and the test function of the case (the name before the first `/`: a subtest is `TestX/name`) is one of
    the functions of the file."""
    return _in_package(_folder(test), case) and case.name.split("/", 1)[0] in functions


def go_candidates(tests: list[str], cases: list[Case]) -> list[str]:
    """The tests to run that may be Go test files with a package record: named `..._test.go` or `..._test`
    (a module), in a folder that is the end of a package a report names. Their functions are asked for."""
    return [test for test in tests if _GO_TEST.search(_path(test)) and any(_in_package(_folder(test), case) for case in cases)]


def _status(statuses: list[str]) -> str:
    """The status of a test file from those of its cases: failed when one failed; passed only when every one
    passed; skipped when none passed; partial when one passed and another was skipped or has no record."""
    recorded = [status for status in statuses if status != NO_RECORD]
    if not recorded:
        return NO_RECORD
    if FAILED in recorded:
        return FAILED
    if all(status == PASSED for status in statuses):
        return PASSED
    return PARTIAL if PASSED in recorded else SKIPPED


def evidence(tests: list[str], cases: list[Case], functions: Optional[Mapping[str, tuple[str, ...]]] = None) -> tuple[Evidence, ...]:
    """For each test to run, the recorded cases that belong to it and the status they give it (see _status).

    functions: for a Go test file, by its name among the tests, the functions the file holds. A record of a
    Go package counts for the file that holds its test function, and a test function of the file that no
    report names is kept as a case without a record; a Go test file whose functions are not known gets no
    package record."""
    found = []
    for test in tests:
        mine = (functions or {}).get(test)
        matched = [case for case in cases if belongs(test, case) or (mine is not None and in_go_file(test, mine, case))]
        records = [CaseResult(case.classname or case.file or case.suite, case.name, case.status) for case in matched]
        if mine is not None:
            ran = {case.name.split("/", 1)[0] for case in matched}
            records += [CaseResult(_folder(test), name, NO_RECORD) for name in mine
                        if _GO_TEST_FUNCTION.match(name) and name != "TestMain" and name not in ran]
        found.append(Evidence(test, _status([record.status for record in records]), len(matched), tuple(records)))
    return tuple(found)
