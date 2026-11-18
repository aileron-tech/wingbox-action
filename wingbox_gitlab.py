#!/usr/bin/env python3
"""Wingbox pull request check in GitLab CI: the same report for a merge request or a push.

Runs in a job of gitlab/wingbox.gitlab-ci.yml, on a checkout with its history (GIT_DEPTH: "0"). It
chooses the two commits to compare from the variables GitLab gives the job, builds the report with
the functions of wingbox_pr (the same facts, comment, review plan and strict result as on GitHub),
and then:

- prints the comment into the job log;
- writes wingbox-out/wingbox-report.md, wingbox-out/wingbox-plan.sexp and wingbox-out/wingbox.env
  (the result and the counts as WINGBOX_ variables for later jobs) for the job's artifacts;
- in a merge request pipeline, when the variable WINGBOX_GITLAB_TOKEN holds a token that may write
  notes (a project access token with the `api` scope: the job token cannot), writes the comment as
  one note on the merge request, found again by its marker and updated on each push.

Standard library only; the only network use is the GitLab API of the instance the job runs on
(CI_API_V4_URL), and only for the note.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))   # the modules next to this file, however it is loaded

import wingbox_pr as pr  # noqa: E402
from wingbox_facts import MARKER  # noqa: E402

ZERO_COMMIT = "0" * 40
OUT_FOLDER = "wingbox-out"
TOKEN_VARIABLE = "WINGBOX_GITLAB_TOKEN"

# (method, address, body) -> (the answer as JSON, the number of the next page of a list or "")
Call = Callable[..., tuple[Any, str]]


@dataclass(frozen=True)
class Compare:
    """The two commits the report compares."""
    base: str
    head: str


@dataclass(frozen=True)
class Skip:
    """A pipeline with nothing to compare, and why."""
    reason: str


def commits(env: Mapping[str, str]) -> Union[Compare, Skip]:
    """The commits to compare in this pipeline: given ones first, then the merge request's diff base, then
    the commit before a push."""
    head = env.get("WINGBOX_HEAD") or env.get("CI_COMMIT_SHA", "")
    if env.get("WINGBOX_BASE"):
        return Compare(env["WINGBOX_BASE"], head or "HEAD")
    if not head:
        return Skip("no commit to check: CI_COMMIT_SHA is not set (run this in a GitLab CI job, or set WINGBOX_BASE and WINGBOX_HEAD)")
    merge_base = env.get("CI_MERGE_REQUEST_DIFF_BASE_SHA", "")
    if merge_base:
        return Compare(merge_base, head)
    before = env.get("CI_COMMIT_BEFORE_SHA", "")
    if before and before != ZERO_COMMIT:
        return Compare(before, head)
    if before == ZERO_COMMIT:
        return Skip("nothing to compare: the first push of a branch has no commit before it (the merge request pipeline checks it)")
    return Skip("no base commit: not a merge request or push pipeline (set WINGBOX_BASE to compare with a commit of your choice)")


def post_note(body: str, api: str, project: str, merge_request: str, call: Call, create: bool = True) -> str:
    """Write the report as the one Wingbox note of the merge request: update the note that holds the
    marker, else create one when there is something to say."""
    notes = f"{api}/projects/{urllib.parse.quote(project, safe='')}/merge_requests/{merge_request}/notes"
    page = "1"
    while page:
        listed, page = call("GET", f"{notes}?per_page=100&page={page}")
        for note in listed:
            if MARKER in (note.get("body") or ""):
                call("PUT", f"{notes}/{note['id']}", {"body": body})
                return f"updated note {note['id']}"
    if not create:
        return "no note: no source module changed"
    created, _ = call("POST", notes, {"body": body})
    return f"created note {created.get('id')}"


def api_call(token: str) -> Call:
    """A call of the GitLab API with the token, answering (JSON, next page)."""
    def call(method: str, url: str, body: Optional[dict[str, Any]] = None) -> tuple[Any, str]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method, headers={
            "PRIVATE-TOKEN": token, "Content-Type": "application/json", "User-Agent": "wingbox-action"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null"), response.headers.get("X-Next-Page", "") or ""
    return call


def in_gitlab_words(text: str) -> str:
    """The report as GitLab's users read it: a merge request, where the report built for GitHub says a pull
    request. The marker (written with hyphens) is not touched."""
    return text.replace("pull request", "merge request").replace("Pull request", "Merge request")


def dotenv(values: Mapping[str, str]) -> str:
    """The outputs as lines of a dotenv file: WINGBOX_RESULT=PASS; a value with a space is quoted and kept
    to one line."""
    lines = []
    for name, value in values.items():
        text = " ".join(str(value).split())
        key = "WINGBOX_" + name.upper().replace("-", "_")
        lines.append(f'{key}="{text}"' if (" " in text or not text) else f"{key}={text}")
    return "".join(line + "\n" for line in lines)


def main() -> int:
    env = os.environ
    root = env.get("WINGBOX_ROOT") or os.getcwd()
    fail_on = env.get("WINGBOX_FAIL_ON", "none")
    if fail_on not in pr.FAIL_ON:
        print(f"ERROR: WINGBOX_FAIL_ON must be one of {', '.join(pr.FAIL_ON)}, not {fail_on!r}")
        return 2
    chosen = commits(env)
    out = Path(env.get("CI_PROJECT_DIR") or root) / OUT_FOLDER
    out.mkdir(parents=True, exist_ok=True)
    if isinstance(chosen, Skip):
        # The job's dotenv report needs its file also when there is nothing to compare.
        (out / "wingbox.env").write_text(dotenv({"result": "NOT_APPLICABLE", "execution": "ok", "reasons": chosen.reason}),
                                         encoding="utf-8", newline="\n")
        print(f"Wingbox: {chosen.reason}")
        return 0
    if pr._run(["git", "cat-file", "-e", f"{chosen.base}^{{commit}}"], root).returncode != 0:
        print(f'ERROR: the base commit {chosen.base[:12]} is not in the checkout: set GIT_DEPTH: "0" for this job')
        return 2
    patterns = [p.strip() for p in env.get("WINGBOX_PATHS", "").splitlines() if p.strip()]
    report = pr.collect(root, chosen.base, chosen.head, int(env.get("WINGBOX_HOPS", "2")), patterns, env.get("WINGBOX_VERSION", "?"))
    comment = in_gitlab_words(pr.cut(pr.build_comment(report), pr.COMMENT_LIMIT))
    outcome, execution, reasons = pr.result(report)
    (out / "wingbox-report.md").write_text(in_gitlab_words(pr.cut(pr.build_comment(report, full=True), pr.SUMMARY_LIMIT)),
                                           encoding="utf-8", newline="\n")
    (out / "wingbox-plan.sexp").write_text(pr.build_plan(report) + "\n", encoding="utf-8", newline="\n")
    (out / "wingbox.env").write_text(dotenv({
        "result": outcome, "execution": execution, "reasons": "; ".join(reasons),
        "untouched-dependents": str(pr.untouched_count(report)), "introduced-findings": str(len(report.introduced)),
        "not-checked-parts": str(len(report.not_checked)), "tests-to-run": str(len(pr.tests_to_run(report)))}),
        encoding="utf-8", newline="\n")
    print(comment)
    print(f"Result of the boundary check: {outcome} (execution {execution})" + (f": {'; '.join(reasons)}" if reasons else ""))
    merge_request = env.get("CI_MERGE_REQUEST_IID", "")
    if merge_request and env.get("WINGBOX_COMMENT", "true") == "true":
        token = env.get(TOKEN_VARIABLE, "")
        if not token:
            print(f"Wingbox: no note written: the variable {TOKEN_VARIABLE} is not set; the report is in the job log and in {OUT_FOLDER}/")
        else:
            try:
                print(post_note(comment, env["CI_API_V4_URL"], env["CI_PROJECT_ID"], merge_request, api_call(token), pr.needs_comment(report)))
            except (urllib.error.URLError, KeyError, ValueError) as error:
                print(f"WARNING: could not write the merge request note ({error}); the report is in the job log and in {OUT_FOLDER}/")
    return pr.exit_status(report, fail_on)


if __name__ == "__main__":
    sys.exit(main())
