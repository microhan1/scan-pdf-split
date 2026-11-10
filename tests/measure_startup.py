"""Times the built exe from launch to the start-up picture and to the window.

    python tests/measure_startup.py dist\\scan-pdf-split.exe [runs=3]

Not a test (pytest does not collect it); run it by hand before a release.
- The internet is cut off with a dead proxy, so the start-up update check
  cannot reach the server; APPDATA and TEMP point at a temporary folder, so
  the user's settings stay untouched and the _MEI* folder a forced kill
  leaves behind is removed with that folder.
- Only windows of this exe's own process tree are counted. A window with a
  title bar (WS_CAPTION) whose title ends in the version is the program; a
  visible top-level window without a title bar is the start-up picture.
- "taskbar" counts windows that would get a taskbar button (visible, no
  owner, not a tool window): it must never reach 2.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from version import APP_VERSION  # noqa: E402

DEAD_PROXY = "http://127.0.0.1:9"
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_EX_TOOLWINDOW = 0x00C00000, 0x00000080
GW_OWNER = 4

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindow.restype = wintypes.HWND
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE


class _Entry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def family(root_pid: int) -> set[int]:
    """The process and its descendants (the one-file exe runs the app in a child)."""
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)
    entry = _Entry()
    entry.dwSize = ctypes.sizeof(_Entry)
    parents = {}
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    found, grew = {root_pid}, True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in found and pid not in found:
                found.add(pid)
                grew = True
    return found


def windows_of(pids: set[int]) -> list[dict]:
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append(dict(hwnd=hwnd, title=buf.value, caption=(style & WS_CAPTION) == WS_CAPTION,
                            taskbar=not (ex & WS_EX_TOOLWINDOW) and not user32.GetWindow(hwnd, GW_OWNER),
                            size=(r.right - r.left, r.bottom - r.top)))
        return True

    user32.EnumWindows(each, 0)
    return out


def measure(exe: str, limit: float = 90.0) -> dict:
    root = tempfile.mkdtemp(prefix="scanpdfsplit_measure_")
    tmp = os.path.join(root, "tmp")
    app_dir = os.path.join(root, "app")
    os.makedirs(tmp)
    os.makedirs(app_dir)
    exe_copy = os.path.join(app_dir, os.path.basename(exe))
    shutil.copy2(exe, exe_copy)                 # settings.json is written beside the exe, so run a copy
    env = dict(os.environ, HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, http_proxy=DEAD_PROXY,
               https_proxy=DEAD_PROXY, NO_PROXY="", APPDATA=os.path.join(root, "appdata"),
               LOCALAPPDATA=os.path.join(root, "appdata"), TEMP=tmp, TMP=tmp)
    res = dict(splash=None, window=None, splash_gone=None, tasks_max=0, tasks_after=None, leftover=None,
               splash_info=None, mei=0)
    t0 = time.perf_counter()
    proc = subprocess.Popen([exe_copy], env=env, cwd=app_dir)
    try:
        pids, last_family, settled = family(proc.pid), 0.0, None
        while time.perf_counter() - t0 < limit:
            now = time.perf_counter() - t0
            if now - last_family > 0.1:
                pids, last_family = family(proc.pid), now
            ws = windows_of(pids)
            plain = [w for w in ws if not w["caption"]]
            main = [w for w in ws if w["caption"] and w["title"].endswith(f"v{APP_VERSION}")]
            res["tasks_max"] = max(res["tasks_max"], sum(w["taskbar"] for w in ws))
            if plain and res["splash"] is None:
                res["splash"], res["splash_info"] = now, plain[0]
            if main and res["window"] is None:
                res["window"] = settled = now
            if res["window"] is not None and not plain and res["splash_gone"] is None:
                res["splash_gone"] = now
            if settled is not None and now - settled > 1.5:     # the picture must be gone by then
                res["tasks_after"] = sum(w["taskbar"] for w in ws)
                res["leftover"] = len(plain)
                break
            time.sleep(0.01)
        res["mei"] = len([d for d in os.listdir(tmp) if d.startswith("_MEI")])
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(1.0)
        shutil.rmtree(root, ignore_errors=True)
    return res


def fmt(v) -> str:
    return f"{v:.2f}" if isinstance(v, float) else "-"


def main() -> None:
    exe = sys.argv[1]
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print(f"{exe}  ({os.path.getsize(exe) / 1024 / 1024:.1f}MB)")
    print("회 | 시작 그림(초) | 창(초) | 시작 그림 사라짐(초) | 작업 표시줄 최대 | 창 뜬 뒤 1.5초: 칸 | 남은 시작 그림 | _MEI")
    rows = []
    for k in range(1, runs + 1):
        r = measure(exe)
        rows.append(r)
        print(f"{k} | {fmt(r['splash'])} | {fmt(r['window'])} | {fmt(r['splash_gone'])} | {r['tasks_max']} | "
              f"{r['tasks_after']} | {r['leftover']} | {r['mei']}", flush=True)
        if r["splash_info"]:
            print("   시작 그림 창:", {k2: r["splash_info"][k2] for k2 in ("title", "taskbar", "size")}, flush=True)
    win = [r["window"] for r in rows if r["window"]]
    spl = [r["splash"] for r in rows if r["splash"]]
    if win:
        print(f"평균: 창 {sum(win) / len(win):.2f}초" + (f" · 시작 그림 {sum(spl) / len(spl):.2f}초" if spl else ""))


if __name__ == "__main__":
    main()
