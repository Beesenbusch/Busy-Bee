"""Rules that switch, pause or stop tracking depending on which window has focus.

A rule matches a window by program file name and/or a piece of its title. Windows
without a matching rule change nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from . import timeutil

if TYPE_CHECKING:
    from .windows import WindowInfo

SWITCH, PAUSE, STOP = "switch", "pause", "stop"
ACTION_LABELS = {SWITCH: "Switch to project", PAUSE: "Pause tracking", STOP: "Stop tracking"}

# Timing defaults, both adjustable under Settings.
FOCUS_CHECK_DEFAULT_S = 15  # how often the focused window is looked at
FOCUS_HOLD_DEFAULT_S = 60  # how long a window must keep focus before its rule fires


@dataclass(frozen=True)
class Rule:
    id: int
    exe: str  # program file name, e.g. "Code.exe"; empty matches any program
    title_contains: str  # empty matches any title
    action: str
    project_id: int | None = None

    def matches(self, window: WindowInfo) -> bool:
        if self.exe and self.exe.lower() != window.exe.lower():
            return False
        return self.title_contains.lower() in window.title.lower()

    @property
    def specificity(self) -> tuple[int, int]:
        return (bool(self.exe) + bool(self.title_contains), len(self.title_contains))


def clean_rule(exe: str, title_contains: str, action: str, project_id: int | None) -> tuple[str, str, str, int | None]:
    """Normalise rule fields; raises ValueError if they don't make a usable rule."""
    exe, title_contains = exe.strip(), " ".join(title_contains.split())
    if not exe and not title_contains:
        raise ValueError("Enter a program, a piece of the window title, or both.")
    if action not in ACTION_LABELS:
        raise ValueError(f"Unknown action: {action}")
    if action == SWITCH and project_id is None:
        raise ValueError("Choose the project to switch to.")
    return exe, title_contains, action, project_id if action == SWITCH else None


def find_rule(rules: list[Rule], window: WindowInfo) -> Rule | None:
    """The most specific matching rule.

    Program and title beat only one of them, a longer title text beats a shorter one,
    and on a tie the earlier rule in the list wins.
    """
    best = None
    for rule in rules:
        if rule.matches(window) and (best is None or rule.specificity > best.specificity):
            best = rule
    return best


class AutoSwitcher:
    """Decides when a rule fires.

    A rule fires once its windows have kept focus for `hold_seconds`, and only once per
    stretch of focus, so a manual change is not undone while you stay in that window.
    """

    def __init__(self, clock: Callable[[], datetime] = timeutil.now):
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._rule_id: int | None = None
        self._since: datetime | None = None
        self._fired = False

    def observe(self, rules: list[Rule], window: WindowInfo | None, hold_seconds: float) -> Rule | None:
        """Feed the currently focused window; returns the rule to carry out, if any."""
        if window is None:
            return None  # desktop, Busy Bee itself, ...: neither a match nor a reason to cancel one
        rule = find_rule(rules, window)
        current = self._clock()
        rule_id = rule.id if rule else None
        if rule_id != self._rule_id or self._since is None:
            self._rule_id, self._since, self._fired = rule_id, current, False
        if rule is None or self._fired or (current - self._since).total_seconds() < hold_seconds:
            return None
        self._fired = True
        return rule
