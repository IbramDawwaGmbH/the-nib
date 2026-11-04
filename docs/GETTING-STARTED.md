# Getting started

For anyone who has never run a Python program. If you already have Python, the whole thing is
`pip install -r requirements.txt` then `pythonw main.py` — skip to [Step 3](#step-3-start-him).

**Don't want Python at all?** Download `Nib-…-windows-x64.zip` from the
[latest release](https://github.com/IbramDawwaGmbH/the-nib/releases/latest), extract it, and run
`Nib.exe`. It isn't code-signed, so Windows SmartScreen may warn you the first time: choose
**More info → Run anyway**. The rest of this page is for running him from source.

[← back to the README](../README.md)

---

## What you're installing

A small program that puts an animated character on your taskbar. Clicking him opens a box where you
type a question, and your AI assistant opens with that question already filled in.

It runs entirely on your computer. It doesn't create an account, doesn't sign in to anything, and
makes no internet connections of its own — except, when you ask it to, to fetch its own update from
GitHub (and, if you run it from a git checkout, any Python packages that update needs, from PyPI).

**You need Windows 10 or 11.** It uses Windows-specific features — the taskbar, the tray, global
shortcuts — so it won't run on a Mac or on Linux.

---

## Step 1: Install Python

Python is the language he's written in. Your computer probably doesn't have it yet.

1. Go to **[python.org/downloads](https://www.python.org/downloads/)**
2. Click the big yellow **Download Python** button
3. Run the file you downloaded
4. **Important:** on the first screen, tick **"Add python.exe to PATH"** at the bottom before you
   click Install. If you miss it, the commands below won't be found and you'll have to reinstall.
5. Click **Install Now** and wait

To check it worked, press <kbd>Win</kbd>+<kbd>R</kbd>, type `cmd`, press Enter, then type:

```bash
python --version
```

You should see something like `Python 3.12.4`. Any version from 3.10 upwards is fine.

<details>
<summary>It says "python is not recognised"</summary>

The PATH box wasn't ticked. Re-run the installer, choose **Modify**, click Next, tick
**Add Python to environment variables**, and finish. Then close and reopen the black window.
</details>

---

## Step 2: Download and set up Nib

**Download it:** on the GitHub page, click the green **Code** button → **Download ZIP**. Then
right-click the downloaded file → **Extract All**. Remember where you put it.

**Open a command window in that folder:** open the extracted folder in File Explorer, click the
address bar at the top, type `cmd`, and press Enter. A black window opens, already pointing at the
right place.

**Install what he needs:**

```bash
pip install -r requirements.txt
```

That's mostly PySide6, the toolkit that draws his window - it's the large majority of roughly
250 MB, and can take a few minutes. A wall of scrolling text is normal.

---

## Step 3: Start him

```bash
pythonw main.py
```

He appears on your taskbar and starts walking. The window you typed into can be closed —
`pythonw` (with the **w**) runs him without a console attached.

**The first time**, he gives you a short tour — four tips over about half a minute, covering the
things you'd otherwise never find.

<details>
<summary>Nothing appeared</summary>

- **Look on your other monitor.** He roams every screen by default and starts about three quarters
  of the way across your whole desktop. Right-click his tray icon (a small orange square near the
  clock) → **Come here** and he'll walk to your mouse.
- **Is a fullscreen app open?** He hides for those on purpose and comes back when you exit.
- **Check the log** at `%APPDATA%\NibMascot\nib.log` — paste that path into File Explorer's
  address bar. The last lines say what happened.
</details>

---

## Step 4: Choose your assistant

He points at Claude by default. To change it:

1. Right-click his **tray icon** (near the clock, bottom-right)
2. **Settings…** → the **Shortcuts** tab
3. Change **Provider** to `claude`, `chatgpt` or `gemini`
4. **Apply**

**Claude** opens its desktop app, which you need
[installed](https://claude.ai/download). **ChatGPT** and **Gemini** open in your normal browser and
need nothing extra.

---

## Step 5: Try these

| Do this | And |
| --- | --- |
| **Click him** | A box appears. Type a question, press Enter |
| **Press Ctrl+Alt+C** | The same box, wherever your mouse is |
| **Drag a file onto him** | Your assistant opens, asking about that file |
| **Rest your mouse on him** and hold still | He leans into it and a heart floats up |
| **Drag him and let go** | He drops. From high up he lands on his head |
| **Flick him sideways** | He flies and bounces off the end of the taskbar |
| **Scroll the wheel over him** | He spins |
| **Right-click the tray icon → Do something** | One of his rarer animations, now |
| **→ Costume → Surprise me** | A random one of his 53 outfits |

---

## Starting him automatically

Right-click the tray icon → **Launch on Windows startup**. He'll be there after every reboot.

## Making him leave you alone

Four options, from gentlest to firmest — all in the tray menu:

- **Ghost mode** — faded and click-through. Still there, completely out of the way. `Ctrl+Alt+C`
  still works
- **Hide for 10 minutes** (right-click *him*) — he comes back on his own
- **Pause** — freezes him where he stands
- **Quit**

## Turning on sound

Off by default, and there are no sound files until you make them:

```bash
python tools/gen_sounds.py
```

Then tray → **Settings…** → **Look and sound** → tick **Sound enabled**.

---

## Where his settings live

`%APPDATA%\NibMascot\config.json` — paste that into File Explorer's address bar.

You don't need to touch it; the settings window covers everything. If you break it, delete it and
he'll start fresh with the defaults.

**Deleting that folder removes everything he remembers** — position, costume, recent prompts, mood.
The program itself is just the folder you extracted; delete that and he's gone.

---

## Still stuck?

**[Troubleshooting](TROUBLESHOOTING.md)** covers the common ones. If that doesn't help, open an
issue with your Windows version and the last 20 lines of the log.
