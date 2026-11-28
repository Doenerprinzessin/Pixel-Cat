"""Anthropic's sign-in helper (``ant``, Anthropic's command-line tool), set up in one click.

* Download: the latest Windows (or macOS) build from Anthropic's official GitHub releases
  (github.com/anthropics/anthropic-cli).  It is used only if its SHA-256 matches the one
  GitHub publishes for the file *and* the one in the release's checksums file (ant.exe has
  no Windows code signature, so that is the check).  Only ant.exe is unpacked, into
  %LOCALAPPDATA%\\PixelCat\\tools\\ant - no installer, no admin rights, nothing on the PATH.
  On macOS the binary is ``ant`` (in ~/Library/Application Support/PixelCat/tools/ant), made
  executable after unpacking; the sign-in folder and its token files are readable by the user only.
* Sign-in: ``ant auth login`` runs hidden and Pixel Cat opens the sign-in page in the
  browser; when the user has approved it there, ant gets the answer on this PC by itself
  (no code to paste).  The sign-in is Pixel Cat's own: ant runs with ANTHROPIC_CONFIG_DIR
  set to %APPDATA%\\PixelCat\\anthropic (for that process only), so Claude Code and
  Anthropic's other tools on this PC keep theirs.  The SDK gets that profile directly
  (``client_config()`` -> ``Anthropic(config=...)``) and renews the token itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from ... import paths

log = logging.getLogger(__name__)

RELEASES = "https://api.github.com/repos/anthropics/anthropic-cli/releases/latest"
DOWNLOAD_PREFIX = "https://github.com/anthropics/anthropic-cli/releases/download/"
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_ZIP = 200 * 1024 * 1024
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # 0 off Windows: POSIX refuses any other flags
_lock = threading.Lock()


class AntError(Exception):
    """The message is for the user."""


def _macos() -> bool:
    return sys.platform == "darwin"


def _exe_name() -> str:
    return "ant" if _macos() else "ant.exe"


def tool_path() -> Path:
    return paths.local_data_dir() / "PixelCat" / "tools" / "ant" / _exe_name()


def signin_dir() -> Path:
    return paths.data_dir() / "anthropic"


def _arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64",
            "x86": "386", "i386": "386", "i686": "386"}.get(m, "amd64")


def _is_ant(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    text = (out.stdout + out.stderr).lower()
    return out.returncode == 0 and text.startswith("ant version") and "apache" not in text


def find() -> str | None:
    """Pixel Cat's own copy, else one the user already has (not Apache Ant, same name)."""
    exe = _exe_name()
    spots = [str(tool_path()), shutil.which("ant"), str(Path.home() / "go" / "bin" / exe),
             str(paths.local_data_dir() / "Programs" / "ant" / exe)]
    for exe in dict.fromkeys(s for s in spots if s and Path(s).is_file()):
        if _is_ant(exe):
            return exe
    return None


def _check_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        raise AntError("The download didn't come from Anthropic's GitHub page, so I stopped.")


def latest() -> dict[str, Any]:
    """The newest release's build for this PC (Windows, or macOS on a Mac): name, url, size, sha256."""
    try:
        r = requests.get(RELEASES, headers={"Accept": "application/vnd.github+json", "User-Agent": "PixelCat"},
                         timeout=20)
        rel = r.json()
    except (requests.RequestException, ValueError) as e:
        raise AntError("I couldn't reach Anthropic's download page - is the internet on?") from e
    if r.status_code != 200 or not isinstance(rel, dict):
        raise AntError("Anthropic's download page didn't answer properly - try again later.")
    version = str(rel.get("tag_name", "")).lstrip("v")
    assets = {a.get("name"): a for a in rel.get("assets") or [] if isinstance(a, dict)}
    name = f"ant_{version}_{'macos' if _macos() else 'windows'}_{_arch()}.zip"
    asset = assets.get(name)
    if not version or asset is None:
        raise AntError(f"There's no {'Mac' if _macos() else 'Windows'} version of Anthropic's sign-in helper "
                       "right now.")
    url = str(asset.get("browser_download_url", ""))
    if not url.startswith(DOWNLOAD_PREFIX):
        raise AntError("The download didn't come from Anthropic's GitHub page, so I stopped.")
    sums: set[str] = set()
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        sums.add(digest[7:].lower())
    sums_asset = assets.get(f"ant_{version}_checksums.txt")
    if sums_asset:
        try:
            surl = str(sums_asset.get("browser_download_url", ""))
            if not surl.startswith(DOWNLOAD_PREFIX):
                raise AntError("The checksums didn't come from Anthropic's GitHub page.")
            text = requests.get(surl, timeout=20).text
            for line in text.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                    sums.add(parts[0].lower())
        except requests.RequestException as e:
            raise AntError("I couldn't get the download's checksums - try again later.") from e
    if len(sums) != 1:
        raise AntError("I couldn't confirm the download's checksum, so I didn't use it.")
    return {"name": name, "url": url, "size": int(asset.get("size") or 0), "sha256": sums.pop(),
            "version": version}


