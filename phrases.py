"""Lines from a table: shuffle bags, and the phrase files that fill them.

Moved here out of ``guard_scene.py`` so that the guard's running commentary and the lines Nib
says on the desktop (``voice.py``) share one loader and one bag, without the voice having to import
the guard's painting code - and Qt with it. Nothing in this module draws, plays or decides *when*
to say anything; it only answers "which line next?".

``PhraseBag``
    A shuffle bag per category, so a line never repeats until every other line in its category has
    been shown.

``load_phrases``
    The built-in table from ``assets/<filename>``, with the user's own file of the same name in the
    settings folder merged in - never replacing - and a one-line fallback per category for when the
    asset is missing or broken. Every caller passes its own file name and fallback table.

Qt-free, like ``mood.py`` and ``memory.py``. Every line is local text someone wrote: nothing here
is generated or fetched.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class _KeepUnknown(dict):
    """``format_map`` helper: an unknown ``{placeholder}`` in a user's line stays as written.

    A hand-edited phrase file is exactly where a stray brace turns up, and a KeyError in the
    paint path would take the whole overlay down over a typo.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format(text: str, fmt: dict[str, Any]) -> str:
    try:
        return text.format_map(_KeepUnknown(fmt))
    except (ValueError, IndexError, AttributeError):  # "{", "{0}", "{a.b}" ...
        return text


class PhraseBag:
    """A shuffle bag per category: no line repeats until the whole category has been shown.

    Refills reshuffle, and if the refill would put the line just shown first, it is swapped away -
    otherwise the bag boundary is exactly where a back-to-back repeat sneaks in.
    """

    def __init__(self, phrases: dict[str, list[str]], rng: random.Random) -> None:
        self._phrases = {k: list(v) for k, v in phrases.items() if v}
        self._rng = rng
        self._bags: dict[str, list[str]] = {}
        self._last: dict[str, str] = {}

    def categories(self) -> list[str]:
        return list(self._phrases)

    def next(self, category: str, **fmt: Any) -> str:
        """The next line for ``category``, formatted. Unknown category -> ``idle``; none -> ``""``."""
        pool = self._phrases.get(category)
        if not pool:
            category = "idle"
            pool = self._phrases.get(category)
            if not pool:
                return ""
        bag = self._bags.get(category)
        if not bag:
            bag = list(pool)
            self._rng.shuffle(bag)
            # bag.pop() takes from the end, so the end is "first out".
            if len(bag) > 1 and bag[-1] == self._last.get(category):
                bag[-1], bag[0] = bag[0], bag[-1]
            self._bags[category] = bag
        line = bag.pop()
        self._last[category] = line
        return _format(line, fmt)


def _read_phrase_file(path: Path) -> dict[str, list[str]]:
    """One phrase file -> {category: [lines]}. Keys starting with ``_`` are comments."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top level must be an object")
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            log.warning("%s: category %r is not a list; skipped", path.name, key)
            continue
        out[key] = [s.strip() for s in value if isinstance(s, str) and s.strip()]
    return out


def load_phrases(assets_dir: Path, user_dir: Path | None, *, filename: str,
                 builtin: dict[str, list[str]]) -> dict[str, list[str]]:
    """Built-in lines plus the user's own, merged per category (deduplicated, order kept).

    ``filename`` is looked for in ``assets_dir`` (the shipped table) and then in ``user_dir`` (the
    user's additions); ``builtin`` is the fallback line per category for anything the shipped table
    lacks. A missing or malformed file is logged and skipped - the guard must come up, and Nib must
    start, even if someone's custom phrase file has a trailing comma in it.
    """
    merged: dict[str, list[str]] = {}

    def merge(src: dict[str, list[str]]) -> None:
        for category, lines in src.items():
            bucket = merged.setdefault(category, [])
            for line in lines:
                if line not in bucket:
                    bucket.append(line)

    shipped = Path(assets_dir) / filename
    try:
        merge(_read_phrase_file(shipped))
    except (OSError, ValueError) as exc:
        log.warning("could not read %s (%s); using the built-in fallback lines", shipped, exc)
    # Any category the asset lacks still gets its fallback line, so no reaction is ever silent.
    for category, lines in builtin.items():
        if not merged.get(category):
            merged[category] = list(lines)

    if user_dir is not None:
        custom = Path(user_dir) / filename
        if custom.is_file():
            try:
                merge(_read_phrase_file(custom))
                log.info("merged custom phrases from %s", custom)
            except (OSError, ValueError) as exc:
                log.warning("ignoring %s: %s", custom, exc)
    return {k: v for k, v in merged.items() if v}
