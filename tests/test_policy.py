"""A change that edits what it is checked by: the policy files, the findings judged with the policy of the base."""
import dataclasses
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import layers_fixture as layers  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_facts as facts  # noqa: E402
import wingbox_policy as policy  # noqa: E402
import wingbox_pr as pr  # noqa: E402
from test_evidence_record import RUN, problems  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

VIOLATION = ('(violation src/models/models0 src/routes/routes1 :src-layer domain :tgt-layer api :via imports :lang ts :part . '
             ':style layered :rule "domain -> api" :src-file /w/src/models/models0.ts :tgt-file /w/src/routes/routes1.ts "domain imports API layer")')
KNOWN = VIOLATION.replace("(violation ", "(violation-known ").replace(' :src-file', ' :known .wingbox/baseline.sexp :src-file')
KNOWN_WITH_TERMS = KNOWN.replace(" :src-file", ' :owner "lead" :reason "moves with the next release" :expires 2099-01-01 :src-file')
CAPABILITY = ('(capability-violation "src/models/models1.ts" :capability sql :layer domain :forbidden (domain) :line 3 '
              ':evidence "SELECT" :part "." :style layered)')
CAPABILITY_KNOWN = CAPABILITY.replace("(capability-violation ", "(capability-violation-known ").replace(
    ' :part "."', ' :part "." :known ".wingbox/baseline.sexp" :owner "lead"')
OK = "(boundaries-ok :checked 14)"
KEY = ("violation", "src/models/models0", "src/routes/routes1", "domain -> api")
GRAPH = [
    '(arch-part "." :style layered :state configured :origin config :level container :kind "npm package" :files 14)',
    '(arch-part "platform/backend" :style declared :state declared :origin declared :level container :kind "npm package" :files 300 '
    ':declared "platform/backend/layers.mjs")',
    '(arch-declared-unread "web" ".dependency-cruiser.js" :reason "a computed value")',
    '(arch-known "platform/backend" pl0 pl7 "platform/backend/layers.known-upward-imports.json")',
    '(arch-known "." re0 re7 ".wingbox/baseline.sexp" :owner "lead")',
    '(capability-known "." mo1 sql ".wingbox/baseline.sexp")',
    "(node mo1 :lang ts)",
]


def changed(*names: str) -> tuple:
    """The policy changes of a change that edits each named file (present on both sides, with other text)."""
    return policy.policy_changes({name: ("old\n", "new\n") for name in names}, frozenset(), {})


