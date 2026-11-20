# AGENTS.md - working on Pixel Cat

Pixel Cat is a Windows 11 desktop pet: a pixel-art cat (drawn in code, no image files) that lives on
the desktop - walks on the taskbar, sits on and inside windows, climbs, plays minigames - plus an AI
chat that can act on the PC (open apps, edit files, type, take screenshots, wait for messages...).
Python 3.12 + PySide6, Windows APIs through `ctypes`. Built into one exe with PyInstaller.
The repo is public: https://github.com/worstDevWW/Pixel-Cat

## House rules (from the owner - follow them)

- **README.md belongs to the owner.** Only edit it when asked. Keep their text; anything you add
  matches their chill, first-person tone.
- **Commit / push only when asked.**
- **The chat AI asks before acting.** Actions show a confirmation card unless the user turned on
  auto mode. Anything needing **admin rights always asks, even in auto mode**. Command lines / the
  Run box are always the user's call. Never weaken this (see `_next_call` in `pixelcat/app.py`).
- **Nothing that needs admin rights** (for the app or for you) without asking the owner first.
- **Tests run on the owner's live PC.** Don't steal focus if you can avoid it, never save whole-screen
  screenshots to disk, never read the user's Notepad tabs. Keep test data out of the real
  `%APPDATA%\PixelCat` (redirect `APPDATA` / `LOCALAPPDATA` like the existing tests do).
- **Secrets:** API keys and sign-in tokens live only in `keys.dat` (encrypted with Windows DPAPI).
  Never write them to config.json, the log, or test output; only ever send them to their provider.
- **Claude:** use Anthropic's official `anthropic` SDK. A claude.ai subscription (Pro/Max) can't be used
  by other apps - never reuse Claude Code's / the Claude app's login or automate the Claude app.
- **OpenAI:** "Sign in with ChatGPT" follows OpenAI's open-source-app docs
  (developers.openai.com/siwc) exactly. Don't invent endpoints or reuse other apps' client IDs.
- **GitHub Copilot:** only through GitHub's official Copilot SDK (`microsoft-copilot-sdk`), in its
  "empty" mode with Pixel Cat's own token / sign-in (`keys.dat`). Never reuse the Copilot CLI's,
  VS Code's or `gh`'s login (or the keychain), never call Copilot's internal API directly, never use
  another app's OAuth client ID. Copilot's own tools stay off: only the cat's tools, through `_next_call`.

## Run, build, test

`python` may not be on PATH - use the venv: `.venv\Scripts\python.exe`.

| What | Command |
|---|---|
| Run from source | `.venv\Scripts\python.exe main.py` (`--debug` for verbose logging) |
| Quick check of a build | `dist\PixelCat.exe --selftest 6` (runs 6 s, logs `selftest: ...`, quits) |
| Build | `.venv\Scripts\python.exe build.py` -> `dist\PixelCat.exe` and `dist\PixelCat-Setup.exe` (same exe; the Setup name makes it an installer) |
| Installer from the command line | `PixelCat.exe --install [--quiet --with-ai --launch --no-desktop-shortcut]`, `--uninstall [--quiet --delete-data]` |
| A test | `.venv\Scripts\python.exe tools\<name>_test.py` - each prints `PASS`/`FAIL` lines and `N/M checks passed`, exit code 0 = all passed |

