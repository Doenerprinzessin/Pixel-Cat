"""Smart setup test (developer tool): Setup looks at the PC and recommends a local model or an
online AI, and the cat follows it so a weak PC doesn't lag.  Everything happens under
build/advisortest (APPDATA, the install places); nothing is downloaded.

* This PC's hardware is read (memory, graphics card + its memory, CPU, disk).
* Recommendations for example PCs (an old laptop ... a 64 GB workstation).
* What Setup writes: online -> no local AI and nothing loaded at start; a local model on a PC
  with little memory -> only loaded when chatting, and gone soon after; the default on a strong
  PC -> nothing; keep / none -> nothing.
* Updating: a model that is too big is pointed out and the one that fits is preselected; a
  setup that suits the PC (or an online model) is kept.
* In the app: online -> no local AI started, the chat says how to connect one; a model too big
  for the PC isn't loaded at start and the chat says so; the menu marks BEST / SLOW / TOO BIG.
Usage:  python tools/advisor_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "build" / "advisortest"
shutil.rmtree(TEST, ignore_errors=True)
(TEST / "AppData").mkdir(parents=True)
os.environ["APPDATA"] = str(TEST / "AppData")
os.environ["PIXELCAT_TEST_ROOT"] = str(TEST / "root")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelcat.adapters.windows import hardware, installer  # noqa: E402
from pixelcat.adapters.windows.hardware import Gpu, Hardware  # noqa: E402
from pixelcat.llm import models  # noqa: E402

app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


PCS = {  # name: (hardware, expected: "online" or a model tag, light)
    "an old laptop: 4 GB, 2 cores": (Hardware(3.9, 2, "", True, [], 50), "online", True),
    "an 8 GB laptop, 4 cores, built-in graphics": (Hardware(7.8, 4, "", True, [Gpu("Intel(R) UHD Graphics", .1, True)],
                                                            100), "online", True),
    "8 GB without AVX2 (an old CPU)": (Hardware(7.8, 8, "", False, [], 100), "online", True),
    "12 GB, 4 cores": (Hardware(11.8, 4, "", True, [], 100), "qwen3.5:4b", True),
    "16 GB, 4 cores": (Hardware(15.8, 4, "", True, [], 100), "qwen3.5:4b", True),
    "16 GB, 8 cores": (Hardware(15.8, 8, "", True, [], 100), "qwen3.5:9b", True),
    "16 GB + an RTX 3060 (12 GB)": (Hardware(15.8, 8, "", True, [Gpu("NVIDIA GeForce RTX 3060", 12, False)], 200),
                                    "qwen3.5:9b", True),
    "32 GB, 8 cores": (Hardware(31.7, 8, "", True, [], 200), "qwen3.5:35b-a3b", False),
    "32 GB but only 10 GB free disk": (Hardware(31.7, 8, "", True, [], 10), "qwen3.5:9b", False),
    "64 GB + an RTX 4090 (24 GB)": (Hardware(63.7, 16, "", True, [Gpu("NVIDIA GeForce RTX 4090", 24, False)], 500),
                                    "qwen3.5:35b-a3b", False),
}


def plan_for(hw: Hardware, updating: bool = False, user: dict | None = None):  # type: ignore[no-untyped-def]
    hardware._cached = hw
    lay = installer.layout()
    shutil.rmtree(lay.data_dir, ignore_errors=True)
    lay.data_dir.mkdir(parents=True)
    if user is not None:
        (lay.data_dir / "config.json").write_text(json.dumps(user), "utf-8")
    return lay, installer.ai_plan(lay, updating=updating)


def written(lay) -> dict:  # type: ignore[no-untyped-def]
    p = lay.data_dir / "config.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def main() -> int:  # noqa: C901 - a linear test script
    hw = hardware.detect(fresh=True)
    check("this PC is read: memory, graphics, CPU, disk", hw.ram_gb > 1 and hw.cores >= 1 and hw.disk_free_gb > 0
          and "GB memory" in hw.summary(), hw.summary())
    for name, (h, want, light) in PCS.items():
        a = models.advise(h)
        got = "online" if a.online else (a.model.tag if a.model else "-")
        check(f"{name} -> {want}{' (loads only when chatting)' if light else ''}", got == want and a.light == light,
              f"{got}, light {a.light}")
    tiny = models.advise(PCS["an 8 GB laptop, 4 cores, built-in graphics"][0])
    check("...the weak laptop is told why, and that a tiny local model would be slow and basic",
          "lag" in tiny.why and "Tiny" in tiny.why and tiny.model is not None and tiny.model.tag == "qwen3.5:0.8b")

    # what Setup writes ------------------------------------------------------------------------------
    ran: list[str] = []
    lay, plan = plan_for(PCS["an 8 GB laptop, 4 cores, built-in graphics"][0])
    opts = plan.options()
    check("Setup on a weak PC: an online AI preselected and first in the list, small models marked",
          plan.choice == "online" and opts[0] == ("online", "Online AI: ChatGPT or Claude (recommended)")
          and any("too big for this PC" in lbl for _k, lbl in opts), str(opts[:3]))
    note = installer.setup_ai(lay, plan, lambda _t: None, __import__("threading").Event(),
                              runner=lambda p: ran.append(str(p)), finder=lambda: None)
    check("...installing: nothing downloaded, the cat set to the online AI, nothing loaded at start",
          not ran and written(lay) == {"ai_mode": "online", "preload_on_start": False}
          and "External AI models" in note, str(written(lay)))
    lay, plan = plan_for(PCS["16 GB, 8 cores"][0])
    plan.ollama, plan.model_ready = Path("ollama app.exe"), True
    installer.setup_ai(lay, plan, lambda _t: None, __import__("threading").Event(),
                       runner=lambda p: ran.append(str(p)), finder=lambda: Path("ollama app.exe"))
    check("a 16 GB PC: Medium 9B, loaded only when chatting and freed soon after",
          written(lay) == {"model": "qwen3.5:9b", "preload_on_start": False, "keep_alive": "3m"} and not ran,
          str(written(lay)))
    lay, plan = plan_for(PCS["64 GB + an RTX 4090 (24 GB)"][0])
    plan.ollama, plan.model_ready = Path("ollama app.exe"), True
    installer.setup_ai(lay, plan, lambda _t: None, __import__("threading").Event(), finder=lambda: Path("x"))
    check("a strong PC: the default model (Smart 35B) - no setting written", written(lay) == {}
          and plan.model == models.DEFAULT, str(written(lay)))
    for key in ("none", "keep"):
        lay, plan = plan_for(PCS["16 GB, 8 cores"][0])
        plan.choose(key)
        out = installer.setup_ai(lay, plan, lambda _t: None, __import__("threading").Event(), finder=lambda: None)
        check(f"'{key}': nothing done, nothing written", out == "" and written(lay) == {} and not plan.needed)

    # updating -----------------------------------------------------------------------------------------
    lay, plan = plan_for(PCS["16 GB, 8 cores"][0], updating=True, user={"model": "qwen3.5:35b-a3b"})
    check("update: Smart 35B is too big for a 16 GB PC -> Medium 9B preselected (no 'keep')",
          plan.choice == "qwen3.5:9b" and not plan.current_ok and plan.options()[0][0] == "qwen3.5:9b",
          plan.choice)
    lay, plan = plan_for(PCS["16 GB, 8 cores"][0], updating=True, user={"model": "anthropic:claude-opus-5-5"})
    check("update: an online model in use is kept", plan.choice == "keep" and plan.options()[0][0] == "keep"
          and "Claude" in plan.options()[0][1] or "claude" in plan.options()[0][1], plan.options()[0][1])
    lay, plan = plan_for(PCS["an 8 GB laptop, 4 cores, built-in graphics"][0], updating=True,
                         user={"ai_mode": "online"})
    check("update: a PC set up for an online AI stays so", plan.choice == "keep"
          and "online AI" in plan.options()[0][1], plan.options()[0][1])

    # the installer window ------------------------------------------------------------------------------
    from pixelcat.sprites.library import SpriteLibrary

    hardware._cached = PCS["an 8 GB laptop, 4 cores, built-in graphics"][0]
    installer.installed_version = lambda _lay: None
    w = installer.InstallerWindow("install", installer.layout(), Path(sys.executable), SpriteLibrary("charcoal"))
    title, note1 = w.ai_title.text(), w.ai_note.text()
    w.ai_combo.setCurrentIndex(w.ai_combo.findData("qwen3.5:2b"))
    note2 = w.ai_note.text()
    check("the window: 'Your PC: ...', the online AI preselected; picking a model too big says so",
          "Your PC: 8 GB memory" in title and w.ai_combo.currentData() == "qwen3.5:2b" and "online AI" in note1
          and "Too big for this PC" in note2, note2[:60])
    w.deleteLater()

    # the app ---------------------------------------------------------------------------------------------
    app_checks()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


def app_checks() -> None:
    from pixelcat import persistence
    from pixelcat.app import PixelCatApp
    from pixelcat.bootstrap import build_platform, build_providers
    from pixelcat.adapters.windows import win32
    from pixelcat.llm.model_status import ONLINE_ONLY
    from pixelcat.logsetup import setup_logging
    from pixelcat.settings import load_config

    setup_logging(False)
    win32.set_dpi_awareness()
    weak = PCS["an 8 GB laptop, 4 cores, built-in graphics"][0]
    hardware._cached = weak
    cfg = load_config()
    cfg.ai_mode, cfg.preload_on_start = "online", False
    pet = PixelCatApp(app, cfg, persistence.SavedState(autostart_asked=True), selftest=1, platform=build_platform(),
                      providers=build_providers())
    pet.save = lambda: None
    pet.model_status.ensure(force=True)
    end = time.time() + 10
    while pet.model_status.busy() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    check("app, online mode: no local AI is started - the chat says how to connect one",
          pet.model_status.state == "online_only" and pet.model_status.banner() == ONLINE_ONLY)
    pet.chat_ui.show_chat_quietly()
    pet._on_user_message("hello")
    check("...a message gets that hint instead of waiting for a local AI", pet.session.worker is None
          and not pet.session.task_active and ONLINE_ONLY in pet.chat_ui.chat.msgs[-1].text)
    items = {i.key: i for i in pet.menus.menu_items()}
    ai = next(c for c in items["settings"].children if c.key == "set_model")
    notes = {c.key: c.note for c in ai.children}
    check("...the menu: External AI models marked BEST; the models too big for this PC marked so",
          notes.get("ext") == "BEST" and "TOO BIG" in notes["model:qwen3.5:35b-a3b"]
          and "BEST" not in notes["model:qwen3.5:0.8b"], str({k: v for k, v in notes.items() if "qwen3.5:3" in k}))
    pet.model_choice.set_default_model("qwen3.5:0.8b")
    from pixelcat.settings import user_settings
    check("...picking a local model as the default switches back to a local AI",
          pet.cfg.ai_mode == "local" and user_settings().get("ai_mode") == "local")
    pet.chat_ui.chat.hide()
    pet.chat_state.chat_model = None
    pet.cfg.model = "qwen3.5:35b-a3b"
    pet.chat_ui._too_big_said = False
    n = len(pet.chat_ui.chat.msgs)
    pet.chat_ui.open_chat()
    said = [m.text for m in pet.chat_ui.chat.msgs[n:]]
    check("a model too big for this PC: not loaded at start, and the chat says what suits it better",
          pet.model_choice.model_fit("qwen3.5:35b-a3b") == "too_big" and any("too big for this PC" in t for t in said),
          said[-1][:80] if said else "")
    pet.chat_ui.chat.hide()


if __name__ == "__main__":
    sys.exit(main())
