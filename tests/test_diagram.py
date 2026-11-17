"""The blast radius as a Mermaid flowchart: its text, the cap, the escaping, the order."""
import random
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_diagram as diagram  # noqa: E402
from wingbox_diagram import CHANGED, DIRECT, FAR, Module  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SMALL = (
    Module("src/models/order.ts", CHANGED),
    Module("src/services/order.service", DIRECT, ("src/models/order.ts",)),
    Module("src/api/orders.controller", FAR, ("src/services/order.service",)),
    Module("src/jobs/invoice.job", FAR, ("src/services/order.service",)),
)


def large(changed: int = 3, direct: int = 50, far: int = 100, folders: int = 5, tops: int = 1) -> tuple[Module, ...]:
    """changed files in core/, direct dependents spread over folders (under one top folder `app/`, or under tops
    top folders), and farther dependents in the same folders, each reached through a direct one of its folder."""
    def folder(index: int) -> str:
        return f"app/p{index % folders}" if tops == 1 else f"t{index % folders % tops:02d}/p{index % folders}"

    files = [f"core/f{index}.py" for index in range(changed)]
    modules = [Module(path, CHANGED) for path in files]
    for index in range(direct):
        modules.append(Module(f"{folder(index)}/d{index:03d}", DIRECT, (files[index % changed], files[(index + 1) % changed])))
    for index in range(far):
        modules.append(Module(f"{folder(index)}/x{index:03d}", FAR, (f"{folder(index)}/d{index % direct:03d}",)))
    return tuple(modules)


def nodes(text: str) -> list[str]:
    return re.findall(r"^\s+([cm]\d+)[\[(]", text, flags=re.M)


def edges(text: str) -> list[str]:
    return re.findall(r"^\s+[cm]\d+ -\.?-> [cm]\d+$", text, flags=re.M)


class DiagramTextTest(unittest.TestCase):
    def test_a_small_blast_radius_is_drawn_whole(self) -> None:
        """/* REQ-ACTION-015: the diagram is a Mermaid flowchart with the changed files in one box, every dependent the pull request does not change in the box of its folder (named without the folder), a solid arrow from a direct dependent to the changed file it uses, a dashed arrow and a dashed border for a farther one, to the module it is reached through; the changed files have a thick border. */"""
        drawn = diagram.blast_radius(SMALL)
        self.assertEqual((FIXTURES / "diagram_small.mmd").read_text(encoding="utf-8"), drawn.text + "\n")
        self.assertEqual((3, 0, 0, 0, False), (drawn.modules, drawn.hidden_modules, drawn.hidden_changed, drawn.hidden_edges, drawn.collapsed))

    def test_nothing_to_draw_gives_no_diagram(self) -> None:
        """/* REQ-ACTION-015: without a dependent there is no diagram; a changed file nothing drawn uses is left out of it. */"""
        self.assertEqual("", diagram.blast_radius((Module("a.py", CHANGED),)).text)
        self.assertEqual("", diagram.blast_radius(()).text)
        drawn = diagram.blast_radius(SMALL + (Module("src/lonely.ts", CHANGED),))
        self.assertNotIn("lonely", drawn.text)
        self.assertEqual(0, drawn.hidden_changed)

    def test_test_modules_are_one_counted_box_per_folder(self) -> None:
        """/* REQ-ACTION-015: test modules are never drawn one by one: each folder gets one box with their count, linked to what they use. */"""
        modules = SMALL + tuple(Module(f"tests/unit/test_{name}", DIRECT, ("src/models/order.ts",), True) for name in ("a", "b", "c"))
        drawn = diagram.blast_radius(modules)
        self.assertIn('  subgraph g3["tests/unit/"]\n    m3[["3 test modules"]]\n  end\n', drawn.text)
        self.assertIn("  m3 --> c0\n", drawn.text)
        self.assertEqual((6, 0, True), (drawn.modules, drawn.hidden_modules, drawn.collapsed))


