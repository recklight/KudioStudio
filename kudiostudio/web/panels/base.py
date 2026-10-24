# -*- coding: utf-8 -*-
"""The Python half of a tool panel.

A panel is a set of `command` methods the page calls as ``<id>.<name>``,
plus whatever it holds between calls (a loaded impulse response, the last
split). Its other half is a page module at ``static/<script>``, which builds
the controls and calls these. Panels talk to the session and to the status
bar, never to each other.

A panel from another package (see `kudiostudio.plugins`) keeps its page
module in its own package and says where in `static_dir`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from kudiostudio.web.commands import command


class WebPanel:
    """Base class: one tool on the rail."""

    #: the command prefix, the rail's key and the panel's element id
    id = ""
    #: its label on the rail, in English; the page translates it
    title = ""
    #: its icon, a name from `kudiostudio.shapes`
    icon = ""
    #: its page module, relative to the static folder
    script = ""
    #: the folder `script` is in, for a panel from another package; None for
    #: the ones that ship here, whose modules are in KudioStudio's own
    static_dir: Optional[Path] = None

    def __init__(self, studio) -> None:
        self.studio = studio

    @property
    def session(self):
        return self.studio.session

    # ------------------------------------------------------------- helpers

    def report(self, text: str) -> None:
        """A line for the status bar."""
        self.studio.say(text)

    def require_audio(self) -> bool:
        if self.session.has_audio:
            return True
        self.report("Load an audio file first.")
        return False

    def publish(self, **data) -> None:
        """Something for this panel's page module, as a ``panel`` event."""
        self.studio.events.publish("panel", panel=self.id, **data)

    def start_job(self, label: str, fn, *args, **kwargs) -> bool:
        """`Jobs.run` under this panel's name; False while one is running."""
        return self.studio.jobs.run(self.id, label, fn, *args, **kwargs)

    @property
    def busy(self) -> bool:
        return self.studio.jobs.busy(self.id)

    # ---------------------------------------------------------------- hooks

    def on_session_changed(self) -> None:
        """The current audio changed. Override as needed."""

    def cancel_preview(self) -> None:
        """Something else took over playback; drop any preview state."""

    def shutdown(self) -> None:
        """The window is closing; let go of devices."""

    @command
    def describe(self) -> Dict:
        """What the page module needs to draw the panel; override to add."""
        return {"id": self.id, "busy": self.busy}


__all__ = ["WebPanel"]
