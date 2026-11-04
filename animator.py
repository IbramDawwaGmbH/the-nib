"""Sprite-sheet loading and frame playback.

Two objects live here:

``SpriteSheet`` owns the pixels - it slices ``nib_sheet.png`` into per-state frames, scales
them up with nearest-neighbour so the pixel art stays crisp, and caches both the scaled
``QPixmap`` and a per-frame ``QRegion`` hit mask. The mask is exact because the art contract
forbids partial alpha: every pixel is either fully opaque or fully transparent.

``Animator`` owns the clock - it walks a state's frame list in real time. ``advance()`` takes a
*scaled* delta, which is how the walk cycle stays locked to the mascot's ground speed: the
behaviour layer slows the animation and the movement by the same factor, so stride length in
pixels-per-frame never changes and the feet never slide.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion, QTransform

log = logging.getLogger(__name__)

#: States synthesised at load time by mirroring another state, rather than stored in the sheet.
MIRRORED_STATES: dict[str, str] = {"walk_left": "walk_right"}

FALLBACK_STATE = "idle"

#: Colour re-mappings for the body tones. Only the four body tones are swapped: the eye/outline
#: black, the angry red and the light detail stay put, so expressions read the same on every skin.
_BODY = (0xD9, 0x55, 0x2E)
_HIGHLIGHT = (0xE8, 0x72, 0x4A)
_SHADOW = (0xB2, 0x3F, 0x1E)
_FEET = (0x8A, 0x3D, 0x22)

SKINS: dict[str, dict[tuple[int, int, int], tuple[int, int, int]]] = {
    "default": {},
    "ocean": {
        _BODY: (0x2E, 0x6F, 0xD9), _HIGHLIGHT: (0x4E, 0x8D, 0xEA),
        _SHADOW: (0x1F, 0x4E, 0xAE), _FEET: (0x1B, 0x3B, 0x7A),
    },
    "forest": {
        _BODY: (0x3E, 0x94, 0x55), _HIGHLIGHT: (0x5C, 0xB4, 0x72),
        _SHADOW: (0x2C, 0x6E, 0x3D), _FEET: (0x22, 0x53, 0x2E),
    },
    "grape": {
        _BODY: (0x8A, 0x4F, 0xC8), _HIGHLIGHT: (0xA5, 0x6F, 0xDE),
        _SHADOW: (0x6A, 0x3A, 0x9C), _FEET: (0x4E, 0x2A, 0x73),
    },
    "charcoal": {
        _BODY: (0x6E, 0x6E, 0x76), _HIGHLIGHT: (0x8C, 0x8C, 0x94),
        _SHADOW: (0x4E, 0x4E, 0x56), _FEET: (0x3A, 0x3A, 0x40),
    },
}


def available_skins() -> list[str]:
    return list(SKINS)


def _mirror(image: QImage) -> QImage:
    """Flip horizontally, exactly (no resampling)."""
    mirrored = getattr(image, "mirrored", None)
    if callable(mirrored):
        try:
            return mirrored(True, False)
        except TypeError:
            pass
    return image.transformed(QTransform().scale(-1.0, 1.0), Qt.FastTransformation)


class SpriteSheet:
    """A loaded sprite sheet plus its frame manifest."""

    def __init__(
        self,
        sheet_path: str | Path,
        manifest_path: str | Path,
        scale: int,
        *,
        costume: str | None = None,
        skin: str = "default",
    ) -> None:
        self.sheet_path = Path(sheet_path)
        self.manifest_path = Path(manifest_path)
        self.scale = max(1, int(scale))
        self.skin = skin if skin in SKINS else "default"
        self.costume = costume or None

        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"frame manifest not found: {self.manifest_path}")
        if not self.sheet_path.is_file():
            raise FileNotFoundError(f"sprite sheet not found: {self.sheet_path}")

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        self.frame_w = int(manifest["frame_w"])
        self.frame_h = int(manifest["frame_h"])
        self.cols = int(manifest["cols"])
        self.rows = int(manifest.get("rows", 0))
        self.baseline_offset = int(manifest.get("baseline_offset", 0))
        self.walk_step_px = max(1, int(manifest.get("walk_step_px", 3)))

        self._sheet = QImage(str(self.sheet_path))
        if self._sheet.isNull():
            raise ValueError(f"could not decode sprite sheet: {self.sheet_path}")
        self._sheet = self._sheet.convertToFormat(QImage.Format_ARGB32)

        expected = (self.frame_w * self.cols, self.frame_h * max(self.rows, 1))
        if self.rows and (self._sheet.width(), self._sheet.height()) != expected:
            log.warning(
                "sheet is %dx%d but manifest implies %dx%d",
                self._sheet.width(),
                self._sheet.height(),
                *expected,
            )

        # state -> list of (frame_index, duration_ms)
        self._states: dict[str, list[tuple[int, int]]] = {}
        self._loops: dict[str, bool] = {}
        for name, spec in manifest["states"].items():
            frames = [(int(i), max(1, int(ms))) for i, ms in spec["frames"]]
            if not frames:
                log.warning("state %r has no frames; skipping", name)
                continue
            self._states[name] = frames
            self._loops[name] = bool(spec.get("loop", True))

        if FALLBACK_STATE not in self._states:
            raise ValueError(f"manifest must define a {FALLBACK_STATE!r} state")

        for synthetic, source in MIRRORED_STATES.items():
            if source in self._states and synthetic not in self._states:
                self._states[synthetic] = list(self._states[source])
                self._loops[synthetic] = self._loops[source]

        # frame index -> {"head": [x, y], "eyes": [x, y]} in unscaled sprite coordinates
        self._anchors: dict[int, dict[str, list[int]]] = {}
        for key, value in (manifest.get("anchors") or {}).items():
            try:
                self._anchors[int(key)] = value
            except (TypeError, ValueError):
                continue

        self._costume_sheet: QImage | None = None
        self._costume_items: dict[str, dict] = {}
        self._costume_cell = 0
        #: costume index -> its lowest opaque row, so the floor check below is one scan
        #: per costume rather than one per frame.
        self._costume_lows: dict[int, int] = {}
        #: costume name -> its trimmed cell, for the wardrobe. See ``costume_thumbnail``.
        self._thumbnails: dict[str, QPixmap] = {}
        self._load_costumes()
        if self.costume and self.costume not in self._costume_items:
            log.warning("unknown costume %r; going bare-headed", self.costume)
            self.costume = None

        self._pixmaps: dict[tuple[str, int], QPixmap] = {}
        self._regions: dict[tuple[str, int], QRegion] = {}
        #: (state, index) -> (left, right, lift), scanned once per frame. See ``foot_print``.
        self._footprints: dict[tuple[str, int], tuple[int, int, int]] = {}
        self._warned_states: set[str] = set()

    # --- costumes -----------------------------------------------------------------

    def _load_costumes(self) -> None:
        """Costumes are optional - a missing sheet just means no hats are available."""
        manifest_path = self.sheet_path.parent / "costumes.json"
        if not manifest_path.is_file():
            return
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            image = QImage(str(self.sheet_path.parent / data.get("sheet", "costumes.png")))
            if image.isNull():
                raise ValueError("costume sheet could not be decoded")
            self._costume_sheet = image.convertToFormat(QImage.Format_ARGB32)
            self._costume_items = dict(data.get("items") or {})
            self._costume_cell = int(data.get("cell", 0))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("could not load costumes: %s", exc)
            self._costume_sheet = None
            self._costume_items = {}

    def _costume_lowest(self, item: dict) -> int:
        """Lowest opaque row within a costume's own cell - across all its frames, so a moving
        costume is held at one height rather than bobbing as its frames change. Scanned once per
        cell, then cached."""
        cell = self._costume_cell
        lowest = 0
        for index in self._costume_cells(item):
            cached = self._costume_lows.get(index)
            if cached is None:
                piece = self._costume_sheet.copy(index * cell, 0, cell, cell)
                cached = 0
                for y in range(cell - 1, -1, -1):
                    if any(piece.pixelColor(x, y).alpha() > 0 for x in range(cell)):
                        cached = y
                        break
                self._costume_lows[index] = cached
            lowest = max(lowest, cached)
        return lowest

    @staticmethod
    def _costume_cells(item: dict) -> list[int]:
        """The sheet columns of a costume's frames: just ``index`` for one that doesn't move."""
        frames = item.get("frames")
        if isinstance(frames, list) and frames:
            try:
                return [int(f) for f in frames]
            except (TypeError, ValueError):
                pass
        return [int(item["index"])]

    def available_costumes(self) -> list[str]:
        return list(self._costume_items)

    def costume_categories(self) -> dict[str, list[str]]:
        """Costume names grouped by their declared category, for a menu that has to hold fifty.

        Insertion-ordered both ways, so the sheet's own ordering is what the menu shows. A costume
        with no category lands under "Other" rather than vanishing - the same rule the settings
        window applies to a setting nobody assigned to a tab.
        """
        grouped: dict[str, list[str]] = {}
        for name, item in self._costume_items.items():
            grouped.setdefault(str(item.get("category") or "Other"), []).append(name)
        return grouped

    def costume_category(self, name: str) -> str:
        """The category costumes.json files ``name`` under - "Other" if it has none, "" if unknown."""
        item = self._costume_items.get(name)
        return str(item.get("category") or "Other") if item else ""

    def costume_thumbnail(self, name: str) -> QPixmap | None:
        """The costume's own cell of costumes.png, trimmed to its opaque pixels, unscaled - for the
        wardrobe. Cropped rather than rendered onto him: 53 sheet builds at ~7ms would hold the
        wheel up for a third of a second, and the crops measure ~1ms for all of them."""
        cached = self._thumbnails.get(name)
        if cached is not None:
            return cached
        item = self._costume_items.get(name)
        if item is None or self._costume_sheet is None:
            return None
        cell = self._costume_cell
        piece = QPixmap.fromImage(self._costume_sheet.copy(int(item["index"]) * cell, 0, cell, cell))
        box = QRegion(piece.mask()).boundingRect()
        if box.isEmpty():
            return None
        self._thumbnails[name] = piece.copy(box)
        return self._thumbnails[name]

    def anchors(self, frame_index: int) -> dict[str, list[int]]:
        return self._anchors.get(frame_index, {})

    def _apply_skin(self, image: QImage) -> None:
        """Remap the body tones in place. Cheap enough: 48x48, and the result is cached."""
        mapping = SKINS.get(self.skin) or {}
        if not mapping:
            return
        lookup = {QColor(*src).rgb(): QColor(*dst) for src, dst in mapping.items()}
        for y in range(image.height()):
            for x in range(image.width()):
                rgb = image.pixel(x, y)
                if rgb >> 24 == 0:
                    continue
                replacement = lookup.get(rgb | 0xFF000000)
                if replacement is not None:
                    image.setPixelColor(x, y, replacement)

    def _apply_costume(self, image: QImage, sheet_index: int, step: int = 0) -> QImage:
        """Composite the costume onto an unscaled frame, at that frame's own anchor.

        Returns the image to carry on with - normally the one passed in, but a ``behind`` costume
        needs a fresh canvas, since a backpack or a jetpack has to be painted *first* and the
        creature drawn over the top of it. Drawing it over him instead would put the straps on his
        chest.

        ``step`` is the frame's position in its animation: a moving costume (a propeller, a
        flame) shows its frame ``step`` of its own, so it moves as he does - a turn per frame of
        his idle sway, faster when he walks - with no clock of its own and nothing extra to redraw.
        A hat's shadow is drawn first, and only over pixels he already has (SourceAtop), so it
        darkens his head and never the air beside it; his alpha is untouched, and the click mask
        with it.
        """
        if not self.costume or self._costume_sheet is None:
            return image
        item = self._costume_items.get(self.costume)
        anchors = self._anchors.get(sheet_index)
        if not item or not anchors:
            return image
        target = anchors.get(item.get("kind", "head"))
        anchor = item.get("anchor") or [0, 0]
        if not target:
            # A costume anchored to a point this frame does not publish. Skipped rather than
            # guessed: a scarf stamped at a fallback position would float somewhere arbitrary.
            return image
        cell = self._costume_cell
        cells = self._costume_cells(item)
        column = cells[step % len(cells)]
        piece = self._costume_sheet.copy(column * cell, 0, cell, cell)
        x = int(target[0]) - int(anchor[0])
        y = int(target[1]) - int(anchor[1])

        # Nothing may reach below the row his soles rest on. The hanging costumes - a cape, an
        # apron hem, a scarf's tail - drop well past their own anchor (the cape by ten rows), so
        # no amount of clearance built into the anchor itself can hold them up; the constraint is
        # per-costume and only knowable once it has been placed.
        #
        # It has to hold or `foot_print` reads the wrong row, and the contact shadow ends up sized
        # to the width of the cape rather than to his stance.
        overhang = y + self._costume_lowest(item) - (self.frame_h - 1 - self.baseline_offset)
        if overhang > 0:
            y -= overhang

        if item.get("layer") == "behind":
            canvas = QImage(image.size(), QImage.Format_ARGB32)
            canvas.fill(0)
            painter = QPainter(canvas)
            painter.drawImage(x, y, piece)
            painter.drawImage(0, 0, image)
            painter.end()
            return canvas

        painter = QPainter(image)
        if item.get("shadow") and self._costume_sheet.height() >= 2 * cell:
            painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            painter.drawImage(x, y, self._costume_sheet.copy(column * cell, cell, cell, cell))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawImage(x, y, piece)
        painter.end()
        return image

    # --- geometry -----------------------------------------------------------------

    @property
    def sprite_w(self) -> int:
        return self.frame_w * self.scale

    @property
    def sprite_h(self) -> int:
        return self.frame_h * self.scale

    @property
    def sole_gap(self) -> int:
        """Scaled pixels between the soles of the feet and the bottom of the sprite image."""
        return self.baseline_offset * self.scale

    # --- state metadata -----------------------------------------------------------

    def states(self) -> list[str]:
        return sorted(self._states)

    def has_state(self, state: str) -> bool:
        return state in self._states

    def _resolve(self, state: str) -> str:
        if state in self._states:
            return state
        # Warn once per name: this is reached from the render path, so logging every time would
        # write a line every frame for as long as the state is requested.
        if state not in self._warned_states:
            self._warned_states.add(state)
            log.warning("unknown animation state %r; falling back to %r", state, FALLBACK_STATE)
        return FALLBACK_STATE

    def frame_count(self, state: str) -> int:
        return len(self._states[self._resolve(state)])

    def duration(self, state: str, index: int) -> int:
        frames = self._states[self._resolve(state)]
        return frames[index % len(frames)][1]

    def loops(self, state: str) -> bool:
        return self._loops[self._resolve(state)]

    def total_duration(self, state: str) -> int:
        return sum(ms for _, ms in self._states[self._resolve(state)])

    # --- pixels -------------------------------------------------------------------

    def _frame_image(self, state: str, index: int) -> QImage:
        frames = self._states[state]
        sheet_index = frames[index % len(frames)][0]
        row, col = divmod(sheet_index, self.cols)
        image = self._sheet.copy(
            col * self.frame_w, row * self.frame_h, self.frame_w, self.frame_h
        )
        # Recolour and dress him while the frame is still 48x48 and unmirrored: the costume then
        # flips with him for the left-facing walk, and the whole thing scales up crisply as one.
        self._apply_skin(image)
        # Returns the image to carry on with: a `behind` costume hands back a new canvas with the
        # piece painted under him, so the result has to be taken rather than assumed in place.
        image = self._apply_costume(image, sheet_index, index % len(frames))
        if state in MIRRORED_STATES:
            image = _mirror(image)
        if self.scale != 1:
            image = image.scaled(
                self.frame_w * self.scale,
                self.frame_h * self.scale,
                Qt.IgnoreAspectRatio,
                Qt.FastTransformation,  # nearest neighbour - never smooth pixel art
            )
        return image

    def pixmap(self, state: str, index: int) -> QPixmap:
        state = self._resolve(state)
        key = (state, index % len(self._states[state]))
        cached = self._pixmaps.get(key)
        if cached is None:
            cached = QPixmap.fromImage(self._frame_image(*key))
            self._pixmaps[key] = cached
        return cached

    def foot_print(self, state: str, index: int) -> tuple[int, int, int]:
        """Where this frame touches the ground: ``(left, right, lift)`` in scaled pixels.

        ``left``/``right`` span the lowest opaque row - his stance, which widens for the walk and
        narrows for the hop. ``lift`` is how far that row sits *above* where the soles normally
        rest, so a frame that is legitimately airborne reports a positive number.

        That is what lets the contact shadow behave correctly with no art knowledge anywhere else:
        it is read off the pixels, so the hop's mid-air frames shrink the shadow automatically and
        any future airborne frame does too, without a hard-coded list of exceptions.
        """
        state = self._resolve(state)
        key = (state, index % len(self._states[state]))
        cached = self._footprints.get(key)
        if cached is not None:
            return cached

        # Read off the click mask, which is already built and cached for this frame, rather than
        # scanning the pixels. The first version walked the image in Python - 48x48 pixelColor()
        # calls per frame - and measured at 26ms to cover all 154 frames, more than doubling the
        # 22ms it takes to build every pixmap and mask in the first place. The region already
        # knows where its lowest opaque row is; asking it costs two calls into Qt.
        region = self.mask_region(*key)
        if region.isEmpty():  # a wholly empty frame; nothing to cast a shadow
            self._footprints[key] = (0, 0, self.sprite_h)
            return self._footprints[key]

        box = region.boundingRect()
        lowest = box.bottom()
        row = region.intersected(QRegion(0, lowest, self.sprite_w, 1)).boundingRect()
        sole_row = self.sprite_h - 1 - self.sole_gap
        self._footprints[key] = (row.left(), row.right(), max(0, sole_row - lowest))
        return self._footprints[key]

    def mask_region(self, state: str, index: int) -> QRegion:
        """Per-pixel input/paint mask for a frame, so clicks pass through empty space."""
        state = self._resolve(state)
        key = (state, index % len(self._states[state]))
        cached = self._regions.get(key)
        if cached is None:
            bitmap = self.pixmap(*key).mask()
            cached = QRegion(bitmap) if not bitmap.isNull() else QRegion()
            self._regions[key] = cached
        return cached


class Animator:
    """Plays one state of a :class:`SpriteSheet` against a caller-supplied clock."""

    def __init__(self, sheet: SpriteSheet, state: str = FALLBACK_STATE) -> None:
        self._sheet = sheet
        self._state = sheet._resolve(state)
        self._index = 0
        self._elapsed = 0.0
        self._finished = False

    @property
    def sheet(self) -> SpriteSheet:
        return self._sheet

    @property
    def state(self) -> str:
        return self._state

    @property
    def frame_index(self) -> int:
        return self._index

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def pixmap(self) -> QPixmap:
        return self._sheet.pixmap(self._state, self._index)

    @property
    def mask_region(self) -> QRegion:
        return self._sheet.mask_region(self._state, self._index)

    @property
    def frame_duration(self) -> int:
        """Nominal duration of the frame on screen, in ms - the stride clock for walking."""
        return self._sheet.duration(self._state, self._index)

    @property
    def remaining_ms(self) -> float:
        """Time until the displayed frame changes. Drives the adaptive tick."""
        if self._finished:
            return float("inf")
        return max(0.0, self._sheet.duration(self._state, self._index) - self._elapsed)

    def set_state(self, state: str, *, restart: bool = True) -> None:
        resolved = self._sheet._resolve(state)
        if resolved == self._state and not restart:
            return
        self._state = resolved
        self._finished = False
        if restart:
            self._index = 0
            self._elapsed = 0.0
        else:
            self._index %= self._sheet.frame_count(resolved)

    def hold(self, state: str, index: int) -> None:
        """Pin one specific frame instead of playing the state on a clock.

        Used for the cursor-tracked eyes, where the *app* chooses which frame to show from the
        pointer's direction rather than time deciding it.
        """
        resolved = self._sheet._resolve(state)
        count = self._sheet.frame_count(resolved)
        self._state = resolved
        self._index = index % count
        self._elapsed = 0.0
        self._finished = False

    def advance(self, dt_ms: float) -> bool:
        """Advance by ``dt_ms`` (already scaled by the caller). True if the frame changed."""
        if self._finished or dt_ms <= 0:
            return False
        self._elapsed += dt_ms
        count = self._sheet.frame_count(self._state)
        looping = self._sheet.loops(self._state)
        changed = False
        # Guard against a pathological dt (e.g. the machine woke from sleep) spinning forever.
        for _ in range(256):
            duration = self._sheet.duration(self._state, self._index)
            if self._elapsed < duration:
                break
            if self._index + 1 >= count:
                if not looping:
                    self._finished = True
                    self._elapsed = float(duration)
                    break
                self._index = 0
            else:
                self._index += 1
            self._elapsed -= duration
            changed = True
        else:
            self._elapsed = 0.0
        return changed
