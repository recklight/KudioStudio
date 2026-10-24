# -*- coding: utf-8 -*-
"""Interface language.

The interface is written in English, in the code, where the strings are read
alongside what they describe. A translation is a table from those strings to
another language (`kudiostudio.locales`), applied where text reaches the
screen rather than where it is written:

* the page looks every label, tooltip and menu entry up itself, in the table
  `table` hands it, so the code that builds a panel needs no ``tr()``;
* what the Python side says -- the status line, a result, a job's progress --
  goes through `translate` (or `tr`, in the window's own language) on its way
  out, so a message assembled at run time is translated too, by templates
  with ``{}`` where its values go.

A string with no translation is shown in English rather than missing. A
choice in a list keeps its English as its value and shows the translation,
so what the code reads back -- a method name, a mode -- never changes with
the language.
"""
from __future__ import annotations

import importlib
import logging
import os
import re
import string
import threading
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: language code -> its own name, as the menu lists it
LANGUAGES = {
    "en": "English",
    "zh_TW": "繁體中文",
}
DEFAULT = "en"

_messages: Dict[str, str] = {}
#: (characters outside the fields, pattern, translation), most specific first
_templated: List[Tuple[int, re.Pattern, str]] = []
_templates: List[Tuple[re.Pattern, str]] = []
_cache: Dict[str, str] = {}
#: language -> tables plugins have added, kept so `load` can apply them again
_extra: Dict[str, List[Dict[str, str]]] = {}
_switch_lock = threading.Lock()
_language = DEFAULT


def current() -> str:
    """The language in use, e.g. ``"en"`` or ``"zh_TW"``."""
    return _language


def normalize(code: Optional[str]) -> str:
    """A language code as `LANGUAGES` spells it, or `DEFAULT`.

    Accepts the spellings people type and locales report: ``zh-TW``,
    ``zh_tw``, ``zh_Hant_TW``, ``zh_HK``.
    """
    if not code:
        return DEFAULT
    key = str(code).strip().replace("-", "_").lower()
    if key.startswith("zh") and ("tw" in key or "hant" in key or "hk" in key):
        return "zh_TW"
    if key.startswith("en"):
        return "en"
    return DEFAULT


def system_language() -> str:
    """The operating system's interface language, when there is a
    translation for it; else English."""
    if os.name == "nt":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
                return normalize(buffer.value)
        except (AttributeError, OSError):
            pass
    import locale
    for name in (os.environ.get("LANG"), (locale.getlocale()[0] or "")):
        if name:
            return normalize(name.split(".")[0])
    return DEFAULT


# ------------------------------------------------------------------ lookup


def _compile(key: str) -> Optional[re.Pattern]:
    """A regex matching what the template `key` formats to."""
    parts = []
    fields = 0
    for literal, field, _, _ in string.Formatter().parse(key):
        parts.append(re.escape(literal))
        if field is not None:
            parts.append("(.*?)")
            fields += 1
    if not fields:
        return None
    return re.compile("".join(parts) + r"\Z", re.S)


def _merge(table: Dict[str, str]) -> None:
    """Add *table* to the current language's, keeping what is there."""
    known = {pattern.pattern for _, pattern, _ in _templated}
    for key, value in table.items():
        pattern = _compile(key)
        if pattern is None:
            _messages.setdefault(key, value)
        elif pattern.pattern not in known:
            static = len(re.sub(r"\{[^{}]*\}", "", key))
            _templated.append((static, pattern, value))
            known.add(pattern.pattern)
    # the most specific template first: "{} failed — {}: {}" before "{} — {}"
    _templated.sort(key=lambda t: -t[0])
    _templates[:] = [(p, v) for _, p, v in _templated]
    _cache.clear()


def load(language: str) -> None:
    """Make `language` the one `tr` translates into."""
    global _language
    _language = normalize(language)
    _messages.clear()
    _templated.clear()
    _templates.clear()
    _cache.clear()
    if _language == DEFAULT:
        return
    module = importlib.import_module(f"kudiostudio.locales.{_language}")
    _merge(module.MESSAGES)
    for table in _extra.get(_language, ()):
        _merge(table)


def add_messages(language: str, table: Dict[str, str]) -> None:
    """More translations into *language*, from a plugin panel.

    They count from now on, and again whenever the language is loaded. The
    table that ships with KudioStudio wins where both translate a string, so
    a plugin cannot change how the rest of the window reads.
    """
    code = normalize(language)
    if code == DEFAULT:
        return
    _extra.setdefault(code, []).append(dict(table))
    if code == _language:
        _merge(table)


def _lookup(text: str) -> Optional[str]:
    exact = _messages.get(text)
    if exact is not None:
        return exact
    for pattern, value in _templates:
        m = pattern.match(text)
        if m is not None:
            # a value can itself be a phrase with a translation, or a sentence
            # built from a template of its own ("within ±{} LU of {} LUFS"
            # inside a line about the whole tree). Each is shorter than the
            # text it came from, so this ends.
            values = [tr(g) if g else g for g in m.groups()]
            try:
                return value.format(*values)
            except (IndexError, KeyError, ValueError):
                return None
    return None


def tr(text):
    """`text` in the current language; unchanged when there is no translation."""
    if not _messages and not _templates:
        return text
    if not isinstance(text, str) or not text:
        return text
    hit = _cache.get(text)
    if hit is not None:
        return hit
    out = _lookup(text)
    if out is None and "\n" in text:
        # a message assembled from lines: translate what it is made of
        out = "\n".join(tr(line) for line in text.split("\n"))
    if out is None:
        out = text
    if len(_cache) > 4096:  # live readouts change every tick
        _cache.clear()
    _cache[text] = out
    return out


# ------------------------------------------------- one language, by name


def table(language: str) -> Dict[str, str]:
    """Every translation into *language*, keyed by the English: the table
    that ships with KudioStudio and what plugins added, the shipped one
    winning where both have a string. Empty for English.

    For the page, which looks strings up itself; reading it changes
    nothing about the language `tr` translates into.
    """
    code = normalize(language)
    if code == DEFAULT:
        return {}
    merged: Dict[str, str] = {}
    for extra in _extra.get(code, ()):
        merged.update(extra)
    module = importlib.import_module(f"kudiostudio.locales.{code}")
    merged.update(module.MESSAGES)
    return merged


def translate(text: str, language: str) -> str:
    """*text* in *language*, by the same rules as `tr`, whichever language
    `tr` is set to."""
    code = normalize(language)
    if code == DEFAULT or not isinstance(text, str) or not text:
        return text
    if code == _language:
        return tr(text)
    # the tables are module state: swap them in and back under a lock. The
    # window keeps `tr` on its own language, so it never gets here
    with _switch_lock:
        saved = (_language, dict(_messages), list(_templated),
                 list(_templates), dict(_cache))
        try:
            load(code)
            return tr(text)
        finally:
            _restore(saved)


def _restore(saved) -> None:
    global _language
    language, messages, templated, templates, cache = saved
    _language = language
    _messages.clear()
    _messages.update(messages)
    _templated[:] = templated
    _templates[:] = templates
    _cache.clear()
    _cache.update(cache)


__all__ = ["LANGUAGES", "DEFAULT", "current", "normalize", "system_language", "load",
           "add_messages", "tr", "table", "translate"]
