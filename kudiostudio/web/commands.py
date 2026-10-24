# -*- coding: utf-8 -*-
"""What the page may call, by name.

A method marked with `command` is callable from the page; nothing else is.
`Studio` collects its own and those of every panel, under the panel's id:
the Effects panel's ``apply`` is ``effects.apply`` on the wire.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional


def command(fn: Optional[Callable] = None, *, name: Optional[str] = None):
    """Mark *fn* as callable from the page, as *name* or its own name."""
    def mark(f: Callable) -> Callable:
        f._command = name or f.__name__
        return f
    return mark(fn) if fn is not None else mark


def commands_of(obj, prefix: str = "") -> Dict[str, Callable]:
    """Every command *obj* has, bound to it, keyed by *prefix* + its name.

    Walks the class hierarchy so a panel inherits its base's commands, and
    a subclass that redefines one keeps its own.
    """
    found: Dict[str, Callable] = {}
    for cls in type(obj).__mro__:
        for attr, value in vars(cls).items():
            name = getattr(value, "_command", None)
            if name and prefix + name not in found:
                found[prefix + name] = getattr(obj, attr)
    return found


__all__ = ["command", "commands_of"]
