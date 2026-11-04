"""Update and restart: bring this copy of Nib up to date, then start the new one.

Picked from the wheel's App group, and only ever then - nothing here runs on a timer, at start-up,
or in the background. It is the one network request Nib makes of its own (two, when pip has
packages to fetch), and it is made only because you asked for it (see docs/PRIVACY.md).

There are two ways people run him, and each updates the way it was installed:

* **A git checkout** (``pyw main.py``): ``git fetch origin``, then a fast-forward of the current
  branch to its upstream, and nothing else. Never a merge, a rebase, a reset or a stash - a checkout
  with local changes to tracked files, or one that has gone a different way from origin, is left
  exactly as it is and he says why. If ``requirements.txt`` changed, pip installs it - from PyPI,
  or whatever package index your pip is set up to use.
* **The release zip** (``Nib.exe``, PyInstaller one-folder): the latest GitHub release is compared
  with ``version.__version__``; a newer one is downloaded, checked (digest, a real zip, ``Nib/Nib.exe``
  inside, no path that climbs out), and unpacked to a temporary folder. A running exe cannot
  overwrite itself, so a small ``.cmd`` helper waits for this Nib to exit, swaps the new files in
  for the old ones and starts Nib again - the old one, put back whole, if the swap failed.

Anything else (no ``.git``, no git, a branch with no upstream) gets a friendly sentence and no
change at all.

This module is Qt-free, like ``ocr.py``: the work runs on a plain thread and reports through
callbacks, and ``main`` marshals those back onto the UI thread with a Qt signal. Every outside
effect - running git or pip, the HTTP calls, the clock, "is that process still alive" - is a
parameter, so the tests drive every path with fakes and never touch the network or this checkout.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from version import __version__

log = logging.getLogger("nib.updater")

#: The only URL Nib ever asks for itself. One constant, so it is easy to find and to audit.
LATEST_RELEASE_URL = "https://api.github.com/repos/IbramDawwaGmbH/the-nib/releases/latest"
#: Generic on purpose: GitHub's API wants a User-Agent, and this says nothing about who is asking.
USER_AGENT = "Nib-updater"
#: What release.yml names the zip - the tag (``v1.2.0``) is part of it.
ASSET_TEMPLATE = "Nib-{tag}-windows-x64.zip"
#: The exe inside that zip. Compress-Archive keeps the ``Nib`` folder as the top level.
ZIP_EXE = "Nib/Nib.exe"
#: The only remote that is ever fetched, and so the only one a branch may be fast-forwarded from.
GIT_REMOTE = "origin"
#: For the fetch: give up on a transfer that has stalled (git's default is to wait for ever) ...
FETCH_SETTINGS = ["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30"]
#: ... and on an ssh remote, fail rather than ask - for a key's passphrase or about an unknown host
#: - on a console nobody can see. Only when you have not set an ssh command of your own.
FETCH_SSH = ["-c", "core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=15"]

GIT_TIMEOUT_S = 20.0
FETCH_TIMEOUT_S = 90.0
MERGE_TIMEOUT_S = 60.0
PIP_TIMEOUT_S = 600.0
HTTP_TIMEOUT_S = 20.0
DOWNLOAD_TIMEOUT_S = 300.0
#: A release zip is ~100MB. Anything far past that is not one of ours.
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024

#: ``main.py --after-update <old pid>``: wait for the Nib being replaced to exit before taking the
#: single-instance mutex, which it holds until it does - otherwise the new one would find it taken
#: and quietly exit, and the restart would be a quit.
AFTER_UPDATE_FLAG = "--after-update"
AFTER_UPDATE_WAIT_S = 20.0
#: How long the release helper waits for the old Nib before copying anyway.
HELPER_WAIT_S = 60

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

MODE_GIT = "git"
MODE_RELEASE = "release"
MODE_NONE = "none"

#: Nothing to do - and so no restart.
CURRENT = "current"
#: The code on disk is new: start a fresh process and quit this one.
RESTART = "restart"
#: A new build is unpacked in a temporary folder: run ``Outcome.helper`` and quit.
HANDOFF = "handoff"
#: Nothing changed (or nothing past the step that failed). ``message`` says what and why.
FAILED = "failed"

#: ``(argv, cwd, timeout_s) -> (exit code, stdout, stderr)``. 127 means "no such program".
Runner = Callable[[list, Path, float], tuple]
Progress = Callable[[str], None]


@dataclass
class Mode:
    """How this copy was installed, and so how it updates. ``reason`` is set for MODE_NONE."""

    kind: str
    app_dir: Path
    reason: str = ""


@dataclass
class Outcome:
    """What an update came to. ``message`` is the one line he says; ``sub`` the small print."""

    kind: str
    message: str
    sub: str = ""
    #: HANDOFF only: the helper script to run, inside the temporary folder it cleans up.
    helper: Path | None = None


class Http(Protocol):
    def get_json(self, url: str, timeout: float) -> dict: ...

    def download(self, url: str, dest: Path, timeout: float, limit: int) -> None: ...


# --- the outside world, as defaults the tests replace ----------------------------------------------


def run_command(argv: list, cwd: Path, timeout: float) -> tuple[int, str, str]:
    """Run ``argv`` with no window and no prompt, bounded by ``timeout``. Never raises.

    The output goes to temporary files, not pipes, and the wait is on the process, not on the
    end of its output: git hands its stderr on to ``git-remote-https`` and ``ssh``, and a pipe
    stays open until the last of them exits - so a stuck ssh would outlive a killed git, and the
    timeout with it. On a timeout the whole tree goes, children and all.
    """
    env = dict(os.environ)
    # A credential prompt (or Git Credential Manager's dialog) would hang the thread until the
    # timeout - origin is public, so there is nothing to sign in to anyway.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            child = subprocess.Popen(
                [str(a) for a in argv], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, creationflags=_CREATE_NO_WINDOW,
            )
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(child)
                return 124, "", f"{argv[0]} took longer than {timeout:.0f}s"
            return code, _read_back(out), _read_back(err)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _read_back(handle) -> str:
    handle.seek(0)
    return handle.read().decode("utf-8", errors="replace").replace("\r\n", "\n")


def kill_tree(child: subprocess.Popen) -> None:
    """End ``child`` and everything it started. ``taskkill /T`` walks the tree while the parent is
    still there to walk it from, so it goes first; the plain kill is for when taskkill can't."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15, creationflags=_CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        child.kill()
        child.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


