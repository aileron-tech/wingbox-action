"""The files a change is checked by, and what a change of them means for the check.

The result of the check rests on files of the repository itself: the breaks a baseline accepts, the rules of
`.wingbox/`, the rule files a project declares, the workflow that runs the check, the list of required tests.
A change that edits one of them would be judged by its own edit. This module names these files, reads a
change of them, and says what the record holds about it; the findings themselves are judged with the files
of the base (wingbox_facts.judged, with the build wingbox_pr makes for it). Pure functions only.
"""
from __future__ import annotations

from typing import Mapping, Optional

from wingbox_facts import Acceptance, BaselineEntry, PolicyChange, Report, parse_line
from wingbox_tests import MANIFEST_PATH

BASELINE = ".wingbox/baseline.sexp"
DECISIONS = ".wingbox/decisions.sexp"    # a log every build adds to; a policy file only for the layers it sets
# The policy files with a fixed place, and what each is.
FIXED = {BASELINE: "baseline", ".wingbox/config.yaml": "rules", ".wingbox/learned.sexp": "rules", MANIFEST_PATH: "required-tests"}
JUDGING = frozenset({"baseline", "rules", "declared-rules"})   # the kinds that decide what `wingbox boundaries` reports
MODES = ("fail", "report")
_WORKFLOW_FOLDER = ".github/workflows/"
_GITLAB_FILE = ".gitlab-ci.yml"
_LEARNED = ("(learned-layer ", "(learned-removed ")


def default_mode(fail_on: str) -> str:
    """`policy-change` when the job does not set it: a job that fails on everything fails on this too."""
    return "fail" if fail_on == "strict" else "report"


def declared_files(graph: list[str]) -> frozenset[str]:
    """The rule files a graph names, from its architecture facts: the file a part's declared rules were read
    from, a rule file that could not be read, and a project's own list of known exceptions."""
    found: set[str] = set()
    for line in graph:
        parsed = parse_line(line)
        if parsed is None:
            continue
        head, args, keys = parsed
        if head == "arch-part" and keys.get("declared"):
            found.add(keys["declared"])
        elif head == "arch-declared-unread" and len(args) >= 2:
            found.add(args[1])
        elif head in ("arch-known", "capability-known") and len(args) >= 4:
            found.add(args[3])
    return frozenset(found - set(FIXED))


def named(env: Mapping[str, str]) -> dict[str, str]:
    """The policy files the settings of the run name, each with its kind: the file of the workflow this run is
    part of, as the CI system names it (a workflow of this repository in GitHub Actions, the pipeline file in
    GitLab CI), and the manifest of required tests when the job gives its path (WINGBOX_REQUIRED_TESTS)."""
    found: dict[str, str] = {}
    reference, repository = env.get("GITHUB_WORKFLOW_REF", ""), env.get("GITHUB_REPOSITORY", "")
    if reference and repository and reference.startswith(f"{repository}/"):
        found[reference[len(repository) + 1:].split("@", 1)[0]] = "workflow"
    elif env.get("CI_JOB_ID"):
        found[env.get("CI_CONFIG_PATH") or _GITLAB_FILE] = "workflow"
    manifest = env.get("WINGBOX_REQUIRED_TESTS", "").strip().replace("\\", "/")
    if manifest:
        found[manifest[2:] if manifest.startswith("./") else manifest] = "required-tests"
    return found


def _may_be_workflow(path: str) -> bool:
    return path == _GITLAB_FILE or (path.startswith(_WORKFLOW_FOLDER) and path.endswith((".yml", ".yaml")))


def candidates(names: list[str], declared: frozenset[str], by_setting: Mapping[str, str]) -> list[str]:
    """The changed files that may be policy files: the caller reads both sides of each for `policy_changes`.
    by_setting: the files the settings of the run name (`named`)."""
    return [name for name in names if name in FIXED or name == DECISIONS or name in declared or name in by_setting or _may_be_workflow(name)]


def _learned(text: Optional[str]) -> frozenset[str]:
    """The lines of the decisions log that set the layer of a folder."""
    return frozenset(line.strip() for line in (text or "").splitlines() if line.startswith(_LEARNED))


def _kind(path: str, base: Optional[str], head: Optional[str], declared: frozenset[str], by_setting: Mapping[str, str]) -> Optional[str]:
    if path in FIXED:
        return FIXED[path]
    if path in by_setting:
        return by_setting[path]
    if path == DECISIONS:
        return "rules" if _learned(base) != _learned(head) else None
    if path in declared:
        return "declared-rules"
    if _may_be_workflow(path) and any("wingbox" in (text or "").lower() for text in (base, head)):
        return "workflow"
    return None


def policy_changes(texts: Mapping[str, tuple[Optional[str], Optional[str]]], declared: frozenset[str],
                   by_setting: Mapping[str, str]) -> tuple[PolicyChange, ...]:
    """The policy files a change edits, by path. texts: per candidate its text on the base and on the head,
    None where the file is absent. A file with the same text on both sides is not changed."""
    found = []
    for path, (base, head) in sorted(texts.items()):
        kind = _kind(path, base, head, declared, by_setting)
        if kind is not None and base != head:
            found.append(PolicyChange(path, kind, "added" if base is None else "removed" if head is None else "changed"))
    return tuple(found)


def baseline_entries(text: Optional[str]) -> tuple[BaselineEntry, ...]:
    """The entries of a baseline file, each with the terms it states."""
    found = []
    for line in (text or "").splitlines():
        parsed = parse_line(line)
        if parsed is None or parsed[0] not in ("baseline", "baseline-capability") or len(parsed[1]) < 3:
            continue
        head, args, keys = parsed
        terms = Acceptance(BASELINE, keys.get("owner", ""), keys.get("reason", ""), keys.get("expires", ""))
        found.append(BaselineEntry(head, args[0], args[1], args[2], keys.get("rule", ""), terms))
    return tuple(found)


def new_entries(base: Optional[str], head: Optional[str]) -> tuple[BaselineEntry, ...]:
    """The entries of the head's baseline that the base's does not hold as they are: new ones and changed ones."""
    before = set(baseline_entries(base))
    return tuple(entry for entry in baseline_entries(head) if entry not in before)


def policy_status(report: Report) -> int:
    """1 when a change of a policy file fails the job (`policy-change: fail`) and the change edits one; else 0."""
    return 1 if report.policy and report.policy_mode == "fail" else 0


def record(report: Report) -> dict[str, object]:
    """The `policy` object of the evidence record."""
    def accepted(finding_head: str, text: str, terms: Acceptance) -> dict[str, object]:
        return {"finding": finding_head, "text": text, "by": terms.file or "rules", "owner": terms.owner, "reason": terms.reason,
                "expires": terms.expires, "accepts": terms.complete()}
    own = [finding for finding in (*report.introduced, *report.self_accepted) if finding.accepted is not None]
    return {
        "mode": report.policy_mode,
        "changed": [{"path": change.path, "kind": change.kind, "change": change.how} for change in report.policy],
        "judged-with": report.policy_base,
        "entries": [{"entry": entry.head, "part": entry.part, "source": entry.source, "target": entry.target, "rule": entry.rule,
                     "owner": entry.terms.owner, "reason": entry.terms.reason, "expires": entry.terms.expires,
                     "complete": entry.terms.complete()} for entry in report.baseline_entries],
        "self-accepted": [accepted(finding.head, finding.text, finding.accepted) for finding in own if finding.accepted is not None],
    }
