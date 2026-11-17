"""The facts of `wingbox impact --sexp` (Wingbox 0.10): why each file is on the list."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_facts as facts  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


class ImpactFactsTest(unittest.TestCase):
    def test_a_dependent_carries_the_names_it_uses_and_the_module_it_is_reached_through(self) -> None:
        """/* REQ-ACTION-013: from `wingbox impact --sexp` of Wingbox 0.10 each dependent keeps its depth, the relation of its edge, below depth 1 the module it is reached through with the number of other such modules, and the name lists of the edge (use, reexport, glob, member, macro) with the facade, glob module or type they arrive through and their full count (`:of N`); a fact without lists (an older Wingbox, a plain import) gives a dependent without names. */"""
        root, found = facts.dependents(lines("impact_names.sexp"))
        self.assertEqual("src/app", root)
        by_module = {dependent.module: dependent for dependent in found}
        self.assertEqual(["src/app/mouse", "src/main", "src/ui", "src/macros", "src/bin/tool"], [d.module for d in found])
        self.assertEqual(
            facts.Dependent("src/app/mouse", 1, "imports", "", 0, (facts.Route("use", "", ("AppCommand", "AppData", "AppEvent"), 14),)),
            by_module["src/app/mouse"],
        )
        self.assertEqual(
            (facts.Route("member", "ProcessManager", ("new",), 1),
             facts.Route("reexport", "crate::infra", ("AttachTtyError", "KillProcessError", "ProcessExit"), 10)),
            by_module["src/main"].routes,
        )
        self.assertEqual((facts.Route("glob", "super", ("Frame", "render_row"), 2),), by_module["src/ui"].routes)
        self.assertEqual((facts.Route("macro", "", ("log_line",), 1),), by_module["src/macros"].routes)
        far = by_module["src/bin/tool"]
        self.assertEqual((2, "src/main", 2, (facts.Route("use", "", ("run",), 1),)), (far.depth, far.through, far.more, far.routes))
        old = facts.dependents(["(impact src/models/order src/api/orders.controller :lang ts :via imports :depth 2)"])[1][0]
        self.assertEqual(facts.Dependent("src/api/orders.controller", 2), old)

    def test_the_real_output_of_a_rust_project_is_read_whole(self) -> None:
        """/* REQ-ACTION-013: the output of Wingbox 0.10.1 on a changed Rust file (a fixture) gives its seven direct dependents, six of them with the names they use through the facade that re-exports the file and one that only declares it, and six farther ones, each with the module it is reached through. */"""
        root, found = facts.dependents(lines("impact_rust_reexports.sexp"))
        self.assertEqual("src/infra/session_detail", root)
        direct = [d for d in found if d.depth == 1]
        self.assertEqual(7, len(direct))
        self.assertEqual(["src/infra"], [d.module for d in direct if not d.routes])
        self.assertEqual(
            ("LoadSessionTimelineError", "load_session_timeline"),
            next(d for d in direct if d.module == "src/cli").routes[0].names,
        )
        farther = [d for d in found if d.depth == 2]
        self.assertEqual(6, len(farther))
        self.assertEqual({"src/infra"}, {d.through for d in farther})
        self.assertEqual(3, next(d for d in farther if d.module == "src/infra/scan_all").more)

    def test_the_nearest_row_of_a_dependent_keeps_its_names(self) -> None:
        """/* REQ-ACTION-013: the dependents a pull request does not change keep, per module, the row of its nearest depth with its names and the module it is reached through. */"""
        _root, found = facts.dependents(lines("impact_names.sexp"))
        kept = facts.untouched(found + [facts.Dependent("src/ui", 2, "imports", "src/main")], {"src/app", "src/main"})
        self.assertEqual(["src/app/mouse", "src/macros", "src/ui", "src/bin/tool"], [d.module for d in kept])
        self.assertEqual((facts.Route("glob", "super", ("Frame", "render_row"), 2),), kept[2].routes)

    def test_a_list_that_is_not_a_name_list_is_no_route(self) -> None:
        """/* REQ-ACTION-013: a token that is not a `(:way names...)` list gives no route, and a quoted name keeps its spaces and brackets. */"""
        self.assertIsNone(facts.parse_route("src/app"))
        self.assertIsNone(facts.parse_route("(infra module)"))
        self.assertEqual(facts.Route("use", "", ("operator ()", "b"), 2), facts.parse_route('(:use "operator ()" b)'))


if __name__ == "__main__":
    unittest.main()
