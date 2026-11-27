"""``FakePlatform``: a :class:`~pixelcat.ports.Platform` for core tests, without any desktop.

Every port is the null adapter (nothing there, nothing happens) that also records its calls and can be told
what to answer::

    platform = FakePlatform()
    platform.display.returns("monitors", [MONITOR])
    platform.system.answers("message_box", lambda text, title="Pixel Cat": shown.append(text))
    ...
    assert platform.windows.names() == ["make_tool_window", "set_click_through"]
    assert platform.windows.called("set_click_through") == [((hwnd, True), {})]

A port can also be replaced as a whole: ``FakePlatform(display=MyDisplay())``.
"""
from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from pixelcat.adapters.null import (NullAccessibility, NullApps, NullAutostart, NullCapture, NullClipboard,
                                    NullDisplay, NullEscapeWatcher, NullInput, NullInstaller, NullKnownFolders,
                                    NullNotifications, NullProcesses, NullSaveMarks, NullSecretStore, NullSystem,
                                    NullWindows)
from pixelcat.ports import (AccessibilityPort, AppsPort, AutostartPort, CapturePort, ClipboardPort, DisplayPort,
                            InputPort, InstallerPort, KnownFolders, NotificationPort, Platform, ProcessPort,
                            SaveMarks, SecretStore, SystemPort, WindowPort)


class Recording:
    """Records every call to a port method in ``calls``; answers what the test said, else what the null adapter says."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._answers: dict[str, Callable[..., Any]] = {}

    def returns(self, method: str, value: Any) -> None:
        """``method`` returns ``value`` from now on."""
        self.answers(method, lambda *_a, **_k: value)

    def answers(self, method: str, fn: Callable[..., Any]) -> None:
        """``method`` calls ``fn`` with its arguments and returns what it returns (or raises what it raises)."""
        if method not in getattr(type(self), "_recorded", ()):
            raise AttributeError(f"{type(self).__name__} has no port method {method!r}")
        self._answers[method] = fn

    def names(self) -> list[str]:
        """The methods called, in order."""
        return [name for name, _args, _kwargs in self.calls]

    def called(self, method: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        """(args, kwargs) of every call to ``method``, in order."""
        return [(args, kwargs) for name, args, kwargs in self.calls if name == method]


def _recorded(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def method(self: Recording, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        answer = self._answers.get(name)
        return answer(*args, **kwargs) if answer is not None else fn(self, *args, **kwargs)
    return method


def recording(cls: type) -> type:
    """Class decorator: record every public method of the port (the most derived implementation of each)."""
    names = {name for base in cls.__mro__ if base not in (object, Recording)
             for name, fn in vars(base).items() if not name.startswith("_") and inspect.isfunction(fn)}
    for name in names:
        setattr(cls, name, _recorded(name, getattr(cls, name)))
    cls._recorded = frozenset(names)  # type: ignore[attr-defined]
    return cls


class FakeEscapeWatcher(NullEscapeWatcher):
    """Active between start() and stop(); ``fire()`` is the user pressing Esc."""

    def __init__(self, on_escape: Callable[[], None]) -> None:
        super().__init__(on_escape)
        self.started = False

    @property
    def active(self) -> bool:
        return self.started

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def fire(self) -> None:
        self.on_escape()


@recording
class FakeDisplay(Recording, NullDisplay):
    pass


@recording
class FakeWindows(Recording, NullWindows):
    pass


@recording
class FakeInput(Recording, NullInput):
    def escape_watcher(self, on_escape: Callable[[], None]) -> FakeEscapeWatcher:
        return FakeEscapeWatcher(on_escape)


@recording
class FakeClipboard(Recording, NullClipboard):
    pass


@recording
class FakeAccessibility(Recording, NullAccessibility):
    pass


@recording
class FakeCapture(Recording, NullCapture):
    pass


@recording
class FakeSystem(Recording, NullSystem):
    instance = ""       # a plain attribute here: tests may set it


@recording
class FakeApps(Recording, NullApps):
    pass


@recording
class FakeProcesses(Recording, NullProcesses):
    pass


@recording
class FakeNotifications(Recording, NullNotifications):
    pass


@recording
class FakeSecretStore(Recording, NullSecretStore):
    pass


@recording
class FakeKnownFolders(Recording, NullKnownFolders):
    pass


@recording
class FakeAutostart(Recording, NullAutostart):
    pass


@recording
class FakeSaveMarks(Recording, NullSaveMarks):
    pass


@recording
class FakeInstaller(Recording, NullInstaller):
    pass


@dataclass(frozen=True)
class FakePlatform(Platform):
    """Every port faked; pass a port to use it instead."""

    name: str = "fake"
    display: DisplayPort = field(default_factory=FakeDisplay)
    windows: WindowPort = field(default_factory=FakeWindows)
    input: InputPort = field(default_factory=FakeInput)
    clipboard: ClipboardPort = field(default_factory=FakeClipboard)
    accessibility: AccessibilityPort = field(default_factory=FakeAccessibility)
    capture: CapturePort = field(default_factory=FakeCapture)
    system: SystemPort = field(default_factory=FakeSystem)
    apps: AppsPort = field(default_factory=FakeApps)
    processes: ProcessPort = field(default_factory=FakeProcesses)
    notifications: NotificationPort = field(default_factory=FakeNotifications)
    secrets: SecretStore = field(default_factory=FakeSecretStore)
    folders: KnownFolders = field(default_factory=FakeKnownFolders)
    autostart: AutostartPort = field(default_factory=FakeAutostart)
    save_marks: SaveMarks = field(default_factory=FakeSaveMarks)
    installer: InstallerPort = field(default_factory=FakeInstaller)
