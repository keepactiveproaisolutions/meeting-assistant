"""Native Windows screen capture exclusion via SetWindowDisplayAffinity."""
import ctypes
from ctypes import wintypes
import logging
import sys

log = logging.getLogger(__name__)

# Windows Display Affinity Constants
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 Version 2004 (Build 19041) and Windows 11

STATUS_DISABLED = "Capture exclusion disabled"
STATUS_ENABLED = "Capture exclusion enabled — verify before sharing"
STATUS_UNSUPPORTED = "Capture exclusion unsupported on this system"
STATUS_FAILURE = "Could not enable capture exclusion"

EXPLANATION_TEXT = (
    "Requests exclusion from supported screen captures. "
    "Behavior varies by Windows version and capture application. Not guaranteed."
)

WEB_LIMITATION_NOTE = (
    "Important: The synchronized browser view is not protected by this desktop setting."
)

# 64-bit Windows ctypes function signatures
_user32 = None
if sys.platform == "win32":
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

        _user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    except Exception as exc:
        log.warning("Could not initialize user32 display affinity APIs: %s", exc)
        _user32 = None


def is_display_affinity_supported() -> bool:
    """Check if SetWindowDisplayAffinity is available on this Windows platform."""
    if sys.platform != "win32" or _user32 is None:
        return False
    return hasattr(_user32, "SetWindowDisplayAffinity") and hasattr(_user32, "GetWindowDisplayAffinity")


def get_window_display_affinity(hwnd: int) -> tuple[bool, int, int]:
    """Read back current window display affinity.
    Returns: (success: bool, affinity_value: int, error_code: int)
    """
    if not is_display_affinity_supported() or not hwnd:
        return False, WDA_NONE, -1
    dw_affinity = wintypes.DWORD(0)
    ptr = ctypes.pointer(dw_affinity)
    res = _user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ptr)
    if not res:
        err = ctypes.get_last_error()
        return False, WDA_NONE, err
    return True, dw_affinity.value, 0


def set_window_capture_exclusion(hwnd: int, exclude: bool) -> tuple[bool, str, int]:
    """Apply WDA_EXCLUDEFROMCAPTURE or WDA_NONE to the native HWND.
    Verifies application by reading back with GetWindowDisplayAffinity.
    Returns: (success: bool, status_text: str, error_code: int)
    """
    if not is_display_affinity_supported():
        return False, STATUS_UNSUPPORTED, -1

    if not hwnd:
        return False, STATUS_FAILURE, -2

    target_affinity = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
    res = _user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), target_affinity)
    if not res:
        err = ctypes.get_last_error()
        log.error("SetWindowDisplayAffinity failed for hwnd=%s, err=%d", hwnd, err)
        return False, STATUS_FAILURE, err

    # Read back to verify
    ok, verified_val, read_err = get_window_display_affinity(hwnd)
    if not ok or verified_val != target_affinity:
        log.error(
            "Display affinity verification mismatch for hwnd=%s: expected %d, got %d (err=%d)",
            hwnd, target_affinity, verified_val, read_err
        )
        return False, STATUS_FAILURE, read_err

    if exclude:
        return True, STATUS_ENABLED, 0
    return True, STATUS_DISABLED, 0
