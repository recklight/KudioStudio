# -*- coding: utf-8 -*-
"""The Enhance panel: ``enhance.*`` on the wire.

Denoising always processes the whole clip -- noise estimation needs the full
signal -- so Denoise and Preview are both jobs. Preview loops the selected
part of the result and commits nothing. A change of parameters while a
preview renders is kept and rendered next, not dropped, and not started on
top of the one running.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from kudiostudio.tools import enhance as tools
from kudiostudio.web.commands import command
from kudiostudio.web.panels.base import WebPanel


class EnhancePanel(WebPanel):
    id = "enhance"
    title = "Enhance"
    icon = "enhance"
    script = "js/panels/enhance.js"

    def __init__(self, studio) -> None:
        super().__init__(studio)
        #: kept between runs on the same directory
        self.model = tools.LearnedModel()
        self._result = "—"
        #: the preview the page wants rendered: (method, values), or None
        self._wanted: Optional[Tuple[str, Dict]] = None

    def info(self) -> Dict:
        return {"busy": self.busy, "result": self._result,
                "available": tools.kudioenhance_available(),
                "note": tools.INSTALLED_NOTE if tools.kudioenhance_available()
                else tools.INSTALL_HINT}

    @command
    def describe(self) -> Dict:
        return {**super().describe(), **tools.schema(), **self.info()}

    @command
    def call_text(self, method: str, values: Dict) -> str:
        return tools.call_text(method, values)

    def _operation(self, method: str, values: Dict, y: np.ndarray):
        if method == tools.LEARNED and values.get("run_dir") != self.model.run_dir:
            self.model.forget()             # another run is another model
        return tools.operation(method, values, y, self.session.sr, self.model)

    # ------------------------------------------------------------- denoise

    @command
    def denoise(self, method: str, values: Dict) -> Dict:
        if not self.require_audio():
            return self.info()
        self.studio.stop_preview()
        # refused before anything is touched: the job already running commits
        # through what is set up here, and would be recorded under this one
        if self.busy:
            self.report("A denoising job is already running.")
            return self.info()
        y = np.asarray(self.session.y, dtype=np.float32)
        try:
            fn, args, kwargs, label = self._operation(method, values, y)
        except RuntimeError as e:
            self.report(str(e))
            return self.info()
        before = y.copy()
        # taken now: the controls can move while the job runs
        code = tools.script(fn, args, kwargs, self.model)
        ran_on = self.session.y

        def finished(out) -> None:
            out = np.asarray(out, dtype=np.float32)
            # wavelet reconstruction can return one extra sample on odd lengths
            n = min(len(out), len(before))
            # the code repeats this run on the audio the history has reached;
            # if that audio was edited meanwhile, no code says what this was
            # made from
            same = self.session.y is ran_on
            self.session.set_audio(out[:n], self.session.sr, undo_label=label,
                                   keep_original=True, code=code if same else None)
            self._result = tools.distance(before[:n], out[:n])
            self.report(f"{label} finished.")
            self.publish(info=self.info())

        def failed(message: str) -> None:
            self._result = "—"
            self.report(f"{label} failed — {message}")
            self.publish(info=self.info())

        self.report(f"Running {label} …")
        self.start_job("Denoising…", fn, *args, on_done=finished, on_failed=failed, **kwargs)
        return self.info()

    # ------------------------------------------------------------- preview

    @command
    def preview(self, method: str, values: Dict) -> Dict:
        """Render the denoise of the whole clip, then loop the selected part.
        Called again with new values, it renders again -- after the render
        already running, never beside it."""
        if not self.session.has_audio:
            self.report("Load an audio file first.")
            return {"on": False}
        self._wanted = (method, dict(values))
        if not self.busy:
            self._render()
        return {"on": True}

    def _render(self) -> None:
        if self._wanted is None or not self.session.has_audio:
            return
        method, values = wanted = self._wanted
        y = np.asarray(self.session.y, dtype=np.float32)
        try:
            fn, args, kwargs, label = self._operation(method, values, y)
        except RuntimeError as e:
            self._wanted = None
            self.report(str(e))
            self.publish(preview=False)
            return
        version = self.studio.version

        def done(out):
            if self._wanted is None:
                return None                 # switched off while it rendered
            if self._wanted != wanted:
                return self._render         # the values moved: once more, after this
            if self.studio.version != version:
                return None                 # the clip it was made from is gone
            out = np.asarray(out, dtype=np.float32)[:len(self.session.y)]
            sr = self.session.sr
            selection = self.session.selection()
            if selection is not None:
                out = out[int(selection[0] * sr):int(selection[1] * sr)]
            if len(out) == 0 or not self.studio.start_preview(self.id, out, sr):
                self._wanted = None
                self.publish(preview=False)
                return None
            self.report("Previewing — nothing has been committed.")
            return None

        def failed(message: str) -> None:
            self._wanted = None
            self.report(f"Preview failed — {message}")
            self.publish(preview=False)

        self.report(f"Rendering a preview of {label} …")
        self.start_job("Rendering a preview…", fn, *args, on_done=done, on_failed=failed,
                       **kwargs)

    def cancel_preview(self) -> None:
        self._wanted = None

    @command
    def stop_preview(self) -> Dict:
        self._wanted = None
        return self.studio.stop_preview()

    def on_session_changed(self) -> None:
        self._wanted = None


__all__ = ["EnhancePanel"]