class PolicyFilesTest(unittest.TestCase):
    def test_the_check_knows_the_files_it_is_checked_by(self) -> None:
        """/* REQ-ACTION-140: the policy files are `.wingbox/baseline.sexp` (accepted breaks), `.wingbox/config.yaml` and `.wingbox/learned.sexp` (rules), `.wingbox/required-tests.yaml`, the rule files the graph of the base or of the head names (a declared rule file read or not read, the project's own list of known exceptions), and the workflow that runs the check; each change of one is added, changed or removed; a source file, a document and another file of `.wingbox/` are not policy files */"""
        declared = policy.declared_files(GRAPH)
        self.assertEqual({"platform/backend/layers.mjs", ".dependency-cruiser.js", "platform/backend/layers.known-upward-imports.json"},
                         set(declared))
        names = [".wingbox/baseline.sexp", ".wingbox/config.yaml", ".wingbox/learned.sexp", ".wingbox/required-tests.yaml",
                 "platform/backend/layers.mjs", ".dependency-cruiser.js", ".github/workflows/check.yml", "src/a.ts", "README.md",
                 ".wingbox/REPORT.md", "docs/layers.mjs"]
        workflows = {".github/workflows/check.yml": "workflow"}
        wanted = policy.candidates(names, declared, workflows)
        self.assertEqual(names[:7], wanted)
        texts = {name: ("old\n", "new\n") for name in wanted}
        texts[".wingbox/baseline.sexp"] = (None, "new\n")
        texts[".wingbox/learned.sexp"] = ("old\n", None)
        found = policy.policy_changes(texts, declared, workflows)
        self.assertEqual([(".dependency-cruiser.js", "declared-rules", "changed"), (".github/workflows/check.yml", "workflow", "changed"),
                          (".wingbox/baseline.sexp", "baseline", "added"), (".wingbox/config.yaml", "rules", "changed"),
                          (".wingbox/learned.sexp", "rules", "removed"), (".wingbox/required-tests.yaml", "required-tests", "changed"),
                          ("platform/backend/layers.mjs", "declared-rules", "changed")],
                         [(change.path, change.kind, change.how) for change in found])
        self.assertEqual((), policy.policy_changes({".wingbox/config.yaml": ("same\n", "same\n")}, frozenset(), {}))
        self.assertEqual(frozenset(), policy.declared_files([]))

    def test_a_workflow_is_a_policy_file_when_it_runs_the_check(self) -> None:
        """/* REQ-ACTION-140: the workflow that runs the check is the one the CI system names for the run (GITHUB_WORKFLOW_REF of this repository, CI_CONFIG_PATH in GitLab CI), and any changed workflow file whose text on the base or on the head names wingbox; another workflow is not a policy file, and a workflow of another repository is not a file of this one; a manifest of required tests whose path the job gives (WINGBOX_REQUIRED_TESTS) is a policy file too */"""
        self.assertEqual({".github/workflows/check.yml": "workflow"}, policy.named(
            {"GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/check.yml@refs/pull/7/merge", "GITHUB_REPOSITORY": "owner/repo"}))
        self.assertEqual({}, policy.named(
            {"GITHUB_WORKFLOW_REF": "owner/policies/.github/workflows/required.yml@refs/heads/main", "GITHUB_REPOSITORY": "owner/repo"}))
        self.assertEqual({".gitlab-ci.yml": "workflow"}, policy.named({"CI_JOB_ID": "5"}))
        self.assertEqual({"ci/pipeline.yml": "workflow", "quality/required.yaml": "required-tests"},
                         policy.named({"CI_JOB_ID": "5", "CI_CONFIG_PATH": "ci/pipeline.yml", "WINGBOX_REQUIRED_TESTS": "./quality/required.yaml"}))
        self.assertEqual({}, policy.named({}))
        self.assertEqual([facts.PolicyChange("quality/required.yaml", "required-tests", "changed")], list(policy.policy_changes(
            {"quality/required.yaml": ("a\n", "b\n")}, frozenset(), policy.named({"WINGBOX_REQUIRED_TESTS": "quality/required.yaml"}))))
        names = [".github/workflows/lint.yml", ".github/workflows/review.yaml", ".gitlab-ci.yml", "docs/workflows/x.yml"]
        self.assertEqual(names[:3], policy.candidates(names, frozenset(), {}))
        texts = {".github/workflows/lint.yml": ("run: ruff\n", "run: ruff check\n"),
                 ".github/workflows/review.yaml": ("uses: aileron-tech/wingbox-action@v1\n", None),
                 ".gitlab-ci.yml": ("stages: [test]\n", "include:\n  - remote: https://example.invalid/wingbox.gitlab-ci.yml\n")}
        self.assertEqual([(".github/workflows/review.yaml", "workflow", "removed"), (".gitlab-ci.yml", "workflow", "changed")],
                         [(change.path, change.kind, change.how) for change in policy.policy_changes(texts, frozenset(), {})])

    def test_the_decisions_log_is_a_policy_file_only_for_the_layers_it_sets(self) -> None:
        """/* REQ-ACTION-140: `.wingbox/decisions.sexp` gains a line at every build; it counts as a change of the rules only when the layers it sets (`learned-layer`, `learned-removed`) differ between the base and the head */"""
        log = "(build-run :graph aa :at 2026-01-01)\n(learned-layer \".\" \"src/tools\" shared :from x :at 2026-01-01)\n"
        more_runs = log + "(build-run :graph bb :at 2026-01-02)\n"
        self.assertEqual((), policy.policy_changes({policy.DECISIONS: (log, more_runs)}, frozenset(), {}))
        other_layer = log.replace(" shared ", " domain ")
        self.assertEqual([facts.PolicyChange(policy.DECISIONS, "rules", "changed")],
                         list(policy.policy_changes({policy.DECISIONS: (log, other_layer)}, frozenset(), {})))
        self.assertEqual((), policy.policy_changes({policy.DECISIONS: (None, "(build-run :graph aa :at 2026-01-01)\n")}, frozenset(), {}))


