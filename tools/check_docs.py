"""Check the documentation against the code, so the two cannot drift apart.

Usage:
    python tools/check_docs.py

Three things get checked, and each of them has silently broken at least once:

1.  Every key in ``config.DEFAULTS`` has a row in the settings reference, and every documented row
    names a real key. A setting nobody documented is a setting nobody can find; a documented one
    that no longer exists is worse, because somebody will try to set it.
2.  Every documented default is the *actual* default.
3.  Every internal ``](#anchor)`` link resolves to a heading that exists.

Exits non-zero and prints every problem, so one run tells you everything to fix.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DEFAULTS  # noqa: E402

#: Where the settings table lives, and every other page that may contain internal links.
SETTINGS_DOC = ROOT / "docs" / "SETTINGS.md"
DOC_GLOBS = ("README.md", "CONTRIBUTING.md", "docs/*.md")


def as_text(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (str, list, dict)):
        return json.dumps(value)
    return str(value)


def main() -> int:
    problems: list[str] = []

    if not SETTINGS_DOC.is_file():
        print(f"missing {SETTINGS_DOC.relative_to(ROOT)}")
        return 1
    settings = SETTINGS_DOC.read_text(encoding="utf-8")

    rows = re.findall(r"^\| `([^`]+)`(?: / `([^`]+)`)? \| `([^`]+)` \|", settings, re.M)
    documented = {a for a, b, _d in rows} | {b for _a, b, _d in rows if b}

    missing = sorted(set(DEFAULTS) - documented)
    phantom = sorted(documented - set(DEFAULTS))
    problems += [f"undocumented setting: {k}" for k in missing]
    problems += [f"documented setting that does not exist: {k}" for k in phantom]

    for key, _second, shown in rows:
        if key in DEFAULTS and as_text(DEFAULTS[key]) != shown:
            problems.append(
                f"{key}: documented default `{shown}` but the code says `{as_text(DEFAULTS[key])}`"
            )

    for pattern in DOC_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            anchors = {
                re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
                for h in re.findall(r"^#{1,6} (.+)$", text, re.M)
            }
            for target in re.findall(r"\]\(#([a-z0-9-]+)\)", text):
                if target not in anchors:
                    problems.append(f"{path.name}: broken link to #{target}")

    print(f"{len(DEFAULTS)} settings, {len(documented)} documented")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print("  " + problem)
        return 1
    print("docs and code agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
