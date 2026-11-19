"""The verdicts of a run, kept apart: introduced and existing violations, the baseline, the tests, the coverage, eligible."""
import dataclasses
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import layers_fixture as layers  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_facts as facts  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_verdicts as verdicts  # noqa: E402
from test_coverage import whole  # noqa: E402
from test_evidence_record import RUN, problems  # noqa: E402
from test_policy import KNOWN, KNOWN_WITH_TERMS, OK, VIOLATION  # noqa: E402
from test_policy_run import run  # noqa: E402
from test_test_evidence import with_tests  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

SCHEMA = json.loads((ROOT / "schema" / "wingbox-evidence-2.schema.json").read_text(encoding="utf-8"))
EXPIRED = VIOLATION.replace(" :src-file", ' :expired 2020-01-01 :owner "lead" :src-file')
OTHER = VIOLATION.replace("models0", "models2").replace("routes1", "routes3")
OLD = facts.findings([VIOLATION])[0][0]
SECOND = facts.findings([OTHER])[0][0]
IN_TERM = facts.accepted([KNOWN_WITH_TERMS])[0]
PAST = facts.expired([EXPIRED])[0]
PASS = ("PASS", "ok", ())


class FactsTest(unittest.TestCase):
    def test_accepted_and_expired_breaks_are_read_with_their_terms(self) -> None:
        """/* REQ-ACTION-160: a break the baseline accepts is read from `violation-known` and `capability-violation-known` as the finding it hides with the file, the owner, the reason and the end date of its entry; a violation whose entry is past its end date (`:expired`) is read with that date and the owner; a plain violation is neither */"""
        self.assertEqual([facts.Finding("violation", OLD.key, layers.VIOLATION,
                                        facts.Acceptance(".wingbox/baseline.sexp", "lead", "moves with the next release", "2099-01-01"))],
                         facts.accepted([KNOWN_WITH_TERMS, OK]))
        self.assertEqual(facts.Acceptance(".wingbox/baseline.sexp"), facts.accepted([KNOWN])[0].accepted)
        self.assertEqual([facts.Finding("violation", OLD.key, layers.VIOLATION, facts.Acceptance("", "lead", "", "2020-01-01"))],
                         facts.expired([EXPIRED, OTHER, KNOWN]))
        self.assertEqual(([], []), (facts.accepted([VIOLATION]), facts.expired([VIOLATION])))
        self.assertEqual(["violation"], [finding.head for finding in facts.findings([EXPIRED])[0]])

    def test_the_existing_findings_are_those_of_the_head_that_the_base_has_too(self) -> None:
        """/* REQ-ACTION-160: a finding the head holds and the base has too was there before the change; a finding of the base that only the change's own baseline entry or rule edit hides is still there, unless an entry with an owner, a reason and an end date accepts it; an introduced finding is not existing */"""
        self.assertEqual([OLD], facts.existing([OLD], [OLD, SECOND], None, {}))
        self.assertEqual([], facts.existing([], [OLD], None, {}))
        hidden = facts.existing([OLD], [], [OLD], facts.known([KNOWN]))
        self.assertEqual([(OLD.key, facts.Acceptance(".wingbox/baseline.sexp"))], [(finding.key, finding.accepted) for finding in hidden])
        self.assertEqual([facts.Acceptance()], [finding.accepted for finding in facts.existing([OLD], [], [OLD], {})])
        self.assertEqual([], facts.existing([OLD], [], [OLD], facts.known([KNOWN_WITH_TERMS])))
        self.assertEqual([], facts.existing([OLD], [], None, {}))


