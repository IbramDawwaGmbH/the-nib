"""Update and restart: which way this copy updates, the git path, the release path, comparing
versions, the helper that swaps the files, waiting out the old Nib, and the wheel tool itself.

Nothing here touches the network or this checkout. git and pip are a fake runner that records
every command - and the git path is checked to never issue anything but a fetch of origin and a
fast-forward. GitHub is a fake that hands back a real zip built in a temp directory. The one real
process this starts is the helper batch file itself, run against temp folders, with its "start Nib"
line swapped for one that writes a marker file - so it proves the relaunch happens even when the
copy fails, without ever starting a Nib.
"""

import inspect
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import logging  # noqa: E402

# The handlers below log their failures, which with nowhere configured would land on stderr.
logging.getLogger().addHandler(logging.NullHandler())

import updater as U  # noqa: E402
import version  # noqa: E402

SCRATCH = _scratch() / "update"
shutil.rmtree(SCRATCH, ignore_errors=True)
SCRATCH.mkdir(parents=True, exist_ok=True)
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh_dir(name):
    return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=SCRATCH))


print("\n--- comparing versions ---")
check("v1.2.0 reads as (1, 2, 0)", U.parse_version("v1.2.0") == (1, 2, 0))
check("1.2 is padded to (1, 2, 0)", U.parse_version("1.2") == (1, 2, 0))
check("a pre-release is not a version", U.parse_version("v1.3.0-rc1") is None)
check("nor is a word", U.parse_version("latest") is None and U.parse_version("") is None)
check("numerically: 1.10.0 is newer than 1.9.0", U.is_newer("v1.10.0", "1.9.0") is True)
check("a patch is newer", U.is_newer("v1.0.3", "1.0.2") is True)
check("the same version is not newer", U.is_newer("v1.0.2", "1.0.2") is False)
check("...even written differently", U.is_newer("v1.0", "1.0.0") is False)
check("an older one is not newer", U.is_newer("v1.0.1", "1.0.2") is False)
check("a major beats any minor", U.is_newer("v2.0.0", "1.99.99") is True)
check("junk compares as None, not as older or newer", U.is_newer("nightly", "1.0.2") is None)

changelog = (SRC / "CHANGELOG.md").read_text(encoding="utf-8")
released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
check("version.py matches the newest released CHANGELOG section (Unreleased skipped)",
      bool(released) and released[0] == version.__version__, (released[:1], version.__version__))
