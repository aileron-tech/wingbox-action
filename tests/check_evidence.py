#!/usr/bin/env python3
"""What an evidence record must say, and its hand-over as examples/evidence-handover.yml does it.

    python tests/check_evidence.py RECORD [--evidence WORD] [--outcome WORD] [--reason TEXT] [--hand-over]

RECORD is the path of wingbox-evidence.json. --evidence is the expected `tests.evidence`, --outcome the
expected `tests.step-outcome`, --reason a reason the result must hold. --hand-over runs the command of the
hand-over step of the example against a stand-in server on this machine and checks that the server got the
bytes of the record (needs bash and curl). Used by the test workflow after it ran the action with a test
step that failed, and by the local tests.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "evidence-handover.yml"
TOKEN = "t0ken"


def problems(record: dict, evidence: Optional[str] = None, outcome: Optional[str] = None, reason: Optional[str] = None) -> list[str]:
    """What the record says other than expected."""
    found = []
    tests = record.get("tests", {})
    if evidence is not None and tests.get("evidence") != evidence:
        found.append(f"tests.evidence is {tests.get('evidence')!r}, expected {evidence!r}")
    if outcome is not None and tests.get("step-outcome") != outcome:
        found.append(f"tests.step-outcome is {tests.get('step-outcome')!r}, expected {outcome!r}")
    if reason is not None and reason not in record.get("result", {}).get("reasons", []):
        found.append(f"result.reasons is {record.get('result', {}).get('reasons')!r}, expected to hold {reason!r}")
    return found


def hand_over_command() -> str:
    """The command of the hand-over step of the example, as written."""
    return EXAMPLE.read_text(encoding="utf-8").split("\n  hand-over:\n")[1].split("run: |\n", 1)[1].strip()


def hand_over(folder: str) -> list[str]:
    """Run the hand-over command in folder (which holds wingbox-evidence.json) against a stand-in server;
    what went wrong, or nothing."""
    seen: list[tuple[str, str, bytes]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.headers.get("Authorization", ""), self.headers.get("Content-Type", ""), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "EVIDENCE_TOKEN": TOKEN, "EVIDENCE_URL": f"http://127.0.0.1:{server.server_port}/evidence"}
        done = subprocess.run(["bash", "-c", hand_over_command()], cwd=folder, env=env, capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
        server.server_close()
    if done.returncode != 0:
        return [f"the hand-over command ended with {done.returncode}: {done.stderr.strip()}"]
    sent = (Path(folder) / "wingbox-evidence.json").read_bytes()
    return [] if seen == [(f"Bearer {TOKEN}", "application/json", sent)] else [f"the server got {len(seen)} request(s), not the record once"]


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check_evidence.py", description="Check an evidence record and its hand-over.")
    parser.add_argument("record")
    parser.add_argument("--evidence")
    parser.add_argument("--outcome")
    parser.add_argument("--reason")
    parser.add_argument("--hand-over", action="store_true")
    chosen = parser.parse_args(args)
    path = Path(chosen.record)
    if not path.is_file():
        print(f"no record at {path}", file=sys.stderr)
        return 1
    record = json.loads(path.read_text(encoding="utf-8"))
    found = problems(record, chosen.evidence, chosen.outcome, chosen.reason)
    if chosen.hand_over:
        found += hand_over(str(path.parent))
    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        print(json.dumps(record, indent=2), file=sys.stderr)
        return 1
    print("record as expected" + (", and handed over" if chosen.hand_over else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
