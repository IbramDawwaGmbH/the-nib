# Art and costumes

The sprite pipeline, the art contract, and how to add a costume.

[← back to the README](../README.md)

---

## Costumes and colours

![A lineup of Nib in seventeen costumes and his five colours](../assets/media/images/features/19-costumes.png)

Pick from the tray: **Costume**, grouped into submenus, and **Colour** (default, ocean, forest,
grape, charcoal). Both persist.

They cost nothing at runtime. Costumes are **not** baked per-frame — that would mean 170 frames per
hat. Instead every frame records **four anchor points**, and the costume is composited at the
matching one the first time that frame is built and cached. So a hat sits correctly on a hop, a nap
or a squat automatically, and flips with him when he walks left.

| Anchor | Where | Used by |
| --- | --- | --- |
| `head` | The centre of the top of his head | Hats, ears, horns, halos |
| `eyes` | The centre of his eye band | Glasses, patches, goggles |
| `neck` | Just below his mouth line, on his body's centre | Scarves, ties, pendants |
| `body` | The middle of his torso | Aprons, belts, packs |

All four are **measured off the finished silhouette** rather than hard-coded, so they track
breathing, squashing, hops and the tumble on their own.

> **The head anchor is not the topmost pixel**, and that distinction cost a bug. It used to be the
> centre of the highest solid row — which is his hand whenever an arm is raised. Measured across
> the sheet: **24 of 154 frames**, 16% of them, put every hat out on his wrist during the wave, the
> dance, the press-ups, the strain, the coffee and both new fidgets. It now walks down until it
> finds a row containing a contiguous run at least 12px wide. His head is 26px across and an arm is
> about 5, so the threshold has enormous margin and needs no per-frame tuning. That took it from 24
> frames to 2, and both survivors are `tumble` frames where he is genuinely rotated onto his side.

Colours are a remap of the body's four tones on the same cached frame. The eye black and the angry
red are deliberately left alone, so expressions read identically on every skin.

Costumes get the same 1px outline the body has — without it, the white santa brim vanished against
a light taskbar. That outline is also why **an enclosed transparent region is a bug**: it floods
any hole with dark, which turns a pair of hollow spectacle lenses into two black discs. Fill them.

Most costumes are painted **over** him. A few declare `"layer": "behind"` and are painted first,
with the creature drawn on top — that is the only way a backpack strap ends up behind his shoulder
rather than across his chest.

### He dresses himself, when there's a reason to

He owns 53 costumes and, left alone, wears none of them. The fix is deliberately **not** to cycle
them on a timer: the costume is a choice you make and it persists, so a timer overwriting it every
few minutes would make the menu decorative — and a hat that changes for no reason says nothing
about him or about what's happening. It'd be the only arbitrary thing in the app.

So a costume goes on when something *means* it, through the same path the reading glasses have
always used. Two kinds, and the difference is how long they last:

| | What | How long |
| --- | --- | --- |
| **Winter** | Beanie | All season |
| **Spring** | Flower crown | All season |
| **Summer** | Sunglasses | All season |
| **Autumn** | A leaf on his head | All season |
| **Machine pinned** | Hard hat, while he fans himself | The length of that one reaction |
| **Knocking-off time** | Party hat, for the dance | The length of that one reaction |
| **A code editor** | Reading glasses | The length of that one reaction |
| **Focus session** | Headphones | For as long as the session runs |

The moment costumes are keyed on the **moment**, not on the animation — so knocking off on a
Friday earns the party hat, and dancing *because* it's Friday afternoon, which is the identical
animation from the ambient pool, does not.

Seasonal is the ambient one: no expiry, worn until the season turns. It's meant to be predictable
rather than surprising — "it's December so he's got a beanie on" is something you can reason about.
A moment costume takes precedence while it lasts, so a code editor swaps the beanie for glasses and
swaps back afterwards.

**Yours always wins.** If you have picked a costume from the tray, none of this ever fires — not a
persona, not a moment, not the season. That rule predates all of them and every automatic costume
routes through the same check, because a hat that keeps vanishing reads as a bug. Set
`auto_costumes` to `false` to switch the whole thing off.

**Surprise me** at the top of the Costume submenu puts a random one on — never the one he's
already wearing — and it counts as a choice, so it sticks and it outranks everything above.

### Adding your own

Add an entry to `COSTUMES` in `tools/gen_sprites.py`: a character-grid pattern, an anchor point,
which of the four attachment points it uses, and a category for the menu. Then check it without
touching the sheet:

```bash
python tools/preview_costume.py my_costume.json preview.png
```

That renders your pattern onto seven real poses — standing, walking, mid-hop, sitting, asleep, and
the two that raise an arm past his head — over a light *and* a dark taskbar, which is where these
usually fail. `tools/integrate_costumes.py` folds reviewed batches back into `gen_sprites.py`.

The palette is wider than the body's: `D` dark, `W` white, `R` red, `G` gold, `B` blue, `N` green,
`K` grey. Costumes live in their own PNG, so the body's seven-colour rule does not bind them —
but the no-partial-alpha rule still does, because the click mask comes from the composited frame.

---

## The art pipeline

The mascot is **generated by code, not drawn in an editor.** `tools/gen_sprites.py` builds every
frame as integer pixel operations on 48×48 character grids, then writes the sheet, the manifest and
the costume sheet.

```bash
python tools/gen_sprites.py
```

```bash
python tools/validate_sheet.py assets/nib_sheet.png assets/frames.json
```

It also writes two review images: `assets/preview.png`, a zoomed contact sheet of every frame on a
checkerboard with a marker on each cell's baseline row, and `assets/banner.png`, the costume
showcase in the README.

The README's demo GIFs are separate: `tools/gen_demo_gif.py` composites frames straight from
`assets/nib_sheet.png` onto a drawn taskbar. Re-run it if you change a state it uses (the list is
at the top of that file) so the GIFs don't go stale against the art.

```bash
python tools/gen_demo_gif.py
```

**The art contract**, enforced by `validate_sheet.py`:

- 10×40 grid of 48×48 frames — 42 animation states, 170 distinct frames
- Exactly seven body colours, and **alpha strictly 0 or 255** — no anti-aliasing anywhere
- Every frame's soles rest on the same baseline row, so he does not jitter vertically between
  states. The mid-air hop frames are the only exemption, and they are checked to be *above* it
- Every frame carries `head` and `eyes` anchors, so any frame can wear any costume
- `walk_left` must **not** be in the sheet — it is mirrored at load time

**Every prop carries its own border.** The light detail tone `#F5EDE6` against a Windows 11 light
taskbar `#F3F3F3` is a contrast ratio of **1.04 : 1** — mathematically the same colour. Against a
dark taskbar it is 14:1, which is why nobody noticed: the art was designed on dark, and a 5×
checkerboard preview flatters it further.

The rule turned out to be about *placement*, not tone. Light pixels drawn **on his body** are fine
— the injury bandage reads perfectly either way. Light pixels **floating on the background** vanish.
So every floating prop now goes through `stamp_prop`, which rings it in the outline tone — the same
fix `outline_costume` applies to hats. It also clips at the baseline row, so a mug standing on the
floor cannot push the silhouette a pixel below the strip.

This was applied retroactively to the sleep Z's, the dance notes, the thought dots, the sparkles,
the seeing-stars and the football, all of which had been invisible on a light taskbar since they
were drawn. Nothing is lost on a dark taskbar: a 1px dark edge on a white note is invisible against
black.

Those rules are not pedantry. The hard-alpha rule is what makes the per-pixel click mask exact; the
baseline rule is what keeps his feet welded to the taskbar; the anchors are what make costumes cost
nothing.