check("...and parses", U.parse_version(version.__version__) is not None)
release_yml = (SRC / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
check("release.yml refuses a tag that is not v + version.py, before it builds",
      "from version import __version__" in release_yml
      and release_yml.index("from version import __version__") < release_yml.index("pyinstaller nib.spec"))

print("\n--- which way this copy updates ---")
plain = fresh_dir("plain")
check("frozen: the release zip", U.detect_mode(plain, frozen=True).kind == U.MODE_RELEASE)
none = U.detect_mode(plain, frozen=False, which=lambda _n: "git.exe")
check("no .git: nothing to do, and a reason to say", none.kind == U.MODE_NONE and none.reason, none)
checkout = fresh_dir("checkout")
(checkout / ".git").mkdir()
check("a .git and git on PATH: the git path",
      U.detect_mode(checkout, frozen=False, which=lambda _n: "git.exe").kind == U.MODE_GIT)
nogit = U.detect_mode(checkout, frozen=False, which=lambda _n: None)
check("a .git but no git: nothing to do, and it says git is missing",
      nogit.kind == U.MODE_NONE and "git" in nogit.reason, nogit.reason)
worktree = fresh_dir("worktree")
(worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
check("a worktree's .git file counts too",
      U.detect_mode(worktree, frozen=False, which=lambda _n: "git.exe").kind == U.MODE_GIT)
check("run_update on MODE_NONE changes nothing and says why",
      U.run_update(none).kind == U.FAILED and U.run_update(none).message == none.reason)


print("\n--- the git path, against a fake git ---")
#: Anything that could rewrite history, throw work away or talk to another remote.
FORBIDDEN = {"pull", "rebase", "reset", "stash", "checkout", "switch", "clean", "push", "commit",
             "tag", "remote", "restore", "cherry-pick", "revert", "am", "apply"}


def subcommand(argv):
    """A git command line from its subcommand on: ``git -c k=v fetch origin`` -> ``[fetch, origin]``."""
    a = [str(x) for x in argv[1:]]
    while a[:1] == ["-c"]:
        a = a[2:]
    return a


def settings_of(argv):
    """The ``-c k=v`` settings in front of a git command's subcommand."""
    a = [str(x) for x in argv[1:]]
    found = []
    while a[:1] == ["-c"]:
        found.append(a[1])
        a = a[2:]
    return found


class FakeGit:
    """A checkout's answers, as data. Records every command, git and pip alike."""

    def __init__(self, **kw):
        self.branch = kw.get("branch", "main")
        self.upstream = kw.get("upstream", "origin/main")
        self.remote = kw.get("remote", "origin")
        self.dirty = kw.get("dirty", "")
        self.fetch = kw.get("fetch", 0)
        self.head = kw.get("head", "a" * 40)
        self.up = kw.get("up", "b" * 40)
        self.behind = kw.get("behind", True)
        self.ahead = kw.get("ahead", False)
        self.merge = kw.get("merge", 0)
        self.reqs = kw.get("reqs", False)
        self.pip = kw.get("pip", 0)
        self.missing = kw.get("missing", False)
        self.ssh_command = kw.get("ssh_command", "")
        #: Called as each fetch runs - a guard or focus session starting while it does.
        self.on_fetch = kw.get("on_fetch", lambda: None)
        self.calls = []
        self.timeouts = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append([str(a) for a in argv])
        self.timeouts.append(timeout)
        if argv[0] != "git":
            return self.pip, "", "ERROR: could not install"
        if self.missing:
            return 127, "", "git: not found"
        a = subcommand(argv)
        if a[:1] == ["symbolic-ref"]:
            return (0, self.branch + "\n", "") if self.branch else (1, "", "")
        if a[:2] == ["rev-parse", "--abbrev-ref"]:
            return (0, self.upstream, "") if self.upstream else (128, "", "no upstream")
        if a == ["config", "--get", "core.sshCommand"]:
            return (0, self.ssh_command, "") if self.ssh_command else (1, "", "")
        if a[:2] == ["config", "--get"]:
            return (0, self.remote, "") if self.remote else (1, "", "")
        if a[:1] == ["status"]:
            return 0, self.dirty, ""
        if a[:1] == ["fetch"]:
            self.on_fetch()
            return self.fetch, "", "fatal: unable to access 'https://github.com/...'"
        if a == ["rev-parse", "HEAD"]:
            return 0, self.head + "\n", ""
        if a == ["rev-parse", "@{upstream}"]:
            return 0, self.up + "\n", ""
        if a[:2] == ["merge-base", "--is-ancestor"]:
            forward = a[2] == "HEAD"
            return 0 if (self.behind if forward else self.ahead) else 1, "", ""
        if a[:1] == ["merge"]:
            if self.merge == 0:
                self.head = self.up
            return self.merge, "", "fatal: Not possible to fast-forward"
        if a[:2] == ["diff", "--quiet"]:
            return (1 if self.reqs else 0), "", ""
        return 99, "", f"unexpected: {a}"

    def git(self):
        return [c for c in self.calls if c[0] == "git"]

    def ran(self, sub):
        return [c for c in self.git() if subcommand(c)[:1] == [sub]]

    def pips(self):
        return [c for c in self.calls if c[0] != "git"]


def safe(fake):
    """Every git command was one that cannot lose work, and every command had a timeout."""
    for call in fake.git():
        rest = subcommand(call)
        sub = rest[0]
        if sub in FORBIDDEN:
            return False, call
        if sub == "merge" and "--ff-only" not in call:
            return False, call
        if sub == "fetch" and [x for x in rest[1:] if not x.startswith("-")] != ["origin"]:
            return False, call
        # A setting in front may only make the fetch give up sooner - never point it elsewhere.
        if any(not s.startswith(("http.lowSpeed", "core.sshCommand=ssh -o BatchMode=yes"))
               for s in settings_of(call)):
            return False, call
    if not all(t and t > 0 for t in fake.timeouts):
        return False, fake.timeouts
    return True, ""


def git_update(*, blocked=lambda: "", environ=None, **kw):
    fake = FakeGit(**kw)
    said = []
    outcome = U.update_git(checkout, run=fake, progress=said.append, python="py-exe",
                           blocked=blocked, environ={} if environ is None else environ)
    ok, bad = safe(fake)
    check(f"   ...[{kw or 'defaults'}] only safe git commands, all with timeouts", ok, bad)
    return outcome, fake, said


out, fake, _said = git_update(up="a" * 40)
check("up to date: says so", out.kind == U.CURRENT and "up to date" in out.message, out)
check("...and does not restart, merge or touch pip",
      out.kind != U.RESTART and not fake.ran("merge") and not fake.pips())
check("...and fetched origin first", len(fake.ran("fetch")) == 1)

out, fake, said = git_update()
check("behind: fast-forwards to the upstream", fake.ran("merge") == [["git", "merge", "--ff-only",
                                                                      "@{upstream}"]], fake.ran("merge"))
check("...then restarts", out.kind == U.RESTART, out)
check("...with requirements.txt unchanged, pip is left alone", not fake.pips())
check("...saying what it is doing as it goes", "Updating…" in said, said)
check("...fetching before it looks, and looking before it merges",
      [subcommand(c)[0] for c in fake.git()].index("fetch")
      < [subcommand(c)[0] for c in fake.git()].index("merge-base")
      < [subcommand(c)[0] for c in fake.git()].index("merge"))
fetch_settings = settings_of(fake.ran("fetch")[0])
check("the fetch gives up on a stalled transfer instead of waiting for ever",
      "http.lowSpeedLimit=1000" in fetch_settings and "http.lowSpeedTime=30" in fetch_settings,
      fetch_settings)
check("...and on ssh, fails rather than asking for a passphrase nobody can see",
      any("BatchMode=yes" in s and "ConnectTimeout" in s for s in fetch_settings), fetch_settings)
for label, env, kw in (("GIT_SSH_COMMAND set", {"GIT_SSH_COMMAND": "ssh -i mykey"}, {}),
                       ("GIT_SSH set (PuTTY's plink)", {"GIT_SSH": "plink.exe"}, {}),
                       ("core.sshCommand configured", {}, {"ssh_command": "ssh -i other"})):
    _out, fake, _said = git_update(environ=env, **kw)
    got = settings_of(fake.ran("fetch")[0])
    check(f"...but your own ssh command is left alone ({label})",
          not any("sshCommand" in s for s in got) and "http.lowSpeedLimit=1000" in got, got)

asked = []


def guard_goes_up():
    asked.append("fetch")


out, fake, said = git_update(on_fetch=guard_goes_up,
                             blocked=lambda: "Not while the screen guard is up." if asked else "")
check("a guard goes up during the fetch: stops before the merge, so nothing on disk changes",
      out.kind == U.FAILED and not fake.ran("merge") and not fake.pips(), (out, fake.ran("merge")))
check("...and says why", "guard" in out.message and out.sub, out)
asked.clear()
gate, fake = [], FakeGit()
U.update_git(checkout, run=fake, python="py-exe", environ={},
             blocked=lambda: gate.append(len(fake.calls)) or "")
check("the gate is asked once, after the fetch and right before the merge",
      len(gate) == 1 and subcommand(fake.calls[gate[0]])[0] == "merge"
      and any(subcommand(c)[0] == "fetch" for c in fake.calls[:gate[0]]), (gate, fake.calls))
out, fake, _said = git_update(up="a" * 40, blocked=lambda: "Not while the screen guard is up.")
check("...and only when there is something to merge: up to date is still up to date",
      out.kind == U.CURRENT, out)

out, fake, _ = git_update(dirty=" M main.py\n")
check("local changes to tracked files: refuses", out.kind == U.FAILED and "changed" in out.message, out)
check("...before fetching anything, and without stashing", not fake.ran("fetch") and not fake.ran("merge"))
check("untracked files do not count as changes",
      ["git", "status", "--porcelain", "--untracked-files=no"] in fake.git())

out, fake, _ = git_update(behind=False, ahead=False)
check("diverged from origin: refuses", out.kind == U.FAILED and "different ways" in out.message, out)
check("...and never merges", not fake.ran("merge"))

out, fake, _ = git_update(behind=False, ahead=True)
check("ahead of origin (unpushed commits): up to date, no merge, no restart",
      out.kind == U.CURRENT and not fake.ran("merge"), out)

out, fake, _ = git_update(upstream="")
check("no upstream: a friendly line, nothing fetched", out.kind == U.FAILED
      and "doesn't follow" in out.message and not fake.ran("fetch"), out)

out, fake, _ = git_update(upstream="fork/main", remote="fork")
check("an upstream on another remote: refuses, since only origin is fetched",
      out.kind == U.FAILED and "origin" in out.message and not fake.ran("fetch"), out)

out, fake, _ = git_update(branch="")
check("a detached HEAD: refuses", out.kind == U.FAILED and "branch" in out.message, out)

out, fake, _ = git_update(missing=True)
check("git missing at run time: says so", out.kind == U.FAILED and "git" in out.message, out)

out, fake, _ = git_update(fetch=128)
check("fetch fails (offline): says so and goes no further",
      out.kind == U.FAILED and "GitHub" in out.message and not fake.ran("merge"), out)

out, fake, _ = git_update(merge=1)
check("the fast-forward itself fails: nothing changed, no restart", out.kind == U.FAILED, out)
check("...and pip is not run for an update that did not happen", not fake.pips())

out, fake, said = git_update(reqs=True)
check("requirements.txt changed: pip installs it with this Python",
      fake.pips() == [["py-exe", "-m", "pip", "install", "--disable-pip-version-check", "-r",
                       "requirements.txt"]], fake.pips())
check("...with the long pip timeout", fake.timeouts[-1] == U.PIP_TIMEOUT_S, fake.timeouts[-1])
check("...then restarts, with nothing to apologise for", out.kind == U.RESTART and not out.sub, out)
check("...after saying so", "Updating my packages…" in said, said)
check("...and requirements.txt is compared between the old and new HEAD",
      ["git", "diff", "--quiet", "a" * 40, "b" * 40, "--", "requirements.txt"] in fake.git())

out, fake, _ = git_update(reqs=True, pip=1)
check("pip fails: the code update is kept and it still restarts", out.kind == U.RESTART
      and fake.ran("merge"), out)
check("...but says the packages did not update", "packages" in out.sub, out.sub)

src_run = inspect.getsource(U.run_command)
check("the real runner never opens a window", "creationflags=_CREATE_NO_WINDOW" in src_run)
check("...never waits on a credential prompt", '"GIT_TERMINAL_PROMPT"] = "0"' in src_run)
check("...and is always bounded", "timeout=timeout" in src_run)

# git hands its stderr on to ssh / git-remote-https. A child that does the same - starts a
# grandchild holding the output, then stalls - must still come back at the timeout, not when the
# grandchild finally lets go, and must not leave the grandchild behind.
GRANDPARENT = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(25)'])\n"
    "open(sys.argv[1], 'w').write(str(g.pid))\n"
    "time.sleep(25)\n"
)
pid_file = SCRATCH / "grandchild.pid"
pid_file.unlink(missing_ok=True)
began = time.monotonic()
code, _out, _err = U.run_command([sys.executable, "-c", GRANDPARENT, str(pid_file)], SCRATCH, 3)
took = time.monotonic() - began
check("a child whose own child holds the output: back at the timeout, not 25s later",
      code == 124 and took < 10, (code, round(took, 1)))
grandchild = int(pid_file.read_text()) if pid_file.exists() else 0
check("...and the grandchild is gone too, not left running",
      grandchild and not U.process_alive(grandchild), grandchild)
if grandchild and U.process_alive(grandchild):
    subprocess.run(["taskkill", "/F", "/PID", str(grandchild)], capture_output=True,
                   creationflags=U._CREATE_NO_WINDOW)
code, _out, err_text = U.run_command(
    [sys.executable, "-c", "import sys; sys.stderr.write('fatal: one\\r\\ntwo\\n'); sys.exit(3)"],
    SCRATCH, 30)
check("stderr and the exit code come back from the temp files", code == 3
      and err_text.splitlines()[:1] == ["fatal: one"], (code, err_text))
code, _out, _err = U.run_command(["nib-no-such-program-xyz"], SCRATCH, 5)
check("a missing program comes back as 127, not an exception", code == 127, code)
code, out_text, _err = U.run_command([sys.executable, "-c", "print('hi')"], SCRATCH, 30)
check("a real command's exit code and output come back", code == 0 and out_text.strip() == "hi",
      (code, out_text))


print("\n--- the release path, against a fake GitHub and a real zip ---")


def zip_bytes(entries):
    """A zip with exactly these names - set on the ZipInfo directly, so backslashes survive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            info = zipfile.ZipInfo("placeholder")
            info.filename = name
            archive.writestr(info, data)
    return buffer.getvalue()


GOOD = {"Nib/Nib.exe": b"MZ the new one", "Nib/_internal/base_library.zip": b"lib",
        "Nib/assets/frames.json": b"{}"}


class FakeHttp:
    def __init__(self, release=None, payload=b"", fail_json=False, fail_download=False):
        self.release = release
        self.payload = payload
        self.fail_json = fail_json
        self.fail_download = fail_download
        self.asked = []
        self.downloaded = []

    def get_json(self, url, timeout):
        self.asked.append((url, timeout))
        if self.fail_json:
            raise OSError("no network")
        return self.release

    def download(self, url, dest, timeout, limit):
        self.downloaded.append((url, timeout, limit))
        if self.fail_download:
            raise OSError("connection reset")
        Path(dest).write_bytes(self.payload)


def release_of(tag, payload, *, digest=None, name=None):
    asset = {"name": name or f"Nib-{tag}-windows-x64.zip",
             "browser_download_url": f"https://github.com/IbramDawwaGmbH/the-nib/releases/download/{tag}/x.zip"}
    if digest is not None:
        asset["digest"] = digest
    return {"tag_name": tag, "assets": [{"name": "source.tar.gz"}, asset]}


def release_update(http, *, current="1.0.2", can_write=lambda _p: True):
    install = fresh_dir("install")
    (install / "Nib.exe").write_bytes(b"MZ the old one")
    workdirs = []

    def make():
        work = fresh_dir("work")
        workdirs.append(work)
        return str(work)

    outcome = U.update_release(install, current=current, http=http, pid=4242,
                               log_path=SCRATCH / "update.log", can_write=can_write,
                               make_workdir=make)
    return outcome, install, workdirs


payload = zip_bytes(GOOD)
import hashlib  # noqa: E402

good_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
http = FakeHttp(release_of("v1.0.3", payload, digest=good_digest), payload)
out, install, works = release_update(http)
check("newer, digest good: staged for the helper", out.kind == U.HANDOFF and out.helper is not None, out)
check("...asking only the one releases/latest URL", [u for u, _t in http.asked] == [U.LATEST_RELEASE_URL])
check("...downloading the asset named for the tag",
      len(http.downloaded) == 1 and http.downloaded[0][0].endswith("/v1.0.3/x.zip"), http.downloaded)
check("...unpacked to the temp folder, not the install folder",
      (works[0] / "new" / "Nib" / "Nib.exe").read_bytes() == b"MZ the new one"
      and (install / "Nib.exe").read_bytes() == b"MZ the old one")
check("...with the zip itself cleaned up", not list(works[0].glob("*.zip")))
helper_text = out.helper.read_text(encoding="utf-8") if out.helper else ""
check("...and a helper that copies into this install, for this pid",
      str(install) in helper_text and "OLDPID=4242" in helper_text and str(works[0]) in helper_text)
check("discard() throws a staged release away", (U.discard(out), not works[0].exists())[1])

out, _install, works = release_update(FakeHttp(release_of("v1.0.3", payload), payload))
check("newer, no digest offered: still fine", out.kind == U.HANDOFF, out)
U.discard(out)

http = FakeHttp(release_of("v1.0.2", payload), payload)
out, _install, works = release_update(http)
check("the same version: up to date, nothing downloaded, no restart",
      out.kind == U.CURRENT and not http.downloaded and not works, out)

http = FakeHttp(release_of("v1.0.1", payload), payload)
out, _install, _works = release_update(http)
check("an older release (a re-tag, a rollback): up to date, nothing downloaded",
      out.kind == U.CURRENT and not http.downloaded, out)

http = FakeHttp(release_of("v1.0.3", payload, name="Nib-v1.0.3-linux.zip"), payload)
out, _install, _works = release_update(http)
check("no asset with our name: says so, nothing downloaded",
      out.kind == U.FAILED and "no download" in out.message and not http.downloaded, out)

http = FakeHttp(release_of("v1.0.3", payload, digest="sha256:" + "0" * 64), payload)
out, _install, works = release_update(http)
check("a bad digest: refused", out.kind == U.FAILED and "checksum" in out.message, out)
check("...and the download thrown away", works and not works[0].exists())

for label, entries in (
    ("a ../ entry", {**GOOD, "Nib/../../evil.txt": b"x"}),
    ("a backslash ..\\ entry", {**GOOD, "Nib\\..\\..\\evil.txt": b"x"}),
    ("an absolute entry", {**GOOD, "/evil.txt": b"x"}),
    ("a drive-letter entry", {**GOOD, "C:/evil.txt": b"x"}),
):
    bad = zip_bytes(entries)
    out, install, works = release_update(FakeHttp(release_of("v1.0.3", bad), bad))
    check(f"{label}: refused as climbing out", out.kind == U.FAILED and "climbs out" in out.message, out)
    check("...nothing extracted, and the temp folder gone",
          not works[0].exists() and not (install / "evil.txt").exists()
          and not (install.parent / "evil.txt").exists())

bad = zip_bytes({"Nib/readme.txt": b"no exe"})
out, _install, _works = release_update(FakeHttp(release_of("v1.0.3", bad), bad))
check("no Nib/Nib.exe in the zip: refused", out.kind == U.FAILED and "Nib/Nib.exe" in out.message, out)
bad = zip_bytes({"Nib.exe": b"MZ", "_internal/x": b"x"})
out, _install, _works = release_update(FakeHttp(release_of("v1.0.3", bad), bad))
check("...including one zipped without the Nib folder", out.kind == U.FAILED, out)
out, _install, _works = release_update(FakeHttp(release_of("v1.0.3", b"<html>"), b"<html>"))
check("not a zip at all (an error page): refused", out.kind == U.FAILED and "zip" in out.message, out)
ok_backslash = zip_bytes({"Nib\\Nib.exe": b"MZ", "Nib\\_internal\\x": b"x"})
backslash_zip = SCRATCH / "backslashes.zip"
backslash_zip.write_bytes(ok_backslash)
check("a Windows PowerShell 5 zip (backslashes) is still a good zip",
      U.check_zip(backslash_zip) == "", U.check_zip(backslash_zip))

http = FakeHttp(release_of("v1.0.3", payload), payload)
out, install, works = release_update(http, can_write=lambda _p: False)
check("an unwritable install folder (Program Files): refused up front",
      out.kind == U.FAILED and "write" in out.message and not http.downloaded and not works, out)
check("...saying to extract the zip by hand", "by hand" in out.sub and "Nib-v1.0.3-windows-x64.zip"
      in out.sub, out.sub)
check("the real writable() says yes to a temp folder", U.writable(fresh_dir("w")))
check("...and no to a folder that does not exist", not U.writable(SCRATCH / "missing" / "deeper"))

out, _install, _works = release_update(FakeHttp(fail_json=True))
check("offline: says it could not reach GitHub", out.kind == U.FAILED and "GitHub" in out.message, out)
http = FakeHttp(release_of("v1.0.3", payload), payload, fail_download=True)
out, _install, works = release_update(http)
check("a download that dies: says so and cleans up", out.kind == U.FAILED and not works[0].exists(), out)
out, _install, _works = release_update(FakeHttp(release_of("nightly", payload), payload))
check("a tag that is not a version: refused rather than guessed",
      out.kind == U.FAILED and "version" in out.message, out)

request = U.UrllibHttp._request(U.LATEST_RELEASE_URL, "application/json")
check("the request carries only the generic User-Agent", request.get_header("User-agent")
      == "Nib-updater" and "@" not in U.USER_AGENT)
try:
    U.UrllibHttp._request("http://github.com/", "x")
    plain_http = True
except ValueError:
    plain_http = False
check("plain http is refused", not plain_http)

print("\n--- the one module that talks to the network ---")
talkers = sorted(p.name for p in SRC.glob("*.py")
                 if re.search(r"^\s*(import|from)\s+(urllib\.request|http\.client|socket|requests)\b",
                              p.read_text(encoding="utf-8"), re.M))
check("only updater.py imports anything that makes a request", talkers == ["updater.py"], talkers)
check("...and it has one URL of its own", len(set(re.findall(r'"(https://[^"{]+)"',
                                                  inspect.getsource(U)))) == 1)


print("\n--- the helper that swaps the files ---")
text = U.helper_script(Path(r"C:\T\work\new\Nib"), Path(r"C:\Users\A B\Nib 100%"),
                       Path(r"C:\T\work"), 4242, Path(r"C:\Users\A B\update.log"),
                       entries=[("Nib.exe", False), ("_internal", True)])
lines = text.splitlines()


def at(fragment):
    return next((i for i, line in enumerate(lines) if fragment in line), -1)


check("it waits for the old Nib, bounded", "Wait-Process -Timeout" in text
      and "Get-Process -Id $env:OLDPID" in text)
check("...and for any other Nib.exe from the install folder (the guard core)",
      "Get-Process -Name Nib" in text and "StartsWith($d" in text)
check("...and skips the copy entirely if one is still there",
      -1 < at("Wait-Process") < at("if errorlevel 1 (") < at("goto relaunch") < at('ren "'))
check("it moves the old files aside before copying",
      -1 < at('ren "%DST%\\Nib.exe" "Nib.exe.nib-old" || goto rollback') < at("robocopy")
      and at('ren "%DST%\\_internal" "_internal.nib-old" || goto rollback') < at("robocopy"))
check("it copies with /E, never /MIR (a file of yours next to Nib.exe is never deleted)",
      "/E" in lines[at("robocopy")] and "/MIR" not in text)
check("a failed copy goes to the rollback", at("if errorlevel 8 goto rollback") == at("robocopy") + 2)
check("the rollback puts each old file back",
      'ren "%DST%\\_internal.nib-old" "_internal"' in text and 'ren "%DST%\\Nib.exe.nib-old" "Nib.exe"' in text)
check("it relaunches Nib.exe from the install folder, passing --after-update",
      'start "" "%DST%\\Nib.exe" --after-update %OLDPID%' in text)
check("...whichever way it went: every jump lands on the rollback or the relaunch, never past it",
      set(re.findall(r"\bgoto (\w+)", text)) == {"rollback", "relaunch"}
      and not any(line.lower().startswith("exit") for line in lines)
      and not lines[at('start ""')].lower().startswith("if"))
check("in order: wait, move aside, copy, rollback, relaunch, clean up",
      -1 < at("Wait-Process") < at('ren "') < at("robocopy") < at(":rollback") < at(":relaunch")
      < at('start ""') < at('rd /s /q "%WORK%"'))
check("a % in a path is escaped for cmd", 'set "DST=C:\\Users\\A B\\Nib 100%%"' in text)
check("...and PowerShell reads the paths from the environment, not from its own quotes",
      "$env:DST" in text and "C:\\Users" not in lines[at("Wait-Process")])
check("it reads as UTF-8, so non-ASCII folders survive", lines[1] == "chcp 65001 >nul")
check("it never touches %APPDATA% (your settings)", "APPDATA" not in text)
check("it never echoes a path (a & in one would end the line)",
      not any(re.match(r"\s*echo\b[^>]*%(SRC|DST|WORK)%", line) for line in lines))
staged = fresh_dir("entries")
(staged / "_internal").mkdir()
(staged / "Nib.exe").write_bytes(b"")
check("the things it swaps are the new build's own top level",
      U.build_entries(staged) == [("Nib.exe", False), ("_internal", True)], U.build_entries(staged))
command = U.helper_command(Path(r"C:\Users\R&D x^y\nib-update.cmd"))
check("it is started as cmd /s /c with the path in two pairs of quotes",
      ' /d /s /c ""C:\\Users\\R&D x^y\\nib-update.cmd""' in command and command.endswith('""'), command)
check("...as one command line, not a list Python would quote its own way",
      "helper_command(outcome.helper)" in inspect.getsource(U.launch_restart))


def lock(path):
    """Open ``path`` the way a scanner might: others may read it, nobody may delete or move it."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "could not open", str(path))
    return lambda: kernel32.CloseHandle(handle)


OLD_FILES = {"Nib.exe": b"old", "_internal/old.dll": b"old", "_internal/stale.dll": b"old"}


def run_helper(*, source_exists=True, old_pid=None, prepare=lambda install: None, wait_s=3):
    """The real batch file, started the way launch_restart starts it, against temp folders whose
    names have a & and a ^ in them, with "start Nib.exe" swapped for writing a marker file."""
    root = fresh_dir("helper R&D x^y")
    work, install = root / "work", root / "install"
    source = work / "new" / "Nib"
    (install / "_internal").mkdir(parents=True)
    for name, data in OLD_FILES.items():
        (install / name).write_bytes(data)
    (install / "yours.txt").write_text("keep me", encoding="utf-8")
    if source_exists:
        (source / "_internal").mkdir(parents=True)
        (source / "Nib.exe").write_bytes(b"new")
        (source / "_internal" / "lib.dll").write_bytes(b"lib")
    else:
        work.mkdir(parents=True)
    if old_pid is None:
        gone = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=U._CREATE_NO_WINDOW)
        gone.wait()
        old_pid = gone.pid
    script = U.helper_script(source, install, work, old_pid, root / "update.log", wait_s=wait_s)
    script = script.replace('start "" "%DST%\\Nib.exe" --after-update %OLDPID%',
                            'echo relaunched %OLDPID%>"%DST%\\relaunched.txt"')
    helper = work / "nib-update.cmd"
    helper.write_text(script, encoding="utf-8")
    release = prepare(install)
    try:
        subprocess.run(U.helper_command(helper), cwd=str(root), stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=120, creationflags=U._CREATE_NO_WINDOW)
    finally:
        if release:
            release()
    log_text = (root / "update.log").read_text(encoding="utf-8", errors="replace") \
        if (root / "update.log").exists() else ""
    return types.SimpleNamespace(root=root, work=work, install=install, pid=old_pid,
                                 log=" ".join(log_text.split()),
                                 relaunched=(install / "relaunched.txt").exists())


