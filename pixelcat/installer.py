"""Install, update and uninstall Pixel Cat for the current user - no admin rights.

PixelCat-Setup.exe is the same program as PixelCat.exe (one build, two names): when the
file name contains "setup" or "install" (or with --install) it shows this installer
instead of the cat.  Uninstalling runs the installed exe with --uninstall (that is what
Settings -> Apps -> Uninstall starts).

Installing:
* copies the exe to %LOCALAPPDATA%\\Programs\\PixelCat (Windows' folder for per-user apps),
* adds "Pixel Cat" to the Start menu (and, if wanted, to the desktop),
* registers it under HKCU so it is listed in Settings -> Apps with an Uninstall button,
* moves an existing "Start with Windows" entry to the installed copy,
* asks a running cat to close first (it saves and quits by itself - nothing is killed).
The cat's memory and settings (%APPDATA%\\PixelCat) are shared, so they survive updates.
Uninstalling removes all of that again; the memory and settings only if asked to.

For tests, PIXELCAT_TEST_ROOT moves every location (folders and registry keys) somewhere
harmless, and PIXELCAT_INSTANCE keeps test cats apart from the user's own.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import __version__, paths
from .adapters.windows import elevation, system

log = logging.getLogger(__name__)

EXE_NAME = "PixelCat.exe"
LINK_NAME = "Pixel Cat.lnk"
APP_TITLE = "Pixel Cat"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PixelCat"
_USER_PROGRAMS = "5CD7AEE2-2219-4A67-B85D-6C9CE15660CB"     # %LOCALAPPDATA%\Programs
_START_MENU = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"        # Start menu \ Programs


class InstallError(Exception):
    """Shown to the user."""


def _known(guid: str) -> Path | None:
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        g = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        out = ctypes.c_wchar_p()
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        fn.restype = ctypes.c_long
        ok = fn(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0 and out.value
        path = Path(out.value) if ok else None
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Layout:
    install_dir: Path
    programs_dir: Path          # Start menu
    desktop_dir: Path | None
    uninstall_key: str          # under HKCU
    run_key: str                # under HKCU ("Start with Windows")
    data_dir: Path              # the cat's memory and settings
    tools_dir: Path | None = None   # helpers the cat fetched (Anthropic's sign-in helper)

    @property
    def exe(self) -> Path:
        return self.install_dir / EXE_NAME

    @property
    def start_link(self) -> Path:
        return self.programs_dir / LINK_NAME

    @property
    def desktop_link(self) -> Path | None:
        return self.desktop_dir / LINK_NAME if self.desktop_dir else None


def layout() -> Layout:
    root = os.environ.get("PIXELCAT_TEST_ROOT")
    if root:
        r = Path(root)
        return Layout(r / "Programs" / "PixelCat", r / "StartMenu", r / "Desktop",
                      r"Software\PixelCatInstallTest\Uninstall\PixelCat", r"Software\PixelCatInstallTest\Run",
                      r / "AppData" / "PixelCat", r / "Local" / "PixelCat")
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Layout((_known(_USER_PROGRAMS) or local / "Programs") / "PixelCat",
                  _known(_START_MENU) or roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                  paths.shell_folder("desktop"), UNINSTALL_KEY, system.RUN_KEY, roaming / "PixelCat",
                  local / "PixelCat")


def is_setup_name(path: str | Path) -> bool:
    """PixelCat-Setup.exe, "PixelCat-Setup (1).exe", PixelCatInstaller.exe ... (only the built exe)."""
    return bool(getattr(sys, "frozen", False)) and bool(re.search(r"setup|install", Path(path).stem, re.I))


# ---------------------------------------------------------------- registry
def _reg_read(key: str, name: str) -> Any:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def installed_version(lay: Layout) -> str | None:
    """The installed version, or None if Pixel Cat isn't installed."""
    if not lay.exe.is_file():
        return None
    return str(_reg_read(lay.uninstall_key, "DisplayVersion") or "?")


def _write_uninstall_entry(lay: Layout) -> None:
    import winreg

    exe = str(lay.exe)
    size_kb = max(1, lay.exe.stat().st_size // 1024)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, lay.uninstall_key, 0, winreg.KEY_WRITE) as k:
        for name, value in (("DisplayName", APP_TITLE), ("DisplayVersion", __version__), ("Publisher", APP_TITLE),
                            ("DisplayIcon", f"{exe},0"), ("InstallLocation", str(lay.install_dir)),
                            ("UninstallString", f'"{exe}" --uninstall'),
                            ("QuietUninstallString", f'"{exe}" --uninstall --quiet'),
                            ("InstallDate", datetime.now().strftime("%Y%m%d"))):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, num in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, num)


