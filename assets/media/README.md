# Nib media

The launch film, its README GIFs and the README images. **Where each file is used** below says
where it is placed; every path there is relative to this folder.

Source: `Desktop\remotion-studio\video\src\nib\`. The film (Rev. 3) is in `v3/`, the README
images in `stills/`. Art, sounds, the guard's eyes, the snow piles, the costumes of the day and every number or line on screen
come from the app's own code via `scripts/nib-export.py`.

## What's here

| Path | What |
| --- | --- |
| `video/nib-film-v3.mp4` | **The film.** 70 s, 1920×1080 at 60 fps, 26 shots on a 120 BPM grid, music and sound. Christmas snow, the away guard as the climax, then the costumes of the day. For the Release, social media, and the README (drag it into the GitHub editor so it plays inline) |
| `video/nib-15s-v3.mp4` | 15 s hook cut from the film, with its sound: meet him, poke him, the prompt types itself, the guard's blackout, the logo |
| `gifs/00-story.gif` | 8 s opening: meet Nib, he lives on your taskbar, poke him, he sulks. README hero, under the header |
| `gifs/01…18-*.gif` | One GIF per feature, cut from the film at 960×540, silent, with the film's big type |
| `images/repo/` | `header-dark.png`, `header-light.png` (1600×400), `social-preview.png` (1280×640, for repo **Settings → Social preview**) |
| `images/features/` | 21 still feature cards, 1600×800, alt text in `images/INDEX.md`. A still alternative to the GIFs, also usable for docs and social posts |

The style frames, the motion test and the older rev. 2 media stay outside the repo, in
`Desktop\claudy\nib-media\`.

## Where each file is used

`assets/gifs/` and `banner.png` stay as they are: they're the pixel-exact sprite GIFs
`tools/gen_demo_gif.py` makes, and these files add to them rather than replace them.

| File | Used in |
| --- | --- |
| `images/repo/header-*.png` | Top of `README.md`, in a `<picture>` that follows the reader's light/dark scheme |
| `images/repo/social-preview.png` | Not in the README — set it in the repo's **Settings → Social preview** |
| `gifs/00-story.gif` | README, under the header; `hero.gif` moved down to *See him in action* |
| `video/nib-film-v3.mp4`, `video/nib-15s-v3.mp4` | Linked from the README header. On GitHub, drag the film into the editor so it plays inline, and attach both to the Release |
| `gifs/01-ask.gif`, `02-drop-file.gif`, `05-tool-wheel.gif`, `15-pet.gif` | README, *What he's for* |
| `gifs/03-ocr.gif` | README, *Drop-to-OCR* |
| `gifs/04-calculator.gif` | README, *The inline calculator*, above `calculator-flow.svg` |
| `gifs/06-ruler.gif` | README, *The pixel ruler*, above `ruler-flow.svg` |
| `gifs/07-billboard.gif` | README, *The snippet billboard*, beside `billboard.gif` |
| `gifs/08-personas.gif` | README, *What makes him different* |
| `gifs/09-focus.gif` | README, *Focus sessions* |
| `gifs/10-task-finished.gif` | README, *He notices when a job finishes* |
| `gifs/11-mic-muted.gif` | README, *He shows when your mic or camera is live* |
| `gifs/12-perch.gif` | README, *Drop him on a window*, beside `perch.gif` |
| `gifs/13-guard-fog.gif`, `14-guard-blackout.gif` | README, *The screen guard* |
| `gifs/16-costumes.gif` | README, under the costume banner |
| `gifs/17-snow.gif` | README, *Snow on your windows* |
| `gifs/18-calendar.gif` | README, under the costume run: the costume of the day |
| `images/features/*.png` | The matching sections of `docs/USING-HIM.md`, `BEHAVIOUR.md`, `PROVIDERS.md`, `ART.md` and `PRIVACY.md` (alt text from `images/INDEX.md`) |


## Audio

- **Music:** ElevenLabs Music (`eleven_music_v2_5`), take A of two, 120 BPM, downbeat at 0 s. It's cut to
  the film's sections on its bar lines by `scripts/nib-music-edit.py`: the guard section is the
  same groove low-passed ("heard through the fog"), and the blackout is silent.
- **Sound effects:** ElevenLabs SFX (`eleven_text_to_sound_v2`): whoosh, click, pop, key, typing,
  fog, blackout, chime, boom, riser. Normalized to one peak by `scripts/nib-sfx-normalize.py`.
  They were generated in the ElevenLabs flow "Nib launch film — audio".
- **Nib's own sounds:** poke, grump, sleep, send, open, cheer, land and pet, from `assets/sounds`.
- **Mix:** peaks at 0.89, no clipping.

## Before anything goes public

- **Remotion licence:** free only for individuals and companies with ≤3 employees, non-profits,
  or evaluation.
- **ElevenLabs:** check your plan's commercial-use terms for the music and effects.

## Honesty notes

- The film is **staged**: Remotion plays Nib's real sprite frames at their real timings, with real
  calculator, ruler and tool-wheel outputs, the guard's real eyes (baked from `guard_scene.EyesPainter`) and his
  real lines (`guard_phrases.json`). It isn't a screen recording of the running app.
- The time skips in the film are labelled ("24 min later", and the snow's "3 min, sped up").
- The snow piles, lumps and snowball are drawn by the app's own `snow_overlay.render_cap`,
  `render_lump` and `render_ball`, at the film's display scale; the falling lumps' paths are
  animated for the film, not the app's physics.
- The OCR beat's extracted text was written to match the screenshot in the scene; it wasn't
  produced by running `ocr.py`.
- The costume run uses hand-picked cool costumes. Costumes the app puts on for a reason stay
  real: reading glasses (code editor), headphones (focus), earmuffs (muted mic), hard hat
  (machine pinned), party hat (job finished).
