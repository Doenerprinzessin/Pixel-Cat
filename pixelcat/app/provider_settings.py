"""Settings -> AI model -> External AI models: each provider's menu (connection, models, token and credit limits),
the key and amount dialogs, and signing in (Anthropic's helper, ChatGPT in the browser, GitHub's device code,
Copilot's runtime)."""
from __future__ import annotations

import html
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication

from ..llm import external, models
from ..ui.menu import MenuItem
from .popups import paras

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..domain import Monitor
    from ..llm.model_status import ModelStatus
    from ..persistence import SavedState
    from ..ports import ModelProvider
    from ..settings import Config
    from ..ui.menu import PixelMenu
    from ..ui.overlay import CatOverlay
    from .accounts import Accounts
    from .models_menu import ModelChoice
    from .popups import Popups
    from .state import ChatState

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class ProviderSettings(QObject):
    """The providers' account settings.  ``key_dialog`` is the key / amount dialog shown last.

    The controller answers ``save()``, ``ui_unit(monitor)``, ``cat_monitor()``, ``menu_items()``,
    ``open_menu_at(keys)``, ``advice()`` (the model advice: is an online model best?), ``model_choice()`` and
    ``model_status()`` (made later), and the chat's ``credit_budget()``, ``drop_copilot_chat()``,
    ``stop_copilot(background)``, ``copilot_quota(force)`` and ``show_model_banner()``; asked at call time.
    ``ext_disconnect(p, only_key)``: a key removed or signed out (the chat's model and the default go back if
    they were that provider's)."""

    def __init__(self, accounts: Accounts, providers: Mapping[str, ModelProvider],
                 saved: SavedState, cfg: Config, popups: Popups, menu: PixelMenu, overlay: CatOverlay,
                 chat_state: ChatState, *,
                 parent: QObject | None = None, save: Callable[[], None], ui_unit: Callable[[Monitor], int],
                 cat_monitor: Callable[[], Monitor], menu_items: Callable[[], list[MenuItem]],
                 open_menu_at: Callable[[list[str]], None], advice: Callable[[], Any],
                 credit_budget: Callable[[], external.CreditBudget], model_choice: Callable[[], ModelChoice],
                 model_status: Callable[[], ModelStatus], drop_copilot_chat: Callable[[], None],
                 stop_copilot: Callable[..., None], copilot_quota: Callable[..., None],
                 show_model_banner: Callable[[], None]) -> None:
        super().__init__(parent)
        self.accounts = accounts
        self.providers = providers
        self.saved = saved
        self.cfg = cfg
        self.popups = popups
        self.menu = menu
        self.overlay = overlay
        self.chat_state = chat_state
        self._save = save
        self._ui_unit = ui_unit
        self._cat_monitor = cat_monitor
        self._menu_items = menu_items
        self._open_menu_at = open_menu_at
        self._advice = advice
        self._credit_budget = credit_budget
        self._model_choice = model_choice
        self._model_status = model_status
        self._drop_copilot_chat = drop_copilot_chat
        self._stop_copilot = stop_copilot
        self._copilot_quota = copilot_quota
        self._show_model_banner = show_model_banner
        self._signin_cancel: threading.Event | None = None
        self._ant_pop: Any = None
        self._ant_wait_pop: Any = None
        self._gpt_wait_pop: Any = None
        self.key_dialog: Any = None
        self._gh_wait_pop: Any = None
        self._cop_pop: Any = None

    @property
    def model_choice(self) -> ModelChoice:
        return self._model_choice()

    @property
    def model_status(self) -> ModelStatus:
        return self._model_status()

    def external_menu(self) -> MenuItem:
        """Settings -> AI model -> External AI models."""
        st = self.saved.ext_settings
        kids = [self.provider_menu(p) for p in self.accounts.external]
        kids.append(MenuItem("ext:thinking", "Thinking", checked=bool(st.get("thinking", True)), separator_before=True))
        kids.append(MenuItem("ext_effort", "Effort", "bolt_on", children=[
            MenuItem(f"ext:effort:{k}", v, radio=True, checked=st.get("effort", "medium") == k)
            for k, v in external.EFFORTS.items()]))
        kids.append(MenuItem("ext:websearch", "Web search", checked=bool(st.get("web_search", True)),
                             note="$10 PER 1K SEARCHES"))
        return MenuItem("ext", "External AI models", "plus", children=kids, separator_before=True,
                        note="BEST" if self._advice().online else "")

    def provider_menu(self, p: str) -> MenuItem:
        prov = self.providers[p].info
        auth = self.accounts.auth(p)
        b = self.accounts.budget(p)
        fmt = external.fmt_tokens
        if auth == "chatgpt":
            email = (self.accounts.creds.get("chatgpt") or {}).get("email", "")
            state = "Signed in with ChatGPT" + (f" ({email})" if email else "")
        else:
            state = {"key": prov.key_state, "signin": prov.signed_in}.get(auth, "Not connected")
            if prov.shows_login:
                login = self.accounts.ext_p(p).get("login", "")
                state += f" ({login})" if login and auth else ""
        kids = [MenuItem(f"ext:state:{p}", state, info=True)]
        if auth:
            ms = self.accounts.models(p)
            kids.append(MenuItem(f"ext_models_{p}", "Model", "bolt_on", children=(
                [MenuItem(f"model:{p}:{m['id']}", m["name"], radio=True, checked=self.cfg.model == f"{p}:{m['id']}")
                 for m in ms]
                + [MenuItem(f"ext:refresh:{p}", "Reload the list" if ms else "Load the model list", "arrow",
                            separator_before=bool(ms))])))
        if auth not in ("signin", "chatgpt") and self._signin_available(p):
            kids.append(MenuItem(f"ext:signin:{p}", prov.signin, "arrow"))
        kids.append(MenuItem(f"ext:key:{p}", prov.key_change if auth == "key" else prov.key_use, "plus"))
        maximum = float(self.accounts.ext_p(p).get("maximum", external.DEFAULTS["maximum"]) or 0)
        custom = b.limit not in external.LIMITS
        kids.append(MenuItem(f"ext_limit_{p}", "Token limit", "dot", separator_before=True,
                             note=f"{fmt(b.limit)}/{b.period}" if b.limit else "NONE", children=[
            MenuItem(f"ext:limit:{p}:{n}", f"{fmt(n)} tokens" if n else "No limit", radio=True, checked=b.limit == n)
            for n in external.LIMITS] + [
            MenuItem(f"ext:custom:{p}", f"Custom: {fmt(b.limit)} tokens" if custom else "Custom...", radio=True,
                     checked=custom, note=f"UP TO {fmt(external.MAX_LIMIT)}", separator_before=True)]))
        if b.limit:
            kids.append(MenuItem(f"ext_period_{p}", "Resets", "dot", children=[
                MenuItem(f"ext:period:{p}:{k}", v, radio=True, checked=b.period == k)
                for k, v in external.PERIODS.items()]))
            kids.append(MenuItem(f"ext_max_{p}", "Raise up to", "dot", children=[
                MenuItem(f"ext:max:{p}:{x:g}", f"{x:g}x ({fmt(int(b.limit * x))})" if x else "No maximum", radio=True,
                         checked=abs(maximum - x) < 1e-6) for x in external.MAXIMUMS]))
            used, allowed = b.used(), b.allowed()
            kids.append(MenuItem(f"ext:used:{p}", f"Used {fmt(used)} of {fmt(allowed)}", info=True,
                                 bar=min(1.0, used / max(1, allowed)), note=external.PERIOD_WORD[b.period].upper()))
        elif b.used():
            kids.append(MenuItem(f"ext:used:{p}", f"Used {fmt(b.used())} {external.PERIOD_WORD[b.period]}", info=True))
        if prov.credits:
            kids += self._credit_items()
        if auth:
            kids.append(MenuItem(f"ext:out:{p}", "Sign out" if auth in ("signin", "chatgpt") else prov.key_remove,
                                 "close", separator_before=True))
        return MenuItem(f"ext_{p}", f"{prov.brand} ({prov.name})", "chat", children=kids, note="ON" if auth else "")

    def _credit_items(self) -> list[MenuItem]:
        """Copilot: how many AI credits the cat may use this month, and the plan's own allowance."""
        cb = self._credit_budget()
        fc = external.fmt_credits
        lim = cb.limit
        cap = self.credit_cap()
        presets = [n for n in external.CREDIT_LIMITS if cap is None or n <= cap]
        whole = cap is not None and cap not in presets
        choices = [MenuItem(f"ext:climit:copilot:{n}", f"{fc(n)} credits" if n else "No limit", radio=True,
                            checked=abs(lim - n) < 1e-6) for n in presets]
        if whole:
            choices.append(MenuItem(f"ext:climit:copilot:{cap:.10g}", f"{fc(cap)} credits (your whole plan)",
                                    radio=True, checked=abs(lim - cap) < 1e-6))
        custom = all(abs(lim - n) > 1e-6 for n in presets + ([cap] if whole else []))
        choices.append(MenuItem("ext:ccustom:copilot", f"Custom: {fc(lim)} credits" if custom else "Custom...",
                                radio=True, checked=custom, note=f"UP TO {fc(cap)}" if cap else "",
                                separator_before=True))
        kids = [MenuItem("ext_climit_copilot", "Credit limit", "dot", separator_before=True,
                         note=f"{fc(lim)}/MONTH" if lim else "NONE", children=choices)]
        used = cb.used()
        if lim:
            allowed = cb.allowed()
            kids.append(MenuItem("ext:cused:copilot", f"Used {fc(used)} of {fc(allowed)} credits", info=True,
                                 bar=min(1.0, used / max(1e-9, allowed)), note="THIS MONTH"))
        elif used:
            kids.append(MenuItem("ext:cused:copilot", f"Used {fc(used)} credits this month", info=True))
        q = self.accounts.ext_p(self.accounts.credit_p).get("quota")
        if isinstance(q, dict) and self.accounts.auth(self.accounts.credit_p):
            if q.get("unlimited"):
                kids.append(MenuItem("ext:quota:copilot", "Copilot plan: no monthly limit", info=True))
            elif q.get("total"):
                total = float(q["total"])
                kids.append(MenuItem("ext:quota:copilot", f"Copilot plan: {fc(q.get('used', 0))} of {fc(total)} used",
                                     info=True, bar=min(1.0, float(q.get("used", 0)) / max(1e-9, total)),
                                     note=self.quota_reset_note(q)))
        return kids

    @staticmethod
    def quota_reset_note(q: dict[str, Any]) -> str:
        try:
            at = datetime.fromisoformat(str(q.get("reset", "")).replace("Z", "+00:00")).astimezone()
            return f"RESETS {at:%b} {at.day}".upper()
        except ValueError:
            return ""

    def ext_action(self, act: str) -> None:
        parts = act.split(":")
        what, p = parts[0], (parts[1] if len(parts) > 1 else "")
        st = self.saved.ext_settings
        if what == "thinking":
            st["thinking"] = not st.get("thinking", True)
        elif what == "websearch":
            st["web_search"] = not st.get("web_search", True)
        elif what == "effort" and p in external.EFFORTS:
            st["effort"] = p
        elif what == "limit":
            self._set_token_limit(p, int(parts[2]))
        elif what == "climit":
            self._set_credit_limit(float(parts[2]))
        elif what in ("custom", "ccustom"):
            self.menu.close()
            if what == "custom":
                self._ask_limit(p)
            else:
                self._ask_credit_limit()
            return
        elif what == "period" and parts[2] in external.PERIODS:
            self.accounts.ext_p(p)["period"] = parts[2]
            self.accounts.budget(p).u["start"] = external.period_start(parts[2], datetime.now()).timestamp()
        elif what == "max":
            self.accounts.ext_p(p)["maximum"] = float(parts[2])
        elif what == "refresh":
            self.load_models(p, announce=True)
        elif what in ("signin", "key", "out"):
            self.menu.close()
            if what == "signin":
                {"cli": self._cli_signin, "browser": self._browser_signin,
                 "device": self._device_signin}[self.providers[p].info.signin_kind](p)
            elif what == "key":
                self._ask_key(p)
            else:
                self.ext_disconnect(p)
            return
        else:
            return
        self._save()
        if self.menu.isVisible():
            self.menu.refresh(self._menu_items())

    def _set_token_limit(self, p: str, n: int) -> None:
        self.accounts.ext_p(p)["limit"] = n
        self.accounts.budget(p).u["allowed"] = n                # a new limit starts without raises

    def _set_credit_limit(self, n: float) -> None:
        self.accounts.ext_p(self.accounts.credit_p)["credit_limit"] = n
        self._credit_budget().u["allowed"] = n          # (also without raises)

    def show_amount_dialog(self, dlg: Any) -> None:
        mon = self._cat_monitor()
        dlg.show_near(self.overlay.cat_rect(), mon.work, self._ui_unit(mon))
        self.key_dialog = dlg

    def _ask_limit(self, p: str) -> None:
        """Token limit -> Custom...: any amount up to 100M tokens."""
        from ..ui.dialogs import AmountDialog

        b = self.accounts.budget(p)
        fmt = external.fmt_tokens
        brand = self.providers[p].info.brand
        dlg = AmountDialog(f"Token limit for {brand} models",
                           f"How many tokens may I use for {brand} models "
                           f"{external.PERIODS[b.period].lower()}? For example <b>3.5M</b>, <b>750k</b> or "
                           f"<b>2000000</b> - from {fmt(external.MIN_LIMIT)} up to {fmt(external.MAX_LIMIT)}.",
                           "e.g. 3.5M", fmt(b.limit) if b.limit else "")
        dlg.submitted.connect(lambda text, d=dlg: self._check_limit(p, text, d))
        self.show_amount_dialog(dlg)

    def _check_limit(self, p: str, text: str, dlg: Any) -> None:
        fmt = external.fmt_tokens
        v = external.parse_amount(text)
        n = int(round(v)) if v is not None else 0
        if v is None:
            msg = "That isn't an amount I understand - try e.g. 3.5M, 750k or 2000000."
        elif n < external.MIN_LIMIT:
            msg = f"That's too little to chat with - at least {fmt(external.MIN_LIMIT)} tokens."
        elif n > external.MAX_LIMIT:
            msg = f"The highest limit is {fmt(external.MAX_LIMIT)} tokens."
        else:
            msg = ""
        try:
            dlg.checked(not msg, msg)
        except RuntimeError:
            return                                       # (the dialog was closed meanwhile)
        if not msg:
            self._set_token_limit(p, n)
            self._save()
            log.info("%s token limit: %d (custom)", p, n)

    def credit_cap(self) -> float | None:
        """The most credits the cat may be given: the Copilot plan's monthly total (None: not known
        or no monthly limit)."""
        q = self.accounts.ext_p(self.accounts.credit_p).get("quota")
        if isinstance(q, dict) and not q.get("unlimited") and float(q.get("total") or 0) > 0:
            return float(q["total"])
        return None

    def _ask_credit_limit(self) -> None:
        """Credit limit -> Custom...: any amount up to the Copilot plan's own monthly credits."""
        from ..ui.dialogs import AmountDialog

        cap = self.credit_cap()
        fc = external.fmt_credits
        lim = self._credit_budget().limit
        upto = (f" - up to <b>{fc(cap)}</b>, all the credits your Copilot plan has a month" if cap else
                " - up to all the credits your Copilot plan has a month")
        dlg = AmountDialog("Copilot credit limit",
                           f"How many of your Copilot plan's AI credits may I use per month? For example <b>2500</b> "
                           f"or <b>7.5k</b>{upto}.", "e.g. 2500", fc(lim).replace(",", "") if lim else "")
        dlg.submitted.connect(lambda text, d=dlg: self._check_credit_limit(text, d))
        self.show_amount_dialog(dlg)

    def _check_credit_limit(self, text: str, dlg: Any) -> None:
        v = external.parse_amount(text)
        if v is None:
            try:
                dlg.checked(False, "That isn't an amount I understand - try e.g. 2500 or 7.5k.")
            except RuntimeError:
                pass
            return
        cp = self.accounts.credit_p
        auth = self.accounts.auth(cp)
        if self.credit_cap() is None and auth:          # (the plan's total isn't known yet: ask GitHub)
            prov = self.providers[cp]

            def got(q: Any) -> None:
                if isinstance(q, dict):
                    self.accounts.ext_p(cp)["quota"] = q
                self._apply_credit_limit(v, dlg)
            self.popups.in_background(lambda: prov.quota(self.accounts.creds, auth, start=True), got)
            return
        self._apply_credit_limit(v, dlg)

    def _apply_credit_limit(self, v: float, dlg: Any) -> None:
        cap = self.credit_cap()
        fc = external.fmt_credits
        v = round(v, 1)
        if v < 1:
            msg = "That's too little - at least 1 credit."
        elif cap is not None and v > cap + 1e-6:
            msg = f"Your Copilot plan has {fc(cap)} credits a month - that's the most I can be given."
        else:
            msg = ""
        try:
            dlg.checked(not msg, msg)
        except RuntimeError:
            return
        if not msg:
            self._set_credit_limit(v)
            self._save()
            log.info("copilot credit limit: %s (custom)", fc(v))

    def load_models(self, p: str, announce: bool = False, after: Any = None) -> None:
        auth = self.accounts.auth(p)
        if not auth:
            return

        prov = self.providers[p]

        def work() -> list[dict[str, Any]]:
            return prov.list_models(self.accounts.creds, auth)

        def got(res: Any) -> None:
            if isinstance(res, Exception):
                log.warning("couldn't load the %s models: %s", p, res)
                if announce:
                    self.popups.popup("Models", paras([html.escape(prov.key_problem(res))]), "OK")
                return
            self.accounts.set_models(p, res)
            log.info("%s: %d models", p, len(res))
            if self.menu.isVisible():
                self.menu.refresh(self._menu_items())
            if after is not None:
                after()
        self.popups.in_background(work, got)

    def _ext_connected(self, p: str) -> None:
        prov = self.providers[p].info
        n = len(self.accounts.models(p))
        pop = self.popups.popup(f"{prov.brand} is connected", paras([
            f"{n} {prov.brand} models are ready." if n else f"{prov.brand} is ready.",
            f"Pick one in Settings &gt; AI model &gt; External AI models &gt; {prov.brand} &gt; Model - or just for "
            "one chat, with the button next to SEND."]), "PICK A MODEL", "LATER")
        pop.answered.connect(lambda ok, p=p: self._open_menu_at(["settings", "set_model", "ext", f"ext_{p}",
                                                                   f"ext_models_{p}"]) if ok else None)

    def ext_disconnect(self, p: str, only_key: bool = False) -> None:
        prov = self.providers[p].info
        auth = self.accounts.auth(p)
        s = self.accounts.ext_p(p)
        if only_key or auth == "key":
            self.accounts.creds.set(f"{p}_key", None)
            if s.get("auth") == "key":
                s["auth"] = ""
            msg = prov.key_removed
        elif auth in ("signin", "chatgpt"):
            signin = self.providers[p].signin()
            s["auth"] = ""
            msg = prov.signed_out
            if prov.signin_kind == "browser":
                self.popups.in_background(lambda: signin.sign_out(self.accounts.creds), lambda ok: None if ok is True else self.popups.popup(
                    "Signed out", paras(["OpenAI couldn't confirm the sign-out. To be sure, disconnect Pixel Cat "
                                               "in ChatGPT's settings (Connected apps)."]), "OK"))
            elif prov.signin_kind == "device":
                signin.sign_out(self.accounts.creds)              # (quick: it forgets the token)
            else:
                self.popups.in_background(lambda: signin.sign_out(self.accounts.creds), lambda _r: None)
        else:
            return
        if prov.credits:
            s.pop("quota", None)
        if prov.shows_login and not self.accounts.auth(p):
            s.pop("login", None)
        if prov.keeps_chat:
            self._stop_copilot(background=True)
        self._save()
        if not self.accounts.auth(p):
            if (external.parse(self.cfg.model) or ("",))[0] == p:
                self.model_choice.set_default_model(models.DEFAULT)
            if (external.parse(self.chat_state.chat_model) or ("",))[0] == p:
                self.chat_state.chat_model = None
                self.model_status.override = None
                self.model_choice.update_model_chip()
        self.popups.popup(prov.brand, paras([msg]), "OK")

    def _ask_key(self, p: str) -> None:
        prov = self.providers[p].info
        if prov.runtime:
            self._runtime_ready(p, lambda: self._key_dialog_for(p))
            return
        self._key_dialog_for(p)

    def _key_dialog_for(self, p: str) -> None:
        """The dialog for a provider's API key / GitHub token."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..ui.dialogs import KeyDialog

        prov = self.providers[p].info
        dlg = KeyDialog(prov.key_title, prov.key_text, f"{prov.key_hint}...", bool(self.accounts.creds.get(f"{p}_key")),
                        noun=prov.key_noun)
        dlg.link.connect(lambda: QDesktopServices.openUrl(QUrl(prov.key_url)))
        dlg.submitted.connect(lambda key, d=dlg: self._check_key(p, key, d))
        dlg.removed.connect(lambda: self.ext_disconnect(p, only_key=True))
        mon = self._cat_monitor()
        dlg.show_near(self.overlay.cat_rect(), mon.work, self._ui_unit(mon))
        self.key_dialog = dlg

    def _check_key(self, p: str, key: str, dlg: Any) -> None:
        prov = self.providers[p]
        if prov.info.key_kind == "token":
            self._check_token(p, key, dlg)
            return

        def work() -> list[dict[str, Any]]:
            return prov.check_key(key)[1]

        def got(res: Any) -> None:
            if isinstance(res, Exception):
                log.info("%s key refused: %s", p, type(res).__name__)
                try:
                    dlg.checked(False, prov.key_problem(res))
                except RuntimeError:
                    pass                                 # (the dialog was closed meanwhile)
                return
            self.accounts.creds.set(f"{p}_key", key)
            self.accounts.ext_p(p)["auth"] = "key"
            self.accounts.set_models(p, res)
            log.info("%s connected with an API key (%d models)", p, len(res))
            try:
                dlg.checked(True, "")
            except RuntimeError:
                pass
            self._ext_connected(p)
        self.popups.in_background(work, got)

    def _cli_signin(self, p: str) -> None:
        """Sign in with Anthropic: one click - Pixel Cat gets Anthropic's sign-in helper if it
        isn't here yet, then the browser opens and the user only signs in there."""
        signin = self.providers[p].signin()
        self.popups.in_background(signin.find, lambda exe: self._cli_signin_ask(p, exe))

    def _cli_signin_ask(self, p: str, exe: Any) -> None:
        have = isinstance(exe, str)
        lines = ["Your browser opens Anthropic's page: sign in, pick your organization and click "
                 "<b>Allow</b>. That's all - I notice it by myself."]
        if not have:
            lines.insert(0, "First I get Anthropic's small sign-in helper (about 15 MB) from Anthropic's official "
                            "download page and check that it's the real one. No admin rights, nothing else changes "
                            "on your PC.")
        lines += ["This uses your Claude Console account (platform.claude.com) - the API is paid there, separately "
                  "from a claude.ai subscription.",
                  "Chats with Claude go to Anthropic, including what my tools read (screenshots, files, window text)."]
        pop = self.popups.popup("Sign in with Anthropic", paras(lines), "SIGN IN", "CANCEL")
        pop.answered.connect(lambda ok, e=exe: self._cli_go(p, e if isinstance(e, str) else None) if ok else None)

    def _cli_go(self, p: str, exe: str | None) -> None:
        signin = self.providers[p].signin()
        if self._signin_cancel is not None:
            self._signin_cancel.set()
        cancel = threading.Event()
        self._signin_cancel = cancel
        if exe is not None:
            self._cli_login(p, exe, cancel)
            return
        pop = self.popups.popup("Sign in with Anthropic", paras(["Getting Anthropic's sign-in helper…"]), "CANCEL")
        pop.answered.connect(lambda _ok: cancel.set())
        self._ant_pop = pop

        def progress(done: int, total: int) -> None:
            self.popups.later.ran.emit(self._cli_progress, (done, total))
        self.popups.in_background(lambda: signin.install(progress, cancel),
                            lambda res, c=cancel: self._cli_installed(p, res, c))

    def _cli_progress(self, value: Any) -> None:
        pop = getattr(self, "_ant_pop", None)
        done, total = value
        if pop is None:
            return
        try:
            pct = f" {int(100 * done / total)}%" if total else ""
            pop.text.setText(paras([f"Getting Anthropic's sign-in helper…{pct}"]))
        except RuntimeError:
            self._ant_pop = None

    def _cli_installed(self, p: str, res: Any, cancel: threading.Event) -> None:
        self.popups.dismiss(getattr(self, "_ant_pop", None))
        self._ant_pop = None
        if cancel.is_set():
            return
        if isinstance(res, Exception):
            msg = self.providers[p].error_text(res, f"That didn't work ({res}).")
            pop = self.popups.popup("Sign in with Anthropic", paras([html.escape(msg),
                              "You can also use an API key instead - that needs nothing else."]), "API KEY", "CLOSE")
            pop.answered.connect(lambda ok: self._ask_key(p) if ok else None)
            return
        self._cli_login(p, res, cancel)

    def _cli_login(self, p: str, exe: str, cancel: threading.Event) -> None:
        import webbrowser

        signin = self.providers[p].signin()
        opened: list[str] = []

        def open_url(url: str) -> None:
            opened.append(url)
            webbrowser.open(url)
        pop = self.popups.popup("Finish in your browser", paras([
            "Anthropic's sign-in page is open in your browser. Sign in, pick your organization and click "
            "<b>Allow</b> - I notice it by myself.",
            "No page? Click OPEN AGAIN."]), "OPEN AGAIN", "CANCEL")
        pop.answered.connect(lambda ok: (webbrowser.open(opened[-1]) if opened else None) if ok else cancel.set())
        self._ant_wait_pop = pop
        self.popups.in_background(lambda: signin.login(exe, open_url, cancel),
                            lambda res, c=cancel: self._cli_signed_in(p, res, c))

    def _cli_signed_in(self, p: str, res: Any, cancel: threading.Event) -> None:
        self.popups.dismiss(getattr(self, "_ant_wait_pop", None))
        self._ant_wait_pop = None
        if isinstance(res, Exception):
            if not cancel.is_set():
                msg = self.providers[p].error_text(res, f"The sign-in didn't work ({res}).")
                self.popups.popup("Sign in with Anthropic", paras([html.escape(msg)]), "OK")
            return
        self.accounts.ext_p(p)["auth"] = "signin"
        self._save()
        self.load_models(p, announce=True, after=lambda: self._ext_connected(p))

    def _browser_signin(self, p: str) -> None:
        pop = self.popups.popup("Sign in with ChatGPT", paras([
            "Your browser opens OpenAI's sign-in. Once you allow it there, chats with OpenAI models use your "
            "ChatGPT plan instead of API credits.",
            "When OpenAI asks for an account or workspace, pick your <b>personal</b> one with ChatGPT Plus or Pro - "
            "work and school workspaces usually can't be used by apps like this.",
            "On Plus, the 5-hour limit is shared with your other apps; Pro has no 5-hour limit.",
            "Your chats go to OpenAI, including what my tools read (screenshots, files, window text)."]),
            "SIGN IN", "CANCEL")
        pop.answered.connect(lambda ok: self._browser_start(p) if ok else None)

    def _browser_start(self, p: str) -> None:
        signin = self.providers[p].signin()
        if self._signin_cancel is not None:
            self._signin_cancel.set()
        ev = threading.Event()
        self._signin_cancel = ev
        pop = self.popups.popup("Finish in your browser", paras([
            "OpenAI's sign-in is open in your browser. Pick your personal ChatGPT account and allow Pixel Cat - "
            "I notice it by myself.",
            "OpenAI shows an error instead (for example about a workspace)? Close that page and click START OVER."]),
            "START OVER", "CANCEL")
        pop.answered.connect(lambda ok, e=ev: (e.set(), self._browser_restart(p) if ok else None))
        self._gpt_wait_pop = pop

        def again() -> None:                         # (from the sign-in thread)
            self.popups.later.ran.emit(self._browser_again, None)
        self.popups.in_background(lambda: signin.sign_in(self.accounts.creds, cancel=ev, again=again),
                            lambda res, pp=pop, e=ev: self._browser_done(p, res, pp, e))

    def _browser_restart(self, p: str) -> None:
        """START OVER: a fresh registration, with OpenAI's account/workspace picker."""
        self.providers[p].signin().start_over(self.accounts.creds)
        log.info("ChatGPT sign-in: starting over")
        QTimer.singleShot(700, self, lambda: self._browser_start(p))   # (the old attempt lets go of its port first)

    def _browser_again(self, _r: Any) -> None:
        pop = getattr(self, "_gpt_wait_pop", None)
        try:
            if pop is not None:
                pop.text.setText(paras([
                    "One more step: your browser asks once more - that's normal the first time. Allow Pixel Cat "
                    "there again."]))
        except RuntimeError:
            pass

    def _browser_done(self, p: str, res: Any, pop: Any = None, ev: threading.Event | None = None) -> None:
        self.popups.dismiss(pop)
        if self._gpt_wait_pop is pop:
            self._gpt_wait_pop = None
        if ev is not None and ev is not self._signin_cancel:
            return                                   # (an attempt that was started over)
        if isinstance(res, Exception):
            msg = self.providers[p].error_text(res, f"The sign-in didn't work ({res}).")
            if "cancelled" not in msg:
                self.popups.popup("Sign in with ChatGPT", paras([html.escape(msg)]), "OK")
            return
        s = self.accounts.ext_p(p)
        if not res.get("plan"):
            self.popups.popup("Signed in - but no plan usage", paras([
                "You're signed in, but using your ChatGPT plan wasn't allowed, so OpenAI models can't run on it yet.",
                "Sign in again and allow it - or use an API key instead."]), "OK")
            return
        s["auth"] = "chatgpt"
        first = not s.get("welcomed")
        s["welcomed"] = True
        self._save()

        def ready() -> None:
            if not first:
                self._ext_connected(p)
                return
            pop = self.popups.popup("Using your ChatGPT plan", paras([
                "Chats with OpenAI models now use your ChatGPT plan's usage. ChatGPT's settings show how much I "
                "used and let you cap it - the Manage usage link above the chat's input takes you there.",
                f"{len(self.accounts.models(p))} models are ready - pick one in Settings &gt; AI model &gt; "
                "External AI models &gt; ChatGPT &gt; Model."]), "PICK A MODEL", "LATER")
            pop.answered.connect(lambda ok: self._open_menu_at(["settings", "set_model", "ext", f"ext_{p}",
                                                                  f"ext_models_{p}"]) if ok else None)
        self.load_models(p, after=ready)
        self._show_model_banner()

    # ---------------------------------------------------------------- GitHub Copilot
    def _signin_available(self, p: str) -> bool:
        """Whether "Sign in with ..." can be offered: the provider has one (GitHub's needs this build's app id)."""
        prov = self.providers[p]
        kind = prov.info.signin_kind
        return bool(kind) and (kind != "device" or prov.signin().available())

    def _runtime_ready(self, p: str, then: Any) -> None:
        """Copilot needs GitHub's runtime (about 100 MB): get it first if it isn't here yet."""
        prov = self.providers[p]

        def got(ready: Any) -> None:
            if ready is True:
                then()
                return
            pop = self.popups.popup("GitHub Copilot", paras([
                "For Copilot models I first get GitHub's Copilot runtime (about 100 MB) from GitHub's official release "
                "page - the download is checked against GitHub's checksums. No admin rights, nothing else changes on "
                "your PC.",
                "Chats with Copilot models go to GitHub, including what my tools read (screenshots, files, window "
                "text), and use your Copilot plan's AI credits."]), "GET IT", "CANCEL")
            pop.answered.connect(lambda ok: self._runtime_install(p, then) if ok else None)
        self.popups.in_background(prov.runtime_ready, got)

    def _runtime_install(self, p: str, then: Any) -> None:
        prov = self.providers[p]
        cancel = threading.Event()
        pop = self.popups.popup("GitHub Copilot", paras(["Getting GitHub's Copilot runtime…"]), "CANCEL")
        pop.answered.connect(lambda _ok: cancel.set())
        self._cop_pop = pop

        def progress(done: int, total: int) -> None:
            self.popups.later.ran.emit(self._runtime_progress, (done, total))

        def finished(res: Any) -> None:
            self.popups.dismiss(self._cop_pop)
            self._cop_pop = None
            if cancel.is_set():
                return
            if isinstance(res, Exception):
                msg = prov.error_text(res, f"That didn't work ({res}).")
                self.popups.popup("GitHub Copilot", paras([html.escape(msg)]), "OK")
                return
            then()
        self.popups.in_background(lambda: prov.install_runtime(progress, cancel), finished)

    def _runtime_progress(self, value: Any) -> None:
        pop = self._cop_pop
        done, total = value
        if pop is None:
            return
        try:
            pct = f" {int(100 * done / total)}%" if total else ""
            pop.text.setText(paras([f"Getting GitHub's Copilot runtime…{pct}"]))
        except RuntimeError:
            self._cop_pop = None

    def _check_token(self, p: str, key: str, dlg: Any) -> None:
        prov = self.providers[p]

        def got(res: Any) -> None:
            if isinstance(res, Exception):
                log.info("GitHub token refused: %s", getattr(res, "kind", "") or type(res).__name__)
                try:
                    dlg.checked(False, prov.key_problem(res))
                except RuntimeError:
                    pass                                 # (the dialog was closed meanwhile)
                return
            login, ms = res
            self.accounts.creds.set(f"{p}_key", key)
            s = self.accounts.ext_p(p)
            s["auth"], s["login"] = "key", login
            self._drop_copilot_chat()
            self.accounts.set_models(p, ms)
            log.info("Copilot connected with a GitHub token (%d models)", len(ms))
            try:
                dlg.checked(True, "")
            except RuntimeError:
                pass
            self._ext_connected(p)
            if prov.info.credits:
                self._copilot_quota(force=True)
        self.popups.in_background(lambda: prov.check_key(key), got)

    def _device_signin(self, p: str) -> None:
        pop = self.popups.popup("Sign in with GitHub", paras([
            "Your browser opens GitHub with a short code: enter it there and allow Pixel Cat. That's all - I notice "
            "it by myself.",
            "Copilot models then use your Copilot plan's AI credits. If your Copilot comes from work, your "
            "organization's rules decide whether apps like this may use it.",
            "Chats with Copilot models go to GitHub, including what my tools read (screenshots, files, window text)."]),
            "SIGN IN", "CANCEL")
        pop.answered.connect(lambda ok: self._runtime_ready(p, lambda: self._device_start(p)) if ok else None)

    def _device_start(self, p: str) -> None:
        signin = self.providers[p].signin()
        if self._signin_cancel is not None:
            self._signin_cancel.set()
        ev = threading.Event()
        self._signin_cancel = ev

        def show_code(code: str, uri: str) -> None:      # (from the sign-in thread)
            self.popups.later.ran.emit(self._device_code, (code, uri, ev))
        self.popups.in_background(lambda: signin.sign_in(self.accounts.creds, show_code, ev),
                            lambda res, e=ev: self._device_done(p, res, e))

    def _device_code(self, value: Any) -> None:
        import webbrowser

        code, uri, ev = value
        if ev.is_set():
            return
        QGuiApplication.clipboard().setText(code)
        pop = self.popups.popup("Finish in your browser", paras([
            f"Enter this code on GitHub: <b>{html.escape(code)}</b> (it's copied - just paste it), then allow Pixel "
            "Cat. I notice it by myself.",
            "No page? Click OPEN AGAIN."]), "OPEN AGAIN", "CANCEL")
        pop.answered.connect(lambda ok: webbrowser.open(uri) if ok else ev.set())
        self._gh_wait_pop = pop
        webbrowser.open(uri)

    def _device_done(self, p: str, res: Any, ev: threading.Event) -> None:
        self.popups.dismiss(self._gh_wait_pop)
        self._gh_wait_pop = None
        if ev is not self._signin_cancel:
            return                                       # (an attempt that was started over)
        if isinstance(res, Exception):
            msg = self.providers[p].error_text(res, f"The sign-in didn't work ({res}).")
            if "cancelled" not in msg:
                self.popups.popup("Sign in with GitHub", paras([html.escape(msg)]), "OK")
            return
        s = self.accounts.ext_p(p)
        s["auth"], s["login"] = "signin", res.get("login", "")
        self._drop_copilot_chat()
        self._save()

        def loaded() -> None:
            self._ext_connected(p)
            if self.providers[p].info.credits:
                self._copilot_quota(force=True)
        self.load_models(p, announce=True, after=loaded)
