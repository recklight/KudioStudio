# -*- coding: utf-8 -*-
"""Interface language.

The interface is written in English, in the code, where the strings are read
alongside what they describe. A translation is a table from those strings to
another language (`kudiostudio.locales`), applied where text reaches the
screen rather than where it is written:

* the Qt setters that put text in front of a person -- ``QLabel.setText``,
  ``setToolTip``, ``QStatusBar.showMessage``, menu and tab titles, ... -- are
  wrapped, so a message assembled at run time is translated when it is shown;
* a widget built with its text in the constructor is translated once, when it
  is first polished, before it is ever painted.

So the panels need no ``tr()`` around every string, and a string with no
translation is simply shown in English rather than missing.

**A combo box keeps its English.** Panels read ``currentText()`` back as a
value -- a method name, a mode -- so the item shows the translation while
``currentText()`` and ``itemText()`` still return the English it was added
with. Everything else the code reads back is user input (a path, a number),
which is never translated.

The language is picked once, before the window is built (`install`); changing
it takes a restart, since text already on screen was translated as it was set.
"""
from __future__ import annotations

import importlib
import logging
import re
import string
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: language code -> its own name, as the menu lists it
LANGUAGES = {
    "en": "English",
    "zh_TW": "繁體中文",
}
DEFAULT = "en"
SETTINGS_KEY = "language"

_messages: Dict[str, str] = {}
_templates: List[Tuple[re.Pattern, str]] = []
_cache: Dict[str, str] = {}
_language = DEFAULT
_installed = False

#: item role holding a combo box item's untranslated text
_ORIGINAL_ROLE = 0x0100 + 0x4B53  # Qt.UserRole + an arbitrary offset


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
    """What the operating system asks for, if we have it; else English."""
    from PySide6.QtCore import QLocale
    for name in QLocale.system().uiLanguages():
        code = normalize(name)
        if code != DEFAULT or name.lower().startswith("en"):
            return code
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


def load(language: str) -> None:
    """Make `language` the one `tr` translates into. No Qt involved."""
    global _language
    _language = normalize(language)
    _messages.clear()
    _templates.clear()
    _cache.clear()
    if _language == DEFAULT:
        return
    module = importlib.import_module(f"kudiostudio.locales.{_language}")
    templated = []
    for key, value in module.MESSAGES.items():
        pattern = _compile(key)
        if pattern is None:
            _messages[key] = value
        else:
            static = len(re.sub(r"\{[^{}]*\}", "", key))
            templated.append((static, pattern, value))
    # the most specific template first: "{} failed — {}: {}" before "{} — {}"
    templated.sort(key=lambda t: -t[0])
    _templates.extend((p, v) for _, p, v in templated)


def _lookup(text: str) -> Optional[str]:
    exact = _messages.get(text)
    if exact is not None:
        return exact
    for pattern, value in _templates:
        m = pattern.match(text)
        if m is not None:
            # a value can itself be a phrase with a translation
            values = [_messages.get(g, g) for g in m.groups()]
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


# --------------------------------------------------------------- Qt glue


def _wrap(cls, name: str, positions=(0,)) -> None:
    """Translate the str arguments at `positions` of ``cls.name``."""
    original = getattr(cls, name)

    def wrapper(self, *args, **kwargs):
        if args:
            args = list(args)
            for i in positions:
                if i < len(args) and isinstance(args[i], str):
                    args[i] = tr(args[i])
        return original(self, *args, **kwargs)

    wrapper.__name__ = name
    wrapper.__wrapped__ = original
    setattr(cls, name, wrapper)


def _wrap_static(cls, name: str, positions) -> None:
    original = getattr(cls, name)

    def wrapper(*args, **kwargs):
        args = list(args)
        for i in positions:
            if i < len(args) and isinstance(args[i], str):
                args[i] = tr(args[i])
        for key in ("caption", "title", "label", "text", "filter"):
            if isinstance(kwargs.get(key), str):
                kwargs[key] = tr(kwargs[key])
        return original(*args, **kwargs)

    wrapper.__wrapped__ = original
    setattr(cls, name, staticmethod(wrapper))


