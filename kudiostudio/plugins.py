# -*- coding: utf-8 -*-
"""Panels that come from other packages.

A package puts a panel on the tool rail by naming it in the
``kudiostudio.panels`` entry point group of its own ``pyproject.toml``::

    [project.entry-points."kudiostudio.panels"]
    spectra = "mypackage.web:SpectraPanel"

What it names is a `kudiostudio.web.panels.base.WebPanel` subclass, built
the way the panels that ship with KudioStudio are -- ``cls(studio)`` -- whose
``command`` methods the page calls as ``<id>.<name>``. Its page module ships
in the package: ``static_dir`` names the folder that holds it and ``script``
the module, relative to that folder, and the window serves the folder at
``/plugins/<id>/``. The module mounts the way the built-in ones do (see
``static/js/panels.js``), with the same controls and the same view over the
plots. Two more class attributes are read when it is found:

``icon_svg``
    its rail icon, as an SVG body on the 24-unit grid of
    `kudiostudio.shapes`, registered under the panel's ``icon`` name
``messages``
    its translations, ``{"zh_TW": {"English": "中文", ...}}``, in the form
    `kudiostudio.locales` uses

A plugin that fails to import, names something that is not a panel, or
raises while it is built is left off the rail and named in the status bar;
the window opens without it. ``--no-plugins``, or the environment variable
``KUDIOSTUDIO_NO_PLUGINS``, leaves every plugin out, for when one of them is
the problem.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib import metadata
from typing import List, Optional

from kudiostudio import i18n, shapes

log = logging.getLogger(__name__)

#: the entry point group a package lists its panels under
GROUP = "kudiostudio.panels"

#: set to anything but an empty string to load no plugins at all
DISABLE_ENV = "KUDIOSTUDIO_NO_PLUGINS"


@dataclass
class Plugin:
    """One entry point found under `GROUP`, loaded or not."""

    #: the entry point's name, ``spectra`` in the example above
    name: str
    #: the distribution that declared it, with its version, for About
    source: str
    #: the panel class, or ``None`` when it could not be loaded
    panel: Optional[type] = None
    #: why it was left out; empty when it loaded
    error: str = ""


def enabled() -> bool:
    """False when plugins have been switched off for this process."""
    return not os.environ.get(DISABLE_ENV)


def _entry_points(group: str = GROUP) -> list:
    found = metadata.entry_points()
    if hasattr(found, "select"):  # Python 3.10 and later
        listed = found.select(group=group)
    else:  # 3.9: a dict of lists
        listed = found.get(group, ())
    # 3.9 lists a package installed with ``pip install -e`` twice, once for
    # its dist-info and once for the egg-info the build leaves in its source
    unique = {}
    for entry_point in listed:
        unique.setdefault((entry_point.name, entry_point.value), entry_point)
    return list(unique.values())


def _source(entry_point) -> str:
    dist = getattr(entry_point, "dist", None)
    if dist is not None:
        try:
            return f"{dist.metadata['Name']} {dist.version}"
        except Exception:
            pass
    return entry_point.value.split(":")[0]


def discover() -> List[Plugin]:
    """Every panel other packages have registered, in entry point name order.

    Each is imported and its icon and translations registered here, before
    any panel is built, so that its constructor's text is translated like
    everyone else's.
    """
    if not enabled():
        return []
    from kudiostudio.web.panels.base import WebPanel

    plugins = []
    for entry_point in sorted(_entry_points(GROUP), key=lambda e: e.name):
        plugin = Plugin(entry_point.name, _source(entry_point))
        try:
            cls = entry_point.load()
            if not (isinstance(cls, type) and issubclass(cls, WebPanel)):
                raise TypeError(f"{entry_point.value} is not a WebPanel subclass")
            register(cls)
            plugin.panel = cls
        except Exception as e:
            plugin.error = f"{type(e).__name__}: {e}"
            log.warning("plugin %s left out -- %s", plugin.name, plugin.error)
        plugins.append(plugin)
    return plugins


def register(cls: type) -> None:
    """Take a plugin panel's icon and translations on board."""
    shape = getattr(cls, "icon_svg", "")
    if shape:
        if not cls.icon:
            raise ValueError(f"{cls.__name__} has an icon_svg but no icon name")
        shapes.register(cls.icon, shape)
    for language, table in (getattr(cls, "messages", None) or {}).items():
        i18n.add_messages(language, table)


__all__ = ["GROUP", "DISABLE_ENV", "Plugin", "enabled", "discover", "register"]
