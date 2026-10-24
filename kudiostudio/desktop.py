# -*- coding: utf-8 -*-
"""What the operating system needs to know about KudioStudio.

Plain ctypes; nothing here imports a GUI toolkit.
"""
from __future__ import annotations

import sys

#: Windows' name for this application, in its Company.Product form. No
#: version in it: the taskbar groups windows by this string, and one that
#: changed with each release would split them apart.
APP_ID = "Kudio.KudioStudio"


def set_windows_app_id() -> bool:
    """Tell Windows this process is KudioStudio, not python.exe.

    Without it the taskbar files the window under the interpreter that runs
    it and shows that program's icon, whatever icon the window sets. Has to
    happen before the first window is shown. Returns False elsewhere, and if
    the call fails, which costs nothing but the icon.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        return False
    return result == 0  # S_OK


__all__ = ["APP_ID", "set_windows_app_id"]