Tips: set `PYTHONIOENCODING=utf-8` when redirecting test output (emoji in messages). If the build says
`dist\PixelCat.exe is in use`, the owner's cat is running from `dist` - ask them to quit it (right-click
the cat -> Quit). The packaged exe always shows two `PixelCat.exe` processes (PyInstaller's starter + the app).
Run several cats side by side with `PIXELCAT_INSTANCE=<name>` (tests do), and move the installer's
locations with `PIXELCAT_TEST_ROOT=<folder>`.

### Tests (`tools/`)

- **No windows (safe any time):** `sim_test`, `screens_test`, `ball_test`, `games_test`, `hats_test`,
  `menu_test` (renders offscreen), `ledges_test` (`--live` reads the real screen, counts only),
  `save_test`, `external_test` (fake Anthropic/OpenAI servers), `ant_test` (`--live` downloads the real
  `ant` helper into the test folder), `advisor_test`, `copilot_test` (GitHub's real Copilot runtime
  against a fake model server - no account, no credits; downloads the runtime, ~100 MB, once into
  `build\copilottest-local`; takes a few minutes because the runtime retries rate limits itself).
- **Real windows on the desktop:** `ui_test` (includes the idle-CPU check, < 3% of one core),
  `drag_test`, `climb_test`, `ride_test`, `games_ui_test`, `ball_ui_test`, `install_test` (needs a fresh
  `build.py` first; installs into `build\installtest`).
- **Need Ollama and/or a real model:** `agent_test`, `control_test`, `memory_test`, `watch_test`,
  `file_test`, `model_test` (`--real` for the real Ollama part).
- Known flaky on a busy PC: the idle-CPU check in `ui_test`, the mice check in `games_ui_test` - rerun.
- Previews (pictures in `build\preview`): `preview.py`, `art_preview.py`, `style_preview.py`,
  `cosmetics_preview.py`, `ball_preview.py`.

## Where things are

```
main.py                  entry point: CLI options, logging, starts PixelCatApp (or the installer)
build.py                 PyInstaller build (+ icon rendered from the sprite)
config.json              bundled defaults (model qwen3.5:35b-a3b, 64k context, apps, allowed folders)
pixelcat/
  app.py                 PixelCatApp: ties everything together - menu, chat + agent loop, tools,
                         minigames, cosmetics, external models, token limits, window riding
  settings.py            Config (defaults <- bundled config.json <- %APPDATA%\PixelCat\config.json)
  persistence.py         the protected save (save.dat/save.bak), export/import of progress
  savecrypt.py           DPAPI encryption + signed export files
  progress.py            the Cat Pass: XP stages, unlocks (skins, hats, games)
  installer.py           Setup window/quiet mode, Ollama setup, the AI recommendation per PC
  paths.py, logsetup.py  folders; logging to %APPDATA%\PixelCat\log.txt (falls back to log-<pid>.txt)
  engine/                the cat's brain: states, needs, animations, surfaces (floors, window tops,
                         ledges inside windows), path planner, ball game
  desktop/               Windows: monitors, windows, ledge finder (screen analysis), input,
                         UI Automation, app control, hardware detection, elevation checks
  sprites/               procedural pixel art: cat poses, palettes/skins, hats, effects, font
  ui/                    overlay (the cat window), chat, menu, dialogs, tray, theme
  games/                 the minigames (laser, treats, knock, mice, fishing, hide & seek, boxes)
  llm/
    ollama_client.py     local models (Ollama /api/chat, streaming, text tool-call fallback)
    models.py            local model list + what suits this PC (fit / advise)
    model_status.py      starts Ollama / downloads / loads the model in the background
    tools.py, agent.py   the chat's tools (specs, validation, confirmation levels) and their actions
    prompt.py            the system prompt
    external.py          external models: tags `anthropic:<id>` / `openai:<id>` / `copilot:<id>`, keys.dat,
                         token limits (Budget), Copilot's credit limit (CreditBudget), the worker base class
    anthropic_client.py  Claude via the official SDK (thinking, effort, drop_block, fallbacks)
    ant_tool.py          one-click "Sign in with Anthropic": fetches Anthropic's `ant` helper
    openai_client.py     OpenAI Responses API (API key or ChatGPT sign-in)
    chatgpt_signin.py    "Sign in with ChatGPT" (OAuth + PKCE, dynamic client registration)
    copilot_client.py    GitHub Copilot via the official Copilot SDK: fetches GitHub's runtime, one
                         runtime session per chat (CopilotChat), models, the plan's quota
    github_signin.py     "Sign in with GitHub" (device flow; hidden until CLIENT_ID is set)
    watch.py, files.py   long tasks (wait_for), safe file editing with backups
tools/                   tests and previews (see above)
```

## The user's data

- `%APPDATA%\PixelCat\`: `save.dat` + `save.bak` (the protected save - Cat Pass XP, cosmetics,
  external-model settings and token usage), `keys.dat` (keys/tokens), `config.json` (the user's own
  settings), `log.txt`, `backups\` (copies of files the chat edited), `anthropic\` (Pixel Cat's own
  Anthropic sign-in, kept apart from Claude Code's), `copilot\` (the Copilot runtime's home - its
  settings and the running chat; sessions are deleted when a chat ends), `progress-backups\`.
- `%LOCALAPPDATA%\PixelCat\tools\ant\` - the downloaded `ant` helper; `...\tools\copilot\` - GitHub's
  Copilot runtime (from github.com/github/copilot-cli releases, checked by the SDK).
- `HKCU\Software\PixelCat\ProtectedSaves` - marks a data folder as converted to the protected save
  (an old plain `state.json` is then ignored). Tests use their own keys and clean them up.
- Installed copy: `%LOCALAPPDATA%\Programs\PixelCat` (Start menu + Settings -> Apps entry, no admin).

## Things that bite

- **Windows only.** Lots of `ctypes`/Win32; tests and the app assume Windows 11.
- **CPU budget:** the cat must stay light when idle (`ui_test` checks < 3% of one core). The ledge
  finder searches the screen in a background thread and backs off on slow PCs.
- **Weak PCs:** never load a model that doesn't fit (`models.fit`); Setup recommends a local model or an
  online AI from the hardware (`desktop/hardware.py`, `models.advise`).
- **Claude thinking blocks** are bound to the conversation; the app shortens old history, so requests use
  `block_binding: drop_block` and replay a reply's own blocks only within the running task.
- **Copilot is stateful:** the runtime keeps the conversation and runs the turn; a tool call waits inside
  it until the app hands over the result. `CopilotWorker` gets the whole history and `CopilotChat.step`
  sends only what's new (`consumed`); `new_chat` must drop the session. The SDK is big - import it lazily.
- **Editing files from Git Bash:** long Python snippets in heredocs break on quotes - write a small
  script (e.g. into `.claude\tmp\`, which git ignores) and run it.
- The version is in `pixelcat/__init__.py` (`__version__`).