class VerdictTest(unittest.TestCase):
    def test_the_existing_violations_have_their_own_verdict(self) -> None:
        """/* REQ-ACTION-161: `boundaries.existing` is PRESENT with the number of violations the head holds that the base has too and the first of them (a candidate is not a violation), NONE when there is none and everything was checked, and UNKNOWN when none was found but a part was not checked, a language was left out, a command failed or the tree is not the commit named */"""
        candidate = facts.Finding("violation-candidate", ("c",), "`a` -> `b` (shared -> infra)")
        self.assertEqual(("PRESENT", 2, layers.VIOLATION), verdicts.existing(whole(existing=(candidate, OLD, SECOND))))
        self.assertEqual(("NONE", 0, ""), verdicts.existing(whole(existing=(candidate,))))
        for partial in (whole(not_checked=(facts.NotChecked("web", "no-layer-style"),)), whole(skipped_languages=(("csharp", 3),)),
                        whole(tool_errors=("wingbox boundaries exited 3",)), whole(tree=pr.Tree("c" * 40, "other"))):
            self.assertEqual(("UNKNOWN", 0, ""), verdicts.existing(partial))
            self.assertEqual("PRESENT", verdicts.existing(dataclasses.replace(partial, existing=(OLD,)))[0])

    def test_the_baseline_has_its_own_verdict(self) -> None:
        """/* REQ-ACTION-162: `baseline` is NONE without an accepted break, ACCEPTED with the number of breaks the baseline accepts, EXPIRED when an entry is past its end date (with how many), and UNKNOWN when a command failed and no entry was read; entries without an end date are counted */"""
        open_ended = facts.accepted([KNOWN])[0]
        self.assertEqual(("NONE", 0, 0, 0), verdicts.baseline(whole()))
        self.assertEqual(("ACCEPTED", 2, 0, 1), verdicts.baseline(whole(accepted=(IN_TERM, open_ended))))
        self.assertEqual(("EXPIRED", 1, 1, 0), verdicts.baseline(whole(accepted=(IN_TERM,), expired=(PAST,))))
        self.assertEqual(("UNKNOWN", 0, 0, 0), verdicts.baseline(whole(tool_errors=("wingbox build exited 1",))))

    def test_a_record_is_eligible_only_when_no_verdict_is_failed_unknown_incomplete_or_expired(self) -> None:
        """/* REQ-ACTION-163: `eligible` is true when the change introduces no violation (PASS or NOT_APPLICABLE), the head holds none from before, no baseline entry is past its end date, the test evidence is `complete` and the coverage is complete; each other state gives its reason, in the order of the verdicts, and they add up */"""
        self.assertEqual((True, ()), verdicts.eligible(whole(), "PASS", "complete"))
        self.assertEqual((True, ()), verdicts.eligible(whole(accepted=(IN_TERM,)), "NOT_APPLICABLE", "complete"))
        self.assertEqual((False, ("boundaries-introduced: FAIL",)), verdicts.eligible(whole(), "FAIL", "complete"))
        self.assertEqual((False, ("boundaries-existing: 1 violation(s)",)), verdicts.eligible(whole(existing=(OLD,)), "PASS", "complete"))
        self.assertEqual((False, ("boundaries-existing: 1 violation(s)", "baseline: 1 past the end date")),
                         verdicts.eligible(whole(existing=(OLD,), expired=(PAST,)), "PASS", "complete"))
        for word in ("none", "incomplete", "failed", "none-found"):
            self.assertEqual((False, (f"tests: {word}",)), verdicts.eligible(whole(), "PASS", word))
        self.assertEqual((False, ("coverage: incomplete",)), verdicts.eligible(whole(skipped=("docs/a.md",)), "PASS", "complete"))
        unknown = whole(not_checked=(facts.NotChecked("web", "no-layer-style"),),
                        scan=dataclasses.replace(whole().scan, parts=(facts.Part("web", "", 9, False, "no-layer-style"),)))
        self.assertEqual((False, ("boundaries-introduced: UNKNOWN", "boundaries-existing: UNKNOWN", "tests: none", "coverage: incomplete")),
                         verdicts.eligible(unknown, pr.result(unknown)[0], "none"))
        failed = whole(tool_errors=("wingbox build exited 1",))
        self.assertIn("baseline: UNKNOWN", verdicts.eligible(failed, "UNKNOWN", "complete")[1])


