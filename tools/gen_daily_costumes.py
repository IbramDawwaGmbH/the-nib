"""Build the costume-of-the-day sheets and manifest from ``tools/daily_costumes/``.

Usage:
    python tools/gen_daily_costumes.py [--no-check]

Reads one spec per date (``MM-DD.json``, see ``tools/check_costume.py`` for the shape), checks
every one of them, and writes:

* ``assets/daily/costumes-MM.png`` - one sheet per month, laid out exactly like ``costumes.png``:
  row 0 is each day's first frame in date order, then the extra frames of the moving ones; row 1
  is the shadow each one casts, in the same column. Twelve small sheets rather than one 1000-cell
  strip, so wearing a June costume decodes June and nothing else.
* ``assets/daily_costumes.json`` - every costume (each naming its sheet) and the ``days`` table,
  "MM-DD" -> name, which ``costume_calendar`` reads.

The art is outlined and shaded by the same ``costume_pixels`` the main wardrobe goes through, so
a daily costume is indistinguishable in finish from a hand-placed one. Refuses to write anything
if a spec fails its checks, or if any of the 366 dates is missing - a calendar with a hole in it
would leave him bare-headed one day a year for no visible reason.
"""

from __future__ import annotations

import calendar
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from check_costume import Stage, casts_shadow, check, load_specs  # noqa: E402
from gen_sprites import COSTUME_CELL, COSTUME_SHADOW_ALPHA, costume_pixels, costume_shadow  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SPECS = Path(__file__).resolve().parent / "daily_costumes"
ASSETS = ROOT / "assets"
OUT_DIR = ASSETS / "daily"
MANIFEST = ASSETS / "daily_costumes.json"


def every_date() -> list[str]:
    """All 366 "MM-DD" keys, the twenty-ninth of February included (2024 is a leap year)."""
    return [f"{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, calendar.monthrange(2024, m)[1] + 1)]


def _patterns(spec: dict) -> list[list[str]]:
    patterns = [spec["pattern"]] + list(spec.get("frames") or [])
    width = max(len(r) for r in patterns[0])
    return [[row.ljust(width, ".") for row in p] for p in patterns]


def render_month(month: int, specs: list[dict]) -> tuple[Image.Image, dict]:
    cell = COSTUME_CELL
    firsts, extra = [], []
    for spec in specs:
        frames = _patterns(spec)
        firsts.append((spec, frames[0]))
        extra.extend((spec, f) for f in frames[1:])
    columns = firsts + extra
    sheet = Image.new("RGBA", (cell * len(columns), cell * 2), (0, 0, 0, 0))
    pixels = sheet.load()
    frames_of: dict[str, list[int]] = {}
    for column, (spec, pattern) in enumerate(columns):
        frames_of.setdefault(spec["name"], []).append(column)
        ox = column * cell
        for y, row in enumerate(costume_pixels(pattern, shade=spec.get("shade", True) is not False)):
            for x, colour in enumerate(row):
                if colour is not None:
                    pixels[ox + x, y] = colour
        if casts_shadow(spec):
            for y, row in enumerate(costume_shadow(pattern)):
                for x, cast in enumerate(row):
                    if cast and x < cell and y < cell:
                        pixels[ox + x, cell + y] = (0, 0, 0, COSTUME_SHADOW_ALPHA)
    sheet_name = f"daily/costumes-{month:02d}.png"
    items = {}
    for spec in specs:
        frames = frames_of[spec["name"]]
        item = {
            "index": frames[0], "kind": spec["kind"],
            # the outline added a ring, so the attach point moves with it
            "anchor": [spec["anchor"][0] + 1, spec["anchor"][1] + 1],
            "layer": spec.get("layer", "over"),
            # What it IS, which is what his opinion of it falls back to; ``group`` is where the
            # menus file it.
            "category": spec["category"],
            "group": f"Calendar/{calendar.month_name[month]}",
            "sheet": sheet_name, "date": spec["date"], "title": spec["title"],
            "frames": frames,
        }
        if casts_shadow(spec):
            item["shadow"] = True
        items[spec["name"]] = item
    return sheet, items


def main(argv: list[str]) -> int:
    specs = load_specs([SPECS])
    if "--no-check" not in argv:
        report = check(specs, Stage())
        failed = {p: r["problems"] for p, r in report.items() if r["problems"]}
        if failed:
            for path, problems in failed.items():
                print(f"FAIL {Path(path).name}: " + "; ".join(problems))
            print(f"\n{len(failed)} spec(s) failed; nothing written")
            return 1
    by_date = {spec["date"]: spec for _path, spec in specs}
    missing = [d for d in every_date() if d not in by_date]
    if missing:
        print(f"{len(missing)} date(s) have no costume: {', '.join(missing[:12])}"
              + (" ..." if len(missing) > 12 else ""))
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items: dict[str, dict] = {}
    for month in range(1, 13):
        month_specs = [by_date[d] for d in every_date() if d.startswith(f"{month:02d}-")]
        sheet, month_items = render_month(month, month_specs)
        sheet.save(OUT_DIR / f"costumes-{month:02d}.png", optimize=True)
        items.update(month_items)
    manifest = {
        "cell": COSTUME_CELL,
        "days": {d: by_date[d]["name"] for d in every_date()},
        "items": items,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    frames = sum(len(i["frames"]) for i in items.values())
    print(f"wrote {len(items)} costumes ({frames} frames) on 12 sheets, and {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
