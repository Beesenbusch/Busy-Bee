"""Give a window its own taskbar identity so Windows shows our icon on its button.

Without this, the button is grouped under the Python interpreter and shows its icon.
The process-wide SetCurrentProcessExplicitAppUserModelID is not enough: it is ignored
when Python runs with package identity (Microsoft Store Python), so the ID is set on
the window itself through its shell property store instead.
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

_VT_LPWSTR = 31
_IID_IPROPERTYSTORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_FMTID_APPUSERMODEL = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
_PID_APPUSERMODEL_ID = 5


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        u = uuid.UUID(text)
        return cls(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("reserved1", ctypes.c_ushort),
        ("reserved2", ctypes.c_ushort),
        ("reserved3", ctypes.c_ushort),
        ("pwszVal", ctypes.c_wchar_p),
        ("padding", ctypes.c_void_p),
    ]


# IPropertyStore vtable slots (after IUnknown's QueryInterface, AddRef, Release).
_RELEASE, _SET_VALUE, _COMMIT = 2, 6, 7


def set_window_app_id(hwnd: int, app_id: str) -> None:
    """Raises OSError if Windows refuses."""
    ctypes.windll.ole32.CoInitialize(None)
    store = ctypes.c_void_p()
    iid = _GUID.parse(_IID_IPROPERTYSTORE)
    hr = ctypes.windll.shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(iid), ctypes.byref(store))
    if hr != 0 or not store:
        raise OSError(f"SHGetPropertyStoreForWindow failed: 0x{hr & 0xFFFFFFFF:08X}")

    vtable = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(vtable[_SET_VALUE])
    commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtable[_COMMIT])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[_RELEASE])
    try:
        key = _PROPERTYKEY(_GUID.parse(_FMTID_APPUSERMODEL), _PID_APPUSERMODEL_ID)
        value = _PROPVARIANT(_VT_LPWSTR, 0, 0, 0, app_id, None)
        set_value(store, ctypes.byref(key), ctypes.byref(value))  # HRESULT restype raises OSError on failure
        commit(store)
    finally:
        release(store)