def _delete_key(key: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


def _autostart_points_here(lay: Layout, value: str) -> bool:
    v = value.strip().strip('"').lower()
    return v.startswith(str(lay.install_dir).lower())


def _move_autostart(lay: Layout) -> None:
    """If the cat already starts with Windows, start the installed copy from now on."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, lay.run_key, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            value = winreg.QueryValueEx(k, system.RUN_VALUE)[0]
            if value and re.search(r"pixelcat", str(value), re.I):
                winreg.SetValueEx(k, system.RUN_VALUE, 0, winreg.REG_SZ, f'"{lay.exe}"')
                log.info("autostart now starts %s", lay.exe)
    except OSError:
        pass


def _remove_autostart(lay: Layout) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, lay.run_key, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            value = winreg.QueryValueEx(k, system.RUN_VALUE)[0]
            if _autostart_points_here(lay, str(value)):
                winreg.DeleteValue(k, system.RUN_VALUE)
    except OSError:
        pass


# ---------------------------------------------------------------- the work
def install(lay: Layout, src: Path, desktop_shortcut: bool) -> Path:
    """Copy, shortcuts, Settings -> Apps entry, autostart.  The cat must not be running."""
    src = Path(src)
    if not src.is_file():
        raise InstallError(f"can't find the program to install ({src})")
    lay.install_dir.mkdir(parents=True, exist_ok=True)
    if src.resolve() != lay.exe.resolve():
        tmp = lay.exe.with_name(EXE_NAME + ".new")
        shutil.copyfile(src, tmp)
        for _ in range(20):                      # the old exe may take a moment to be released
            try:
                os.replace(tmp, lay.exe)
                break
            except PermissionError:
                time.sleep(0.25)
        else:
            tmp.unlink(missing_ok=True)
            raise InstallError("the old Pixel Cat is still in use - quit it (right-click the cat -> Quit) "
                               "and try again")
    try:
        os.remove(str(lay.exe) + ":Zone.Identifier")   # downloaded mark: no warning on every start
    except OSError:
        pass
    lay.programs_dir.mkdir(parents=True, exist_ok=True)
    if not elevation.make_lnk(lay.start_link, lay.exe, workdir=lay.install_dir, icon=lay.exe,
                              description="Pixel Cat - a pixel-art desktop pet with a local AI chat"):
        raise InstallError("couldn't create the Start menu entry")
    dl = lay.desktop_link
    if dl is not None:
        if desktop_shortcut:
            dl.parent.mkdir(parents=True, exist_ok=True)
            elevation.make_lnk(dl, lay.exe, workdir=lay.install_dir, icon=lay.exe, description="Pixel Cat")
        else:
            dl.unlink(missing_ok=True)
    _write_uninstall_entry(lay)
    _move_autostart(lay)
    log.info("installed Pixel Cat %s to %s", __version__, lay.install_dir)
    return lay.exe


def uninstall(lay: Layout, delete_data: bool) -> list[str]:
    """Everything except the program folder itself (see remove_program_folder).
    Returns what couldn't be removed."""
    left: list[str] = []
    for link in (lay.start_link, lay.desktop_link):
        if link is not None:
            try:
                link.unlink(missing_ok=True)
            except OSError:
                left.append(str(link))
    _remove_autostart(lay)
    _delete_key(lay.uninstall_key)
    log.info("uninstalled Pixel Cat (data %s)", "deleted" if delete_data else "kept")
    if delete_data and lay.data_dir.exists():
        root = logging.getLogger()
        for h in list(root.handlers):            # our own log file lives in there
            if isinstance(h, logging.FileHandler):
                h.close()
                root.removeHandler(h)
        shutil.rmtree(lay.data_dir, ignore_errors=True)
        if lay.data_dir.exists():
            left.append(str(lay.data_dir))
        else:
            from .persistence import forget_folder

            forget_folder(lay.data_dir)
        if lay.tools_dir is not None:
            shutil.rmtree(lay.tools_dir, ignore_errors=True)
    return left


def remove_program_folder(lay: Layout) -> None:
    """Delete the program folder.  If this very exe runs from there (it can't delete itself
    while running), a hidden helper deletes it a moment after we exit."""
    d = lay.install_dir
    if not d.exists():
        return
    running_here = Path(sys.executable).resolve().parent == d.resolve()
    if not running_here:
        shutil.rmtree(d, ignore_errors=True)
        return
    q = str(d)
    script = (f'for /l %i in (1,1,60) do @(if exist "{q}\\" (rmdir /s /q "{q}" 2>nul & '
              f'if exist "{q}\\" ping -n 2 127.0.0.1 >nul))')
    flags = 0x08000000 | 0x00000200            # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(f'cmd.exe /d /s /c "{script}"', cwd=str(d.parent), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch(exe: Path) -> None:
    """Start the installed cat as its own process (it outlives the installer)."""
    flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    for extra in (0x01000000, 0):              # CREATE_BREAKAWAY_FROM_JOB if allowed
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags | extra, close_fds=True)
            return
        except OSError:
            continue