def all_old(install):
    return (all((install / n).is_file() and (install / n).read_bytes() == d
                for n, d in OLD_FILES.items())
            and not (install / "_internal" / "lib.dll").exists()
            and not list(install.glob("*.nib-old")))


if sys.platform == "win32":
    run = run_helper()
    check("run for real: the new files are in", (run.install / "Nib.exe").read_bytes() == b"new"
          and (run.install / "_internal" / "lib.dll").exists(), run.log[-160:])
    check("...nothing an old version left in _internal survives",
          not (run.install / "_internal" / "stale.dll").exists()
          and not (run.install / "_internal" / "old.dll").exists())
    check("...a file of yours in the folder is kept", (run.install / "yours.txt").exists())
    check("...the old copies are tidied away", not list(run.install.glob("*.nib-old")))
    check("...Nib is started again", run.relaunched
          and str(run.pid) in (run.install / "relaunched.txt").read_text())
    check("...and the temp folder is gone", not run.work.exists())

    run = run_helper(source_exists=False)
    check("the copy fails after the old files were moved: they are put back, every one",
          all_old(run.install), sorted(p.name for p in run.install.rglob("*")))
    check("...the old Nib is started again", run.relaunched)
    check("...the failure is written down", "putting the old Nib back" in run.log, run.log[-160:])
    check("...and the temp folder is still cleaned up", not run.work.exists())

    run = run_helper(prepare=lambda install: lock(install / "_internal" / "old.dll"))
    check("a file held open in _internal: every file is still the old one",
          all_old(run.install), sorted(p.name for p in run.install.rglob("*")))
    check("...the old Nib is started again, and it says why", run.relaunched
          and "putting the old Nib back" in run.log, run.log[-160:])

    run = run_helper(prepare=lambda install: lock(install / "Nib.exe"))
    check("Nib.exe itself held open: nothing changes either", all_old(run.install)
          and run.relaunched, sorted(p.name for p in run.install.rglob("*")))

    held = []

    def still_running(install):
        # A second Nib.exe from the install folder outliving the first, as a guard core can: a
        # copy of ping, renamed, pinging for longer than the helper waits.
        ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
        shutil.copyfile(ping, install / "Nib.exe")
        OLD_FILES["Nib.exe"] = (install / "Nib.exe").read_bytes()
        held.append(subprocess.Popen([str(install / "Nib.exe"), "-n", "30", "127.0.0.1"],
                                     stdout=subprocess.DEVNULL, creationflags=U._CREATE_NO_WINDOW))
        return lambda: None

    run = run_helper(prepare=still_running, wait_s=2)
    for process in held:
        process.kill()
        process.wait()
    check("another Nib.exe from the folder still running: nothing is copied",
          all_old(run.install), sorted(p.name for p in run.install.rglob("*")))
    check("...and the old Nib is still started, and it says why",
          run.relaunched and "still running" in run.log, run.log[-160:])
    OLD_FILES["Nib.exe"] = b"old"

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               creationflags=U._CREATE_NO_WINDOW)
    try:
        run = run_helper(old_pid=sleeper.pid, wait_s=2)
    finally:
        sleeper.kill()
        sleeper.wait()
    check("the old Nib outlasts the wait: nothing is copied", all_old(run.install)
          and run.relaunched and "still running" in run.log, run.log[-160:])

