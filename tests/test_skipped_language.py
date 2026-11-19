"""A language whose parser is not installed is never read as checked."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import wingbox_pr as pr  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

NOTE = '[wingbox] NOTE: (skipped-language csharp :files 214 :add "wingbox add csharp")\n'


class SkippedLanguageTest(unittest.TestCase):
    def test_the_fact_of_a_skipped_language_is_read_from_the_commands(self) -> None:
        """/* REQ-ACTION-086: a `(skipped-language <name> :files N ...)` fact a Wingbox command prints (queries print it on stderr) is recorded with the language and the largest number of files said */"""
        def fake_run(argv: list[str], cwd: str):
            return subprocess.CompletedProcess(argv, 0, "", NOTE + '[wingbox] NOTE: (skipped-language go :files 3 :add "x")\n')
        real_run, pr._run = pr._run, fake_run
        try:
            pr.SKIPPED_LANGUAGES.clear()
            pr._wingbox("/nowhere", "boundaries")
            pr._wingbox("/nowhere", "impact", "a.ts")
            self.assertEqual({"csharp": 214, "go": 3}, pr.SKIPPED_LANGUAGES)
        finally:
            pr._run = real_run
            pr.SKIPPED_LANGUAGES.clear()

    def test_a_skipped_language_makes_the_result_unknown(self) -> None:
        """/* REQ-ACTION-087: with a skipped language the result is UNKNOWN with the reason `skipped-language: <name> (N files)`: where it would be PASS, and where no source module of the graph changed (the changed files may be of that language), so fail-on strict fails; a blocking finding is still FAIL and the reason is kept */"""
        skipped = (("csharp", 214),)
        clean = report(introduced=(), not_checked=(), skipped_languages=skipped)
        self.assertEqual("PASS", pr.result(report(introduced=(), not_checked=()))[0])
        self.assertEqual(("UNKNOWN", "ok", ("skipped-language: csharp (214 files)",)), pr.result(clean))
        self.assertEqual(1, pr.exit_status(clean, "strict"))
        self.assertEqual(0, pr.exit_status(clean, "none"))
        nothing = report(changed=(pr.ChangedFile("src/Service.cs", None),), introduced=(), not_checked=(), skipped_languages=skipped)
        self.assertEqual("NOT_APPLICABLE", pr.result(report(changed=(pr.ChangedFile("src/Service.cs", None),), introduced=(), not_checked=()))[0])
        self.assertEqual("UNKNOWN", pr.result(nothing)[0])
        self.assertTrue(pr.needs_comment(nothing))
        self.assertEqual(1, pr.exit_status(nothing, "strict"))
        failing = pr.result(report(skipped_languages=skipped))
        self.assertEqual("FAIL", failing[0])
        self.assertIn("skipped-language: csharp (214 files)", failing[2])

    def test_the_report_and_the_plan_say_what_was_not_analysed(self) -> None:
        """/* REQ-ACTION-088: the comment and the summary say how many files of which language were not analysed and that the result is not a pass, the plan holds `(skipped-language <name> :files N)`, and a change of nothing but such files gets that sentence instead of "nothing to check" */"""
        skipped = (("csharp", 214), ("go", 1))
        for full in (False, True):
            text = pr.build_comment(report(introduced=(), not_checked=(), skipped_languages=skipped), full=full)
            self.assertIn("214 C# files and 1 Go file were not analysed", text)
            self.assertIn("UNKNOWN, not a pass", text)
        plan = pr.build_plan(report(skipped_languages=skipped))
        self.assertIn("(skipped-language csharp :files 214)", plan)
        self.assertIn(':result FAIL', plan.splitlines()[0])
        only = pr.build_comment(report(changed=(pr.ChangedFile("src/Service.cs", None),), introduced=(), not_checked=(),
                                       skipped_languages=skipped))
        self.assertIn("were not analysed", only)
        self.assertNotIn("nothing to check", only)

    def test_the_check_installs_every_parser(self) -> None:
        """/* REQ-ACTION-089: action.yml and both GitLab templates install `wingbox[all]`, the release with the parser of every language */"""
        for path in (ROOT / "action.yml", ROOT / "gitlab" / "wingbox.gitlab-ci.yml", ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml"):
            self.assertIn('"wingbox[all]==$WINGBOX_VERSION"', path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