class BaselineEntriesTest(unittest.TestCase):
    def test_a_new_or_changed_entry_is_read_with_its_terms(self) -> None:
        """/* REQ-ACTION-141: the entries of a baseline file are read with the owner, the reason and the end date each states; an entry of the head that the base does not hold word for word is new or changed; it is complete only with all three terms, and says which ones it lacks */"""
        old = f"; comment\n{layers.ENTRY})\n"
        capability = '(baseline-capability "." src/models/models1 sql :line 3 :from align :owner "lead")'
        new = f"{old}{layers.ENTRY.replace('models0', 'models2')}{layers.TERMS})\n{capability}\n"
        entries = policy.baseline_entries(new)
        self.assertEqual(3, len(entries))
        self.assertEqual(facts.BaselineEntry("baseline", ".", "src/models/models0", "src/routes/routes1", "domain -> api",
                                             facts.Acceptance(policy.BASELINE)), entries[0])
        added = policy.new_entries(old, new)
        self.assertEqual(["src/models/models2", "src/models/models1"], [entry.source for entry in added])
        self.assertEqual(facts.Acceptance(policy.BASELINE, "lead", "moves with the next release", "2099-01-01"), added[0].terms)
        self.assertEqual((True, ()), (added[0].terms.complete(), added[0].terms.missing()))
        self.assertEqual((False, ("reason", "expires")), (added[1].terms.complete(), added[1].terms.missing()))
        self.assertEqual(("baseline-capability", "sql", ""), (added[1].head, added[1].target, added[1].rule))
        self.assertEqual(3, len(policy.new_entries(None, new)))
        self.assertEqual((), policy.new_entries(new, old))
        with_terms = f"{layers.ENTRY}{layers.TERMS})\n"
        self.assertEqual(1, len(policy.new_entries(old, with_terms)))
        self.assertEqual(("owner", "reason", "expires"), entries[0].terms.missing())