# ---------------------------------------------------------------- the AI part (Ollama + model)
OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"   # Ollama's own link (their GitHub release)
OLLAMA_SIGNER = "CN=Ollama Inc."                                    # whose signature the installer must carry
OLLAMA_SETUP_GB = 1.9
MODEL_GB = 22.0          # qwen3.5:35b-a3b, the model in the bundled config.json
MODEL_RAM_GB = 24.0      # what it takes while loaded (64k context)


def total_ram_gb() -> float:
    try:
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ax", ctypes.c_ulonglong)]

        m = _MS()
        m.len = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total / 2**30
    except Exception:  # noqa: BLE001
        return 16.0


@dataclass
class AiPlan:
    """The AI part of Setup.  It looks at the PC first (memory, graphics card, CPU, disk) and
    recommends what suits it: the best local model that fits with room to spare - or an
    online AI when only the tiniest ones would, so a weak PC doesn't lag."""
    ollama: Path | None        # Ollama on this PC
    model: str                 # the local model to set up (the recommended one, or what the user picked)
    model_ready: bool          # already downloaded
    ram_gb: float
    choice: str = ""           # a model tag, "online", "keep" (what's set up now) or "none"
    hw: Any = None             # this PC (domain.Hardware)
    advice: Any = None         # what suits it (llm.models.Advice)
    current: str = ""          # what the cat uses now ("" on a new install)
    current_ok: bool = False   # ...and it suits this PC: "keep" is offered

    @property
    def local(self) -> bool:
        return self.choice in ("", self.model)

    @property
    def needed(self) -> bool:
        """Is there anything to do (download, or a setting to write)?"""
        if self.choice in ("keep", "none"):
            return False
        if self.choice == "online":
            return True
        return self.ollama is None or not self.model_ready or self.advice is not None

    @property
    def recommended(self) -> str:
        if self.advice is None:
            return self.model
        return "online" if self.advice.online else self.advice.model.tag

    def choose(self, key: str) -> None:
        from .llm import models
        from .llm.model_status import model_on_disk

        self.choice = key
        if models.find(key) is not None:
            self.model = key
            self.model_ready = model_on_disk(key)

    def options(self) -> list[tuple[str, str]]:
        """(key, label) for the drop-down, the recommendation first."""
        from .llm import models
        from .llm.external import label as ext_label

        out: list[tuple[str, str]] = []
        rec = self.recommended
        if self.current_ok:
            cur = models.label(self.current) if models.find(self.current) else ext_label(self.current) \
                if ":" in self.current and not models.find(self.current) else "an online AI"
            out.append(("keep", f"Keep what I use now: {cur}"))
        keys = [rec] + [m.tag for m in models.MODELS if m.tag != rec] + (["online"] if rec != "online" else [])
        for key in keys:
            out.append((key, self._label(key, key == rec)))
        out.append(("none", "No AI chat for now"))
        return out

    def _label(self, key: str, rec: bool) -> str:
        from .llm import models
        from .llm.model_status import model_on_disk

        tag = " (recommended)" if rec else ""
        if key == "online":
            return "Online AI: ChatGPT or Claude" + tag
        m = models.find(key)
        assert m is not None
        if not rec and self.hw is not None:
            tag = {"too_big": " (too big for this PC)", "slow": " (slow on this PC)"}.get(models.fit(m, self.hw), "")
        if model_on_disk(m.tag):
            tag += " ✓"
        return f"{m.name} {m.short} · {m.download_gb:g} GB{tag}"

    def describe(self, key: str | None = None) -> str:
        """The note under the drop-down for one option."""
        from .llm import models

        key = key or self.choice or self.model
        adv = self.advice
        if key == "keep":
            return "Nothing changes."
        if key == "none":
            return "No AI for now - run Setup again whenever you want it."
        if key == "online":
            why = adv.why if adv is not None and adv.online else "ChatGPT or Claude answer from their servers."
            return (f"{why} Nothing to download now. After installing: right-click the cat → Settings → AI model → "
                    "External AI models, and sign in with ChatGPT (uses your ChatGPT plan) or Claude.")
        m = models.find(key)
        if m is None:
            return ""
        if adv is not None and key == self.recommended:
            head = adv.why
        elif self.hw is not None:
            head = {"too_big": "Too big for this PC: it would fill the memory and make everything slow.",
                    "slow": "Fits, but answers slowly on this CPU."}.get(models.fit(m, self.hw),
                                                                       f"{m.name} {m.short} fits this PC.")
        else:
            head = f"{m.name} {m.short}."
        from .llm.model_status import model_on_disk

        ready = self.model_ready if m.tag == self.model else model_on_disk(m.tag)
        parts = [head]
        if self.ollama is None:
            parts.append(f"Gets Ollama, the free AI engine ({OLLAMA_SETUP_GB:g} GB from ollama.com, no admin rights).")
        if not ready:
            parts.append(f"The model ({m.download_gb:g} GB) then downloads in the background while the cat runs.")
        return " ".join(parts)