src_launch = inspect.getsource(U.launch_restart) + inspect.getsource(U.spawn)
check("the helper and the restart start with no window", "_CREATE_NO_WINDOW" in src_launch)
check("...and the helper is not run from inside the folder it deletes",
      "gettempdir()" in inspect.getsource(U.launch_restart))


print("\n--- waiting out the old Nib (--after-update) ---")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = 0

    def sleep(self, s):
        self.now += s
        self.slept += 1


clock = Clock()
polls = iter([True, True, True, False])
check("waits while the old pid is alive, then goes",
      U.wait_for_exit(1234, 20, alive=lambda _p: next(polls), sleep=clock.sleep,
                      clock=lambda: clock.now) and clock.slept == 3, clock.slept)
clock = Clock()
check("gives up after the timeout rather than hanging",
      U.wait_for_exit(1234, 20, alive=lambda _p: True, sleep=clock.sleep, clock=lambda: clock.now)
      is False and 19.0 <= clock.now <= 20.5, clock.now)
check("its own pid is never waited for", U.wait_for_exit(os.getpid(), 20, alive=lambda _p: True))
seen = []
argv = ["main.py", "--after-update", "1234", "--extra"]
check("--after-update <pid> is consumed",
      U.consume_after_update(argv, alive=lambda p: seen.append(p) or False) and argv == ["main.py", "--extra"],
      argv)
