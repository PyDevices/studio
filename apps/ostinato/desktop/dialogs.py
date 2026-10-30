"""Windows' own Open and Save dialogs, through comdlg32 and ctypes.

No dependency: ``GetOpenFileNameW`` and ``GetSaveFileNameW`` with the
Explorer flags give the same dialogs every Windows app shows. They are modal
to the window that asks and run their own message loop, so the app's window
keeps painting and the engines keep playing while one is open.

    open_file(owner, "Ostinato project", "*.ostinato.json;*.json")  -> path or None
    save_file(owner, "files test.ostinato.json", "Ostinato project", "*.ostinato.json", "json") -> path or None
"""

import ctypes
from ctypes import wintypes

OFN_OVERWRITEPROMPT = 0x00000002
OFN_HIDEREADONLY = 0x00000004
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
MAX_PATH = 32768


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def _filter(description, pattern):
    # "Description\0pattern\0All files\0*.*\0\0"
    return "%s\0%s\0All files\0*.*\0\0" % (description, pattern)


def _run(fn, owner, name, description, pattern, ext, flags, title):
    buf = ctypes.create_unicode_buffer(name or "", MAX_PATH)
    flt = _filter(description, pattern)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = owner or None
    ofn.lpstrFilter = flt
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = MAX_PATH
    ofn.lpstrDefExt = ext
    ofn.lpstrTitle = title
    ofn.Flags = flags | OFN_EXPLORER | OFN_NOCHANGEDIR | OFN_HIDEREADONLY
    if fn(ctypes.byref(ofn)):
        return buf.value
    err = ctypes.windll.comdlg32.CommDlgExtendedError()
    if err:
        raise OSError("the file dialog failed (CommDlgExtendedError %#x)" % err)
    return None          # cancelled


def open_file(owner, description, pattern, title=None):
    return _run(ctypes.windll.comdlg32.GetOpenFileNameW, owner, "", description, pattern, None,
                OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST, title)


def save_file(owner, name, description, pattern, ext, title=None):
    return _run(ctypes.windll.comdlg32.GetSaveFileNameW, owner, name, description, pattern, ext,
                OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST, title)