def _user_config(lay: Layout) -> dict[str, Any]:
    from .settings import user_settings

    try:
        return user_settings(lay.data_dir)
    except Exception:  # noqa: BLE001
        return {}


def ai_plan(lay: Layout, updating: bool | None = None) -> AiPlan:
    """What the AI part should do on this PC: looks at the hardware and recommends."""
    from .adapters.windows.hardware import detect
    from .llm import models
    from .llm.external import parse
    from .llm.model_status import find_ollama_app, model_on_disk
    from .settings import Config

    hw = detect()
    adv = models.advise(hw)
    user = _user_config(lay)
    updating = installed_version(lay) is not None if updating is None else updating
    current = str(user.get("model") or Config().model) if updating else ""
    ollama = find_ollama_app()
    cur_m = models.find(current) if current else None
    current_ok = bool(updating and (
        parse(current) is not None or user.get("ai_mode") == "online"
        or (cur_m is not None and ollama is not None and model_on_disk(cur_m.tag)
            and models.fit(cur_m, hw) != "too_big")))
    if updating and user.get("ai_mode") == "online" and parse(current) is None:
        current = "online"
    local = adv.model.tag if adv.model is not None else models.DEFAULT
    plan = AiPlan(ollama, local, model_on_disk(local), hw.ram_gb, "", hw, adv, current, current_ok)
    plan.choose("keep" if current_ok else plan.recommended)
    return plan


def download(url: str, dest: Path, progress: Callable[[int, int], None], cancel: threading.Event) -> None:
    import requests

    tmp = dest.with_name(dest.name + ".part")
    done = 0
    with requests.get(url, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                if cancel.is_set():
                    raise InstallError("cancelled")
                fh.write(chunk)
                done += len(chunk)
                progress(done, total)
    os.replace(tmp, dest)


def signature_of(path: Path) -> tuple[str, str]:
    """(status, signer) of a program's digital signature, as Windows checks it."""
    env = dict(os.environ, PIXELCAT_SIGNED_FILE=str(path))
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:PIXELCAT_SIGNED_FILE; "
          "[string]$s.Status; [string]$s.SignerCertificate.Subject")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                       capture_output=True, text=True, timeout=60, creationflags=0x08000000)
    lines = (r.stdout or "").splitlines() + ["", ""]
    return lines[0].strip(), lines[1].strip()


def check_ollama_setup(path: Path) -> None:
    """Only ever run a downloaded installer that Windows confirms is signed by Ollama."""
    status, signer = signature_of(path)
    if status != "Valid" or not signer.startswith(OLLAMA_SIGNER + ","):
        raise InstallError(f"the downloaded file isn't signed by Ollama ({status or 'no signature'}"
                           f"{', ' + signer.split(',')[0] if signer else ''}), so I didn't run it")
    if elevation.exe_needs_admin(str(path)):
        raise InstallError("Ollama's installer wants admin rights this time - please install Ollama yourself "
                           "from ollama.com")


def run_ollama_setup(path: Path) -> None:
    r = subprocess.run([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], timeout=1800)
    if r.returncode != 0:
        raise InstallError(f"Ollama's installer stopped (code {r.returncode})")


def _write_ai_settings(lay: Layout, plan: AiPlan) -> None:
    """What the cat should use: the chosen model, and on PCs where memory is tight the AI only
    loads when the chat needs it (and leaves again soon) - so the PC doesn't lag."""
    from .llm import models
    from .settings import Config, save_user_settings

    user = _user_config(lay)
    values: dict[str, Any] = {}
    if plan.choice == "online":
        values = {"ai_mode": "online", "preload_on_start": False}
    else:
        if plan.model != Config().model or "model" in user:
            values["model"] = plan.model
        if user.get("ai_mode", "local") != "local":
            values["ai_mode"] = "local"
        if plan.advice is not None and plan.advice.light:
            values.update(preload_on_start=False, keep_alive="3m")
        m = models.find(plan.model)
        if m is not None and plan.hw is not None and models.fit(m, plan.hw) == "too_big":
            values.update(preload_on_start=False, keep_alive="1m")   # (the user's own choice: at least not at start)
    if values:
        save_user_settings(values, lay.data_dir)
        log.info("AI settings for this PC: %s", values)


def _save_ai_choice(lay: Layout, plan: AiPlan) -> None:
    """The user agreed: the cat may download the model (written into the cat's protected save)."""
    from .persistence import load_state, save_state

    st = load_state(lay.data_dir)
    st.model_download_ok = True
    save_state(st, lay.data_dir, touch=False)


