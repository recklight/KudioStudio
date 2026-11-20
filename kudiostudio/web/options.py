# -*- coding: utf-8 -*-
"""The Settings dialog: what each setting may be, what it starts as, and
where it takes effect.

The values are kept in the window's settings file under ``options``, beside
the language and the analysis parameters, so ``kudiostudio --reset`` forgets
them with the rest. The window's state carries them, and the page applies
the ones that are its business: the size, the fonts, the spectrogram's
colours and the question before unsaved audio is replaced.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Tuple

from kudiostudio.theme import TEXT_SETS
from kudiostudio.web.commands import command

log = logging.getLogger(__name__)

#: interface sizes offered. The page zooms as a whole, the plots' labels
#: included, so nothing has to be laid out again for each
ZOOMS = (0.9, 1.0, 1.1, 1.25, 1.5)
#: what Save As writes, by libsndfile's names
BIT_DEPTHS = ("PCM_16", "PCM_24", "FLOAT")
#: matplotlib's colour maps for the spectrogram. The first four are even in
#: lightness from end to end, so a level reads the same in any of them; gray
#: is for a printout
SPECTRUM_MAPS = ("inferno", "magma", "viridis", "cividis", "gray")
#: offered as the font box is typed into; any installed font can be typed
UI_FONTS = ("Segoe UI", "Microsoft JhengHei UI", "Noto Sans TC", "PingFang TC", "Arial")
MONO_FONTS = ("Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "Courier New")
#: undo steps. Each holds a copy of the clip, and an hour of 48 kHz mono is
#: 690 MB in float32, so the top is kept well short of what a long take costs
UNDO_STEPS = (1, 100)
#: the splice crossfade, as the transport's box allows it
CROSSFADE_MS = (0.0, 200.0)

DEFAULTS: Dict[str, object] = {
    "zoom": 1.0,
    # one of theme.TEXT_SETS
    "text_colors": "standard",
    "ui_font": "",
    "mono_font": "",
    "bit_depth": "PCM_16",
    "crossfade_ms": 5.0,
    "undo_depth": 20,
    "spectrum_map": "inferno",
    "ask_before_replacing": True,
    "reopen_last": False,
    # where every save dialog starts; "" is the folder last saved to
    "output_dir": "",
    # "auto" is the model this release recommends (tools/claude.py)
    "claude_model": "auto",
}

#: a font name goes into a CSS value, quoted; these would end the quotes or
#: the declaration
_NOT_IN_A_FONT = re.compile(r"[\"'\\;{}<>\n\r]")
#: a model id as the Models API gives it, claude-sonnet-5-5
_MODEL_ID = re.compile(r"claude-[a-z0-9][a-z0-9.\-]{1,62}")


def checked(key: str, value):
    """*value* as setting *key* keeps it; ValueError when it may not be."""
    if key not in DEFAULTS:
        raise ValueError(f"no setting {key!r}")
    if key == "zoom":
        value = float(value)
        if not any(abs(value - z) < 1e-9 for z in ZOOMS):
            raise ValueError(f"the size is one of {', '.join(f'{z:.0%}' for z in ZOOMS)}")
        return value
    if key in ("ui_font", "mono_font"):
        value = " ".join(str(value).split())
        if len(value) > 64 or _NOT_IN_A_FONT.search(value):
            raise ValueError(f"{value[:64]!r} is not a font name")
        return value
    if key == "bit_depth":
        if value not in BIT_DEPTHS:
            raise ValueError(f"Save As writes {', '.join(BIT_DEPTHS)}")
        return value
    if key == "text_colors":
        if value not in TEXT_SETS:
            raise ValueError(f"the text colours are {', '.join(TEXT_SETS)}")
        return value
    if key == "claude_model":
        value = str(value)
        if value != "auto" and not _MODEL_ID.fullmatch(value):
            raise ValueError(f"{value[:64]!r} is not a model id")
        return value
    if key == "spectrum_map":
        if value not in SPECTRUM_MAPS:
            raise ValueError(f"the colour maps are {', '.join(SPECTRUM_MAPS)}")
        return value
    if key == "crossfade_ms":
        value = float(value)
        low, high = CROSSFADE_MS
        if not low <= value <= high:
            raise ValueError(f"a crossfade is {low:g} to {high:g} ms")
        return value
    if key == "output_dir":
        value = str(value or "").strip()
        if value and not Path(value).is_dir():
            raise ValueError(f"{value} is not a folder")
        return value
    if key == "undo_depth":
        if isinstance(value, bool) or float(value) != int(float(value)):
            raise ValueError("undo steps are a whole number")
        value = int(float(value))
        low, high = UNDO_STEPS
        if not low <= value <= high:
            raise ValueError(f"undo keeps {low} to {high} steps")
        return value
    if not isinstance(value, bool):
        raise ValueError(f"{key} is on or off")
    return value


def subtype_for(path, bit_depth: str) -> Tuple[str, str]:
    """The subtype to write *path* with, and a line to say when it is not
    *bit_depth*: FLAC holds integers only, so float there is 24-bit."""
    if bit_depth == "FLOAT" and Path(path).suffix.lower() == ".flac":
        return "PCM_24", (f"FLAC holds no floating point, so {Path(path).name} "
                          f"was written as 24-bit.")
    return bit_depth, ""


class Options:
    """The settings, read from the window's settings file and kept there."""

    def __init__(self, studio) -> None:
        self.studio = studio
        self.values: Dict[str, object] = dict(DEFAULTS)
        saved = studio.prefs.get("options") or {}
        if isinstance(saved, dict):
            for key, value in saved.items():
                # one value edited by hand into nonsense costs that one alone
                try:
                    self.values[key] = checked(key, value)
                except (TypeError, ValueError):
                    log.info("setting %s=%r ignored", key, value)
        self._apply_to_session()

    def __getitem__(self, key: str):
        return self.values[key]

    def _apply_to_session(self) -> None:
        s = self.studio.session
        s.set_undo_depth(int(self.values["undo_depth"]))
        s.set_crossfade_ms(float(self.values["crossfade_ms"]))

    def _store(self) -> None:
        changed = {k: v for k, v in self.values.items() if v != DEFAULTS[k]}
        self.studio.prefs.set("options", changed)

    def put(self, key: str, value) -> None:
        """Set *key*, keep it, and apply what is applied here."""
        self.values[key] = checked(key, value)
        self._store()
        if key in ("undo_depth", "crossfade_ms"):
            self._apply_to_session()

    @command
    def describe(self) -> Dict:
        return {"values": dict(self.values), "defaults": dict(DEFAULTS),
                "zooms": list(ZOOMS), "bit_depths": list(BIT_DEPTHS),
                "text_colors": list(TEXT_SETS),
                "spectrum_maps": list(SPECTRUM_MAPS),
                "fonts": {"ui_font": list(UI_FONTS), "mono_font": list(MONO_FONTS)},
                "undo_steps": list(UNDO_STEPS), "crossfade_ms": list(CROSSFADE_MS)}

    @command(name="set")
    def set_option(self, key: str, value) -> Dict:
        try:
            self.put(key, value)
        except (TypeError, ValueError) as e:
            self.studio.say(f"Not changed — {e}.")
        self.studio._push_state()
        return self.describe()

    @command
    def reset(self) -> Dict:
        """Every setting back to how it starts. The language and the analysis
        parameters are not settings of this dialog and stay."""
        self.values = dict(DEFAULTS)
        self._store()
        self._apply_to_session()
        self.studio.say("Settings restored to their defaults.")
        self.studio._push_state()
        return self.describe()


__all__ = ["BIT_DEPTHS", "DEFAULTS", "MONO_FONTS", "Options", "SPECTRUM_MAPS", "UI_FONTS",
           "UNDO_STEPS", "ZOOMS", "checked", "subtype_for"]
