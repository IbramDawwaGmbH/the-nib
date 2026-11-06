# Drawing a costume of the day

One file per date, `MM-DD.json`, in this folder. `tools/gen_daily_costumes.py` turns all 366 into
the month sheets; `tools/check_costume.py` is the gate every one of them has to pass first.

## Who you are dressing

Nib is a 48×48 pixel creature: a blocky orange body (`#D9552E`) with a 1px dark-brown outline,
two tall dark eyes, a small mouth, short legs. Coordinates below are on the standing frame,
`idle[0]`, origin top-left.

| Part | Where |
| --- | --- |
| Top of his head | row 18, centred on x = 24. The head is 26px wide (x 11–36) |
| Eyes | rows 26–31; left eye x 16–20, right eye x 27–31 |
| Mouth | row 34 |
| Body bottom | row 41; feet stand on row 46 |

The costume is stamped so its **anchor** lands on one of his four anchor points, which move with
him on every frame — a hop, a squat, a nap — so you only ever design against the standing pose.

| `kind` | His point (idle) | Your anchor is… |
| --- | --- | --- |
| `head` | (24, 18), the top of his head | the **bottom-centre** of the hat: the row that rests on his head, its middle column |
| `eyes` | (24, 26), the top of his eye band | the centre column, on the row level with the top of his eyes |
| `neck` | (23, 35), just under his mouth | the **top edge**, centre column — anything that hangs is anchored at its top |
| `body` | (23, 40), low on his torso | the point on the costume that should sit there |

## The format

```json
{
  "date": "03-14",
  "name": "pi_day",
  "title": "Pi Day",
  "theme": "a teal party cone with a gold pi symbol; the pom-pom bobs",
  "category": "Hats",
  "kind": "head",
  "anchor": [6, 11],
  "layer": "over",
  "pattern": ["...", "..."],
  "frames": [["...", "..."]]
}
```

* `name` — unique, lowercase, `a-z0-9_`, 2–31 characters, and not one of the existing wardrobe
  names (santa, party, cap, crown, beanie, tophat, wizard, witch, cowboy, scarf, cape, …).
* `category` — `Hats`, `Head`, `Eyes`, `Neck`, `Body` or `Behind`. What it *is*: his opinion of
  it falls back on this.
* `layer` — `over` almost always. `behind` only for things that hang behind him (a cape, wings, a
  backpack), which are drawn first with him on top.
* `pattern` — rows of equal length, one character per pixel. **At most 38×38.**
* `frames` — **1 to 3** more patterns, each exactly the same width and height as `pattern`, each
  different. Every daily costume moves.

## The palette

`.` transparent · `D` dark outline · `W` white · `R` red · `G` gold · `B` blue · `N` green ·
`K` grey · `P` purple · `M` pink · `O` brown · `A` charcoal · `S` silver · `F` flame orange ·
`T` teal

Draw **flat colour**. The sheet adds a 1px dark outline round the whole silhouette and shades it
(highlight top-left, shade bottom-right), so do not draw your own outer outline and do not hand-
shade. A `D` *inside* the costume becomes a dark tone of the colour next to it — use it for a band
or a seam.

`F` and `O` sit close to his own orange. Fine as an accent, wrong as the main colour of a hat:
it melts into his head.

## How it moves

A costume's frame *n* shows on his frame *n*: as he sways at idle (4 frames, about 1.5 s round)
and faster as he walks (6 frames). So the motion has to read at that pace and in a few frames:

* a flame flickering, a star twinkling, lights blinking in turn, a pom-pom or a feather bobbing,
  a flag or a ribbon rippling, a propeller turning, a bubble rising, an antenna wiggling.
* Change a **few** pixels per frame, not the whole thing. The silhouette stays put; something on
  it moves. Two frames is enough for a blink; three or four for anything that travels.

## Rules the checker enforces

1. Fits 38×38; the frames match the pattern's size; 2–4 frames in all, each different.
2. **No narrow enclosed transparent pocket.** The outline rings the inside of a hole too, so a
   small hole — the middle of a ring, a gap in a letter — renders as a dark blob. Fill it with a
   colour, or open it to the outside.
3. **Off his face.** Only an `eyes` costume may cover rows 26–31 between x 15 and 32. A hat's brim
   must sit above them; a scarf must hang below.
4. A `neck` costume ends by row 45. Nothing reaches below his feet while he stands.
5. Not cut off at the top standing still, and not by more than 3 rows on any upright pose — keep a
   hat's height above its anchor to **12 rows or fewer**.

## Rules the reviewer enforces

* **It reads at 1×.** Someone glancing at a 48px creature on a taskbar should get "that's a
  snowman hat" in a second. Big simple shapes, 2–3 colours, no 1px noise, no text smaller than a
  4px-tall glyph (you cannot write words — a single letter or numeral is the most).
* **It is about the day.** The title and the theme must match what is drawn.
* **Proportion.** A hat is 14–30px wide and sits on his 26px head; glasses span both eyes.
* **Nothing real and owned.** No brands, logos, mascots or characters from films, games or
  books. Generic only — "a superhero mask", never a particular hero. Treat cultural and
  religious observances with respect: their symbols, not caricatures.

## Commands

Check (and render a review sheet of) one file, a few, or the whole folder:

```bash
python tools/check_costume.py tools/daily_costumes/03-14.json --preview /path/to/review.png
```

Then look at the PNG: every pose on a light and a dark taskbar, and its frames side by side.
