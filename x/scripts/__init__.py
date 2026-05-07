"""Install script and systemd unit templates served by X."""

from __future__ import annotations

import os
from string import Template

_HERE = os.path.dirname(os.path.abspath(__file__))


def _read(name: str) -> str:
    with open(os.path.join(_HERE, name), "r", encoding="utf-8") as f:
        return f.read()


def render(name: str, **vars) -> str:
    """Render an installer template with the given variables.

    Templates use $VAR / ${VAR} syntax. Missing keys → empty string (safe_substitute).
    """
    tmpl = Template(_read(name))
    safe = {k: ("" if v is None else str(v)) for k, v in vars.items()}
    return tmpl.safe_substitute(**safe)