def _patch_combo_box() -> None:
    from PySide6.QtWidgets import QComboBox

    add_item = QComboBox.addItem
    insert_item = QComboBox.insertItem
    set_item_text = QComboBox.setItemText
    item_text = QComboBox.itemText
    current_text = QComboBox.currentText
    find_text = QComboBox.findText
    set_current_text = QComboBox.setCurrentText

    def _shown(text):
        return tr(text) if isinstance(text, str) else text

    def _remember(box, index, text):
        if isinstance(text, str) and _shown(text) != text:
            box.setItemData(index, text, _ORIGINAL_ROLE)

    def addItem(self, *args, **kwargs):  # noqa: N802 - Qt name
        args = list(args)
        at = 1 if args and not isinstance(args[0], str) else 0  # (icon, text)
        text = args[at] if len(args) > at else None
        if isinstance(text, str) and not self.isEditable():
            args[at] = _shown(text)
        add_item(self, *args, **kwargs)
        if not self.isEditable():
            _remember(self, self.count() - 1, text)

    def insertItem(self, index, *args, **kwargs):  # noqa: N802
        args = list(args)
        at = 1 if args and not isinstance(args[0], str) else 0
        text = args[at] if len(args) > at else None
        if isinstance(text, str) and not self.isEditable():
            args[at] = _shown(text)
        insert_item(self, index, *args, **kwargs)
        if not self.isEditable():
            _remember(self, index if 0 <= index < self.count() else self.count() - 1, text)

    def addItems(self, texts):  # noqa: N802
        for text in texts:
            addItem(self, text)

    def insertItems(self, index, texts):  # noqa: N802
        for offset, text in enumerate(texts):
            insertItem(self, index + offset, text)

    def setItemText(self, index, text):  # noqa: N802
        set_item_text(self, index, _shown(text) if not self.isEditable() else text)
        self.setItemData(index, None, _ORIGINAL_ROLE)
        if not self.isEditable():
            _remember(self, index, text)

    def itemText(self, index):  # noqa: N802
        original = self.itemData(index, _ORIGINAL_ROLE)
        return original if isinstance(original, str) else item_text(self, index)

    def currentText(self):  # noqa: N802
        if not self.isEditable() and self.currentIndex() >= 0:
            return itemText(self, self.currentIndex())
        return current_text(self)

    def findText(self, text, *args):  # noqa: N802
        for i in range(self.count()):
            if itemText(self, i) == text:
                return i
        return find_text(self, text, *args)

    def setCurrentText(self, text):  # noqa: N802
        if not self.isEditable():
            index = findText(self, text)
            if index >= 0:
                self.setCurrentIndex(index)
                return
        set_current_text(self, text)

    for fn in (addItem, insertItem, addItems, insertItems, setItemText,
               itemText, currentText, findText, setCurrentText):
        setattr(QComboBox, fn.__name__, fn)


def _translate_widget(widget) -> None:
    """Translate the text a widget was constructed with."""
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractSpinBox,
        QDockWidget,
        QGroupBox,
        QLabel,
        QLineEdit,
        QMenu,
        QMenuBar,
        QToolBar,
    )

    if widget.property("_kudio_i18n"):
        return
    widget.setProperty("_kudio_i18n", True)

    if isinstance(widget, (QLabel, QAbstractButton)):
        text = widget.text()
        if text:
            widget.setText(text)  # the wrapped setter translates
    elif isinstance(widget, QGroupBox):
        if widget.title():
            widget.setTitle(widget.title())
    elif isinstance(widget, QLineEdit):
        if widget.placeholderText():
            widget.setPlaceholderText(widget.placeholderText())
    elif isinstance(widget, QAbstractSpinBox):
        for get, put in (("suffix", "setSuffix"), ("prefix", "setPrefix"),
                         ("specialValueText", "setSpecialValueText")):
            if hasattr(widget, get) and getattr(widget, get)():
                getattr(widget, put)(getattr(widget, get)())
    if isinstance(widget, (QMenu, QMenuBar, QToolBar)):
        if isinstance(widget, QMenu) and widget.title():
            widget.setTitle(widget.title())
        for action in widget.actions():
            if action.text():
                action.setText(action.text())
            if action.toolTip() and action.toolTip() != action.text():
                action.setToolTip(action.toolTip())
    if widget.toolTip():
        widget.setToolTip(widget.toolTip())
    if (widget.isWindow() or isinstance(widget, QDockWidget)) and widget.windowTitle():
        widget.setWindowTitle(widget.windowTitle())