class JudgedByTheBaseTest(unittest.TestCase):
    def test_a_break_only_the_change_accepts_is_introduced(self) -> None:
        """/* REQ-ACTION-142: a finding that the files of the change give under the policy of the base, and that the head does not report because its own baseline lists it, is introduced, with the entry that would accept it; an entry without an owner, a reason and an end date accepts nothing, also when an older Wingbox prints no terms */"""
        base, _ = facts.findings([OK])
        head, _ = facts.findings([KNOWN, OK])
        with_base_policy, _ = facts.findings([VIOLATION])
        self.assertEqual({KEY: facts.Acceptance(".wingbox/baseline.sexp")}, facts.known([KNOWN, OK]))
        self.assertEqual(([], 0), facts.introduced(base, head))
        new, own, resolved = facts.judged(base, head, with_base_policy, facts.known([KNOWN, OK]))
        self.assertEqual(([KEY], [], 0), ([finding.key for finding in new], own, resolved))
        self.assertEqual(facts.Acceptance(".wingbox/baseline.sexp"), new[0].accepted)
        self.assertEqual(("violation", layers.VIOLATION), (new[0].head, new[0].text))
        report_ = report(introduced=tuple(new), not_checked=())
        self.assertEqual(("FAIL", "ok", ("introduced-violations: 1", "accepted-only-by-this-change: 1")), pr.result(report_))
        self.assertEqual(1, pr.exit_status(report_, "strict"))
        self.assertEqual(1, pr.exit_status(report_, "violations"))

    def test_a_complete_entry_accepts_the_break_and_the_rules_of_the_change_accept_nothing(self) -> None:
        """/* REQ-ACTION-142: a new entry with an owner, a reason and an end date accepts its break: the finding is listed as accepted by the change itself and is no violation; a finding that only the rules as the change edits them no longer report is introduced; a finding of the head under its own policy stays one; a finding of the base counts as gone only when neither policy reports it */"""
        base, _ = facts.findings([OK])
        head, _ = facts.findings([KNOWN_WITH_TERMS, OK])
        with_base_policy, _ = facts.findings([VIOLATION])
        terms = facts.Acceptance(".wingbox/baseline.sexp", "lead", "moves with the next release", "2099-01-01")
        self.assertEqual({KEY: terms}, facts.known([KNOWN_WITH_TERMS]))
        new, own, resolved = facts.judged(base, head, with_base_policy, facts.known([KNOWN_WITH_TERMS]))
        self.assertEqual(([], [KEY], 0), (new, [finding.key for finding in own], resolved))
        self.assertEqual(terms, own[0].accepted)
        clean = report(introduced=(), self_accepted=tuple(own), not_checked=())
        self.assertEqual(("PASS", "ok", ()), pr.result(clean))
        self.assertEqual(0, pr.exit_status(clean, "strict"))
        by_rules, _own, _resolved = facts.judged(base, [], with_base_policy, {})
        self.assertEqual([facts.Acceptance()], [finding.accepted for finding in by_rules])
        self.assertFalse(facts.Acceptance().complete())
        capability, _ = facts.findings([CAPABILITY])
        self.assertEqual({capability[0].key: facts.Acceptance(".wingbox/baseline.sexp", "lead")}, facts.known([CAPABILITY_KNOWN]))
        both, _own, _resolved = facts.judged(base, capability, with_base_policy, {})
        self.assertEqual([None, facts.Acceptance()], [finding.accepted for finding in both])
        self.assertEqual((0, 1), (facts.judged(with_base_policy, [], with_base_policy, {})[2], facts.judged(with_base_policy, [], [], {})[2]))
        self.assertEqual(facts.introduced(base, capability), facts.judged(base, capability, None, {})[::2])


class PolicyChangeModeTest(unittest.TestCase):
    def test_policy_change_fail_fails_the_job_and_report_only_reports(self) -> None:
        """/* REQ-ACTION-143: `policy-change` is `fail` or `report`; without a value it is `fail` with `fail-on: strict` and `report` otherwise; with `fail` a change of a policy file fails the job, with `report` it does not, and a change that edits none never fails for it; the result of the boundary check does not depend on it */"""
        self.assertEqual(("fail", "report"), policy.MODES)
        self.assertEqual(["fail", "report", "report", "report"], [policy.default_mode(value) for value in ("strict", "none", "violations", "not-checked")])
        edits = report(policy=changed(".wingbox/config.yaml"), introduced=(), not_checked=())
        self.assertEqual("report", edits.policy_mode)
        self.assertEqual(0, policy.policy_status(edits))
        strict = dataclasses.replace(edits, policy_mode="fail")
        self.assertEqual(1, policy.policy_status(strict))
        self.assertEqual(0, policy.policy_status(report(policy_mode="fail", introduced=(), not_checked=())))
        self.assertEqual(pr.result(edits), pr.result(strict))
        self.assertEqual(("PASS", 0), (pr.result(strict)[0], pr.exit_status(strict, "strict")))

    def test_a_change_of_policy_files_alone_is_reported(self) -> None:
        """/* REQ-ACTION-143: a change that edits only policy files gets the comment (it is not "nothing to check") and its result is the one of the findings, not NOT_APPLICABLE; the output `policy-changed` holds the number of policy files changed */"""
        only = report(changed=(pr.ChangedFile(".wingbox/config.yaml", None),), introduced=(), not_checked=(),
                      policy=changed(".wingbox/config.yaml", ".wingbox/baseline.sexp"))
        self.assertTrue(pr.needs_comment(only))
        self.assertEqual("PASS", pr.result(only)[0])
        self.assertEqual("2", pr.outputs(only)["policy-changed"])
        self.assertEqual("0", pr.outputs(report())["policy-changed"])
        body = pr.build_comment(only)
        self.assertNotIn("nothing to check", body)
        self.assertIn("### This change edits what it is checked by", body)
        failed = pr.build_comment(report(changed=(pr.ChangedFile(".wingbox/config.yaml", None),), introduced=(), not_checked=(),
                                         policy=changed(".wingbox/config.yaml"), tool_errors=("wingbox usages exited 70",)))
        self.assertIn("### This change edits what it is checked by", failed)


