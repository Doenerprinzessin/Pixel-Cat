"""The three ways to sign in (:mod:`pixelcat.ports.llm`'s CliSignIn, BrowserSignIn, DeviceSignIn), over the sign-in
modules next to them (ant_tool, chatgpt_signin, github_signin).  Each call looks the module's function up when it
runs (tests replace them)."""
from __future__ import annotations

from typing import Any, Callable


class AntSignIn:
    """Anthropic: its ``ant`` CLI signs in (``pixelcat.adapters.llm.ant_tool``)."""

    def find(self) -> str | None:
        from . import ant_tool

        return ant_tool.find()

    def install(self, progress: Callable[[int, int], None] | None = None, cancel: Any = None) -> str:
        from . import ant_tool

        return ant_tool.install(progress, cancel)

    def login(self, exe: str, open_url: Callable[[str], Any], cancel: Any = None) -> Any:
        from . import ant_tool

        return ant_tool.login(exe, open_url, cancel)

    def sign_out(self, creds: Any) -> Any:
        from . import ant_tool

        return ant_tool.logout(ant_tool.find())


class ChatGPTSignIn:
    """OpenAI: "Sign in with ChatGPT" in the browser (``pixelcat.adapters.llm.chatgpt_signin``)."""

    def sign_in(self, creds: Any, cancel: Any = None, again: Callable[[], None] | None = None) -> dict[str, Any]:
        from . import chatgpt_signin

        return chatgpt_signin.sign_in(creds, cancel=cancel, again=again)

    def start_over(self, creds: Any) -> None:
        from . import chatgpt_signin

        chatgpt_signin.forget_unconfirmed(creds)

    def sign_out(self, creds: Any) -> bool:
        from . import chatgpt_signin

        return chatgpt_signin.sign_out(creds)

    def forget(self, creds: Any) -> Any:
        from . import chatgpt_signin

        return chatgpt_signin.forget(creds)

    def revoke(self, tokens: Any) -> bool:
        from . import chatgpt_signin

        return chatgpt_signin.revoke(tokens)


class GitHubSignIn:
    """GitHub: a code entered on GitHub's page (``pixelcat.adapters.llm.github_signin``)."""

    def available(self) -> bool:
        from . import github_signin

        return github_signin.available()

    def sign_in(self, creds: Any, show_code: Callable[[str, str], None], cancel: Any = None) -> dict[str, Any]:
        from . import github_signin

        return github_signin.sign_in(creds, show_code, cancel)

    def sign_out(self, creds: Any) -> None:
        from . import github_signin

        github_signin.sign_out(creds)