class RecordTest(unittest.TestCase):
    def test_the_record_holds_each_verdict_apart(self) -> None:
        """/* REQ-ACTION-164: the evidence record has `boundaries.introduced` (the result, with the number of findings and of violations and the reasons), `boundaries.existing` (verdict, number, the first), `baseline` (verdict, the numbers, each accepted entry with owner, reason and end date, the entries past it), `eligible` and `eligible-reasons`, next to `tests` and `coverage`; `eligible` reads the test evidence from the record's own summary; the record meets the version 2 schema, and `result` stays as it was */"""
        debt = whole(existing=(OLD,), accepted=(IN_TERM,), expired=(PAST,))
        record = we.build_evidence(debt, RUN, {})
        self.assertEqual([], problems(record, SCHEMA))
        self.assertEqual({"introduced": {"verdict": "PASS", "findings": 0, "violations": 0, "reasons": []},
                          "existing": {"verdict": "PRESENT", "violations": 1, "first": layers.VIOLATION}}, record["boundaries"])
        self.assertEqual({"verdict": "EXPIRED", "accepted": 1, "expired": 1, "without-end-date": 0,
                          "entries": [{"finding": "violation", "text": layers.VIOLATION, "file": ".wingbox/baseline.sexp", "owner": "lead",
                                       "reason": "moves with the next release", "expires": "2099-01-01"}],
                          "past": [{"finding": "violation", "text": layers.VIOLATION, "file": "", "owner": "lead", "reason": "",
                                    "expires": "2020-01-01"}]}, record["baseline"])
        self.assertEqual((False, ["boundaries-existing: 1 violation(s)", "baseline: 1 past the end date", "tests: none"]),
                         (record["eligible"], record["eligible-reasons"]))
        self.assertEqual({"boundaries": "PASS", "execution": "ok", "reasons": []}, record["result"])
        self.assertEqual(record["tests"]["evidence"], "none")
        failing = we.build_evidence(report(), RUN, {})
        self.assertEqual([], problems(failing, SCHEMA))
        self.assertEqual(("FAIL", 3, 3), tuple(failing["boundaries"]["introduced"][key] for key in ("verdict", "findings", "violations")))
        self.assertEqual(failing["result"]["reasons"], failing["boundaries"]["introduced"]["reasons"])
        self.assertFalse(failing["eligible"])
        clean = whole(changed=())
        passed = we.build_evidence(with_tests(**{name: getattr(clean, name) for name in ("introduced", "not_checked", "tree", "scan")}), RUN, {})
        self.assertEqual(passed["tests"]["evidence"] == "complete", passed["eligible"])

    def test_an_eligible_record(self) -> None:
        """/* REQ-ACTION-164: a run with nothing failed, unknown, incomplete or expired gives `eligible: true` with no reason */"""
        record = verdicts.record(whole(accepted=(IN_TERM,)), PASS, "complete")
        self.assertEqual((True, [], "ACCEPTED", "NONE"), (record["eligible"], record["eligible-reasons"], record["baseline"]["verdict"],
                                                         record["boundaries"]["existing"]["verdict"]))


class FailOnTest(unittest.TestCase):
    def test_fail_on_existing_fails_when_the_head_holds_a_violation_old_or_new(self) -> None:
        """/* REQ-ACTION-165: `fail-on: existing` fails the job when the head holds a violation, one the change introduces or one that was there before, and not for a candidate; `strict` keeps its meaning: a violation that was there before does not fail it, nor does an expired baseline entry, a test verdict or `eligible` */"""
        self.assertEqual(("none", "violations", "not-checked", "strict", "existing"), pr.FAIL_ON)
        old = whole(existing=(OLD,), expired=(PAST,))
        self.assertEqual((1, 0, 0, 0), tuple(pr.exit_status(old, value) for value in ("existing", "strict", "violations", "none")))
        self.assertEqual("PASS", pr.result(old)[0])
        new = whole(introduced=(OLD,))
        self.assertEqual((1, 1, 1), tuple(pr.exit_status(new, value) for value in ("existing", "strict", "violations")))
        candidate = facts.Finding("violation-candidate", ("c",), "x")
        self.assertEqual(0, pr.exit_status(whole(existing=(candidate,), introduced=(candidate,)), "existing"))
        self.assertEqual(0, pr.exit_status(whole(), "existing"))


