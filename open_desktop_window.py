import ctypes
from ctypes import wintypes
import sys
import os
import time

class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_char_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]

def main():
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    si.lpDesktop = r"WinSta0\Default"
    pi = PROCESS_INFORMATION()

    py = sys.executable
    script = os.path.abspath("desktop.py")
    cmd = f'"{py}" "{script}"'
    cwd = r"C:\Github\dyflow"

    flags = 0
    res = ctypes.windll.kernel32.CreateProcessW(
        None, cmd, None, None, False, flags, None, cwd, ctypes.byref(si), ctypes.byref(pi)
    )
    if not res:
        err = ctypes.GetLastError()
        print(f"CreateProcess failed: {err}")
        return 1

    print(f"Launched desktop app PID: {pi.dwProcessId} on WinSta0\\Default", flush=True)

    # Monitor for window creation
    time.sleep(3)
    u = ctypes.windll.user32
    h_default = u.OpenDesktopW("Default", 0, False, 0x01FF)
    if h_default:
        u.SetThreadDesktop(h_default)
        found = []
        def cb(hwnd, extra):
            pid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, title, 512)
            cls = ctypes.create_unicode_buffer(512)
            u.GetClassNameW(hwnd, cls, 512)
            if pid.value == pi.dwProcessId or "VideosCreator" in title.value or "VideosCreator" in cls.value:
                found.append((pid.value, hwnd, bool(u.IsWindowVisible(hwnd)), cls.value, title.value))
            return True
        CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(cb)
        u.EnumWindows(CB, 0)
        print(f"Windows on Default desktop: {found}", flush=True)

    # Keep process open as daemon monitor until user closes the window
    ctypes.windll.kernel32.WaitForSingleObject(pi.hProcess, 0xFFFFFFFF)
    print("VideosCreator desktop app exited.", flush=True)
    ctypes.windll.kernel32.CloseHandle(pi.hProcess)
    ctypes.windll.kernel32.CloseHandle(pi.hThread)
    return 0

if __name__ == "__main__":
    main()