def setup_ai(lay: Layout, plan: AiPlan, progress: Callable[[str], None], cancel: threading.Event,
             url: str = OLLAMA_SETUP_URL, runner: Callable[[Path], None] = run_ollama_setup,
             finder: Callable[[], Path | None] | None = None) -> str:
    """Install Ollama if needed and let the cat download the model.  Returns a note for the user."""
    import tempfile

    from .llm import models
    from .llm.model_status import find_ollama_app

    finder = finder or find_ollama_app
    if plan.choice in ("keep", "none"):
        return ""
    if plan.choice == "online":
        _write_ai_settings(lay, plan)
        return ("Pixel Cat uses an online AI on this PC. To start: right-click the cat → Settings → AI model → "
                "External AI models, and sign in with ChatGPT or Claude.")
    if plan.ollama is None:
        dest = Path(tempfile.gettempdir()) / "PixelCat-OllamaSetup.exe"
        try:
            def show(done: int, total: int) -> None:
                pct = f"{int(100 * done / total)}% " if total else ""
                tot = f" of {total / 1e9:.1f}" if total else ""
                progress(f"Downloading Ollama… {pct}({done / 1e9:.1f}{tot} GB)")
            download(url, dest, show, cancel)
            progress("Checking that it really comes from Ollama…")
            check_ollama_setup(dest)
            progress("Installing Ollama… (about a minute)")
            runner(dest)
        finally:
            for p in (dest, dest.with_name(dest.name + ".part")):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        if finder() is None:
            raise InstallError("Ollama's installer finished, but I can't find Ollama")
        log.info("Ollama installed")
    _write_ai_settings(lay, plan)
    _save_ai_choice(lay, plan)
    if plan.model_ready:
        return "The AI chat is ready."
    m = models.find(plan.model)
    gb = f" ({m.download_gb:g} GB)" if m else ""
    return (f"The AI model {plan.model}{gb} downloads in the background while the cat runs - "
            "the chat shows how far it is.")


# ---------------------------------------------------------------- quiet mode (no window)
def run_quiet(args: Any) -> int:
    lay = layout()
    if args.uninstall:
        if system.close_running(10.0) == "still running":
            return 2
        left = uninstall(lay, bool(getattr(args, "delete_data", False)))
        remove_program_folder(lay)
        return 0 if not left else 1
    if system.close_running(10.0) == "still running":
        return 2
    try:
        exe = install(lay, Path(sys.executable), not getattr(args, "no_desktop_shortcut", False))
    except (InstallError, OSError) as exc:
        log.error("install failed: %s", exc)
        return 1
    rc = 0
    if getattr(args, "with_ai", False):
        plan = ai_plan(lay)
        if plan.choice == "keep":
            plan.choose(plan.recommended) if not plan.current_ok else None
        if plan.needed:
            try:
                log.info(setup_ai(lay, plan, log.info, threading.Event()))
            except Exception as exc:  # noqa: BLE001 - the cat itself is installed
                log.error("AI setup failed: %s", exc)
                rc = 3
    if getattr(args, "launch", False):
        launch(exe)
    return rc


# ---------------------------------------------------------------- the window
def run_window(mode: str) -> int:
    """mode: "install" or "uninstall"."""
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .sprites.library import SpriteLibrary

    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    qapp.setApplicationName("PixelCat Setup")
    sprites = SpriteLibrary("charcoal")
    win = InstallerWindow(mode, layout(), Path(sys.executable), sprites)
    win.setWindowIcon(QIcon(sprites.face_icon(32)))
    win.present()
    qapp.exec()
    if win.remove_folder_on_exit:
        remove_program_folder(win.lay)
    return win.exit_code