check("...after waiting on that pid", seen == [1234], seen)
argv = ["main.py"]
check("no flag: argv left alone", not U.consume_after_update(argv) and argv == ["main.py"])
argv = ["main.py", "--after-update", "junk"]
check("a junk pid: dropped, nothing waited for",
      U.consume_after_update(argv, alive=lambda p: seen.append(p) or True) and argv == ["main.py"]
      and seen == [1234])
check("the real liveness check: this process is alive", U.process_alive(os.getpid()))
gone = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=U._CREATE_NO_WINDOW)
gone.wait()
check("...and one that has exited is not", not U.process_alive(gone.pid))
check("the default wait is about twenty seconds", 10 <= U.AFTER_UPDATE_WAIT_S <= 30)

main_text = (SRC / "main.py").read_text(encoding="utf-8")
top = main_text.index('sys.argv[1] == "--after-update"')
check("main.py waits before Qt is even imported",
      top < main_text.index("import win_integration as win") < main_text.index("from PySide6"))

import main as M  # noqa: E402

src_main = inspect.getsource(M.main)
check("...and main() waits before it takes the single-instance mutex",
      -1 < src_main.index("updater.consume_after_update(sys.argv)")
      < src_main.index("win.acquire_single_instance()"))
check("the restart command carries --after-update and this pid",
      U.restart_command(777)[-2:] == ["--after-update", "777"])