class CommentTest(unittest.TestCase):
    def test_the_comment_has_one_line_per_verdict_in_a_fixed_order(self) -> None:
        """/* REQ-ACTION-166: the comment and the job summary hold, under "Verdicts", one line per verdict, always these six in this order: boundaries introduced, boundaries there before, baseline, tests, coverage, eligible; the first line of the comment stays the marker and the heading stays what it was; the outputs `eligible`, `existing-violations`, `baseline-accepted` and `baseline-expired` hold the same */"""
        debt = whole(existing=(OLD, SECOND), accepted=(IN_TERM, facts.accepted([KNOWN])[0]), expired=(PAST,))
        for full in (False, True):
            body = pr.build_comment(debt, full=full)
            self.assertEqual([pr.MARKER, "**Wingbox**: no module outside this pull request uses the files it changes directly, and it adds no "
                              "boundary finding."], body.split("\n")[:2])
            self.assertIn("### Verdicts\n\n"
                          "- Boundaries, introduced by this change: **PASS**\n"
                          f"- Boundaries, there before this change: **2 violations** on the head that the base has too; the first: {layers.VIOLATION}\n"
                          "- Baseline: **2 accepted breaks**; 1 past its end date, 1 without an end date\n"
                          "- Tests: **none**\n"
                          "- Coverage: **complete**\n"
                          "- Eligible as evidence: **no** (boundaries-existing: 2 violation(s); baseline: 1 past the end date; tests: none)\n", body)
        self.assertEqual({"eligible": "false", "existing-violations": "2", "baseline-accepted": "2", "baseline-expired": "1"},
                         {name: pr.outputs(debt)[name] for name in ("eligible", "existing-violations", "baseline-accepted", "baseline-expired")})
        plain = pr.build_comment(report())
        self.assertEqual([pr.MARKER, "## Wingbox: 3 new boundary findings"], plain.split("\n")[:2])
        self.assertIn("- Boundaries, introduced by this change: **FAIL** (introduced-violations: 3)\n"
                      "- Boundaries, there before this change: **UNKNOWN**: no violation was found, but not everything was checked\n"
                      "- Baseline: **no accepted break**\n"
                      "- Tests: **none**\n"
                      "- Coverage: **incomplete** (not-checked: web (declared-rules-not-read); graph: UNKNOWN (not-read))\n"
                      "- Eligible as evidence: **no** (boundaries-introduced: FAIL; boundaries-existing: UNKNOWN; tests: none; coverage: incomplete)\n", plain)
        self.assertEqual(["- Boundaries, introduced by this change: **PASS**", "- Boundaries, there before this change: **none**",
                          "- Baseline: **1 accepted break**", "- Tests: **complete**", "- Coverage: **complete**", "- Eligible as evidence: **yes**"],
                         verdicts.lines(whole(accepted=(IN_TERM,)), PASS, "complete"))
        self.assertIn("**UNKNOWN**: the boundaries could not be read", verdicts.lines(whole(tool_errors=("wingbox build exited 1",)), PASS, "none")[2])


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class WholeScriptTest(unittest.TestCase):
    def test_a_violation_that_was_there_before_is_said(self) -> None:
        """/* REQ-ACTION-167: the scenario of issue 19, the whole script with a real Wingbox: the base already holds a break and the change edits an unrelated file; the result is still PASS and `fail-on: strict` still passes, but the report and the record now say that the head holds 1 violation that the base has too and name it, that no test report was given, and that the record is not eligible, with the reasons */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, _clean = layers.repository(Path(tmp))
            base = layers.commit(repo, layers.BREAK, "a break lands on the base")
            head = layers.branch(repo, "feature", layers.UNRELATED)
            done, plan, record, text = run(repo, Path(tmp) / "out", base, head, WINGBOX_FAIL_ON="strict")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertTrue(plan[0].startswith("(review-plan :schema 1 :result PASS :execution ok "), plan[0])
            self.assertEqual({"introduced": {"verdict": "PASS", "findings": 0, "violations": 0, "reasons": []},
                              "existing": {"verdict": "PRESENT", "violations": 1, "first": layers.VIOLATION}}, record["boundaries"])
            self.assertEqual({"verdict": "NONE", "accepted": 0, "expired": 0, "without-end-date": 0, "entries": [], "past": []}, record["baseline"])
            self.assertEqual((False, ["boundaries-existing: 1 violation(s)", "tests: none"]), (record["eligible"], record["eligible-reasons"]))
            self.assertIn(f"- Boundaries, there before this change: **1 violation** on the head that the base has too; the first: {layers.VIOLATION}\n", text)
            self.assertIn("- Eligible as evidence: **no** (boundaries-existing: 1 violation(s); tests: none)\n", text)
            self.assertIn("- Eligible as evidence: **no**", done.stdout)
            self.assertEqual([], problems(record, SCHEMA))

    def test_fail_on_existing_and_a_baseline_entry_past_its_end_date(self) -> None:
        """/* REQ-ACTION-167: the whole script on a base that holds two breaks, one accepted by a baseline entry within its terms and one whose entry is past its end date: the record says ACCEPTED 1 and EXPIRED 1, the expired one is a violation that was there before, and `fail-on: existing` fails the job while the result stays PASS */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, _clean = layers.repository(Path(tmp))
            second = {"src/models/models2.ts": "import { routes3 } from '../routes/routes3';\nexport const models2 = routes3;\n"}
            entries = (f"{layers.ENTRY} :owner \"lead\" :reason \"was due last year\" :expires 2020-01-01)\n"
                       f"{layers.ENTRY.replace('models0', 'models2').replace('routes1', 'routes3')}{layers.TERMS})\n")
            base = layers.commit(repo, {**layers.BREAK, **second, layers.BASELINE: entries}, "two breaks and their entries")
            head = layers.branch(repo, "feature", layers.UNRELATED)
            done, plan, record, text = run(repo, Path(tmp) / "out", base, head, WINGBOX_FAIL_ON="existing")
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            self.assertTrue(plan[0].startswith("(review-plan :schema 1 :result PASS :execution ok "), plan[0])
            self.assertEqual(("PRESENT", 1, layers.VIOLATION), tuple(record["boundaries"]["existing"][key] for key in ("verdict", "violations", "first")))
            self.assertEqual(("EXPIRED", 1, 1, 0), tuple(record["baseline"][key] for key in ("verdict", "accepted", "expired", "without-end-date")))
            self.assertEqual([{"finding": "violation", "text": layers.VIOLATION.replace("models0", "models2").replace("routes1", "routes3"),
                               "file": ".wingbox/baseline.sexp", "owner": "lead", "reason": "moves with the next release", "expires": "2099-01-01"}],
                             record["baseline"]["entries"])
            self.assertEqual(("lead", "2020-01-01"), (record["baseline"]["past"][0]["owner"], record["baseline"]["past"][0]["expires"]))
            self.assertIn("- Baseline: **1 accepted break**; 1 past its end date\n", text)
            self.assertIn("fail-on existing: the head holds 1 violation", done.stdout)
            self.assertEqual([], record["policy"]["changed"])


