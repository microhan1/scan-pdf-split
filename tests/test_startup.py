"""Start-up: the heavy libraries arrive after the window, the exe's start-up
picture goes once the window is drawn (or an error is on its way), and a file
handled before the libraries have arrived gives the same result.

numpy, pymupdf and PIL are already imported in the pytest process by the other
tests, so what depends on a fresh interpreter runs in a subprocess."""
from __future__ import annotations

import builtins
import os
import subprocess
import sys
import textwrap
import threading
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import main  # noqa: E402
import split  # noqa: E402

SAMPLE = os.path.join(ROOT, "samples", "sample_spread_scan.pdf")
DEAD_PROXY = "http://127.0.0.1:9"


def run_py(code: str, tmp_path) -> str:
    """Run ``code`` in a fresh interpreter with the repo on its path, offline,
    settings in a temporary folder; returns stdout."""
    env = dict(os.environ, HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, APPDATA=str(tmp_path),
               LOCALAPPDATA=str(tmp_path), PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ------------------------------------------------------------ lazy imports

def test_importing_split_and_gui_does_not_import_the_heavy_libraries(tmp_path):
    out = run_py("""
        import sys
        import split, gui, main
        print(sorted(m for m in ("numpy", "pymupdf", "PIL", "PIL.Image", "PIL.ImageTk") if m in sys.modules))
        print(type(split.np).__name__, type(split.pymupdf).__name__, type(gui.Image).__name__, bool(split._heavy))
    """, tmp_path)
    assert out.splitlines() == ["[]", "_Deferred _Deferred _Deferred False"]


def test_first_use_of_a_stand_in_imports_the_real_modules(tmp_path):
    out = run_py("""
        import sys, split, gui
        a = split.np.zeros(3)                      # first touch: imports everything
        import numpy, pymupdf
        from PIL import Image
        print(split.np is numpy, split.pymupdf is pymupdf, split.Image is Image)
        print(gui.Image.LANCZOS == Image.LANCZOS, type(a).__name__, sorted(split._heavy))
        print(pymupdf.TOOLS.mupdf_display_errors(), pymupdf.TOOLS.mupdf_display_warnings())
    """, tmp_path)
    assert out.splitlines() == ["True True True", "True ndarray ['Image', 'np', 'pymupdf']", "False False"]


def test_load_heavy_from_many_threads_imports_once(tmp_path):
    out = run_py("""
        import builtins, sys, threading, split
        real_import, first = builtins.__import__, []
        def counting(name, *a, **k):
            if name in ("numpy", "pymupdf") and name not in sys.modules:
                first.append(name)
            return real_import(name, *a, **k)
        builtins.__import__ = counting
        errors, done = [], []
        def go(k):
            try:
                if k % 2:
                    split.load_heavy()
                else:
                    split.pymupdf.open                 # through the stand-in
                done.append(k)
            except Exception as e:
                errors.append(repr(e))
        ts = [threading.Thread(target=go, args=(k,)) for k in range(12)]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
        print(len(done), errors, sorted(first))
    """, tmp_path)
    assert out.strip() == "12 [] ['numpy', 'pymupdf']"


def test_a_file_handled_while_preloading_gives_the_same_result(tmp_path):
    """The window is up, preloading has just started, and the user already
    dropped a file and pressed start: the call waits for the libraries and the
    output is what it would have been a second later."""
    out_a, out_b = tmp_path / "a_split.pdf", tmp_path / "b_split.pdf"
    out = run_py(f"""
        import threading, time, split
        from split import Options
        t = threading.Thread(target=split.preload_heavy, daemon=True)
        t.start()
        loaded_before = bool(split._heavy)
        pages, scanned = split.inspect_pdf({SAMPLE!r})          # raced against the preload
        r1 = split.process_pdf({SAMPLE!r}, Options(), output_path={str(out_a)!r})
        t.join(60)
        r2 = split.process_pdf({SAMPLE!r}, Options(), output_path={str(out_b)!r})
        import pymupdf
        def boxes(p):
            d = pymupdf.open(p)
            try:
                return [tuple(round(v, 2) for v in pg.rect) for pg in d]
            finally:
                d.close()
        same = (r1.pages, r1.out_pages, r1.split_pages, r1.detected_pages, r1.failed_pages) == \\
               (r2.pages, r2.out_pages, r2.split_pages, r2.detected_pages, r2.failed_pages)
        print(loaded_before, t.is_alive(), pages, scanned, r1.out_pages, same, boxes({str(out_a)!r}) == boxes({str(out_b)!r}))
    """, tmp_path)
    assert out.strip() == "False False 10 True 18 True True"


def test_an_import_error_is_raised_to_the_user_and_retried(monkeypatch):
    """preload swallows the error; the next real use raises it; once the
    cause is gone the import succeeds and the stand-ins are replaced."""
    monkeypatch.setattr(split, "_heavy", {})
    real_import = builtins.__import__
    broken = {"on": True}

    def failing(name, *a, **k):
        if broken["on"] and name == "pymupdf":
            raise ImportError("pymupdf is missing (test)")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", failing)
    split.preload_heavy()                                   # quiet
    assert not split._heavy
    with pytest.raises(ImportError, match="missing"):
        split.load_heavy()
    with pytest.raises(ImportError, match="missing"):
        split.open_pdf(SAMPLE)                              # a real use reports it
    broken["on"] = False
    doc = split.open_pdf(SAMPLE)                            # and recovers
    try:
        assert len(doc) == 10
    finally:
        doc.close()
    import numpy
    assert split.np is numpy and sorted(split._heavy) == ["Image", "np", "pymupdf"]


# ------------------------------------------------------------- the splash

class FakeSplash:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.fixture
def splash(monkeypatch):
    fake = FakeSplash()
    mod = types.ModuleType("pyi_splash")
    mod.close = fake.close
    monkeypatch.setitem(sys.modules, "pyi_splash", mod)
    return fake


def test_close_splash_is_quiet_without_pyi_splash(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyi_splash", None)   # import raises ImportError
    main.close_splash()


def test_close_splash_swallows_a_failing_close(monkeypatch):
    mod = types.ModuleType("pyi_splash")

    def boom():
        raise RuntimeError("splash already gone")

    mod.close = boom
    monkeypatch.setitem(sys.modules, "pyi_splash", mod)
    main.close_splash()


def test_cli_closes_the_splash_before_working(splash, monkeypatch, tmp_path):
    order = []
    splash_close = splash.close

    def close():
        splash_close()
        order.append("splash")

    sys.modules["pyi_splash"].close = close
    monkeypatch.setattr(main, "run_cli", lambda args: order.append("cli") or 0)
    monkeypatch.setattr(i18n, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    assert main.main([SAMPLE, "-y"]) == 0
    assert order == ["splash", "cli"] and splash.closed == 1


@pytest.mark.parametrize("argv, code", [(["--version"], 0), (["--help"], 0), (["--position", "abc"], 2)])
def test_help_version_and_a_bad_option_close_the_splash(splash, monkeypatch, tmp_path, argv, code):
    monkeypatch.setattr(i18n, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    with pytest.raises(SystemExit) as e:
        main.main(argv)
    assert e.value.code == code and splash.closed == 1


def test_gui_path_hands_close_splash_to_launch(splash, monkeypatch, tmp_path):
    calls = {}
    fake_gui = types.ModuleType("gui")

    def launch(initial_files=None, shown=None):
        calls["files"], calls["shown"] = initial_files, shown
        shown()

    fake_gui.launch = launch
    monkeypatch.setitem(sys.modules, "gui", fake_gui)
    monkeypatch.setattr(i18n, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    assert main.main(["--gui", SAMPLE]) == 0
    assert calls["files"] == [SAMPLE] and calls["shown"] is main.close_splash and splash.closed == 1


# --------------------------------------------------------------- gui.launch

def close(app) -> None:
    """Close a window that never ran its mainloop: cancel what it scheduled
    (the update check, the first file) so nothing fires on a destroyed root."""
    for after_id in app.root.tk.call("after", "info"):
        app.root.after_cancel(after_id)
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass


@pytest.fixture
def gui_env(monkeypatch, tmp_path):
    monkeypatch.setattr(i18n, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    i18n.init("ko")
    import gui

    preloads = []
    monkeypatch.setattr(split, "preload_heavy", lambda: preloads.append(threading.current_thread().name))
    return gui, preloads


def test_launch_closes_the_splash_after_the_window_is_drawn_then_preloads(gui_env):
    gui, preloads = gui_env
    seen = {}

    def shown():
        seen["app"] = apps[0]
        seen["mapped"] = apps[0].root.winfo_ismapped()
        seen["preloads_at_close"] = list(preloads)

    apps = []
    real_init = gui.App.__init__

    def init(self, *a, **k):
        apps.append(self)
        real_init(self, *a, **k)

    gui.App.__init__ = init
    gui.App.mainloop = lambda self: None                  # return at once instead of running the window
    try:
        gui.launch(None, shown=shown)
        for t in threading.enumerate():
            if t.name == "preload-heavy":
                t.join(10)
    finally:
        gui.App.__init__ = real_init
        del gui.App.mainloop
        for app in apps:
            close(app)
    assert seen["mapped"] == 1 and seen["preloads_at_close"] == []     # drawn first, preload only afterwards
    assert preloads == ["preload-heavy"]


def test_launch_closes_the_splash_when_the_window_cannot_be_made(gui_env, monkeypatch):
    gui, preloads = gui_env
    closed = []

    def broken(self, *a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(gui.App, "__init__", broken)
    with pytest.raises(RuntimeError, match="no display"):
        gui.launch(None, shown=lambda: closed.append(1))
    assert closed == [1] and preloads == []               # the error is not hidden behind the picture


def test_launch_without_shown_still_opens(gui_env):
    gui, preloads = gui_env
    apps = []
    gui.App.mainloop = lambda self: apps.append(self)
    try:
        gui.launch(None)
        for t in threading.enumerate():
            if t.name == "preload-heavy":
                t.join(10)
    finally:
        del gui.App.mainloop
        for app in apps:
            close(app)
    assert len(apps) == 1 and preloads == ["preload-heavy"]


# ----------------------------------------------------------- build inputs

def test_build_and_readmes_name_the_splash_picture():
    png = os.path.join(ROOT, "assets", "splash.png")
    assert os.path.isfile(png) and os.path.getsize(png) < 20_000     # small: it is shown before anything loads
    with open(png, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    with open(os.path.join(ROOT, "build.bat"), encoding="utf-8") as f:
        assert "--splash assets\\splash.png" in f.read()
    for name in ("README.md", "README.en.md", "README.zh-CN.md", "README.ja.md"):
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            assert "--splash assets/splash.png" in f.read(), name
