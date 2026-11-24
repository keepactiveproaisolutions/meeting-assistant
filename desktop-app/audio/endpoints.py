"""Windows Core Audio (MMDevice API) via ctypes: stable endpoint IDs for playback devices.

PortAudio / PyAudioWPatch exposes only an index (which shifts when devices connect/disconnect) and a friendly name.
Windows identifies each endpoint by a stable ID string such as "{0.0.0.00000000}.{9c1f...}", which survives
reconnects and reboots. This module reads those IDs, friendly names, states and the current default playback
endpoint. Render (playback) endpoints only — capture/microphone endpoints are never enumerated here.
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_uint, c_ulong, c_void_p, c_wchar_p
from dataclasses import dataclass

E_RENDER, E_CONSOLE = 0, 0
DEVICE_STATE_ACTIVE = 0x1
DEVICE_STATE_ALL = 0xF
STGM_READ = 0
VT_LPWSTR = 31
RPC_E_CHANGED_MODE = -2147417850


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, s: str) -> "GUID":
        g = cls()
        ctypes.memmove(byref(g), uuid.UUID(s).bytes_le, 16)
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("val", c_void_p), ("pad", c_void_p)]


CLSID_MMDeviceEnumerator = GUID.from_str("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID.from_str("A95664D2-9614-4F35-A746-DE8DB63617E6")
PKEY_Device_FriendlyName = PROPERTYKEY(GUID.from_str("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)
CLSCTX_ALL = 0x17


@dataclass(frozen=True)
class Endpoint:
    id: str            # stable Windows endpoint ID
    name: str          # friendly name, e.g. "Headphones (WH-1000XM4)"
    active: bool


class CoreAudioError(RuntimeError):
    pass


def _vcall(obj: c_void_p, index: int, restype, *args_types):
    """Return a callable for vtable slot `index` of COM object `obj`."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *args_types)
    return lambda *args: proto(vtbl[index])(obj, *args)


def _release(obj) -> None:
    if obj:
        _vcall(obj, 2, c_ulong)()


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise CoreAudioError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


def _co_init() -> None:
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # MTA; an STA thread (Qt main) returns RPC_E_CHANGED_MODE
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        _check(hr, "CoInitializeEx")


def _enumerator() -> c_void_p:
    _co_init()
    enum = c_void_p()
    hr = ctypes.windll.ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                              byref(IID_IMMDeviceEnumerator), byref(enum))
    _check(hr, "CoCreateInstance(MMDeviceEnumerator)")
    return enum


def _device_id(dev: c_void_p) -> str:
    p = c_wchar_p()
    _check(_vcall(dev, 5, ctypes.c_long, POINTER(c_wchar_p))(byref(p)), "IMMDevice.GetId")
    try:
        return p.value or ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(p)


def _device_state(dev: c_void_p) -> int:
    st = c_ulong()
    _check(_vcall(dev, 6, ctypes.c_long, POINTER(c_ulong))(byref(st)), "IMMDevice.GetState")
    return st.value


def _device_name(dev: c_void_p) -> str:
    store = c_void_p()
    _check(_vcall(dev, 4, ctypes.c_long, c_ulong, POINTER(c_void_p))(STGM_READ, byref(store)), "OpenPropertyStore")
    try:
        pv = PROPVARIANT()
        _check(_vcall(store, 5, ctypes.c_long, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(
            byref(PKEY_Device_FriendlyName), byref(pv)), "IPropertyStore.GetValue")
        try:
            return ctypes.wstring_at(pv.val) if pv.vt == VT_LPWSTR and pv.val else ""
        finally:
            ctypes.windll.ole32.PropVariantClear(byref(pv))
    finally:
        _release(store)


def list_render_endpoints(include_inactive: bool = False) -> list[Endpoint]:
    """All playback endpoints with their stable IDs (active only by default)."""
    if sys.platform != "win32":
        return []
    enum = _enumerator()
    coll = c_void_p()
    try:
        mask = DEVICE_STATE_ALL if include_inactive else DEVICE_STATE_ACTIVE
        _check(_vcall(enum, 3, ctypes.c_long, c_uint, c_ulong, POINTER(c_void_p))(E_RENDER, mask, byref(coll)),
               "EnumAudioEndpoints")
        count = c_uint()
        _check(_vcall(coll, 3, ctypes.c_long, POINTER(c_uint))(byref(count)), "GetCount")
        out: list[Endpoint] = []
        for i in range(count.value):
            dev = c_void_p()
            _check(_vcall(coll, 4, ctypes.c_long, c_uint, POINTER(c_void_p))(i, byref(dev)), "Item")
            try:
                try:
                    name = _device_name(dev)
                except CoreAudioError:  # unplugged/removed devices often have no readable properties
                    name = ""
                out.append(Endpoint(_device_id(dev), name, _device_state(dev) == DEVICE_STATE_ACTIVE))
            finally:
                _release(dev)
        return out
    finally:
        _release(coll)
        _release(enum)


def default_render_endpoint() -> Endpoint | None:
    """The current Windows default playback device (what most apps, including Teams by default, play to)."""
    if sys.platform != "win32":
        return None
    enum = _enumerator()
    dev = c_void_p()
    try:
        hr = _vcall(enum, 4, ctypes.c_long, c_uint, c_uint, POINTER(c_void_p))(E_RENDER, E_CONSOLE, byref(dev))
        if hr < 0:  # no playback device at all
            return None
        return Endpoint(_device_id(dev), _device_name(dev), _device_state(dev) == DEVICE_STATE_ACTIVE)
    finally:
        _release(dev)
        _release(enum)


def endpoint_by_id(endpoint_id: str) -> Endpoint | None:
    for ep in list_render_endpoints(include_inactive=True):
        if ep.id == endpoint_id:
            return ep
    return None