class PolicyTextTest(unittest.TestCase):
    def setUp(self) -> None:
        base, _ = facts.findings([OK])
        with_base_policy, _ = facts.findings([VIOLATION])
        new, _own, _resolved = facts.judged(base, facts.findings([KNOWN])[0], with_base_policy, facts.known([KNOWN]))
        _new, own, _resolved = facts.judged(base, facts.findings([KNOWN_WITH_TERMS])[0], with_base_policy, facts.known([KNOWN_WITH_TERMS]))
        files = policy.policy_changes({".wingbox/baseline.sexp": (None, "x\n"), ".github/workflows/check.yml": ("a\n", "b\n")},
                                      frozenset(), {".github/workflows/check.yml": "workflow"})
        self.entries = policy.baseline_entries(f"{layers.ENTRY})\n")
        self.weak = report(introduced=tuple(new), not_checked=(), policy=files, policy_base="d" * 40, baseline_entries=self.entries)
        self.accepted = report(introduced=(), self_accepted=tuple(own), not_checked=(), policy=files[1:], policy_base="d" * 40,
                               baseline_entries=policy.baseline_entries(f"{layers.ENTRY}{layers.TERMS})\n"))

    def test_the_comment_has_a_section_for_the_policy_change(self) -> None:
        """/* REQ-ACTION-144: the comment and the job summary name each policy file the change edits with what it is and how it changed, under the heading "This change edits what it is checked by", say that the findings are judged with these files as the base has them, name each new or changed baseline entry with its terms or with what it lacks, and each finding the change accepts itself; the finding that only the change accepts says so in the list of new findings; a change that edits no policy file has no such section */"""
        for full in (False, True):
            body = pr.build_comment(self.weak, full=full)
            self.assertIn("## Wingbox: 1 new boundary finding\n", body)
            self.assertIn(f"- violation: {layers.VIOLATION}. Only this change accepts it: `.wingbox/baseline.sexp`, an entry without "
                          "an owner, a reason and an end date.\n", body)
            self.assertIn("### This change edits what it is checked by\n\n"
                          "- `.github/workflows/check.yml` (changed): the workflow that runs this check\n"
                          "- `.wingbox/baseline.sexp` (added): the breaks of the rules that are accepted\n\n"
                          "The findings are judged with these files as the base has them (commit `ddddddd`): what only this change's "
                          "own edit accepts is reported as new. A change of these files needs the review of someone other than its "
                          "author.\n", body)
            self.assertIn("New or changed baseline entries:\n\n"
                          f"- {layers.VIOLATION}: no owner, no reason, no end date; it accepts nothing in this check\n", body)
            self.assertLess(body.index("### New boundary findings"), body.index("### This change edits what it is checked by"))
        body = pr.build_comment(self.accepted)
        self.assertIn(f'- {layers.VIOLATION}: owner lead, reason "moves with the next release", until 2099-01-01\n', body)
        self.assertIn(f"Accepted by this change itself (1):\n\n- violation: {layers.VIOLATION}, accepted in `.wingbox/baseline.sexp` "
                      'by lead until 2099-01-01 ("moves with the next release")\n', body)
        self.assertNotIn("New boundary findings", body)
        rules = facts.Finding("violation", KEY, layers.VIOLATION, facts.Acceptance())
        by_rules = pr.build_comment(report(introduced=(rules,), not_checked=(), policy=changed(".wingbox/config.yaml")))
        self.assertIn(f"- violation: {layers.VIOLATION}. Only the rules as this change edits them do not report it.\n", by_rules)
        self.assertIn("- `.wingbox/config.yaml` (changed): the boundary rules\n", by_rules)
        self.assertNotIn("edits what it is checked by", pr.build_comment(report()))
        self.assertIn("judged with these files as the base has them: what", pr.build_comment(report(policy=changed(".wingbox/config.yaml"))))

    def test_the_plan_names_the_policy_change(self) -> None:
        """/* REQ-ACTION-144: the plan holds a `(policy-changed "<path>" :kind <kind> :how <how>)` line per policy file, the commit whose policy files judged the findings, on a finding line what alone accepts it, a line per new or changed baseline entry and per finding the change accepts itself; these lines make the comment carry the plan */"""
        plan = pr.build_plan(self.weak).splitlines()
        self.assertIn('(policy-changed ".github/workflows/check.yml" :kind workflow :how changed)', plan)
        self.assertIn('(policy-changed ".wingbox/baseline.sexp" :kind baseline :how added)', plan)
        self.assertIn(f"(policy-of-base {'d' * 40})", plan)
        self.assertIn(f'(finding violation {json.dumps(layers.VIOLATION)} :accepted-only-by ".wingbox/baseline.sexp" :missing (owner reason expires))', plan)
        self.assertIn('(baseline-entry "." src/models/models0 src/routes/routes1 :rule "domain -> api" :missing (owner reason expires))', plan)
        accepted = pr.build_plan(self.accepted).splitlines()
        self.assertIn(f'(accepted-by-change violation {json.dumps(layers.VIOLATION)} :known ".wingbox/baseline.sexp" :owner "lead" '
                      ':reason "moves with the next release" :expires 2099-01-01)', accepted)
        self.assertIn('(baseline-entry "." src/models/models0 src/routes/routes1 :rule "domain -> api" :owner "lead" '
                      ':reason "moves with the next release" :expires 2099-01-01)', accepted)
        rules = facts.Finding("violation", KEY, layers.VIOLATION, facts.Acceptance())
        self.assertIn(f"(finding violation {json.dumps(layers.VIOLATION)} :accepted-only-by rules)",
                      pr.build_plan(report(introduced=(rules,), not_checked=())).splitlines())
        only = report(changed=(pr.ChangedFile(".wingbox/config.yaml", None),), introduced=(), not_checked=(), policy=changed(".wingbox/config.yaml"))
        self.assertIn("Review plan for an agent", pr.build_comment(only))
        self.assertFalse([line for line in pr.build_plan(report()).splitlines() if line.startswith(("(policy-", "(baseline-entry", "(accepted-by"))])

    def test_the_record_holds_the_policy_change(self) -> None:
        """/* REQ-ACTION-144: the evidence record has a `policy` object: the mode, each changed policy file with its kind and change, the commit whose policy files judged the findings, the new or changed baseline entries with their terms, and the findings the policy of the base reports and the change itself accepts, each with whether that acceptance counts; the record meets schema/wingbox-evidence-2.schema.json and names that version */"""
        schema = json.loads((ROOT / "schema" / "wingbox-evidence-2.schema.json").read_text(encoding="utf-8"))
        record = we.build_evidence(dataclasses.replace(self.weak, policy_mode="fail"), RUN, {})
        self.assertEqual([], problems(record, schema))
        self.assertEqual("wingbox-evidence/2", record["schema"])
        self.assertEqual({
            "mode": "fail",
            "changed": [{"path": ".github/workflows/check.yml", "kind": "workflow", "change": "changed"},
                        {"path": ".wingbox/baseline.sexp", "kind": "baseline", "change": "added"}],
            "judged-with": "d" * 40,
            "entries": [{"entry": "baseline", "part": ".", "source": "src/models/models0", "target": "src/routes/routes1",
                         "rule": "domain -> api", "owner": "", "reason": "", "expires": "", "complete": False}],
            "self-accepted": [{"finding": "violation", "text": layers.VIOLATION, "by": ".wingbox/baseline.sexp", "owner": "", "reason": "",
                               "expires": "", "accepts": False}],
        }, record["policy"])
        accepted = we.build_evidence(self.accepted, RUN, {})
        self.assertEqual([], problems(accepted, schema))
        self.assertEqual([{"finding": "violation", "text": layers.VIOLATION, "by": ".wingbox/baseline.sexp", "owner": "lead",
                           "reason": "moves with the next release", "expires": "2099-01-01", "accepts": True}], accepted["policy"]["self-accepted"])
        plain = we.build_evidence(report(), RUN, {})
        self.assertEqual({"mode": "report", "changed": [], "judged-with": "", "entries": [], "self-accepted": []}, plain["policy"])
        self.assertEqual([], problems(plain, schema))


