# Troubleshooting

When he misbehaves.

[← back to the README](../README.md)

---

## Troubleshooting

**He isn't moving.** Check the tray: if he is paused, that item reads **Resume** and is ticked —
click it to unfreeze him. The pause state persists across restarts, so a stray click can leave him
frozen days later. (A left click on the tray icon no longer toggles it, precisely because that was
too easy to hit by accident.)

**He's gone.** Either a fullscreen app is up, or he is snoozed from the right-click menu. Both end
on their own. Check the log for `fullscreen detected` or `snoozing`.

**I can't find him.** Tray → **Come here** and he walks to your pointer. He roams every monitor by
default, so this is a normal thing to need rather than a fault.

**Clicking him does nothing.** Ghost mode is on — the tray item is ticked, and it survives a
restart. He is deliberately click-through in that state. The `Ctrl+Alt+C` shortcut still works.

**He makes no sound.** No audio ships in the repository. Run `python tools/gen_sounds.py`, then
tick **Sound enabled** in the settings window.

**"Couldn't open Claude."** The `claude://` scheme is handled by the Claude desktop app. The
toast that appears is clickable and opens the download page.

**The hotkey did nothing.** Something else has already registered Ctrl+Alt+C. The log says
`hotkey ... is already taken by another app`; pick another in `hotkey`.

**He's too hyper / too calm.** Raise or lower `wander_min_interval_s` / `wander_max_interval_s`.
`antic_chance` tunes antics-versus-walking. `antics_enabled: false` turns off the random idle
antics, the cursor-tracking and the app-switch reactions — but *not* the away sit and the greeting
hop, which are controlled by `away_after_s` (set it to `0` to disable those).

**"Couldn't read that image" / "Didn't find any text in that image."** [Drop-to-OCR](../README.md
#drop-to-ocr) tries Windows' own on-device engine first and falls back to a copy of Tesseract
bundled inside Nib, so this isn't a missing-install problem — it means both engines genuinely
couldn't get text out of that particular file: a screenshot with no text in it, a corrupted image,
or a file over 25MB. `nib.log` records which engine failed and why, at the default `INFO` level —
the image and any extracted text are never logged, only that.

**The PC slept (or the screen turned off) while the screen guard was up.** The guard holds a
"keep awake" request while it runs, which stops *idle* sleep and the display timeout — but closing
the lid or pressing the power button still does whatever Windows is set to do, by design: Nib never
changes power settings. If background work must keep running, set the lid and power-button actions
to **Do nothing** in Control Panel → Power Options → *Choose what closing the lid does*.

**Blacked out and typing does nothing.** Click the black screen once (with several monitors, any
of them works): that brings the PIN field up on the main monitor and puts the cursor in it. If keys
still do nothing, tap Ctrl, Shift, Alt and AltGr once each, in case one of them is stuck down, then
type the PIN. If it still fails, Ctrl+Alt+Del → **Lock** and sign in to Windows, which ends the
guard while `windows_unlock_clears_guard` is on (the default). Then send `%APPDATA%\NibMascot\nib.log`; the lines that say what went wrong contain
`owner_click`, `PIN not ready`, `held back`, `took the foreground during/after Hello` or
`was disabled`.

**I forgot the guard PIN.** Ctrl+Alt+Del → **Lock**, then sign back in to Windows: that ends the
guard while `windows_unlock_clears_guard` is on (the default; with it off, only signing out of
Windows does). Clear `guard_pin_hash` in `config.json` to choose a new PIN
next time.

**The screens stayed black after Nib closed.** Nib died mid-guard and its helper is doing its job.
Lock and unlock Windows (Win+L, then sign in), or just start Nib again — it takes over and you can
unlock as normal.

**Update and restart said no, or didn't come back.** Whatever he said is the reason, and nothing was
changed unless he said **Updated**:

- *"You've changed some of my files"* or *"gone different ways"* — a git checkout he won't touch.
  Commit, stash or sort out the branch yourself (`git status`), then try again; he never merges,
  rebases or resets for you.
- *"This branch doesn't follow anything"* — `git branch --set-upstream-to=origin/main`.
- *"I couldn't reach GitHub"* — offline, a proxy, or GitHub having a moment. Try again later.
- *"I can't write to my own folder"* — he's under Program Files or another protected folder.
  Download the new zip from the releases page and extract it over the old folder yourself.
- *"My packages didn't update"* — usually because a running Nib keeps PySide6's files open. Quit
  him from the tray, run `pip install -r requirements.txt` in his folder, and start him again.
- *He quit and didn't come back* — start him from wherever you normally do. For the release build,
  `%APPDATA%\NibMascot\update.log` says what the file swap did; `nib.log` says what the update
  itself decided. If the swap failed, the old files are put back and the old version starts again.
  If he did come back but as the old version, the log says why — usually something still running
  from his folder, or a file held open (an antivirus scan, say). Pick Update and restart again.
- *"I've updated - restart me first"* — a git checkout's files are already new, but he hasn't
  restarted into them yet (pip is still installing, or a guard or focus session is holding up the
  restart). No new guard starts until he has, the idle auto-guard included. Quit him from the tray
  and start him again (or finish the focus session) before guarding.

**Anything else.** Set `log_level` to `DEBUG` and read `%APPDATA%\NibMascot\nib.log`.
