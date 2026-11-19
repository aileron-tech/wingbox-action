"""The evidence record of one run: what was checked, on which commit, with which result, as JSON.

A report for people (Markdown) and a plan for a reviewing agent (S-expression lines) say what to look at.
The record says what happened, for a system that keeps evidence of change control: the repository and the
commits, the commit whose files were analysed, the result with its reasons, the changed files with their
SHA-256, the tests that use what changed with what the test reports hold about them, and the SHA-256 of the
report and the plan written beside it. Its shape is schema/wingbox-evidence-2.schema.json. Pure: the run's
own facts (time, repository, run address) are given by the caller.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import wingbox_tests
from wingbox_facts import Report
from wingbox_report import result, test_evidence, tests_to_run, untouched_count

SCHEMA = "wingbox-evidence/2"
FILE_NAME = "wingbox-evidence.json"


@dataclass(frozen=True)
class Run:
    """Where and when the check ran, as the CI system says it."""
    provider: str        # github, gitlab or local
    repository: str      # owner/name or the project path; "" when not known
    change: str          # the number of the pull or merge request; "" outside one
    run_id: str
    url: str             # the address of the run or the job; "" when not known
    action_ref: str      # the tag, branch or commit of this action that ran; "" when not known
    generated: str       # UTC time of the record, YYYY-MM-DDTHH:MM:SSZ


def tests_summary(report: Report) -> str:
    """none: no test report was given; failed: a test to run has a failed case; incomplete: one has a case
    that was skipped or did not run, or has no record; complete: every recorded case of every test to run
    passed (also when there is no test to run)."""
    found = test_evidence(report)
    if found is None:
        return "none"
    statuses = {item.status for item in found}
    if wingbox_tests.FAILED in statuses:
        return "failed"
    return "incomplete" if statuses - {wingbox_tests.PASSED} else "complete"


def build_evidence(report: Report, run: Run, documents: dict[str, str]) -> dict[str, object]:
    """The record of a run. documents: the text of each file written beside it, by file name."""
    outcome, execution, reasons = result(report)
    found = test_evidence(report)
    recorded = {item.test: item for item in found or ()}
    tree = report.tree
    return {
        "schema": SCHEMA,
        "generated": run.generated,
        "tool": {"name": "wingbox-action", "ref": run.action_ref, "wingbox": report.version},
        "run": {"provider": run.provider, "repository": run.repository, "change": run.change, "id": run.run_id, "url": run.url},
        "subject": {"base": report.base, "head": report.head, "checked": tree.commit if tree is not None else "",
                    "tree": tree.relation if tree is not None else "unknown",
                    "differs": list(tree.differs) if tree is not None else []},
        "result": {"boundaries": outcome, "execution": execution, "reasons": list(reasons)},
        "counts": {"changed-files": len(report.changed), "untouched-dependents": untouched_count(report),
                   "introduced-findings": len(report.introduced), "not-checked-parts": len(report.not_checked),
                   "tests-to-run": len(tests_to_run(report))},
        "files": [{"path": path, "sha256": digest} for path, digest in report.hashes],
        "tests": {
            "evidence": tests_summary(report),
            "to-run": [{"test": test.shown, "status": recorded[test.shown].status if test.shown in recorded else "none",
                        "cases": recorded[test.shown].cases if test.shown in recorded else 0,
                        "records": [{"where": case.where, "name": case.name, "status": case.status}
                                    for case in (recorded[test.shown].records if test.shown in recorded else ())]}
                       for test in tests_to_run(report)],
            "reports": [{"path": read.path, "sha256": read.sha256, "cases": read.cases} for read in report.test_reports],
        },
        "documents": [{"name": name, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()} for name, text in sorted(documents.items())],
    }