class UrllibHttp:
    """The real HTTP, https only, with the generic User-Agent and nothing else about you."""

    @staticmethod
    def _request(url: str, accept: str) -> urllib.request.Request:
        if not url.startswith("https://"):
            raise ValueError("not an https URL")
        return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})

    def get_json(self, url: str, timeout: float) -> dict:
        with urllib.request.urlopen(self._request(url, "application/vnd.github+json"),
                                    timeout=timeout) as response:
            data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("unexpected reply")
        return data

    def download(self, url: str, dest: Path, timeout: float, limit: int) -> None:
        got = 0
        with urllib.request.urlopen(self._request(url, "application/octet-stream"),
                                    timeout=timeout) as response, open(dest, "wb") as out:
            while chunk := response.read(256 * 1024):
                got += len(chunk)
                if got > limit:
                    raise ValueError("the download is far bigger than a release should be")
                out.write(chunk)


def process_alive(pid: int) -> bool:
    """Whether ``pid`` is still running. A process that cannot be opened at all has gone."""
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return False
    synchronize, query_limited = 0x00100000, 0x1000
    handle = kernel32.OpenProcess(synchronize | query_limited, False, int(pid))
    if not handle:
        return kernel32.GetLastError() == 5   # access denied: there, just not ours to open
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


# --- deciding what to do ---------------------------------------------------------------------------


def app_dir() -> Path:
    """The folder Nib runs from: next to the exe when frozen, next to this file from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def detect_mode(directory: Path | None = None, *, frozen: bool | None = None,
                which: Callable[[str], str | None] = shutil.which) -> Mode:
    """How to update this copy - decided when you pick the tool, not at start-up."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    where = directory if directory is not None else app_dir()
    if frozen:
        return Mode(MODE_RELEASE, where)
    if not (where / ".git").exists():
        return Mode(MODE_NONE, where,
                    "I can't update this copy myself - it isn't a git checkout or a release build.")
    if which("git") is None:
        return Mode(MODE_NONE, where,
                    "I can't update myself without git - it isn't installed, or isn't on PATH.")
    return Mode(MODE_GIT, where)


