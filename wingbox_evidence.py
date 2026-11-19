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
from typing import Optional

import wingbox_tests
from wingbox_facts import Report
from wingbox_report import required_evidence, result, test_evidence, tests_summary, tests_to_run, untouched_count  # noqa: F401

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
    attempt: str = ""    # the attempt of the run (GitHub: a re-run has the same run and the next attempt); "" when the system has none
    job: str = ""        # the job of the run that ran the check; "" when not known


def _test_record(item: Optional[wingbox_tests.Evidence]) -> dict[str, object]:
    """The status, the number of cases and the cases of one test, as the record writes them; `none` when no
    test report was given."""
    if item is None:
        return {"status": "none", "cases": 0, "records": []}
    return {"status": item.status, "cases": item.cases,
            "records": [{"where": case.where, "name": case.name, "status": case.status} for case in item.records]}


def build_evidence(report: Report, run: Run, documents: dict[str, str]) -> dict[str, object]:
    """The record of a run. documents: the text of each file written beside it, by file name."""
    outcome, execution, reasons = result(report)
    found = test_evidence(report)
    recorded = {item.test: item for item in found or ()}
    needed = required_evidence(report)
    manifest = report.tests_manifest
    origin = report.test_origin or wingbox_tests.Origin()
    tree = report.tree
    return {
        "schema": SCHEMA,
        "generated": run.generated,
        "tool": {"name": "wingbox-action", "ref": run.action_ref, "wingbox": report.version},
        "run": {"provider": run.provider, "repository": run.repository, "change": run.change, "id": run.run_id, "url": run.url,
                "attempt": run.attempt, "job": run.job},
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
            "step-outcome": report.test_step_outcome,
            "to-run": [{"test": test.shown, **_test_record(recorded.get(test.shown))} for test in tests_to_run(report)],
            "required": [{"group": wanted.group, "test": wanted.test, **_test_record(None if needed is None else needed[index])}
                         for index, wanted in enumerate(report.required_tests)],
            "manifest": {"path": manifest.path if manifest else "", "source": manifest.source if manifest else "none",
                         "commit": manifest.commit if manifest else "", "sha256": manifest.sha256 if manifest else "",
                         "errors": list(manifest.errors) if manifest else []},
            "provenance": {"commit": origin.commit, "started": origin.started, "run": origin.run_id, "attempt": origin.attempt,
                           "command": origin.command, "sidecar-required": origin.require_sidecar},
            "reports": [{"path": read.path, "sha256": read.sha256, "cases": read.cases, "status": "refused" if read.refused else "accepted",
                         "reasons": list(read.refused), "timestamp": read.timestamp, "modified": read.modified, "tracked": read.tracked,
                         "commits": list(read.commits), "sidecar": read.sidecar, "sidecar-command": read.sidecar_command}
                        for read in report.test_reports],
        },
        "documents": [{"name": name, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()} for name, text in sorted(documents.items())],
    }