class DiagramCapTest(unittest.TestCase):
    def test_over_the_cap_folders_collapse_to_counted_boxes_farther_ones_first(self) -> None:
        """/* REQ-ACTION-016: a diagram never has more nodes or arrows than its caps. Over a cap, the farther dependents of each folder become one box with their count, then those of each top folder; if that is not enough the direct ones of each folder too, then the changed files by folder, then everything by top folder; every module is still counted in a box, and the arrows follow the boxes. */"""
        modules = large()
        whole = diagram.blast_radius(modules, node_cap=1000, edge_cap=1000)
        self.assertEqual((153, 200, False), (len(nodes(whole.text)), len(edges(whole.text)), whole.collapsed))
        first = diagram.blast_radius(modules, node_cap=60, edge_cap=200)
        self.assertEqual(3 + 50 + 5, len(nodes(first.text)))
        self.assertIn('[["20 modules"]]', first.text)
        self.assertIn('("d000")', first.text)
        self.assertEqual((150, 0, 0, True), (first.modules, first.hidden_modules, first.hidden_edges, first.collapsed))
        top = diagram.blast_radius(modules, node_cap=57, edge_cap=200)
        self.assertEqual(3 + 50 + 1, len(nodes(top.text)))
        self.assertIn('  subgraph g0["app/"]\n    m0[["100 modules"]]\n  end\n', top.text)
        self.assertIn('("d000")', top.text)
        second = diagram.blast_radius(modules, node_cap=40, edge_cap=60)
        self.assertEqual(3 + 5 + 1, len(nodes(second.text)))
        self.assertNotIn('("d000")', second.text)
        self.assertIn('[["10 modules"]]', second.text)
        self.assertEqual(5 * 3 + 5, len(edges(second.text)))
        self.assertEqual((150, 0, 0), (second.modules, second.hidden_modules, second.hidden_edges))
        third = diagram.blast_radius(modules, node_cap=8, edge_cap=60)
        self.assertIn('[["3 files in core/"]]', third.text)
        self.assertEqual((1 + 5 + 1, 5 + 5), (len(nodes(third.text)), len(edges(third.text))))
        self.assertEqual((150, 0, 0, 0), (third.modules, third.hidden_modules, third.hidden_changed, third.hidden_edges))
        fourth = diagram.blast_radius(modules, node_cap=4, edge_cap=60)
        self.assertIn('  subgraph g0["app/"]\n    m0[["50 modules"]]\n    m1[["100 modules"]]\n  end\n', fourth.text)
        self.assertEqual((3, 2), (len(nodes(fourth.text)), len(edges(fourth.text))))
        self.assertEqual((150, 0, 0, 0), (fourth.modules, fourth.hidden_modules, fourth.hidden_changed, fourth.hidden_edges))

    def test_what_still_does_not_fit_is_cut_and_counted(self) -> None:
        """/* REQ-ACTION-016: when the collapsed diagram is still over a cap, the boxes of the direct dependents are kept before the farther ones, the largest first, a farther box only when what it points at is drawn; the diagram reports how many modules, changed files and arrows it does not draw; drawn and hidden modules add up to all of them, and no box is left without an arrow. */"""
        modules = large(changed=30, direct=120, far=240, folders=40, tops=40)
        for node_cap, edge_cap in ((40, 60), (10, 5), (3, 1)):
            drawn = diagram.blast_radius(modules, node_cap=node_cap, edge_cap=edge_cap)
            self.assertLessEqual(len(nodes(drawn.text)), node_cap)
            self.assertLessEqual(len(edges(drawn.text)), edge_cap)
            self.assertEqual(360, drawn.modules + drawn.hidden_modules)
            self.assertTrue(drawn.collapsed)
        roomy = diagram.blast_radius(modules, node_cap=61, edge_cap=100)
        self.assertEqual((1 + 40 + 20, 40 + 20), (len(nodes(roomy.text)), len(edges(roomy.text))))
        self.assertEqual((120 + 20 * 6, 20 * 6, 20), (roomy.modules, roomy.hidden_modules, roomy.hidden_edges))
        cut = diagram.blast_radius(modules, node_cap=10, edge_cap=5)
        self.assertGreater(cut.hidden_modules, 0)
        self.assertGreater(cut.hidden_edges, 0)
        for drawn in (roomy, cut):
            ids = set(nodes(drawn.text))
            linked: set[str] = set()
            for edge in edges(drawn.text):
                source, target = edge.replace(" -.-> ", " --> ").strip().split(" --> ")
                self.assertTrue(source in ids and target in ids, edge)
                linked.update((source, target))
            self.assertEqual(ids, linked)

    def test_the_default_caps_hold_for_a_large_pull_request(self) -> None:
        """/* REQ-ACTION-016: with the default caps a pull request with hundreds of dependents gives a diagram within NODE_CAP nodes and EDGE_CAP arrows, far below what Mermaid refuses to draw (500 arrows, 50,000 characters). */"""
        drawn = diagram.blast_radius(large(changed=40, direct=300, far=900, folders=60, tops=60))
        self.assertLessEqual(len(nodes(drawn.text)), diagram.NODE_CAP)
        self.assertLessEqual(len(edges(drawn.text)), diagram.EDGE_CAP)
        self.assertLess(len(drawn.text), 20_000)
        self.assertLessEqual(diagram.EDGE_CAP, 500)