def _make_window_class():  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QPoint, QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
    import html

    from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                                   QWidget)

    from .adapters.windows.monitors import enumerate_monitors
    from .domain import primary
    from .sprites.cat_art import Pose
    from .sprites.library import render_text
    from .ui import theme

    class _InstallerWindow(QWidget):
        def __init__(self, mode: str, lay: Layout, src: Path, sprites) -> None:  # type: ignore[no-untyped-def]
            super().__init__(None)
            self.mode, self.lay, self.src, self.sprites = mode, lay, src, sprites
            self.exit_code = 1
            self.remove_folder_on_exit = False
            self._drag: QPoint | None = None
            self._busy = False
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.NoDropShadowWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowTitle("Uninstall Pixel Cat" if mode == "uninstall" else "Pixel Cat Setup")
            try:
                mon = primary(enumerate_monitors())
                dpi, self._work = mon.dpi, mon.work
            except Exception:  # noqa: BLE001
                dpi, self._work = 96, (0, 0, 1280, 720)
            self.u = u = theme.unit_for_dpi(dpi)
            font_px = theme.font_px_for_dpi(dpi, 10)
            small_px = theme.font_px_for_dpi(dpi, 8.5)
            self.cat = QLabel()
            self.cat.setFixedSize(32 * u, 32 * u)
            self.text = QLabel()
            self.text.setWordWrap(True)
            self.text.setTextFormat(Qt.TextFormat.RichText)
            self.box = QCheckBox()
            self.ai_box = QCheckBox()           # (unused: the drop-down below says what to set up)
            self.ai_title = QLabel()            # "AI chat - your PC: 16 GB memory · ... "
            self.ai_title.setWordWrap(True)
            self.ai_title.setTextFormat(Qt.TextFormat.RichText)
            self.ai_combo = QComboBox()         # what to use: the recommendation first
            self.ai_combo.currentIndexChanged.connect(self._ai_changed)
            self.ai_note = QLabel()
            self.ai_note.setWordWrap(True)
            self.ai_note.setObjectName("status")
            self.plan = ai_plan(lay) if mode == "install" else None
            self._cancel_ev = threading.Event()
            self._progress = ""
            self._shown_progress = ""
            self._ai_running = False
            self.status = QLabel()
            self.status.setWordWrap(True)
            self.status.setObjectName("status")
            self.ok = QPushButton()
            self.cancel = QPushButton()
            self.cancel.setObjectName("noBtn")
            self.ok.clicked.connect(self._on_ok)
            self.cancel.clicked.connect(self._on_cancel)
            top = QHBoxLayout()
            top.setSpacing(4 * u)
            top.addWidget(self.cat, 0, Qt.AlignmentFlag.AlignTop)
            top.addWidget(self.text, 1)
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            buttons.addWidget(self.ok)
            buttons.addWidget(self.cancel)
            lay_v = QVBoxLayout(self)
            lay_v.setContentsMargins(7 * u, 17 * u, 8 * u, 7 * u)
            lay_v.setSpacing(4 * u)
            lay_v.addLayout(top)
            lay_v.addWidget(self.box)
            lay_v.addWidget(self.ai_box)
            lay_v.addWidget(self.ai_title)
            lay_v.addWidget(self.ai_combo)
            lay_v.addWidget(self.ai_note)
            lay_v.addWidget(self.status)
            lay_v.addLayout(buttons)
            self.setStyleSheet(
                theme.chat_stylesheet(max(1, u // 2 + u % 2), font_px)
                + f"QLabel {{ color:{theme.current().text}; font-family:'Segoe UI'; font-size:{font_px}px; }}"
                + f"QLabel#status {{ color:{theme.current().muted}; font-size:{small_px}px; }}"
                + f"QCheckBox {{ color:{theme.current().text}; font-family:'Segoe UI'; font-size:{font_px}px; "
                  f"spacing:{2 * u}px; }}"
                + f"QComboBox {{ background:{theme.current().field}; color:{theme.current().text}; "
                  f"border:{u}px solid {theme.current().ink}; font-family:'Segoe UI'; font-size:{small_px}px; "
                  f"padding:{u}px {2 * u}px; }}"
                + f"QComboBox QAbstractItemView {{ background:{theme.current().field}; color:{theme.current().text};"
                  f" selection-background-color:{theme.current().accent}; font-family:'Segoe UI'; "
                  f"font-size:{small_px}px; }}")
            self.setFixedWidth(205 * u)
            self.old = installed_version(lay)
            self._welcome()

        # ------------------------------------------------------------ screens
        def _pose(self, pose: Pose) -> None:
            self.cat.setPixmap(self.sprites.cat(pose, False, self.u).pixmap)

        def _screen(self, pose: Pose, html: str, ok: str | None, cancel: str | None, box: str | None = None,
                    checked: bool = False, status: str = "",
                    ai: tuple[str | None, bool, str] | None = None) -> None:
            self._pose(pose)
            self.text.setText(html)
            self.box.setVisible(box is not None)
            if box is not None:
                self.box.setText(box)
                self.box.setChecked(checked)
            ai_title, ai_choose, ai_note = ai if ai is not None else (None, False, "")
            self.ai_box.setVisible(False)
            self.ai_title.setVisible(ai_title is not None)
            self.ai_title.setText(ai_title or "")
            self.ai_combo.setVisible(bool(ai_choose))
            self.ai_note.setText(ai_note)
            self.ai_note.setVisible(bool(ai_note))
            self.status.setText(status)
            self.status.setVisible(bool(status))
            for b, label in ((self.ok, ok), (self.cancel, cancel)):
                b.setVisible(label is not None)
                b.setText(label or "")
                b.setEnabled(True)
            self._fit()

        def _fit(self) -> None:
            """Exactly as tall as the content (wrapped labels make adjustSize guess too tall)."""
            lay = self.layout()
            lay.activate()
            h = lay.totalHeightForWidth(self.width())
            if h > 0:
                self.setFixedHeight(h)
            else:
                self.adjustSize()

        def _welcome(self) -> None:
            self.step = "confirm"
            where = f"<br><span style='color:{theme.current().muted}'>{self.lay.install_dir}</span>"
            if self.mode == "uninstall":
                self._screen(Pose(body="sit", eyes="half", ears="back", mouth="flat", blush=False),
                             "<b>Uninstall Pixel Cat?</b><br>The cat leaves your desktop, the Start menu and "
                             "Settings → Apps.", "UNINSTALL", "CANCEL",
                             "Also delete its memory and settings", False,
                             f"That is {self.lay.data_dir}: settings, chat permissions, your Cat Pass progress, "
                             "logs and the backups of files it edited. Otherwise it remembers you next time. "
                             "Ollama and its models stay - they have their own entry in Settings → Apps.")
                return
            if self.old is None:
                head = f"<b>Install Pixel Cat {__version__}?</b>"
                desktop = True
            else:
                same = self.old == __version__
                head = (f"<b>Reinstall Pixel Cat {__version__}?</b>" if same else
                        f"<b>Update Pixel Cat {self.old} → {__version__}?</b>")
                dl = self.lay.desktop_link
                desktop = bool(dl and dl.exists())
            plan = self.plan
            ai = None
            if plan is not None:
                self.ai_combo.blockSignals(True)
                self.ai_combo.clear()
                for key, label in plan.options():
                    self.ai_combo.addItem(label, key)
                self.ai_combo.setCurrentIndex(max(0, self.ai_combo.findData(plan.choice)))
                self.ai_combo.blockSignals(False)
                muted = theme.current().muted
                pc = (f"<br><span style='color:{muted}'>Your PC: {html.escape(plan.hw.summary())}</span>"
                      if plan.hw is not None else "")
                warn = ""
                if plan.current and not plan.current_ok and plan.current != "online":
                    from .llm import models as _m
                    cm = _m.find(plan.current)
                    if cm is not None and plan.hw is not None and _m.fit(cm, plan.hw) == "too_big":
                        warn = (f"<br><span style='color:{muted}'>{cm.name} {cm.short}, which the cat uses now, is "
                                "too big for this PC - that's what makes it lag.</span>")
                ai = (f"<b>AI chat</b>{pc}{warn}", True, plan.describe(plan.choice))
            self._screen(Pose(body="sit", eyes="sparkle", mouth="smile"),
                         f"{head}<br>Just for you - no admin rights needed. You'll find it in the Start menu."
                         f"{where}", "INSTALL" if self.old is None else "UPDATE", "CANCEL",
                         "Put a shortcut on my desktop", desktop,
                         "Your cat keeps its memory and settings." if self.old else "", ai)

        def _ai_changed(self, _i: int) -> None:
            if self.plan is None:
                return
            key = self.ai_combo.currentData() or "none"
            self.ai_note.setText(self.plan.describe(key))
            self.ai_note.setVisible(True)
            self._fit()

        def _working(self, text: str) -> None:
            self._screen(Pose(body="walk", eyes="open"), f"<b>{text}</b>", None, None)

        def _error(self, text: str) -> None:
            self.step = "error"
            self._screen(Pose(body="sit", eyes="wide", mouth="o", blush=False), f"<b>Mrrp, that didn't work.</b><br>{text}",
                         "TRY AGAIN", "CANCEL")

        def _done(self, left: list[str] | None = None, note: str = "") -> None:
            self.step = "done"
            self.exit_code = 0
            if self.mode == "uninstall":
                self.remove_folder_on_exit = True
                extra = ("<br>These couldn't be removed: " + ", ".join(left)) if left else ""
                self._screen(Pose(body="loaf", eyes="content", mouth="w"),
                             "<b>Pixel Cat is uninstalled.</b><br>Bye bye, and thanks for all the pets! 🐾" + extra,
                             None, "CLOSE")
            else:
                self._screen(Pose(body="sit", eyes="happy", mouth="smile"),
                             "<b>All set!</b><br>Pixel Cat is in your Start menu. To remove it later: "
                             "Settings → Apps → Pixel Cat → Uninstall.", "START PIXEL CAT", "CLOSE", status=note)

        # ------------------------------------------------------------ actions
        def _in_background(self, fn: Callable[[], Any], then: Callable[[dict], None]) -> None:
            box: dict[str, Any] = {}

            def work() -> None:
                try:
                    box["result"] = fn()
                except Exception as exc:  # noqa: BLE001 - shown in the window
                    log.exception("setup step failed")
                    box["error"] = exc

            th = threading.Thread(target=work, daemon=True)
            th.start()

            def poll() -> None:
                self._show_progress()
                if th.is_alive():
                    QTimer.singleShot(50, poll)
                else:
                    then(box)

            QTimer.singleShot(50, poll)

        def _set_progress(self, text: str) -> None:      # from the worker thread
            self._progress = text

        def _show_progress(self) -> None:
            if self._progress and self._progress != self._shown_progress:
                self._shown_progress = self._progress
                self.text.setText(f"<b>{self._progress}</b>")
            stoppable = self._ai_running and not self._cancel_ev.is_set()
            if stoppable and self.cancel.isHidden():
                self.cancel.setText("SKIP THE AI PART")
                self.cancel.setVisible(True)
                self.cancel.setEnabled(True)
                self._fit()

        def _on_ok(self) -> None:
            if self.step == "done":
                launch(self.lay.exe)
                self.close()
                return
            if self._busy:
                return
            self._busy = True
            want_box = self.box.isChecked()
            self._ai_choice = (self.ai_combo.currentData() or "none") if not self.ai_combo.isHidden() else "keep"
            if self.plan is not None:
                self.plan.choose(self._ai_choice)
            self._want_ai = self._ai_choice not in ("keep", "none")
            self._working("Asking a running Pixel Cat to close…")
            self._in_background(lambda: system.close_running(10.0), lambda r: self._after_close(r, want_box))

        def _after_close(self, r: dict, want_box: bool) -> None:
            if r.get("result") == "still running":
                self._busy = False
                self._error("Pixel Cat is still running (maybe an older version). Right-click the cat → Quit, "
                            "then press TRY AGAIN.")
                return
            if self.mode == "uninstall":
                self._working("Saying goodbye…")
                self._in_background(lambda: uninstall(self.lay, want_box), self._after_work)
            else:
                self._working("Installing…")
                self._in_background(lambda: self._install_work(want_box), self._after_work)

        def _install_work(self, want_box: bool) -> str:
            install(self.lay, self.src, want_box)
            if not (self._want_ai and self.plan is not None and self.plan.needed):
                return ""
            self._ai_running = True
            try:
                return setup_ai(self.lay, self.plan, self._set_progress, self._cancel_ev)
            except Exception as exc:  # noqa: BLE001 - the cat itself is installed
                if self._cancel_ev.is_set():
                    return "AI setup skipped - run Setup again whenever you want it."
                log.exception("AI setup failed")
                return f"The AI part didn't work: {exc}. You can run Setup again later."
            finally:
                self._ai_running = False

        def _after_work(self, r: dict) -> None:
            self._busy = False
            if "error" in r:
                self._error(str(r["error"]))
                return
            if self.mode == "uninstall":
                self._done(r.get("result"))
            else:
                self._done(None, str(r.get("result") or ""))

        def _on_cancel(self) -> None:
            if self._busy:
                if self._ai_running and not self._cancel_ev.is_set():
                    self._cancel_ev.set()            # stop the Ollama download; the cat stays installed
                    self.cancel.setText("STOPPING…")
                    self.cancel.setEnabled(False)
                return
            self.close()

        def present(self) -> None:
            self.adjustSize()
            x0, y0, x1, y1 = self._work
            self.move(QPoint(x0 + (x1 - x0 - self.width()) // 2, y0 + (y1 - y0 - self.height()) // 3))
            self.show()
            self.raise_()
            self.activateWindow()

        # ------------------------------------------------------------ chrome
        def _close_rect(self) -> QRect:
            u = self.u
            return QRect(self.width() - 15 * u, 3 * u, 9 * u, 9 * u)

        def paintEvent(self, _e: QPaintEvent) -> None:
            p = QPainter(self)
            u = self.u
            theme.draw_pixel_panel(p, self.rect(), u)
            title = "UNINSTALL" if self.mode == "uninstall" else "PIXEL CAT SETUP"
            theme.draw_title_bar(p, self.width(), u, title, self.sprites.face_icon(9 * u))
            p.drawPixmap(self._close_rect().topLeft(), self.sprites.icon("close", u))
            p.end()

        def mousePressEvent(self, e: QMouseEvent) -> None:
            pos = e.position().toPoint()
            if self._close_rect().contains(pos):
                self._on_cancel()
            elif pos.y() <= 14 * self.u:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, e: QMouseEvent) -> None:
            if self._drag is not None:
                self.move(e.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
            self._drag = None

        def keyPressEvent(self, e) -> None:  # type: ignore[no-untyped-def]
            if e.key() == Qt.Key.Key_Escape:
                self._on_cancel()
            elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.ok.isVisible():
                self._on_ok()

    return _InstallerWindow


class _Lazy:
    """InstallerWindow is created on first use (Qt is only imported when needed)."""

    _cls = None

    def __call__(self, *a, **k):  # type: ignore[no-untyped-def]
        if _Lazy._cls is None:
            _Lazy._cls = _make_window_class()
        return _Lazy._cls(*a, **k)


InstallerWindow = _Lazy()