class DeletedTestsTest(unittest.TestCase):
    def test_a_deleted_test_that_used_the_changed_code_is_named(self) -> None:
        """/* REQ-ACTION-149: a test file the change deletes is paired with each changed source file it used directly on the base; a deleted file that is no test, and a deleted test of code the change does not touch, are not; the comment and the plan name the pair, and the plan line makes the comment carry the plan */"""
        users = {"app/services/billing.py": [facts.Dependent("tests/test_billing", 1), facts.Dependent("app/api/routes", 1),
                                              facts.Dependent("tests/test_far", 2)],
                 "app/models/order.py": [facts.Dependent("tests/test_billing", 1)]}
        deleted = ["tests/test_billing.py", "tests/test_far.py", "tests/test_other.py", "app/api/routes.py"]
        pairs = pr.deleted_tests(deleted, users)
        self.assertEqual((("tests/test_billing.py", "app/models/order.py"), ("tests/test_billing.py", "app/services/billing.py")), pairs)
        self.assertEqual((), pr.deleted_tests([], users))
        gone = report(introduced=(), not_checked=(), tests_deleted=pairs[1:])
        body = pr.build_comment(gone)
        self.assertIn("Tests deleted together with the code they tested: `tests/test_billing.py` (tested `app/services/billing.py`). "
                      "What they checked is no longer checked.\n", body)
        self.assertIn('(test-deleted "tests/test_billing.py" :tested "app/services/billing.py")', pr.build_plan(gone).splitlines())
        self.assertIn("Review plan for an agent", body)
        self.assertNotIn("Tests deleted", pr.build_comment(report()))