def install(progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> str:
    """Download, check and unpack ant.exe (macOS: ant); returns its path."""
    with _lock:
        rel = latest()
        dest = tool_path()
        tmpdir = Path(tempfile.mkdtemp(prefix="pixelcat-ant-"))
        try:
            zpath = tmpdir / rel["name"]
            h = hashlib.sha256()
            done = 0
            with requests.get(rel["url"], stream=True, timeout=(20, 120),
                              headers={"User-Agent": "PixelCat"}) as r:
                _check_url(r.url)
                if r.status_code != 200:
                    raise AntError(f"The download didn't work (HTTP {r.status_code}).")
                total = int(r.headers.get("Content-Length") or rel["size"] or 0)
                with open(zpath, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        if cancel is not None and cancel.is_set():
                            raise AntError("Cancelled.")
                        done += len(chunk)
                        if done > MAX_ZIP:
                            raise AntError("The download is far too big - I stopped it.")
                        h.update(chunk)
                        f.write(chunk)
                        if progress is not None:
                            progress(done, total)
            if h.hexdigest() != rel["sha256"]:
                raise AntError("The download doesn't match Anthropic's checksum, so I threw it away.")
            with zipfile.ZipFile(zpath) as z:
                members = [i for i in z.infolist() if Path(i.filename).name.lower() == _exe_name() and not i.is_dir()]
                if len(members) != 1 or members[0].file_size > MAX_ZIP:
                    raise AntError("The download didn't contain the sign-in helper.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".tmp")
                with z.open(members[0]) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
                if os.name != "nt":
                    os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except OSError as e:
            raise AntError(f"I couldn't save the sign-in helper ({e.strerror or e}).") from e
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if not _is_ant(str(dest)):
            dest.unlink(missing_ok=True)
            raise AntError("The sign-in helper didn't start properly, so I removed it again.")
        log.info("ant %s set up in %s (sha256 checked)", rel["version"], dest.parent)
        return str(dest)


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_PROFILE", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["ANTHROPIC_CONFIG_DIR"] = str(signin_dir())
    return env


def login(exe: str, open_url: Callable[[str], Any], cancel: threading.Event | None = None,
          timeout: float = 300.0) -> None:
    """``ant auth login`` without a window: the sign-in page opens in the browser, ant waits
    for the answer on this PC.  Raises AntError if it doesn't finish."""
    signin_dir().mkdir(parents=True, exist_ok=True)
    _private(signin_dir())
    proc = subprocess.Popen([exe, "auth", "login", "--no-browser", "--callback-port", "0", "--timeout",
                             f"{int(timeout)}s"], env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    lines: list[str] = []
    opened = threading.Event()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip())
            url = line.strip()
            if not opened.is_set() and url.startswith("https://"):
                opened.set()
                open_url(url)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    end = time.monotonic() + timeout + 15
    while proc.poll() is None:
        if (cancel is not None and cancel.is_set()) or time.monotonic() > end:
            proc.kill()
            proc.wait(10)
            raise AntError("Sign-in cancelled." if cancel is not None and cancel.is_set()
                           else "Nobody signed in within 5 minutes - try again.")
        time.sleep(0.2)
    reader.join(5)
    if proc.returncode != 0 or client_config() is None:
        last = next((ln for ln in reversed(lines) if ln.strip()), "")
        log.warning("ant auth login ended with %s: %s", proc.returncode, last[:200])
        raise AntError("The sign-in didn't finish" + (f" ({last[:120]})." if last else "."))
    _private(signin_dir())
    log.info("signed in with Anthropic (Pixel Cat's own profile)")


def _private(d: Path) -> None:
    """Off Windows: the sign-in folder (and the tokens ant wrote into it) for this user only."""
    if os.name == "nt":
        return
    try:
        os.chmod(d, 0o700)
        for root, dirs, files in os.walk(d):
            for name in dirs:
                os.chmod(Path(root) / name, 0o700)
            for name in files:
                os.chmod(Path(root) / name, 0o600)
    except OSError:
        log.warning("couldn't make the sign-in folder private", exc_info=True)


def client_config() -> dict[str, Any] | None:
    """Pixel Cat's sign-in as the SDK's in-memory config (None: not signed in).  Only the
    non-secret profile file is read; the SDK reads and renews the tokens itself."""
    d = signin_dir()
    try:
        name = (d / "active_config").read_text(encoding="utf-8").strip() or "default"
    except OSError:
        name = "default"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    try:
        cfg = json.loads((d / "configs" / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    auth = cfg.get("authentication") if isinstance(cfg, dict) else None
    if not isinstance(auth, dict):
        return None
    creds = Path(str(auth.get("credentials_path") or d / "credentials" / f"{name}.json")).expanduser()
    if not creds.is_file():
        return None
    auth["credentials_path"] = str(creds)
    return cfg


def logout(exe: str | None) -> None:
    """Sign Pixel Cat's profile out (ant revokes it at Anthropic) and remove it."""
    if exe:
        try:
            subprocess.run([exe, "auth", "logout"], env=_env(), capture_output=True, timeout=60,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            log.warning("ant auth logout failed", exc_info=True)
    shutil.rmtree(signin_dir(), ignore_errors=True)
