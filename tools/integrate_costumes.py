"""Fold reviewed costume batches into ``gen_sprites.py``'s ``COSTUMES`` table.

Usage:
    python tools/integrate_costumes.py <dir-of-batch-json> [--dry-run]

Each JSON is ``{name: {"kind", "anchor", "pattern", "layer", "category"}}``. They are validated
against the same rules the renderer enforces, emitted as dict entries, and spliced in just before
the table's closing brace.

**Idempotent.** Everything between the generated markers is replaced wholesale on each run, so
re-integrating after a fix does not leave two copies of a hat. The hand-written costumes above the
marker are never touched.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_sprites import COSTUME_CELL, COSTUME_PALETTE, outline_costume  # noqa: E402

SOURCE = Path(__file__).parent / "gen_sprites.py"
BEGIN = "    # --- BEGIN GENERATED COSTUMES ---"
END = "    # --- END GENERATED COSTUMES ---"

VALID_KINDS = {"head", "eyes", "neck", "body"}
VALID_LAYERS = {"over", "behind"}


def validate(name: str, item: dict) -> list[str]:
    """Everything that would make this costume render wrong, as a list of complaints."""
    problems = []
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        problems.append(f"{name}: name must be lowercase letters, digits and underscores")
    kind = item.get("kind")
    if kind not in VALID_KINDS:
        problems.append(f"{name}: kind {kind!r} is not one of {sorted(VALID_KINDS)}")
    if item.get("layer", "over") not in VALID_LAYERS:
        problems.append(f"{name}: layer {item.get('layer')!r} is not one of {sorted(VALID_LAYERS)}")
    anchor = item.get("anchor")
    if not (isinstance(anchor, (list, tuple)) and len(anchor) == 2
            and all(isinstance(v, int) for v in anchor)):
        problems.append(f"{name}: anchor must be two integers, got {anchor!r}")
    pattern = item.get("pattern")
    if not (isinstance(pattern, list) and pattern and all(isinstance(r, str) for r in pattern)):
        problems.append(f"{name}: pattern must be a non-empty list of strings")
        return problems
    bad = {ch for row in pattern for ch in row} - set(COSTUME_PALETTE)
    if bad:
        problems.append(f"{name}: unknown palette characters {sorted(bad)}")
        return problems
    if not any(ch != "." for row in pattern for ch in row):
        problems.append(f"{name}: the pattern is entirely transparent")
        return problems
    outlined = outline_costume(pattern)
    width, height = max(len(r) for r in outlined), len(outlined)
    if width > COSTUME_CELL or height > COSTUME_CELL:
        problems.append(
            f"{name}: {width}x{height} once outlined, over the {COSTUME_CELL}px cell"
        )
    return problems


def emit(name: str, item: dict) -> str:
    rows = ",\n".join(f'            {row!r}' for row in item["pattern"])
    return (
        f'    "{name}": {{\n'
        f'        "kind": "{item["kind"]}",\n'
        f'        "anchor": [{item["anchor"][0]}, {item["anchor"][1]}],\n'
        f'        "layer": "{item.get("layer", "over")}",\n'
        f'        "category": "{item.get("category", "Other")}",\n'
        f'        "pattern": [\n{rows},\n'
        f'        ],\n'
        f'    }},\n'
    )


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    dry = "--dry-run" in sys.argv

    collected: dict[str, dict] = {}
    clashes = []
    for path in sorted(folder.glob("*.json")):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"  skipping {path.name}: {exc}")
            continue
        if not isinstance(batch, dict):
            continue
        for name, item in batch.items():
            if not isinstance(item, dict) or "pattern" not in item:
                continue
            if name in collected:
                clashes.append(f"{name} (also in an earlier batch)")
            collected[name] = item
        print(f"  {path.name}: {len(batch)} costume(s)")

    problems = clashes[:]
    for name, item in collected.items():
        problems += validate(name, item)

    source = SOURCE.read_text(encoding="utf-8")
    existing = set(re.findall(r'^    "([a-z0-9_]+)": \(', source, re.M))
    for name in collected:
        if name in existing:
            problems.append(f"{name}: clashes with a hand-written costume of the same name")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print("  " + p)
        return 1

    block = BEGIN + "\n" + "".join(emit(n, collected[n]) for n in collected) + END
    if BEGIN in source:
        source = re.sub(
            re.escape(BEGIN) + r".*?" + re.escape(END), lambda _m: block, source, flags=re.S
        )
    else:
        # Splice in just above the table's closing brace, which is the line before
        # ``def outline_costume``.
        marker = "\n}\n\n\ndef outline_costume"
        if marker not in source:
            print("could not find the end of the COSTUMES table")
            return 1
        source = source.replace(marker, "\n" + block + "\n}\n\n\ndef outline_costume", 1)

    print(f"\n{len(collected)} costume(s) ready to integrate")
    if dry:
        print("(dry run - nothing written)")
        return 0
    SOURCE.write_text(source, encoding="utf-8")
    print(f"wrote {SOURCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