class DocumentsTest(unittest.TestCase):
    def test_the_input_and_the_setup_are_documented(self) -> None:
        """/* REQ-ACTION-150: action.yml has the input `policy-change` (empty by default: `fail` with `fail-on: strict`), hands it to the script as WINGBOX_POLICY_CHANGE and has the output `policy-changed`; the GitLab templates name the variable and the shell-runner template hands it and CI_CONFIG_PATH to the container; README.md documents the input, the output, the policy files, the plan line, and the setup that separates duties (a code owner rule, a review by someone other than the author) */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  policy-change:\n    description: [^\n]+\n    default: ""\n')
        self.assertIn("WINGBOX_POLICY_CHANGE: ${{ inputs.policy-change }}", action)
        self.assertRegex(action, r"\n  policy-changed:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.policy-changed \}\}\n")
        shell = (ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("--env WINGBOX_POLICY_CHANGE", shell)
        self.assertIn("--env CI_CONFIG_PATH", shell)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for piece in ("`policy-change`", "`policy-changed`", "WINGBOX_POLICY_CHANGE", "WINGBOX_POLICY_CHANGED", "(policy-changed ",
                      "`.wingbox/baseline.sexp`", "`.wingbox/config.yaml`", "`.wingbox/learned.sexp`", "`.wingbox/required-tests.yaml`",
                      "CODEOWNERS", "someone other than the author", ":owner", ":reason", ":expires", "wingbox-evidence-2.schema.json"):
            self.assertIn(piece, readme, piece)
        self.assertEqual(sorted(policy.FIXED), sorted(name for name in policy.FIXED if f"`{name}`" in readme))


if __name__ == "__main__":
    unittest.main()
