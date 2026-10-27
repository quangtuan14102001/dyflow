import ctypes
import time
from ctypes import wintypes

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

def launch():
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    si.lpDesktop = r"WinSta0\Default"
    pi = PROCESS_INFORMATION()
    
    app_path = r"C:\Github\dyflow\dist\VideosCreator\VideosCreator.exe"
    cwd = r"C:\Github\dyflow\dist\VideosCreator"
    
    success = ctypes.windll.kernel32.CreateProcessW(
        app_path,
        None,
        None,
        None,
        False,
        0,
        None,
        cwd,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    
    if not success:
        err = ctypes.GetLastError()
        print(f"CreateProcessW failed with error {err}")
        return False
        
    print(f"Launched VideosCreator.exe on WinSta0\\Default successfully! PID: {pi.dwProcessId}")
    ctypes.windll.kernel32.CloseHandle(pi.hProcess)
    ctypes.windll.kernel32.CloseHandle(pi.hThread)
    return True

if __name__ == "__main__":
    launch()