def refusal(*, running: bool, guarding: bool, focusing: bool) -> str:
    """Why an update cannot start (or restart) right now, or "" if it can.

    A restart mid-guard would end the guard - quitting unlocks it - and one mid-focus would throw
    away the session, so both wait. Two updates at once would race each other over the same files.
    """
    if running:
        return "I'm already updating."
    if guarding:
        return "Not while the screen guard is up."
    if focusing:
        return "I'll update after your focus session."
    return ""


def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v1.2.0"`` -> ``(1, 2, 0)``. Anything else - a pre-release, a stray word - is None."""
    parts = str(text).strip().lstrip("vV").split(".")
    if not 1 <= len(parts) <= 4 or not all(p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    return tuple(numbers + [0] * (3 - len(numbers)))


def is_newer(latest: str, current: str) -> bool | None:
    """Numerically, so 1.10.0 is newer than 1.9.0. None when either is not a version at all."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _first_line(text: str) -> str:
    return next((line.strip() for line in str(text).splitlines() if line.strip()), "")[:160]


# --- a git checkout ---------------------------------------------------------------------------------


def update_git(directory: Path, *, run: Runner = run_command, progress: Progress = lambda _m: None,
               python: str | None = None, blocked: Callable[[], str] = lambda: "",
               environ: Mapping[str, str] = os.environ) -> Outcome:
    """Fast-forward the current branch to its upstream on origin, or say why not.

    ``blocked`` is asked once more just before the merge - the last moment nothing has changed -
    and anything it says (a guard went up, a focus session began while the fetch ran) stops the
    update there. Once the files on disk are new, the restart has to follow straight away: a Nib
    left running on the old code would load new code from disk the next time it imports a module
    or starts a guard core.
    """
    def git(*args: str, timeout: float = GIT_TIMEOUT_S) -> tuple[int, str, str]:
        code, out, err = run(["git", *args], directory, timeout)
        return code, (out or "").strip(), (err or "").strip()

    code, branch, err = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if code == 127:
        return Outcome(FAILED, "I can't update myself without git - it isn't installed.")
    if code != 0 or not branch:
        return Outcome(FAILED, "I'm not on a branch here, so there's nothing to update to.",
                       "Check out a branch (main) and try again.")
    code, upstream, _err = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if code != 0 or not upstream:
        return Outcome(FAILED, "This branch doesn't follow anything, so there's nothing to update from.",
                       f"'{branch}' has no upstream branch.")
    _code, remote, _err = git("config", "--get", f"branch.{branch}.remote")
    if remote != GIT_REMOTE:
        return Outcome(FAILED, f"I only update from {GIT_REMOTE}, and this branch follows "
                               f"'{remote or upstream}'.")
    code, dirty, err = git("status", "--porcelain", "--untracked-files=no")
    if code != 0:
        return Outcome(FAILED, "Git couldn't read this checkout, so I left it alone.", _first_line(err))
    if dirty:
        return Outcome(FAILED, "You've changed some of my files, so I left everything alone.",
                       "Commit or stash them yourself, then try again.")

    settings = list(FETCH_SETTINGS)
    if not (environ.get("GIT_SSH_COMMAND") or environ.get("GIT_SSH")
            or git("config", "--get", "core.sshCommand")[1]):
        settings += FETCH_SSH
    progress("Checking for updates…")
    code, _out, err = run(["git", *settings, "fetch", "--quiet", GIT_REMOTE], directory,
                          FETCH_TIMEOUT_S)
    if code != 0:
        return Outcome(FAILED, "I couldn't reach GitHub to check for updates.", _first_line(err))
    _code, old, _err = git("rev-parse", "HEAD")
    _code, new, _err = git("rev-parse", "@{upstream}")
    if not old or not new:
        return Outcome(FAILED, "Git couldn't tell me where this branch is, so I left it alone.")
    if old == new:
        return Outcome(CURRENT, "I'm up to date.")
    if git("merge-base", "--is-ancestor", "HEAD", "@{upstream}")[0] != 0:
        if git("merge-base", "--is-ancestor", "@{upstream}", "HEAD")[0] == 0:
            return Outcome(CURRENT, "I'm up to date.",
                           f"This checkout has commits that aren't on {GIT_REMOTE} yet.")
        return Outcome(FAILED, f"This branch and {GIT_REMOTE} have gone different ways, so I left it alone.",
                       "Sort it out with git yourself - I never merge or rebase.")

    why = blocked()
    if why:
        return Outcome(FAILED, why, "I left the update alone - pick it again afterwards.")
    progress("Updating…")
    code, _out, err = git("merge", "--ff-only", "@{upstream}", timeout=MERGE_TIMEOUT_S)
    if code != 0:
        return Outcome(FAILED, "The update didn't apply, so nothing changed.", _first_line(err))
    log.info("updated %s from %s to %s", branch, old[:10], new[:10])

    sub = ""
    # Exit 1 is "they differ"; anything else that is not 0 is git failing to say, so install anyway.
    if git("diff", "--quiet", old, new, "--", "requirements.txt")[0] != 0:
        progress("Updating my packages…")
        code, _out, err = run([python or sys.executable, "-m", "pip", "install",
                               "--disable-pip-version-check", "-r", "requirements.txt"],
                              directory, PIP_TIMEOUT_S)
        if code != 0:
            log.warning("pip install -r requirements.txt failed (%s): %s", code, _first_line(err))
            sub = "My packages didn't update, though - see Troubleshooting."
    return Outcome(RESTART, "Updated. Restarting…", sub)


# --- the release zip --------------------------------------------------------------------------------


def writable(directory: Path) -> bool:
    """Whether new files can go into ``directory`` - not, say, under Program Files."""
    probe = directory / f".nib-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def check_zip(path: Path) -> str:
    """"" if ``path`` is a whole release zip that stays inside the folder it is unpacked into,
    otherwise what is wrong with it, short enough to say."""
    if not zipfile.is_zipfile(path):
        return "it isn't a zip file"
    try:
        with zipfile.ZipFile(path) as archive:
            names = []
            for info in archive.infolist():
                # Compress-Archive in Windows PowerShell 5 writes backslashes; normalise first.
                name = info.filename.replace("\\", "/")
                parts = name.split("/")
                if name.startswith("/") or ":" in name or ".." in parts:
                    return "it has a path in it that climbs out of its folder"
                names.append(name)
            if ZIP_EXE not in names:
                return f"there's no {ZIP_EXE} in it"
            if archive.testzip() is not None:
                return "it's damaged"
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return "it's damaged"
    return ""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_entries(source: Path) -> list[tuple[str, bool]]:
    """The top-level files and folders of the new build - what the helper swaps, one by one.
    ``(name, is a folder)``; PyInstaller's one-folder build is ``Nib.exe`` and ``_internal``."""
    if not source.is_dir():
        return [("Nib.exe", False), ("_internal", True)]
    return sorted((entry.name, entry.is_dir()) for entry in source.iterdir())


def helper_script(source: Path, install: Path, work: Path, pid: int, log_path: Path, *,
                  entries: list[tuple[str, bool]] | None = None,
                  wait_s: int = HELPER_WAIT_S) -> str:
    """The ``.cmd`` that swaps the files once this Nib has gone - all of them, or none.

    In order:

    1. Wait (bounded) for ``pid`` and for every other ``Nib.exe`` running from the install folder
       - the guard core is one, and it can outlive the Nib that started it by a few seconds. If any
       is still there when the wait is up, nothing is copied at all: its files are still in use.
    2. Rename each thing the new build brings (``Nib.exe``, ``_internal``) to ``<name>.nib-old``,
       beside itself. A rename on the same disk is instant and either happens or doesn't, and a
       file held open - by a scanner, say - makes it fail before anything has changed.
    3. ``robocopy /E`` the new build in. ``_internal`` arrives in an empty place, so nothing a
       previous version left there survives; anything else in the folder that the build doesn't
       bring (a file of yours) is never touched.
    4. If any rename or the copy failed, take out what came in and rename the old files back -
       the old Nib, whole. Otherwise delete the ``.nib-old`` copies.
    5. Start Nib.exe from the install folder **whichever way it went**, then remove the temporary
       folder this script lives in.

    Settings are not in the install folder (they are in %APPDATA%\\NibMascot), so nothing of yours
    is touched either way. A batch file rather than PowerShell, because a PowerShell *script* is
    subject to execution policy, which some machines lock down; only the wait goes through
    ``powershell -Command``, which is not, and it reads the paths from the environment rather than
    from its command line, so no folder name can break its quoting. UTF-8 (``chcp 65001``) so a
    folder named ``Müller`` survives. Paths are never echoed: a ``&`` in one would end the line.
    """
    def quoted(text) -> str:
        return str(text).replace("%", "%%")   # the only character a quoted set "X=..." still expands

    entries = build_entries(source) if entries is None else entries

    def remove(path: str, folder: bool) -> str:
        return f'rd /s /q "{path}" 2>nul' if folder else f'del /f /q "{path}" 2>nul'

    # Step 1: everything that is this Nib or runs from its folder, waited for together.
    wait = ("$d = $env:DST.TrimEnd('\\') + '\\'; "
            "$nib = { @(Get-Process -Id $env:OLDPID -ErrorAction SilentlyContinue) + "
            "@(Get-Process -Name Nib -ErrorAction SilentlyContinue | Where-Object "
            "{ $_.Path -and $_.Path.StartsWith($d, 'OrdinalIgnoreCase') }) }; "
            f"& $nib | Wait-Process -Timeout {int(wait_s)} -ErrorAction SilentlyContinue; "
            "if (@(& $nib).Count) { exit 3 }")
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "rem Nib's updater: swaps a new build in for the old one once it has exited - or puts the",
        "rem old one back - then starts Nib again. Written and started by updater.py; deletes",
        "rem itself when it is done.",
        "setlocal",
        f'set "SRC={quoted(source)}"',
        f'set "DST={quoted(install)}"',
        f'set "WORK={quoted(work)}"',
        f'set "LOG={quoted(log_path)}"',
        f'set "OLDPID={int(pid)}"',
        'echo Waiting for Nib to exit.>"%LOG%"',
        f'"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile'
        f' -NonInteractive -Command "{wait}"',
        "if errorlevel 1 (",
        '  echo Nib is still running from this folder - nothing was copied.>>"%LOG%"',
        "  goto relaunch",
        ")",
        "rem A leftover from an earlier update that could not finish tidying up.",
    ]
    for name, folder in entries:
        target, old = f"%DST%\\{quoted(name)}", f"%DST%\\{quoted(name)}.nib-old"
        lines.append(f'if exist "{old}" if exist "{target}" {remove(old, folder)}')
    lines.append("rem Out of the way, every one, or the swap does not start.")
    for name, _folder in entries:
        target = f"%DST%\\{quoted(name)}"
        lines.append(f'if exist "{target}" (ren "{target}" "{quoted(name)}.nib-old" || goto rollback)')
    lines += [
        'robocopy "%SRC%" "%DST%" /E /R:2 /W:1 /NP /NFL /NDL /LOG+:"%LOG%"',
        "rem robocopy: 0-7 is success of one kind or another, 8 and up is a failure.",
        "if errorlevel 8 goto rollback",
    ]
    for name, folder in entries:
        lines.append(remove(f"%DST%\\{quoted(name)}.nib-old", folder))
    lines += [
        'echo Updated.>>"%LOG%"',
        "goto relaunch",
        ":rollback",
        'echo The swap failed - putting the old Nib back.>>"%LOG%"',
    ]
    for name, folder in entries:
        target, old = f"%DST%\\{quoted(name)}", f"%DST%\\{quoted(name)}.nib-old"
        lines.append(f'if exist "{old}" ({remove(target, folder)} & ren "{old}" "{quoted(name)}"'
                     f' || echo Could not put back {quoted(name)}.>>"%LOG%")')
    lines += [
        'echo The old Nib is back.>>"%LOG%"',
        ":relaunch",
        'cd /d "%DST%"',
        'start "" "%DST%\\Nib.exe" ' + AFTER_UPDATE_FLAG + " %OLDPID%",
        '(goto) 2>nul & rd /s /q "%WORK%"',
        "",
    ]
    return "\r\n".join(lines)