class DiagramEscapeTest(unittest.TestCase):
    def test_names_cannot_break_out_of_a_label_or_of_the_code_block(self) -> None:
        """/* REQ-ACTION-017: a label keeps letters, digits, space and `_ . / -`; every other character (quote, backtick, bracket, angle bracket, hash, semicolon, colon, a character outside ASCII, a line break) is written as a Mermaid entity code, so a file name cannot end the label, start an icon or a link, or close the code block; node identifiers are generated, never taken from names; a long label is shortened from its start. */"""
        self.assertEqual("src/a-b_c.d e", diagram.escape("src/a-b_c.d e"))
        self.assertEqual("a#34;b#96;c#60;d#62;#35;e#38;f#59;g#58;h", diagram.escape('a"b`c<d>#e&f;g:h'))
        self.assertEqual("app/#91;id#93;/#40;shop#41;/page", diagram.escape("app/[id]/(shop)/page"))
        self.assertEqual("caf#233; x y", diagram.escape("caf\u00e9\nx\ty"))
        self.assertEqual("...67890", diagram.shorten("1234567890", 8))
        self.assertEqual("123", diagram.shorten("123", 8))
        hostile = (
            Module('src/a"]; click c0 href "x`.ts', CHANGED),
            Module("end", DIRECT, ('src/a"]; click c0 href "x`.ts',)),
            Module("lib/```\nsubgraph", DIRECT, ('src/a"]; click c0 href "x`.ts',)),
        )
        text = diagram.blast_radius(hostile).text
        self.assertNotIn("`", text)
        layout, drawn = text.split("\n", 1)   # the first line is the fixed layout directive
        self.assertTrue(layout.startswith("%%{init: ") and layout.endswith("}%%"), layout)
        self.assertEqual(drawn.count('"'), 2 * (drawn.count("subgraph ") + len(nodes(drawn))))
        for line in text.splitlines():
            self.assertRegex(line, r'^(%%\{init: \{"flowchart": \{[\w": ,]+\}\}\}%%|flowchart RL|  subgraph \w+\["[\w ./#;-]*"\]|    \w+(\["|\("|\[\[")[\w ./#;-]*("\]|"\)|"\]\])|  end|  \w+ -\.?-> \w+|  classDef \w+ [\w:\- ]+|  class [\w,]+ \w+)$')
        self.assertIn('m0("end")', text)

    def test_long_labels_are_shortened(self) -> None:
        """/* REQ-ACTION-017: a label longer than LABEL_LIMIT keeps its end, after three dots. */"""
        name = "packages/app/" + "deep/" * 30 + "OrderList"
        text = diagram.blast_radius((Module("a.ts", CHANGED), Module(name, DIRECT, ("a.ts",)))).text
        label = text.split('m0("')[1].split('")')[0]
        self.assertEqual(diagram.LABEL_LIMIT, len(label))
        self.assertTrue(label.startswith("...") and label.endswith("deep/OrderList"))


class DiagramOrderTest(unittest.TestCase):
    def test_the_same_modules_in_any_order_give_the_same_text(self) -> None:
        """/* REQ-ACTION-018: the diagram does not depend on the order of its input: the same modules, shuffled, with their uses shuffled, give the same text byte for byte, at every collapse level. */"""
        modules = list(large(changed=6, direct=40, far=70, folders=7, tops=7)) + [Module(f"tests/test_{i}", DIRECT, ("core/f0.py",), True) for i in range(4)]
        for node_cap, edge_cap in ((1000, 1000), (60, 200), (50, 200), (30, 60), (20, 60), (12, 20), (8, 6)):
            expected = diagram.blast_radius(modules, node_cap=node_cap, edge_cap=edge_cap)
            shuffler = random.Random(7)
            for _round in range(5):
                shuffled = [Module(m.name, m.kind, tuple(shuffler.sample(m.uses, len(m.uses))), m.test) for m in modules]
                shuffler.shuffle(shuffled)
                self.assertEqual(expected, diagram.blast_radius(shuffled, node_cap=node_cap, edge_cap=edge_cap))


if __name__ == "__main__":
    unittest.main()
