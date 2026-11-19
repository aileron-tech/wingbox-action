"""The verdicts of a run, each on its own question, and whether the record is fit to count as evidence.

One word about one question hides the others. A run answers five: does the change introduce a boundary
finding (the result, as before); does the head hold violations that were there before; what does the
baseline accept, and is an entry past its end date; what do the test reports say; was everything analysed.
They are kept apart, in a fixed order, and `eligible` is true only when none of them is failed, unknown,
incomplete or expired. Pure functions; the result of the boundary check and the word for the test evidence
are given by the caller, which owns them.
"""
from __future__ import annotations

import wingbox_coverage as coverage
from wingbox_facts import BLOCKING_HEADS, Finding, Report, tree_reasons

NONE, PRESENT, ACCEPTED, EXPIRED, UNKNOWN = "NONE", "PRESENT", "ACCEPTED", "EXPIRED", "UNKNOWN"
TESTS_COMPLETE = "complete"
ENTRIES_LIMIT = 100   # accepted breaks listed in the record; their number is always given


def _blocking(findings: tuple[Finding, ...]) -> list[Finding]:
    return [finding for finding in findings if finding.head in BLOCKING_HEADS]


def _not_all_checked(report: Report) -> bool:
    """Whether something the boundary rules apply to was not looked at: "none found" is then not "none"."""
    return bool(report.did_not_run or report.tool_errors or report.skipped_languages or report.not_checked or tree_reasons(report))


def existing(report: Report) -> tuple[str, int, str]:
    """(verdict, number, the first one) of the violations the head holds that the base has too. PRESENT: there
    are some. NONE: there is none and everything was checked. UNKNOWN: none was found, but not everything was
    checked."""
    found = _blocking(report.existing)
    if found:
        return PRESENT, len(found), found[0].text
    return (UNKNOWN if _not_all_checked(report) else NONE), 0, ""


def baseline(report: Report) -> tuple[str, int, int, int]:
    """(verdict, accepted breaks, entries past their end date, accepted breaks whose entry states no end date).
    EXPIRED: an entry is past its end date, its break is a violation again. ACCEPTED: the baseline accepts
    breaks, all within their terms. NONE: it accepts none. UNKNOWN: the boundaries could not be read."""
    open_ended = sum(1 for finding in report.accepted if finding.accepted is not None and not finding.accepted.expires)
    numbers = (len(report.accepted), len(report.expired), open_ended)
    if report.expired:
        return (EXPIRED, *numbers)
    if report.accepted:
        return (ACCEPTED, *numbers)
    return (UNKNOWN if report.tool_errors or report.did_not_run else NONE, *numbers)


def eligible(report: Report, outcome: str, tests: str) -> tuple[bool, tuple[str, ...]]:
    """(whether the record is fit to count as evidence, why not). outcome: the result of the boundary check
    (PASS, FAIL, UNKNOWN, NOT_APPLICABLE). tests: the test evidence in one word; only `complete` counts."""
    reasons = []
    if outcome not in ("PASS", "NOT_APPLICABLE"):
        reasons.append(f"boundaries-introduced: {outcome}")
    verdict, number, _first = existing(report)
    if verdict == PRESENT:
        reasons.append(f"boundaries-existing: {number} violation(s)")
    elif verdict == UNKNOWN:
        reasons.append(f"boundaries-existing: {UNKNOWN}")
    accepted, _accepted, past, _open = baseline(report)
    if accepted == EXPIRED:
        reasons.append(f"baseline: {past} past the end date")
    elif accepted == UNKNOWN:
        reasons.append(f"baseline: {UNKNOWN}")
    if tests != TESTS_COMPLETE:
        reasons.append(f"tests: {tests}")
    if not coverage.complete(report):
        reasons.append("coverage: incomplete")
    return not reasons, tuple(reasons)


def record(report: Report, result: tuple[str, str, tuple[str, ...]], tests: str) -> dict[str, object]:
    """The verdict objects of the evidence record: `boundaries` (introduced and existing), `baseline`,
    `eligible` and `eligible-reasons`. The test evidence and the coverage have their own objects there."""
    outcome, _execution, reasons = result
    verdict, number, first = existing(report)
    accepted, count, past, open_ended = baseline(report)
    fit, why = eligible(report, outcome, tests)

    def entry(finding: Finding) -> dict[str, object]:
        terms = finding.accepted
        return {"finding": finding.head, "text": finding.text, "file": terms.file if terms else "", "owner": terms.owner if terms else "",
                "reason": terms.reason if terms else "", "expires": terms.expires if terms else ""}
    return {
        "boundaries": {
            "introduced": {"verdict": outcome, "findings": len(report.introduced), "violations": len(_blocking(report.introduced)),
                           "reasons": list(reasons)},
            "existing": {"verdict": verdict, "violations": number, "first": first},
        },
        "baseline": {"verdict": accepted, "accepted": count, "expired": past, "without-end-date": open_ended,
                     "entries": [entry(finding) for finding in report.accepted[:ENTRIES_LIMIT]],
                     "past": [entry(finding) for finding in report.expired[:ENTRIES_LIMIT]]},
        "eligible": fit,
        "eligible-reasons": list(why),
    }


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def holds_violation(report: Report) -> int:
    """How many violations the head holds, introduced by the change or there before it: what `fail-on: existing` fails on."""
    return len(_blocking(report.introduced)) + len(_blocking(report.existing))


def lines(report: Report, result: tuple[str, str, tuple[str, ...]], tests: str) -> list[str]:
    """The verdicts as lines of the report, one per verdict, always these six in this order."""
    outcome, _execution, reasons = result
    verdict, number, first = existing(report)
    accepted, count, past, open_ended = baseline(report)
    fit, why = eligible(report, outcome, tests)
    introduced = f"**{outcome}**" + (f" ({'; '.join(reasons)})" if reasons else "")
    before = (f"**{_count(number, 'violation')}** on the head that the base has too; the first: {first}" if verdict == PRESENT
              else "**none**" if verdict == NONE else f"**{UNKNOWN}**: no violation was found, but not everything was checked")
    if accepted == UNKNOWN:
        kept = f"**{UNKNOWN}**: the boundaries could not be read"
    else:
        notes = ([f"{past} past {'its' if past == 1 else 'their'} end date"] if past else []) + (
            [f"{open_ended} without an end date"] if open_ended else [])
        kept = f"**{_count(count, 'accepted break')}**" if count else "**no accepted break**"
        kept += f"; {', '.join(notes)}" if notes else ""
    gaps = coverage.reasons(report)
    covered = "**complete**" if not gaps else f"**incomplete** ({'; '.join(gaps)})"
    return [
        f"- Boundaries, introduced by this change: {introduced}",
        f"- Boundaries, there before this change: {before}",
        f"- Baseline: {kept}",
        f"- Tests: **{tests}**",
        f"- Coverage: {covered}",
        f"- Eligible as evidence: {'**yes**' if fit else '**no** (' + '; '.join(why) + ')'}",
    ]
