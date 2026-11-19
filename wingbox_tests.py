"""Evidence that the tests to run did run: the test cases of JUnit XML reports, matched with test files.

The check never runs a test. A job that ran its tests before the check hands over the report files the test
runner wrote (the `test-results` input); this module reads their test cases and says, for each test the
check names, whether the reports hold a passed, failed or skipped record of it, or none. Pure, standard
library only: the files are read by wingbox_pr.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

PASSED, FAILED, SKIPPED, NO_RECORD = "passed", "failed", "skipped", "no-record"
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


@dataclass(frozen=True)
class TestReport:
    """A report file that was read: its path as given, the SHA-256 of its bytes, the cases it holds."""
    __test__ = False
    path: str
    sha256: str
    cases: int


@dataclass(frozen=True)
class Evidence:
    """What the reports hold about one test to run."""
    test: str
    status: str       # PASSED, FAILED, SKIPPED or NO_RECORD
    cases: int        # the recorded cases that belong to it


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
                cases.append(Case(_path(child.get("file", "") or file), child.get("classname", ""), suite, status))

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
    """Whether a recorded case is a case of this test file.

    By file: the file of the case or of its suite, a class name or a suite name that is a path (JavaScript
    runners). By class name: a dotted name that is the file's path (Python: `tests.test_x.TestCase`) or its
    end (Java, C#: `com.shop.OrderTest` for `src/test/java/com/shop/OrderTest.java`). A Go package path that
    ends with the folder of a `_test.go` file."""
    if any(_same_file(test, place) for place in (case.file, _path(case.classname), _path(case.suite))):
        return True
    name = case.classname
    if name and "/" not in name:
        for form in _forms(test):
            dotted = form.replace("/", ".")
            if name == dotted or name.startswith(dotted + ".") or dotted.endswith("." + name):
                return True
    folder = _path(test).rsplit("/", 1)[0] if "/" in test else ""
    if folder and _path(test).endswith("_test.go"):
        return any(place == folder or place.endswith("/" + folder) for place in (_path(case.classname), _path(case.suite)) if place)
    return False


def evidence(tests: list[str], cases: list[Case]) -> tuple[Evidence, ...]:
    """For each test to run, what the recorded cases say: failed when one of its cases failed, passed when
    one passed and none failed, skipped when all were skipped, no record when no case belongs to it."""
    found = []
    for test in tests:
        statuses = [case.status for case in cases if belongs(test, case)]
        status = (NO_RECORD if not statuses else FAILED if FAILED in statuses else PASSED if PASSED in statuses else SKIPPED)
        found.append(Evidence(test, status, len(statuses)))
    return tuple(found)
