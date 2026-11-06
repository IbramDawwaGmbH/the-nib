"""The costume of the day: one for every date of the year, the same one on that date every year.

Not a timer, and that is the point. The rest of his dressing has a reason behind it - a beanie
because it is winter, a hard hat because the machine is pinned - and a costume that changed every
few hours for no reason would be the only arbitrary thing about him. The calendar is a reason you
can reason about: it is the fourteenth of March, so he is wearing the pi hat, and next year on the
fourteenth of March he will be again. Holidays and observances sit on their real dates; the days
between are themed on the season they fall in.

The table lives in ``assets/daily_costumes.json`` (written by ``tools/gen_daily_costumes.py``)
under ``"days"``, keyed "MM-DD" with all 366 of them, so the twenty-ninth of February has its own.
The art is on one sheet per month beside it, which ``animator.SpriteSheet`` loads only when a
costume from that month is actually worn.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def _assets_dir() -> Path:
    """Beside the source, or inside the bundle when frozen - the same rule as ``main``'s."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) / "assets" if base else Path(sys.executable).parent / "assets"
    return Path(__file__).resolve().parent / "assets"


MANIFEST = _assets_dir() / "daily_costumes.json"

#: path -> (mtime, {"MM-DD": name}, {name: title}). Read once; ``auto_costume`` asks every tick.
_cache: dict[Path, tuple[float, dict[str, str], dict[str, str]]] = {}


def _table(path: Path = MANIFEST) -> tuple[dict[str, str], dict[str, str]]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}, {}
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        days = {str(k): str(v) for k, v in (data.get("days") or {}).items()}
        titles = {str(name): str(item.get("title") or "")
                  for name, item in (data.get("items") or {}).items()}
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("could not read the costume calendar: %s", exc)
        days, titles = {}, {}
    _cache[path] = (mtime, days, titles)
    return days, titles


def day_key(when: date | str) -> str:
    """"MM-DD" for a date, or for the "YYYY-MM-DD" ``WorldState.date_key`` already carries."""
    if isinstance(when, str):
        return when[-5:]
    return f"{when.month:02d}-{when.day:02d}"


def costume_for(when: date | str, path: Path = MANIFEST) -> str:
    """The name of the costume for that date, or "" if there is no calendar to ask."""
    days, _titles = _table(path)
    return days.get(day_key(when), "")


def title_for(name: str, path: Path = MANIFEST) -> str:
    """What the day is called ("Pi Day"), or "" for a costume that is not one of the calendar's."""
    return _table(path)[1].get(name, "")


def all_days(path: Path = MANIFEST) -> dict[str, str]:
    """Every "MM-DD" -> costume name, in calendar order."""
    return dict(_table(path)[0])