def update_release(install: Path, *, current: str = __version__, http: Http | None = None,
                   progress: Progress = lambda _m: None, pid: int | None = None,
                   log_path: Path | None = None, can_write: Callable[[Path], bool] = writable,
                   make_workdir: Callable[[], str] = lambda: tempfile.mkdtemp(prefix="nib-update-"),
                   ) -> Outcome:
    """Fetch the latest release if it is newer, check it, unpack it, and write the helper."""
    http = http or UrllibHttp()
    progress("Checking for updates…")
    try:
        release = http.get_json(LATEST_RELEASE_URL, HTTP_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - every way the network fails reads the same to you
        log.info("release check failed: %s", exc)
        return Outcome(FAILED, "I couldn't reach GitHub to check for updates.")
    tag = str(release.get("tag_name") or "")
    newer = is_newer(tag, current)
    if newer is None:
        return Outcome(FAILED, "I couldn't make sense of the latest release's version.", tag[:40])
    if not newer:
        return Outcome(CURRENT, "I'm up to date.", f"Version {current}.")

    name = ASSET_TEMPLATE.format(tag=tag)
    asset = next((a for a in release.get("assets") or []
                  if isinstance(a, dict) and a.get("name") == name), None)
    url = str((asset or {}).get("browser_download_url") or "")
    if asset is None or not url.startswith("https://"):
        return Outcome(FAILED, f"{tag} is out, but it has no download for me yet.",
                       f"Expected {name} on the release page.")
    if not can_write(install):
        return Outcome(FAILED, "I can't write to my own folder, so I can't update myself.",
                       f"Download {name} from GitHub and extract it over {install} by hand.")

    work = Path(make_workdir())
    try:
        progress(f"Downloading {tag}…")
        archive = work / name
        try:
            http.download(url, archive, DOWNLOAD_TIMEOUT_S, MAX_DOWNLOAD_BYTES)
        except Exception as exc:  # noqa: BLE001
            log.info("download failed: %s", exc)
            return _discarded(work, Outcome(FAILED, f"The download of {tag} didn't finish."))
        digest = str(asset.get("digest") or "")
        if digest.lower().startswith("sha256:"):
            if sha256_of(archive) != digest.split(":", 1)[1].strip().lower():
                return _discarded(work, Outcome(
                    FAILED, "The download didn't match its checksum, so I threw it away."))
        elif digest:
            log.info("release digest %r is not sha256 - not checked", digest.split(":", 1)[0])
        problem = check_zip(archive)
        if problem:
            return _discarded(work, Outcome(
                FAILED, f"The download looks wrong ({problem}), so I threw it away."))
        unpacked = work / "new"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(unpacked)
        archive.unlink(missing_ok=True)
        helper = work / "nib-update.cmd"
        helper.write_text(
            helper_script(unpacked / "Nib", install, work, os.getpid() if pid is None else pid,
                          log_path or default_log_path()),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        log.exception("staging the release failed")
        return _discarded(work, Outcome(FAILED, "Something went wrong unpacking the update; nothing changed."))
    log.info("staged %s in %s", tag, work)
    return Outcome(HANDOFF, f"Got {tag}. Restarting…", helper=helper)


def _discarded(work: Path, outcome: Outcome) -> Outcome:
    shutil.rmtree(work, ignore_errors=True)
    return outcome


def discard(outcome: Outcome) -> None:
    """Throw away a staged release that is not going to be used after all."""
    if outcome.helper is not None:
        shutil.rmtree(outcome.helper.parent, ignore_errors=True)


def default_log_path() -> Path:
    """Next to nib.log, in Nib's own data folder - never the install folder being replaced."""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "NibMascot" / "update.log"


# --- running it, and starting again -----------------------------------------------------------------


def run_update(mode: Mode, *, progress: Progress = lambda _m: None, run: Runner = run_command,
               http: Http | None = None, log_path: Path | None = None,
               blocked: Callable[[], str] = lambda: "") -> Outcome:
    """The whole update for ``mode``. Never raises - a surprise becomes a FAILED outcome."""
    try:
        if mode.kind == MODE_GIT:
            return update_git(mode.app_dir, run=run, progress=progress, blocked=blocked)
        if mode.kind == MODE_RELEASE:
            return update_release(mode.app_dir, http=http, progress=progress, log_path=log_path)
        return Outcome(FAILED, mode.reason or "I can't update this copy myself.")
    except Exception:  # noqa: BLE001
        log.exception("update failed")
        return Outcome(FAILED, "Something went wrong while updating; nothing changed.")


def start(mode: Mode, *, progress: Progress, done: Callable[[Outcome], None],
          log_path: Path | None = None, blocked: Callable[[], str] = lambda: "") -> threading.Thread:
    """Run the update on its own thread. Every callback runs on that thread, not the caller's -
    ``blocked`` included, so it must only read state, never change it."""
    thread = threading.Thread(
        target=lambda: done(run_update(mode, progress=progress, log_path=log_path,
                                       blocked=blocked)),
        name="nib-update", daemon=True,
    )
    thread.start()
    return thread


def restart_command(pid: int) -> list[str]:
    """How to start the Nib that replaces process ``pid`` - the same way it was started.

    From source, under the windowless ``pythonw.exe`` next to the running interpreter, the same
    choice ``guard_service.core_command`` makes, so a Nib started from a console does not come back
    in one.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, AFTER_UPDATE_FLAG, str(pid)]
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        windowless = exe.with_name("pythonw.exe")
        if windowless.is_file():
            exe = windowless
    return [str(exe), str(Path(__file__).resolve().parent / "main.py"), AFTER_UPDATE_FLAG, str(pid)]


def spawn(argv: list[str] | str, cwd: Path) -> None:
    """Start ``argv`` detached from this process and with no window. Raises OSError if it can't.

    A string is a whole command line, passed to Windows exactly as written.

    Out of Nib's job object where Windows allows it, so whatever started Nib closing does not take
    the new one with it.
    """
    flags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    for extra in (_CREATE_BREAKAWAY_FROM_JOB, 0):
        try:
            subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, creationflags=flags | extra)
            return
        except OSError:
            if not extra:
                raise


def helper_command(helper: Path) -> str:
    """The command line that runs the helper: ``cmd /s /c ""<helper>""``, as one string.

    With /s, cmd takes off exactly the outer pair of quotes and runs what is inside as it stands.
    Without it - and Python's list quoting cannot add it - cmd applies its own rules, which break on
    a path with a ``&`` or ``^`` in it (a Windows user name may have either), and the helper never
    runs while Nib quits anyway.
    """
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    return f'"{comspec}" /d /s /c ""{helper}""'


def launch_restart(outcome: Outcome, pid: int | None = None) -> None:
    """Start whatever takes over from this Nib once it quits. Raises OSError if nothing could be."""
    pid = os.getpid() if pid is None else pid
    if outcome.kind == HANDOFF and outcome.helper is not None:
        # Not from inside the folder it deletes: a process's working directory cannot be removed.
        spawn(helper_command(outcome.helper), Path(tempfile.gettempdir()))
    else:
        spawn(restart_command(pid), app_dir())


def wait_for_exit(pid: int, timeout_s: float = AFTER_UPDATE_WAIT_S, *,
                  alive: Callable[[int], bool] = process_alive,
                  sleep: Callable[[float], None] = time.sleep,
                  clock: Callable[[], float] = time.monotonic) -> bool:
    """Wait, at most ``timeout_s``, for ``pid`` to exit. True if it has."""
    if pid <= 0 or pid == os.getpid():
        return True
    deadline = clock() + timeout_s
    while alive(pid):
        if clock() >= deadline:
            return False
        sleep(0.1)
    return True


def consume_after_update(argv: list[str], **wait) -> bool:
    """``--after-update <pid>`` at the front of ``argv``: wait for that Nib, then drop the two
    arguments so the rest of start-up never sees them. False (and ``argv`` untouched) otherwise."""
    if len(argv) < 2 or argv[1] != AFTER_UPDATE_FLAG:
        return False
    raw = argv[2] if len(argv) > 2 else ""
    del argv[1:3]
    if raw.isdigit():
        wait_for_exit(int(raw), **wait)
    return True
