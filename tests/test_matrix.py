"""The control matrix of docs/controls: a page and the same rows as a reference mapping.

The rows expected here are written by hand: the identifier and the title of every row, per regime. The page
and the mapping file must both hold exactly these, so a row added to one and not to the other, or a title
changed in one place, fails here.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_controls as wc  # noqa: E402

PAGE = (ROOT / "docs" / "controls" / "matrix.md").read_text(encoding="utf-8")
MAPPING_PATH = "docs/controls/matrix.controls.yaml"
MAPPING_BYTES = (ROOT / MAPPING_PATH).read_bytes().replace(b"\r\n", b"\n")
RECORD = json.loads((ROOT / "tests" / "fixtures" / "controls" / "record.json").read_text(encoding="ascii"))
DAY = "2026-01-02"      # the day of the fixture record

# (control id of the mapping file, framework, edition, identifier of the page, title of the page)
ROWS = (
    ("ISO27001-5.3", "ISO/IEC 27001", "2022", "5.3", "Segregation of duties"),
    ("ISO27001-5.33", "ISO/IEC 27001", "2022", "5.33", "Protection of records"),
    ("ISO27001-8.4", "ISO/IEC 27001", "2022", "8.4", "Access to source code"),
    ("ISO27001-8.25", "ISO/IEC 27001", "2022", "8.25", "Secure development life cycle"),
    ("ISO27001-8.27", "ISO/IEC 27001", "2022", "8.27", "Secure system architecture and engineering principles"),
    ("ISO27001-8.29", "ISO/IEC 27001", "2022", "8.29", "Security testing in development and acceptance"),
    ("ISO27001-8.32", "ISO/IEC 27001", "2022", "8.32", "Change management"),
    ("ISO42001-A.4.2", "ISO/IEC 42001", "2023", "A.4.2", "Resource documentation"),
    ("ISO42001-A.6.2.3", "ISO/IEC 42001", "2023", "A.6.2.3", "Documentation of AI system design and development"),
    ("ISO42001-A.6.2.4", "ISO/IEC 42001", "2023", "A.6.2.4", "AI system verification and validation"),
    ("ISO42001-A.6.2.5", "ISO/IEC 42001", "2023", "A.6.2.5", "AI system deployment"),
    ("TSC-CC4.1", "AICPA Trust Services Criteria", "2017, with revised points of focus 2022", "CC4.1", "Monitoring Activities"),
    ("TSC-CC5.2", "AICPA Trust Services Criteria", "2017, with revised points of focus 2022", "CC5.2", "Control Activities"),
    ("TSC-CC8.1", "AICPA Trust Services Criteria", "2017, with revised points of focus 2022", "CC8.1", "Change Management"),
)
_ROW = re.compile(r"^\| ([^|]+?) \| ([^|]+?) \|(.*)\|$", re.M)


def page_rows() -> list[tuple[str, str, list[str]]]:
    """(identifier, title, the other cells) of every row of a table of the page, the heading rows left out."""
    found = []
    for identifier, title, rest in _ROW.findall(PAGE):
        if identifier in ("Control", "Criterion") or set(identifier) <= set("-"):
            continue
        found.append((identifier, title, [cell.strip() for cell in rest.split(" | ")]))
    return found


class MatrixTest(unittest.TestCase):
    def test_the_page_and_the_reference_mapping_hold_the_same_rows(self) -> None:
        """/* REQ-ACTION-235: the control matrix page (docs/controls/matrix.md) and the reference mapping next to it (matrix.controls.yaml) hold the same fourteen rows, written out here by hand: the page lists each identifier with its title once, and the mapping, which the reader of a control mapping accepts without an error, has one control per row whose title is the row's and whose one reference names the framework, the edition and the identifier with the title */"""
        self.assertEqual([(identifier, title) for _id, _framework, _edition, identifier, title in ROWS],
                         [(identifier, title) for identifier, title, _cells in page_rows()])
        found = wc.read(MAPPING_BYTES, MAPPING_PATH, "base")
        self.assertEqual((), found.errors)
        self.assertEqual([control_id for control_id, *_rest in ROWS], [control.id for control in found.controls])
        for control, (_id, framework, edition, identifier, title) in zip(found.controls, ROWS):
            self.assertEqual(title, control.title, control.id)
            self.assertEqual(1, len(control.references), control.id)
            reference = control.references[0]
            self.assertEqual((framework, edition), (reference.framework, reference.edition), control.id)
            prefix = "Annex A " if framework.startswith("ISO") else ""
            self.assertEqual(f"{prefix}{identifier} {title}", reference.reference, control.id)

    def test_every_row_of_the_page_states_its_evidence_what_is_still_needed_and_its_limit(self) -> None:
        """/* REQ-ACTION-236: every row of the control matrix page fills all of its columns: why the control concerns a software team, the evidence a run gives with at least one field name in code marks, what is still needed from elsewhere, the limit with the words "Does not" or "not", and the source it was checked against with the day; and the page carries its status line, so a draft cannot be read as a reviewed page */"""
        rows = page_rows()
        self.assertEqual(len(ROWS), len(rows))
        for identifier, _title, cells in rows:
            self.assertEqual(5, len(cells), identifier)
            why, evidence, needed, limit, source = cells
            for name, cell in (("why", why), ("evidence", evidence), ("needed", needed), ("limit", limit)):
                self.assertGreater(len(cell), 30, (identifier, name))
            self.assertRegex(evidence, r"`[a-z][a-z0-9.-]*`", identifier)
            self.assertRegex(limit, r"\b[Dd]oes not\b|\bnot\b", identifier)
            self.assertRegex(source, r"^\[S\d\]( \[S\d\])*, \d{4}-\d{2}-\d{2}$", identifier)
        cited = set(re.findall(r"\[S\d\]", "".join(cells[4] for _identifier, _title, cells in rows)))
        listed = set(re.findall(r"^- (\[S\d\])", PAGE, re.M))
        self.assertTrue(cited and cited <= listed, (cited, listed))
        self.assertRegex(PAGE, r"(?m)^Status: (DRAFT, not reviewed|Reviewed)\.$")

    def test_the_reference_mapping_supports_no_claim(self) -> None:
        """/* REQ-ACTION-237: the reference mapping of the control matrix is an example and supports no claim: every control in it is a draft, and evaluated against a record whose verdicts all hold, even as the mapping of the base commit, no control is `supported`: each is not-supported with `mapping-draft` */"""
        found = wc.read(MAPPING_BYTES, MAPPING_PATH, "base", "1" * 40)
        self.assertEqual(["draft"] * len(ROWS), [control.status for control in found.controls])
        said = wc.evaluate(found, RECORD, DAY)
        self.assertEqual(len(ROWS), len(said["controls"]))
        for control in said["controls"]:
            self.assertEqual("not-supported", control["state"], control["id"])
            self.assertIn("mapping-draft", [reason["code"] for reason in control["reasons"]], control["id"])


if __name__ == "__main__":
    unittest.main()
