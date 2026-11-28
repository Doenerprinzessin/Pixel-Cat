"""The external models' limits at request time: the token limit and Copilot's credit limit before a request
(fine, or the chat asks: switch model / raise the limit / stop), the popup when everything is used up or the
provider's own limit is reached, Copilot's credits counted and its plan's allowance fetched, and the chat's model
banner."""
from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..llm import external, models
from .popups import paras

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..llm.model_status import ModelStatus
    from ..persistence import SavedState
    from ..ports import ModelProvider
    from ..ui.menu import PixelMenu
    from .accounts import Accounts
    from .chat_ui import ChatUI
    from .menus import Menus
    from .models_menu import ModelChoice
    from .popups import Popups
    from .provider_settings import ProviderSettings
    from .session import AgentSession

log = logging.getLogger("pixelcat.app")   # (the app's logger name; tests look for it)


class Limits:
    """The token and credit limits the session checks before an external request (``ext_gate(p)``,
    ``credit_gate()``), the limit popup it shows when a provider refuses (``limit_popup(p, info)``), the credits a
    Copilot request used (``on_copilot_credits``) and the plan's allowance (``copilot_quota``, at most every
    2 minutes unless forced).  The controller answers ``save()``; asked at call time."""

    def __init__(self, accounts: Accounts, providers: Mapping[str, ModelProvider], creds: external.Credentials,
                 saved: SavedState, popups: Popups, menu: PixelMenu, model_status: ModelStatus,
                 session: AgentSession, chat_ui: ChatUI, model_choice: ModelChoice,
                 provider_settings: ProviderSettings, menus: Menus, *, save: Callable[[], None]) -> None:
        self.accounts = accounts
        self.providers = providers
        self.creds = creds
        self.saved = saved
        self.popups = popups
        self.menu = menu
        self.model_status = model_status
        self.session = session
        self.chat_ui = chat_ui
        self.model_choice = model_choice
        self.provider_settings = provider_settings
        self.menus = menus
        self._save = save
        self._quota_t = 0.0

    def credit_budget(self) -> external.CreditBudget:
        return external.CreditBudget(self.accounts.ext_p(self.accounts.credit_p),
                                     self.saved.ext_usage.setdefault("copilot_credits", {}))

    def on_copilot_credits(self, credits: float) -> None:
        cb = self.credit_budget()
        cb.add(credits)
        self._save()
        log.info("copilot: %.3f credits (%.1f this month)", credits, cb.used())

    def credit_gate(self) -> bool:
        """The credit limit before a Copilot request: fine, or the chat asks (switch / raise / stop)."""
        if self.credit_budget().status() == "ok":
            return True
        assert self.chat_ui.chat is not None
        self.chat_ui.chat.set_busy(False)
        self._credit_card()
        return False

    def _credit_card(self) -> None:
        assert self.chat_ui.chat is not None
        cb = self.credit_budget()
        fc = external.fmt_credits
        used, allowed, nxt = cb.used(), cb.allowed(), cb.next_allowed()
        q = self.accounts.ext_p(self.accounts.credit_p).get("quota")
        plan = (f" (Copilot's own counter for your whole plan: {fc(q.get('used', 0))} of {fc(q['total'])})"
                if isinstance(q, dict) and q.get("total") and not q.get("unlimited") else "")
        text = (f"🐾 <b>Copilot credit limit reached.</b> I've used {fc(used)} of the {fc(allowed)} AI credits you gave "
                f"me for Copilot models this month{html.escape(plan)}. Switch to another model, or keep going with "
                f"{html.escape(models.label(self.model_choice.effective_model()))} and raise my credit limit to {fc(nxt)} until it "
                f"resets {html.escape(external.fmt_reset(cb.reset_at()))}.")
        log.info("copilot credit limit reached: %.1f of %.1f", used, allowed)
        self.chat_ui.chat.ask_choice(text, [("switch", "SWITCH MODEL"), ("raise", f"RAISE TO {fc(nxt).upper()}"),
                                    ("stop", "STOP")], self._credit_answer)

    def _credit_answer(self, key: str) -> None:
        if self.chat_ui.chat is None or not self.session.task_active:
            return
        if key != "raise":
            self._limit_answer(self.accounts.credit_p, key)
            return
        cb = self.credit_budget()
        new = cb.raise_limit()
        self._save()
        self.chat_ui.chat.add("info", f"Okay - my Copilot credit limit is {external.fmt_credits(new)} until it resets "
                              f"{external.fmt_reset(cb.reset_at())}.")
        self.session.request()

    def copilot_quota(self, force: bool = False) -> None:
        """The Copilot plan's own allowance for the menu (after requests, at most every 2 minutes)."""
        cp = self.accounts.credit_p
        auth = self.accounts.auth(cp)
        if not auth or (not force and time.time() - self._quota_t < 120):
            return
        self._quota_t = time.time()
        prov = self.providers[cp]

        def got(q: Any) -> None:
            if isinstance(q, dict):
                self.accounts.ext_p(cp)["quota"] = q
                self._save()
                if self.menu.isVisible():
                    self.menu.refresh(self.menus.menu_items())
        self.popups.in_background(lambda: prov.quota(self.creds, auth, start=force), got)

    def ext_gate(self, p: str) -> bool:
        """The token limit before a request: fine / suggest switching or raising / all used."""
        st = self.accounts.budget(p).status()
        if st == "ok":
            return True
        assert self.chat_ui.chat is not None
        self.chat_ui.chat.set_busy(False)
        if st == "hard":
            self.limit_popup(p, {"why": "own"})
        else:
            self._limit_card(p)
        return False

    def _limit_card(self, p: str) -> None:
        assert self.chat_ui.chat is not None
        b = self.accounts.budget(p)
        prov = self.providers[p].info
        fmt = external.fmt_tokens
        used, allowed, nxt = b.used(), b.allowed(), b.next_allowed()
        text = (f"🐾 <b>Token limit reached.</b> I've used {fmt(used)} of your {fmt(allowed)}-token limit for "
                f"{prov.brand} models {external.PERIOD_WORD[b.period]}. Switch to another model, or keep going with "
                f"{html.escape(models.label(self.model_choice.effective_model()))} and raise the limit to {fmt(nxt)} until it "
                f"resets {html.escape(external.fmt_reset(b.reset_at()))}.")
        log.info("%s token limit reached: %d of %d", p, used, allowed)
        self.chat_ui.chat.ask_choice(text, [("switch", "SWITCH MODEL"), ("raise", f"RAISE TO {fmt(nxt).upper()}"),
                                    ("stop", "STOP")], lambda k, p=p: self._limit_answer(p, k))

    def _limit_answer(self, p: str, key: str) -> None:
        if self.chat_ui.chat is None or not self.session.task_active:
            return
        if key == "raise":
            b = self.accounts.budget(p)
            new = b.raise_limit()
            self._save()
            self.chat_ui.chat.add("info", f"Okay - the {self.providers[p].info.brand} limit is {external.fmt_tokens(new)} until "
                                  f"it resets {external.fmt_reset(b.reset_at())}.")
            self.session.request()
        elif key == "switch":
            self.session.resume_after_switch = True
            self.chat_ui.chat.add("info", "Pick the model to go on with (the button next to SEND).")
            self.model_choice.open_model_menu()
        else:
            self.chat_ui.chat.add("info", "Okay, I stopped here. Say 'continue' when you want me to go on.")
            self.session.end_task()

    def limit_popup(self, p: str, info: dict[str, Any]) -> None:
        """All tokens are used (our limit), or the provider's own limit: a popup that says when it resets."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        ext = self.accounts.external
        prov = ext.get(p) or external.ANTHROPIC     # (an unknown provider: told Anthropic's way)
        why = info.get("why", "")
        ok, url = "SWITCH MODEL", None
        if why == "own":
            b = self.accounts.budget(p)
            title = "All tokens used"
            lines = [f"All {external.fmt_tokens(b.maximum or b.allowed())} tokens for {prov.brand} models "
                     f"{external.PERIOD_WORD[b.period]} are used up.",
                     f"The limit resets <b>{html.escape(external.fmt_reset(b.reset_at()))}</b>.",
                     "Until then I can go on with a local model or the other provider."]
        elif why == "rate":
            title = "Rate limit reached"
            reset = info.get("reset")
            lines = [f"{prov.name}'s rate limit for your account is reached.",
                     f"It lifts <b>{html.escape(external.fmt_reset(datetime.fromtimestamp(reset)))}</b>." if reset
                     else "It usually lifts within a minute or so."]
        elif why == "plan":
            title = "ChatGPT plan limit reached"
            lines = ["You've reached your ChatGPT plan's usage limit for Pixel Cat.",
                     "ChatGPT's usage page shows when it resets (OpenAI doesn't tell apps that)."]
            ok, url = "MANAGE USAGE", prov.usage_url
        elif why == "copilot":
            title = "Copilot allowance used up"
            q = self.accounts.ext_p(self.accounts.credit_p).get("quota")
            when = self.provider_settings.quota_reset_note(q).replace("RESETS ", "").title() if isinstance(q, dict) else ""
            lines = ["Your Copilot plan's AI credits are used up for now - or a budget your organization set for "
                     "Copilot is reached.",
                     f"The plan's allowance resets <b>{html.escape(when)}</b>." if when else
                     "GitHub's Copilot settings show when it resets.",
                     "Until then I can go on with a local model or another provider."]
            ok, url = "COPILOT SETTINGS", prov.usage_url
        elif why == "credits":
            title = "Credits used up"
            lines = [(ext.get(p) or external.OPENAI).out_of_credits]
            ok, url = "OPEN BILLING", external.BILLING.get(p)
        else:
            title = "Spend limit reached"
            lines = [f"Your {prov.name} organization's spend limit is reached - it's set in your {prov.name} "
                     "account's billing settings."]
            ok, url = "OPEN BILLING", external.BILLING.get(p)
        log.info("%s limit (%s) - task paused", p, why)
        if self.chat_ui.chat is not None:
            self.chat_ui.chat.add("error", re.sub(r"<[^>]+>", "", " ".join(lines)))
        pop = self.popups.popup(title, paras(lines), ok, "OK")

        def answer(yes: bool) -> None:
            if yes and url:
                QDesktopServices.openUrl(QUrl(url))
            if yes and not url and self.session.task_active:
                self.session.resume_after_switch = True
                self.chat_ui.open_chat()
                self.model_choice.open_model_menu()
            elif self.session.task_active and not self.session.resume_after_switch:
                self.session.end_task()
        pop.answered.connect(answer)

    def show_model_banner(self) -> None:
        if self.chat_ui.chat is None:
            return
        text = self.model_status.banner()
        ext = external.parse(self.model_choice.effective_model())
        prov = self.providers[ext[0]].info if ext else None
        if (text is None and prov is not None and prov.signin_kind == "browser"
                and self.accounts.auth(prov.key) == "chatgpt"):
            self.chat_ui.chat.set_model_banner("Using ChatGPT plan", ("Manage usage", prov.usage_url))
        else:
            self.chat_ui.chat.set_model_banner(text)