class DocumentsTest(unittest.TestCase):
    def test_the_verdicts_are_documented(self) -> None:
        """/* REQ-ACTION-168: action.yml names `existing` among the values of `fail-on` and has the outputs `eligible`, `existing-violations`, `baseline-accepted` and `baseline-expired`; the GitLab templates name the value; README.md documents each verdict, `eligible`, the value `existing`, that `strict` is unchanged, and says that schema version 1 is superseded by version 2 */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        fail_on = action.split("\n  fail-on:\n    description: ", 1)[1].split("\n", 1)[0]
        for value in pr.FAIL_ON:
            self.assertIn(value, fail_on)
        for name in ("eligible", "existing-violations", "baseline-accepted", "baseline-expired"):
            self.assertIn(f"\n  {name}:\n    description: ", action)
            self.assertIn(f"value: ${{{{ steps.check.outputs.{name} }}}}", action)
        for template in sorted((ROOT / "gitlab").glob("*.yml")):
            self.assertIn("none, violations, not-checked, strict or existing", template.read_text(encoding="utf-8"), template.name)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for piece in ("`boundaries.introduced`", "`boundaries.existing`", "`baseline`", "`eligible`", "`eligible-reasons`", "`existing`",
                      "`existing-violations`", "`baseline-accepted`", "`baseline-expired`", "WINGBOX_ELIGIBLE", "### Verdicts",
                      "superseded", "`PRESENT`", "`EXPIRED`", "`ACCEPTED`"):
            self.assertIn(piece, readme, piece)
        self.assertRegex(readme, r"`fail-on: strict` [^.]*does not fail")


if __name__ == "__main__":
    unittest.main()
