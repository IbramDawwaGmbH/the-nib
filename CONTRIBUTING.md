# Contributing

Thanks for looking. This is a small Windows desktop app with a deliberately opinionated codebase —
this file is the short version of those opinions so a pull request doesn't surprise anybody.

---

## Getting set up

```bash
git clone https://github.com/IbramDawwaGmbH/the-nib.git
cd the-nib
pip install -r requirements.txt
python main.py
```

Run with `python` rather than `pythonw` while developing — you get a console. Diagnostics still go
to `%APPDATA%\NibMascot\nib.log` either way.

```bash
python tests/run_all.py            # 19 suites, about 16 seconds
python tests/run_all.py --rates    # plus the slow behavioural rate measurements
```

**Run the suites before opening a PR.** They are fast and they encode nearly every non-obvious
decision in the project.

---

## Branching

`main` is always releasable. Everything else is a short-lived branch off it, merged by pull request
and deleted afterwards. No `develop`, no release branches — the project is far too small for
GitFlow, and long-lived branches would rot against the sprite sheet.

| Prefix | For | Example |
| --- | --- | --- |
| `feat/` | A new behaviour, costume or feature | `feat/umbrella-costume` |
| `fix/` | A bug | `fix/hat-anchor-on-pushups` |
| `art/` | Sprite or costume work only | `art/redraw-autumn-leaf` |
| `docs/` | Documentation | `docs/settings-reference` |
| `perf/` | Making something cheaper | `perf/cache-frame-footprints` |
| `chore/` | Tooling, CI, dependencies | `chore/bump-pyside` |
| `test/` | Tests only | `test/costume-floor-rule` |

Lowercase, hyphen-separated, and describe the **outcome** rather than the file you touched:
`fix/hat-anchor-on-pushups`, not `fix/gen-sprites`.

---

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/). The type matches your branch prefix:

```
feat(costumes): add an umbrella that opens when he peeks
fix(anchors): keep the head anchor off his raised arm
docs(settings): document auto_costumes
perf(shadow): stop rebuilding the click mask on every alpha change
```

Body optional, but if the change is non-obvious, **say what you measured**. The commit log for this
project is full of numbers, and that is on purpose:

```
fix(anchors): keep the head anchor off his raised arm

It was the centre of the topmost solid row, which is his hand whenever an
arm is raised: 24 of 154 frames put every hat out on his wrist. Now walks
down to the first row with a 12px contiguous run. 24 frames -> 2, and both
survivors are `tumble` where he is genuinely on his side.
```

---

## Releases

Semantic versioning, tagged on `main`. Bump `version.py` and turn `## [Unreleased]` in
`CHANGELOG.md` into the version's own section first: the release build refuses a tag that doesn't
match `version.py`, and the suites check it matches the changelog.

```bash
git tag -a v1.2.0 -m "v1.2.0"
git push origin v1.2.0
```

- **patch** — a fix, no behaviour change anyone would notice
- **minor** — a new behaviour, costume or setting; existing configs keep working
- **major** — a setting changes *meaning* (which also needs a `_MIGRATIONS` entry in `config.py`)

If the change touched `ocr.py`, `nib.spec`, `vendor/tesseract/`, or bumped the `winrt-*` packages
in `requirements.txt`, actually build and run the bundled exe before tagging
(`pyinstaller nib.spec`, then drop a real image with Shift held on the result) rather than trusting
that the source-tree suite passing means the packaged one still works. `winrt` is a namespace
package PyInstaller cannot fully trace on its own — the `hiddenimports` list in `nib.spec` exists
because a real build silently broke without it, and a future `winrt` release changing its internal
layout could silently break it again. `python tests/run_all.py` never catches this class of bug:
every suite runs against source, never against what a user's install actually receives. If you're
bumping the vendored Tesseract itself, use `tools/vendor_tesseract.py` rather than copying files by
hand - see its docstring - and do the same real build+run afterwards; a different upstream build
could change its DLL dependency closure in a way only that catches.

---

## The rules that aren't negotiable

These are load-bearing. Each one exists because breaking it caused a real bug.

### 1. He never moves another application's window

Not nudged, not resized, not restacked. He reads rectangles and reacts to them; that is all.
`tests/smoke_play.py` greps `behavior.py` for every Win32 call that could and fails if one appears.

### 2. No partial alpha in the sprite art

The per-pixel click mask is derived from the alpha channel, so a soft edge makes his hit region
ragged and he starts swallowing clicks meant for the taskbar. Every pixel is fully opaque or fully
transparent. `tools/validate_sheet.py` enforces it.

### 3. His feet stay on the baseline

Every frame's lowest opaque row is the sole line, with the hop's mid-air frames as the only
exception. Break it and he visibly jitters up and down as states change. Also enforced by the
validator, and by `tests/smoke_costumes.py` for the dressed sprite.

### 4. Nothing reads a window title, keystroke, or file

App reactions use the **executable name** only. The away timer uses the system idle clock. The
overhead-window check reads rectangles and class names. This is a promise in the README and it is
the reason several features are shaped the way they are.

The one named exception is `ocr.py`: Shift-dropping an image on him reads that image's actual
bytes, locally, to extract its text. It is the same shape as the window-pin exception to rule 1 -
narrow, named, and opt-in on every single use (the modifier key, not a setting you forget is on) -
rather than a quiet widening of what "nothing reads a file" means. It never reads a window's
content, only a file you handed it directly.

### 5. `behavior.py`, `world.py` and `mood.py` stay free of Qt

So the whole state machine can be reasoned about and tested headlessly.

### 6. Sprite scaling is by an integer factor, nearest-neighbour

A fractional scale blurs pixel art. Anything that wants smooth motion gets **baked frames**, not a
runtime transform.

---

## Adding things

### A costume

Add an entry to `COSTUMES` in `tools/gen_sprites.py`, then look at it before committing:

```bash
python tools/preview_costume.py my_costume.json preview.png
```

That renders it onto seven real poses over a light **and** a dark taskbar — which is where these
usually fail. Then `python tools/gen_sprites.py` and `python tests/run_all.py`.

See [docs/ART.md](docs/ART.md) for the palette, the anchors and the size limits.

### A behaviour

New animation states go in `gen_sprites.py`; the logic goes in `behavior.py`. If it fires on a
timer, **give it a measured rate** — add it to `tests/quirk_rate.py` or `tests/fidget_rate.py` and
put the number in the docs. This project has twice shipped something that fired 30× more often than
intended, and both times a rate script caught it.

### A setting

Add it to `DEFAULTS` in `config.py`, with bounds in `_BOUNDS` if it is numeric, and a row in
[docs/SETTINGS.md](docs/SETTINGS.md). A test asserts every key has a documented row and every
documented row is a real key, so a missing one fails CI.

---

## Style

- Four spaces, ~100 columns.
- Comments explain **why**, not what. If a value was chosen by measurement, say what you measured.
- Docstrings on anything non-obvious, especially where the obvious implementation is wrong.
- British spelling in prose. Code identifiers are American where an API forces it (`color` in Qt).

---

## Reporting a bug

Include your Windows version, whether you run one monitor or several, and the tail of
`%APPDATA%\NibMascot\nib.log`. **Please skim the log before pasting it** — it never contains
your prompts, but it does record which executables you switched to.
