# -*- coding: utf-8 -*-
"""Signals for the session.

`AudioSession` announces its changes as ``session.changed.connect(slot)`` and
``session.changed.emit()``. It lives behind the local server the window talks
to and imports no GUI toolkit, so it declares them with this class.

A bound method is held weakly, so a slot can be connected and forgotten:
connecting ``panel.refresh`` does not keep the panel alive, and once the
panel is gone its slot quietly drops out.

Slots run on the thread that emits, at once, in the order they connected.
"""
from __future__ import annotations

import inspect
import weakref
from typing import Callable, List

__all__ = ["Signal", "BoundSignal"]


class BoundSignal:
    """One object's instance of a `Signal`."""

    __slots__ = ("_slots", "__weakref__")

    def __init__(self) -> None:
        self._slots: List[Callable[[], Callable]] = []

    def connect(self, slot: Callable) -> None:
        if inspect.ismethod(slot):
            ref = weakref.WeakMethod(slot)
        else:
            def ref(slot=slot):
                return slot
        self._slots.append(ref)

    def disconnect(self, slot: Callable = None) -> None:
        """Remove *slot*, or every slot when none is given."""
        if slot is None:
            self._slots.clear()
            return
        for ref in list(self._slots):
            if ref() == slot:
                self._slots.remove(ref)
                return
        raise RuntimeError(f"{slot!r} is not connected")

    def emit(self, *args) -> None:
        for ref in list(self._slots):
            slot = ref()
            if slot is None:
                self._drop(ref)
                continue
            slot(*args)

    def _drop(self, ref) -> None:
        try:
            self._slots.remove(ref)
        except ValueError:
            pass

    def __len__(self) -> int:
        return len(self._slots)


class Signal:
    """Declared once on a class; each instance gets its own.

    The argument types are documentation only: nothing converts or checks
    them, the slots receive what `emit` was given.
    """

    def __init__(self, *types) -> None:
        self.types = types
        self._attr = ""

    def __set_name__(self, owner, name: str) -> None:
        self._attr = f"_signal_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._attr)
        if bound is None:
            bound = obj.__dict__[self._attr] = BoundSignal()
        return bound
