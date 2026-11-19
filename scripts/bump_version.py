#!/usr/bin/env python3
"""Set the wingbox release the action installs by default.

    python scripts/bump_version.py 0.8.0     that version
    python scripts/bump_version.py           the newest wingbox release on PyPI
    python scripts/bump_version.py --show    print the current default and change nothing

Rewrites the default of the `version` input in action.yml, the version named in README.md and the
release the GitLab job installs (gitlab/wingbox.gitlab-ci.yml).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_ACTION_DEFAULT = re.compile(r'(  version:\n    description: [^\n]+\n    default: ")([^"]+)(")')
_README = re.compile(r"(wingbox )\d+\.\d+\.\d+( from PyPI)")
_TEMPLATE = re.compile(r'(WINGBOX_VERSION: ")\d+\.\d+\.\d+(")')


def with_version(action_text: str, readme_text: str, version: str) -> tuple[str, str]:
    """action.yml and README.md with version as the default release."""
    action, found = _ACTION_DEFAULT.subn(rf"\g<1>{version}\g<3>", action_text)
    if found != 1:
        raise ValueError("action.yml: the default of the version input was not found")
    return action, _README.sub(rf"\g<1>{version}\g<2>", readme_text)


def newest_release() -> str:
    with urllib.request.urlopen("https://pypi.org/pypi/wingbox/json", timeout=30) as response:
        return json.load(response)["info"]["version"]


def current(action_text: str) -> str:
    """The default of the version input in action.yml."""
    found = _ACTION_DEFAULT.search(action_text)
    if found is None:
        raise ValueError("action.yml: the default of the version input was not found")
    return found.group(2)


def main(args: list[str]) -> int:
    if args == ["--show"]:
        print(current((ROOT / "action.yml").read_text(encoding="utf-8")))
        return 0
    version = args[0] if args else newest_release()
    if not VERSION.match(version):
        print(f"not a version: {version}", file=sys.stderr)
        return 2
    action, readme = ROOT / "action.yml", ROOT / "README.md"
    new_action, new_readme = with_version(action.read_text(encoding="utf-8"), readme.read_text(encoding="utf-8"), version)
    action.write_text(new_action, encoding="utf-8", newline="\n")
    readme.write_text(new_readme, encoding="utf-8", newline="\n")
    # The GitLab job installs the same release, and its README example names it.
    for template in sorted((ROOT / "gitlab").glob("*.gitlab-ci.yml")):
        template.write_text(_TEMPLATE.sub(rf'\g<1>{version}\g<2>', template.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    readme.write_text(_TEMPLATE.sub(rf'\g<1>{version}\g<2>', readme.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    print(f"default wingbox release: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
