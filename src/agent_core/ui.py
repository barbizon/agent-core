"""Small terminal styling helpers: colors, icons and panels. No dependencies.

Panels have a border on the left only. Icons (emoji) take two columns in most
terminals but one in some, so anything aligned *after* an icon could drift;
with a left border only, nothing has to line up on the right.

Colors are used only on a real terminal, and never when NO_COLOR is set
(https://no-color.org). Without colors the same text is printed plainly, which
is also what tests and pipes see.
"""

import os
import re
import shutil
import sys
import textwrap
import unicodedata
from typing import Iterable

RESET = "\033[0m"
CODES = {
    "bold": "1", "dim": "2", "italic": "3",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "gray": "90",
}
ANSI = re.compile(r"\033\[[0-9;]*m")

# Rounded box-drawing pieces for panels.
TOP, SIDE, BOTTOM = "╭─", "│", "╰─"


def use_color(stream=None) -> bool:
    """True when output goes to a terminal and the user hasn't asked for no colors."""
    stream = stream or sys.stdout
    return "NO_COLOR" not in os.environ and hasattr(stream, "isatty") and stream.isatty()


class Style:
    """Paint text, or leave it plain when colors are off."""

    def __init__(self, color: bool | None = None) -> None:
        self.color = use_color() if color is None else color

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(CODES[s] for s in styles)
        return f"\033[{codes}m{text}{RESET}"


def width(text: str) -> int:
    """Columns text takes on screen (colors take none, wide emoji take two)."""
    plain = ANSI.sub("", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else
               0 if unicodedata.combining(ch) or ch == "️" else 1
               for ch in plain)


def pad(text: str, size: int) -> str:
    """Pad text with spaces to size columns (colors and emoji counted right)."""
    return text + " " * max(0, size - width(text))


def screen_width(limit: int = 96) -> int:
    return max(48, min(limit, shutil.get_terminal_size((80, 24)).columns))


def wrap(text: str, size: int) -> list[str]:
    return textwrap.wrap(text, width=max(20, size)) or [""]


def panel(title: str, body: Iterable[str], style: Style, footer: str | None = None,
          accent: str = "cyan") -> str:
    """A titled block with a left border:

        ╭─ 🧰 Tools
        │  ...
        ╰─ footer
    """
    edge = lambda piece: style(piece, accent)  # noqa: E731
    lines = [f"{edge(TOP)} {style(title, 'bold')}"]
    lines += [f"{edge(SIDE)}  {line}" if line else edge(SIDE) for line in body]
    lines.append(f"{edge(BOTTOM)} {style(footer, 'dim')}" if footer else edge(BOTTOM))
    return "\n".join(lines)
