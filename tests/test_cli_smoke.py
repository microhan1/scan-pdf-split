"""The command line still works: the sample is split, --version and --help answer."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from version import APP_VERSION  # noqa: E402

MAIN = os.path.join(ROOT, "main.py")


def cli(*args, cwd=None):
    p = subprocess.run([sys.executable, "-W", "ignore", MAIN, *args], capture_output=True, cwd=cwd,
                       stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def test_version():
    code, out = cli("--version")
    assert code == 0 and out.strip() == f"scan-pdf-split {APP_VERSION}"


def test_help_lists_version_in_every_language():
    for lang, word in (("ko", "버전"), ("en", "version"), ("zh-CN", "版本"), ("ja", "バージョン")):
        code, out = cli("--help", "--lang", lang)
        assert code == 0 and "--version" in out and word in out


def test_sample_is_split(tmp_path):
    src = tmp_path / "sample.pdf"
    shutil.copy(os.path.join(ROOT, "samples", "sample_spread_scan.pdf"), src)
    before = src.read_bytes()
    code, out = cli(str(src), "--lang", "en")
    assert code == 0, out
    doc = pymupdf.open(tmp_path / "sample_split.pdf")
    assert len(doc) == 18
    doc.close()
    assert src.read_bytes() == before


def test_cli_never_asks_the_update_server(tmp_path, monkeypatch):
    """The check belongs to the window; a command-line run sends nothing."""
    import update_check

    calls = []
    monkeypatch.setattr(update_check, "fetch", lambda *a, **k: calls.append(a))
    import main

    src = tmp_path / "s.pdf"
    shutil.copy(os.path.join(ROOT, "samples", "sample_spread_scan.pdf"), src)
    assert main.main([str(src), "--lang", "en"]) == 0
    assert calls == []
