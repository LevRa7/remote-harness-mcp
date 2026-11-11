"""core/platforms/windows/winapi.py - the Win32 surface this node uses.

Declared once, with ``argtypes``/``restype`` set, because an undeclared pointer
return is silently truncated to 32 bits on 64-bit Python and the bug that
follows ("the handle is invalid" on one machine and not another) is close to
undebuggable.

Everything is guarded: importing this module on Linux or macOS succeeds and
leaves the DLL handles as ``None``, so a test suite can import it anywhere.  On
Windows it uses ``WinDLL`` with ``use_last_error`` so a failure can be reported
with the real ``GetLastError`` code.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"

#: Constants, spelled out so a reader does not have to look them up.
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DRIVE_FIXED = 3
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _load(name: str):
    """Load a system DLL, or return ``None`` off Windows / on a locked-down host."""
    if not IS_WINDOWS:
        return None
    try:
        return ctypes.WinDLL(name, use_last_error=True)
    except Exception:  # pragma: no cover - only on a broken Windows install
        return None


kernel32 = _load("kernel32")
user32 = _load("user32")
gdi32 = _load("gdi32")
psapi = _load("psapi")


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]

    def value(self) -> int:
        """The 64-bit count (100 ns units) the two halves encode."""
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


# ---------------------------------------------------------------------------
# Prototypes
# ---------------------------------------------------------------------------

def _prototype(dll, name: str, restype, argtypes):
    if dll is None:
        return None
    function = getattr(dll, name, None)
    if function is None:  # pragma: no cover - old Windows without the export
        return None
    function.restype = restype
    function.argtypes = argtypes
    return function


if IS_WINDOWS:  # pragma: no branch - declarations are cheap, keep them declarative
    GlobalMemoryStatusEx = _prototype(
        kernel32, "GlobalMemoryStatusEx", wintypes.BOOL, [ctypes.POINTER(MEMORYSTATUSEX)])
    GetSystemTimes = _prototype(
        kernel32, "GetSystemTimes", wintypes.BOOL,
        [ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)])
    GetTickCount64 = _prototype(kernel32, "GetTickCount64", ctypes.c_ulonglong, [])
    GetLogicalDrives = _prototype(kernel32, "GetLogicalDrives", wintypes.DWORD, [])
    GetDriveTypeW = _prototype(kernel32, "GetDriveTypeW", wintypes.UINT, [wintypes.LPCWSTR])
    GetDiskFreeSpaceExW = _prototype(
        kernel32, "GetDiskFreeSpaceExW", wintypes.BOOL,
        [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_ulonglong),
         ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)])
    GetVolumeInformationW = _prototype(
        kernel32, "GetVolumeInformationW", wintypes.BOOL,
        [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
         ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD])
    OpenProcess = _prototype(
        kernel32, "OpenProcess", wintypes.HANDLE,
        [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
    CloseHandle = _prototype(kernel32, "CloseHandle", wintypes.BOOL, [wintypes.HANDLE])
    GetExitCodeProcess = _prototype(
        kernel32, "GetExitCodeProcess", wintypes.BOOL,
        [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)])
    GetProcessTimes = _prototype(
        kernel32, "GetProcessTimes", wintypes.BOOL,
        [wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
         ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)])
    TerminateProcess = _prototype(
        kernel32, "TerminateProcess", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT])
    CreateToolhelp32Snapshot = _prototype(
        kernel32, "CreateToolhelp32Snapshot", wintypes.HANDLE,
        [wintypes.DWORD, wintypes.DWORD])
    Process32FirstW = _prototype(
        kernel32, "Process32FirstW", wintypes.BOOL,
        [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)])
    Process32NextW = _prototype(
        kernel32, "Process32NextW", wintypes.BOOL,
        [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)])
    GlobalAlloc = _prototype(kernel32, "GlobalAlloc", wintypes.HGLOBAL,
                             [wintypes.UINT, ctypes.c_size_t])
    GlobalFree = _prototype(kernel32, "GlobalFree", wintypes.HGLOBAL, [wintypes.HGLOBAL])
    GlobalLock = _prototype(kernel32, "GlobalLock", ctypes.c_void_p, [wintypes.HGLOBAL])
    GlobalUnlock = _prototype(kernel32, "GlobalUnlock", wintypes.BOOL, [wintypes.HGLOBAL])

    GetProcessMemoryInfo = _prototype(
        psapi, "GetProcessMemoryInfo", wintypes.BOOL,
        [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD])

    GetSystemMetrics = _prototype(user32, "GetSystemMetrics", ctypes.c_int, [ctypes.c_int])
    GetDC = _prototype(user32, "GetDC", wintypes.HDC, [wintypes.HWND])
    ReleaseDC = _prototype(user32, "ReleaseDC", ctypes.c_int, [wintypes.HWND, wintypes.HDC])
    SetProcessDPIAware = _prototype(user32, "SetProcessDPIAware", wintypes.BOOL, [])
    SetProcessDpiAwarenessContext = _prototype(
        user32, "SetProcessDpiAwarenessContext", wintypes.BOOL, [ctypes.c_void_p])
    OpenClipboard = _prototype(user32, "OpenClipboard", wintypes.BOOL, [wintypes.HWND])
    CloseClipboard = _prototype(user32, "CloseClipboard", wintypes.BOOL, [])
    EmptyClipboard = _prototype(user32, "EmptyClipboard", wintypes.BOOL, [])
    GetClipboardData = _prototype(user32, "GetClipboardData", wintypes.HANDLE, [wintypes.UINT])
    SetClipboardData = _prototype(user32, "SetClipboardData", wintypes.HANDLE,
                                  [wintypes.UINT, wintypes.HANDLE])
    IsClipboardFormatAvailable = _prototype(
        user32, "IsClipboardFormatAvailable", wintypes.BOOL, [wintypes.UINT])

    CreateCompatibleDC = _prototype(gdi32, "CreateCompatibleDC", wintypes.HDC, [wintypes.HDC])
    CreateCompatibleBitmap = _prototype(
        gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, [wintypes.HDC, ctypes.c_int, ctypes.c_int])
    CreateDIBSection = _prototype(
        gdi32, "CreateDIBSection", wintypes.HBITMAP,
        [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
         ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD])
    SelectObject = _prototype(gdi32, "SelectObject", wintypes.HGDIOBJ,
                              [wintypes.HDC, wintypes.HGDIOBJ])
    BitBlt = _prototype(
        gdi32, "BitBlt", wintypes.BOOL,
        [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD])
    DeleteObject = _prototype(gdi32, "DeleteObject", wintypes.BOOL, [wintypes.HGDIOBJ])
    DeleteDC = _prototype(gdi32, "DeleteDC", wintypes.BOOL, [wintypes.HDC])
    GetDeviceCaps = _prototype(gdi32, "GetDeviceCaps", ctypes.c_int, [wintypes.HDC, ctypes.c_int])


def last_error() -> int:
    """``GetLastError`` as an integer (0 off Windows)."""
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.get_last_error())
    except Exception:  # pragma: no cover
        return 0