check("...from source, main.py under the same interpreter family",
      U.restart_command(777)[1].endswith("main.py") and "python" in Path(U.restart_command(777)[0]).name.lower())
check("nib.spec bundles updater and version for the frozen --after-update path",
      '"updater", "version"' in (SRC / "nib.spec").read_text(encoding="utf-8"))


print("\n--- the wheel tool ---")
import tool_wheel as W  # noqa: E402
from tool_wheel import ToolWheel, WheelItem  # noqa: E402

holder = types.SimpleNamespace(
    config={"provider": "claude", "last_prompt": "", "mic_indicator_enabled": False,
            "wander_enabled": True, "paused": False, "default_mode": "chat"},
    behavior=types.SimpleNamespace(focus_active=False),
    _device_badge_kind=lambda: "",
)
items = M.NibApp._wheel_items(holder)
update = next((i for i in items if i.key == "update"), None)
check("the ring has Update and restart", update is not None)
check("...in the App group", update is not None and update.group == "App", update)
check("...labelled for what it does", update is not None and update.label == "Update and restart")
check("...last, as far from a stray flick as the ring allows", items[-1].key == "update")
check("its icon is drawn in code", "update" in W.ICONS and update.icon == "update")

image = QImage(48, 48, QImage.Format_ARGB32)
image.fill(Qt.transparent)
painter = QPainter(image)
painter.scale(2, 2)
painter.setPen(QPen(QColor("#000000"), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
W.ICONS["update"](painter)
painter.end()
inked = sum(1 for y in range(48) for x in range(48) if image.pixelColor(x, y).alpha() > 0)
check("...and actually paints something", inked > 80, inked)
wheel = ToolWheel(1.0)
wheel.open([WheelItem("update", "Update and restart", "update", group="App"),
            WheelItem("reset", "Send him home", "home", group="App")],
           QPoint(960, 1040), QRect(0, 0, 1920, 1080), sprite_radius=32, press_global=None)
wheel._progress = 1.0
wheel._hover = 0
check("...on the wheel, hovered, without raising", not wheel.grab().isNull())
wheel.close()

src_chosen = inspect.getsource(M.NibApp._on_tool_chosen)
check("picking it runs the update handler",
      "self._on_update_requested()" in src_chosen.split('key == "update"', 1)[1].split("elif", 1)[0])


print("\n--- refusing, and never two at once ---")
check("refusal: nothing in the way", U.refusal(running=False, guarding=False, focusing=False) == "")
check("refusal: already running comes first",
      "already" in U.refusal(running=True, guarding=True, focusing=True))
check("refusal: the guard", "guard" in U.refusal(running=False, guarding=True, focusing=False))
check("refusal: focus says after the session",
      "after your focus session" in U.refusal(running=False, guarding=False, focusing=True))

started, told, quits, launched, discarded, timers, guards = [], [], [], [], [], [], []
start_kw = []


def fake_nib(*, guarding=False, active=False, setting_up=False, focusing=False):
    nib = types.SimpleNamespace(
        _updating=False, _guarding=guarding, _restart_pending=False, _files_changing=False,
        guard=types.SimpleNamespace(active=active, setting_up=setting_up,
                                    request_guard=lambda: guards.append(True)),
        tool_wheel=types.SimpleNamespace(close=lambda: None),
        behavior=types.SimpleNamespace(focus_active=focusing),
        _update_bridge=types.SimpleNamespace(progress=types.SimpleNamespace(emit=lambda _t: None),
                                             finished=types.SimpleNamespace(emit=lambda _o: None)),
        _tell=lambda text, sub="", duration_ms=0: told.append((text, sub)),
        _on_quit=lambda: quits.append(True),
    )
    nib._guard_busy = lambda: M.NibApp._guard_busy(nib)
    nib._update_blocker = lambda: M.NibApp._update_blocker(nib)
    nib._restart_after_update = lambda o, waited=False: M.NibApp._restart_after_update(nib, o, waited)
    nib._merge_blocker = lambda: M.NibApp._merge_blocker(nib)
    nib._guard_start_blocker = lambda: M.NibApp._guard_start_blocker(nib)
    return nib


real = (U.start, U.detect_mode, U.launch_restart, U.discard, M.QTimer)
U.start = lambda mode, **kw: started.append(mode) or start_kw.append(kw)
U.detect_mode = lambda: U.Mode(U.MODE_GIT, checkout)
U.launch_restart = lambda outcome, pid=None: launched.append(outcome)
U.discard = lambda outcome: discarded.append(outcome)
M.QTimer = types.SimpleNamespace(singleShot=lambda ms, fn: timers.append((ms, fn)))
try:
    for label, kw, word in (("a guard up", {"guarding": True}, "guard"),
                            ("a guard starting (PIN setup open)", {"setting_up": True}, "guard"),
                            ("a guard the service reports active", {"active": True}, "guard"),
                            ("a focus session", {"focusing": True}, "focus")):
        told.clear()
        nib = fake_nib(**kw)
        M.NibApp._on_update_requested(nib)
        check(f"refused during {label}, and says so",
              not started and not nib._updating and told and word in told[-1][0], told)

    nib = fake_nib()
    M.NibApp._on_update_requested(nib)
    check("nothing in the way: the update starts, off the GUI thread",
          len(started) == 1 and nib._updating, (started, nib._updating))
    told.clear()
    M.NibApp._on_update_requested(nib)
    check("a second pick while it runs is refused - no double runs",
          len(started) == 1 and told and "already" in told[-1][0], told)

    U.detect_mode = lambda: U.Mode(U.MODE_NONE, plain, "I can't update this copy myself.")
    nib = fake_nib()
    told.clear()
    M.NibApp._on_update_requested(nib)
    check("a copy that cannot update: says why, starts nothing",
          len(started) == 1 and not nib._updating and "can't update" in told[-1][0], told)

    nib = fake_nib()
    nib._updating = True
    M.NibApp._on_update_finished(nib, U.Outcome(U.CURRENT, "I'm up to date."))
    check("up to date: says so, schedules no restart", not timers and not nib._updating
          and told[-1][0] == "I'm up to date.")
    M.NibApp._on_update_finished(nib, U.Outcome(U.FAILED, "nope"))
    check("failed: says so, schedules no restart", not timers and told[-1][0] == "nope")
    nib._updating = True
    M.NibApp._on_update_finished(nib, U.Outcome(U.RESTART, "Updated. Restarting…"))
    check("updated: says so, then schedules the restart", len(timers) == 1 and not quits)
    before = len(started)
    told.clear()
    U.detect_mode = lambda: U.Mode(U.MODE_GIT, checkout)
    M.NibApp._on_update_requested(nib)
    check("...and a pick before that restart fires is refused, not started alongside it",
          len(started) == before and told and "already" in told[-1][0], told)
    timers.pop()[1]()
    check("...which starts the new Nib, then quits through the normal path",
          len(launched) == 1 and quits == [True])

    blocker = start_kw[0].get("blocked")
    nib = fake_nib()
    check("the update thread is handed a check to make right before the merge",
          blocker is not None and blocker() == "", start_kw[0])
    nib.behavior.focus_active = True
    check("...which says no once a focus session has begun", "focus" in M.NibApp._merge_blocker(nib))
    nib = fake_nib(setting_up=True)
    nib._updating = True
    check("...or a guard is starting - and not merely because the update is running",
          "guard" in M.NibApp._merge_blocker(nib))
    nib = fake_nib()
    check("no guard is blocked before the merge is allowed", nib._guard_start_blocker() == "")
    check("the merge's yes marks the files as changing...", nib._merge_blocker() == ""
          and nib._files_changing)
    check("...so from then on (pip, the moment before the restart) no new guard starts",
          "restart" in nib._guard_start_blocker())
    guards.clear(), told.clear()
    M.NibApp._on_guard_requested(nib)
    check("...refused through the wheel too, with the reason", not guards and told
          and "restart me first" in told[-1][0], told)
    nib._updating = True
    M.NibApp._on_update_finished(nib, U.Outcome(U.FAILED, "The merge didn't go through."))
    check("an update that ends without restarting clears it - the fast-forward never happened",
          not nib._files_changing and nib._guard_start_blocker() == "")
    nib = fake_nib(guarding=True)
    check("a merge that is refused does not mark anything", nib._merge_blocker() != ""
          and not nib._files_changing)
    check("GuardService is given the blocker", "self.guard.start_blocker = self._guard_start_blocker"
          in inspect.getsource(M.NibApp.__init__))
    check("the guard modules are loaded before a checkout's files can change",
          {"guard_overlay", "guard_scene"} <= set(sys.modules))

    quits.clear(), launched.clear(), told.clear(), timers.clear()
    nib = fake_nib(focusing=True)
    nib._updating = True
    M.NibApp._restart_after_update(nib, U.Outcome(U.RESTART, "Updated. Restarting…"))
    check("a focus session started meanwhile: no restart yet, and it says when",
          not launched and not quits and "focus session" in told[-1][0], told)
    check("...it looks again in a few seconds, still counting as a running update",
          len(timers) == 1 and timers[0][0] == M.UPDATE_RESTART_RETRY_MS and nib._updating)
    check("...and this process, still the old code, now refuses to start a guard",
          nib._restart_pending)
    guards.clear(), told.clear()
    M.NibApp._on_guard_requested(nib)
    check("a guard asked for meanwhile: refused, and it says to restart first",
          not guards and told and "restart" in told[-1][0], told)
    nib.guard.active = True
    M.NibApp._on_guard_requested(nib)
    check("...but a guard already up still gets its Lock now", guards == [True])
    nib.guard.active = False
    told.clear()
    timers.pop()[1]()
    check("still focusing at the next look: waits again, without saying it all over again",
          not launched and len(timers) == 1 and not told, (told, timers))
    nib.behavior.focus_active = False
    timers.pop()[1]()
    check("the session is over: it says it is restarting first, rather than vanishing",
          not launched and told and "Restarting" in told[-1][0] and len(timers) == 1, told)
    timers.pop()[1]()
    check("...then starts the new Nib and quits", len(launched) == 1 and quits == [True])

    quits.clear(), launched.clear(), told.clear(), timers.clear(), discarded.clear()
    nib = fake_nib(setting_up=True)
    nib._updating = True
    staged = U.Outcome(U.HANDOFF, "Got v1.0.3. Restarting…", helper=SCRATCH / "x" / "nib-update.cmd")
    M.NibApp._restart_after_update(nib, staged)
    check("a guard started meanwhile on the release build: no restart yet, the download is kept",
          not launched and not quits and not discarded and len(timers) == 1, told)
    check("...it says it will restart once the guard is down", "guard is down" in told[-1][0], told)
    check("...and guards are not refused for it - nothing on disk has changed",
          not nib._restart_pending)
    nib.guard.setting_up = False
    timers.pop()[1]()
    timers.pop()[1]()
    check("...and once the guard is gone, the staged release is handed over",
          launched == [staged] and quits == [True])
    quits.clear(), launched.clear(), timers.clear()

    def cannot(outcome, pid=None):
        raise OSError("no")

    U.launch_restart = cannot
    nib = fake_nib()
    nib._updating = True
    told.clear()
    M.NibApp._restart_after_update(nib, U.Outcome(U.RESTART, "Updated. Restarting…"))
    check("the new Nib cannot be started: this one stays, and says so",
          not quits and "couldn't start" in told[-1][0], told)
    check("...and a later pick can try again", not nib._updating)
    guards.clear()
    M.NibApp._on_guard_requested(nib)
    check("...while guards wait for the restart by hand: the files on disk are new",
          nib._restart_pending and not guards)
finally:
    U.start, U.detect_mode, U.launch_restart, U.discard, M.QTimer = real

src_quit = inspect.getsource(M.NibApp._restart_after_update)
check("the restart goes through the tray's own Quit", "self._on_quit()" in src_quit)
src_tell = inspect.getsource(M.NibApp._tell)
check("nothing is said over a guard", "self._guarding or self.guard.active" in src_tell)
check("the outcome comes back to the UI thread through a Qt signal",
      "finished = Signal(object)" in inspect.getsource(M._UpdateBridge))
check("the work runs on its own daemon thread", "daemon=True" in inspect.getsource(U.start))

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "update and restart behaves")
sys.exit(1 if fails else 0)