def _install_qt(app) -> None:
    from PySide6.QtCore import QEvent, QLibraryInfo, QObject, QTranslator
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QAbstractButton,
        QDoubleSpinBox,
        QFileDialog,
        QGroupBox,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMenu,
        QMenuBar,
        QMessageBox,
        QSpinBox,
        QStatusBar,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QToolBar,
        QWidget,
    )

    _wrap(QLabel, "setText")
    _wrap(QAbstractButton, "setText")
    _wrap(QWidget, "setToolTip")
    _wrap(QWidget, "setWindowTitle")
    _wrap(QGroupBox, "setTitle")
    _wrap(QLineEdit, "setPlaceholderText")
    _wrap(QAction, "setText")
    _wrap(QAction, "setToolTip")
    _wrap(QAction, "setStatusTip")
    _wrap(QMenu, "setTitle")
    _wrap(QMenu, "addMenu", positions=(0, 1))
    _wrap(QMenu, "addAction", positions=(0, 1))
    _wrap(QMenu, "addSection", positions=(0, 1))
    _wrap(QMenuBar, "addMenu", positions=(0, 1))
    _wrap(QMenuBar, "addAction", positions=(0,))
    _wrap(QToolBar, "addAction", positions=(0, 1))
    _wrap(QStatusBar, "showMessage")
    _wrap(QTabWidget, "addTab", positions=(1, 2))
    _wrap(QTabWidget, "insertTab", positions=(2, 3))
    _wrap(QTabWidget, "setTabText", positions=(1,))
    _wrap(QTabWidget, "setTabToolTip", positions=(1,))
    _wrap(QTableWidgetItem, "setText")
    _wrap(QTableWidgetItem, "setToolTip")
    for spin in (QSpinBox, QDoubleSpinBox):
        _wrap(spin, "setSuffix")
        _wrap(spin, "setPrefix")
        _wrap(spin, "setSpecialValueText")

    labels = QTableWidget.setHorizontalHeaderLabels
    QTableWidget.setHorizontalHeaderLabels = (
        lambda self, names: labels(self, [tr(n) for n in names]))
    vlabels = QTableWidget.setVerticalHeaderLabels
    QTableWidget.setVerticalHeaderLabels = (
        lambda self, names: vlabels(self, [tr(n) for n in names]))

    # QTableWidgetItem("Peak") never calls setText; translate on the way in
    set_item = QTableWidget.setItem

    def setItem(self, row, column, item):  # noqa: N802
        if item is not None and item.text():
            item.setText(item.text())
        if item is not None and item.toolTip():
            item.setToolTip(item.toolTip())
        return set_item(self, row, column, item)

    QTableWidget.setItem = setItem

    # (parent, caption, dir, filter) and (parent, title, label/text, ...)
    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName",
                 "getExistingDirectory"):
        _wrap_static(QFileDialog, name, positions=(1, 3))
    for name in ("getText", "getInt", "getDouble", "getItem", "getMultiLineText"):
        _wrap_static(QInputDialog, name, positions=(1, 2))
    for name in ("about", "information", "warning", "critical", "question"):
        _wrap_static(QMessageBox, name, positions=(1, 2))

    _patch_combo_box()

    class _Polisher(QObject):
        """Translate each widget's constructor text before its first paint."""

        def eventFilter(self, obj, event):  # noqa: N802 - Qt hook
            if event.type() == QEvent.Polish and isinstance(obj, QWidget):
                try:
                    _translate_widget(obj)
                except RuntimeError:  # deleted under us
                    pass
            return False

    app._kudio_polisher = _Polisher(app)
    app.installEventFilter(app._kudio_polisher)

    # Qt's own words: OK / Cancel, the file dialog, context menus
    folder = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    app._kudio_translators = []
    for name in ("qtbase", "qt"):
        translator = QTranslator(app)
        if translator.load(f"{name}_{_language}", folder):
            app.installTranslator(translator)
            app._kudio_translators.append(translator)
            break


def install(app, language: Optional[str] = None) -> str:
    """Pick the interface language for this process; returns its code.

    `language` overrides the saved setting; with neither, the system's
    language is used when there is a translation for it. Call once, after
    the QApplication exists (for QSettings) and before any window is built.
    """
    global _installed
    if language is None:
        from PySide6.QtCore import QSettings
        language = QSettings().value(SETTINGS_KEY, None)
        if not language:
            language = system_language()
    load(language)
    if _language != DEFAULT and not _installed:
        _install_qt(app)
        _installed = True
    log.debug("interface language: %s", _language)
    return _language
