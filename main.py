"""Entry point for scan-pdf-split.

    python main.py                      -> GUI
    python main.py input.pdf [...]      -> CLI
    python main.py book.pdf --direction rtl --overlap 1 --position 50 --no-auto
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

import i18n
import split
from i18n import t


def _interactive() -> bool:
    """True only when a person can answer on a real console. On Windows the
    NUL device reports isatty() as True, and getpass reads the console
    directly rather than stdin, so a scheduled job with stdin from NUL used to
    wait forever at a password prompt. GetConsoleMode fails for NUL, files and
    pipes, which is the test that matters."""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError, OSError):
        return False
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import msvcrt

        mode = ctypes.c_uint32()
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return False


def _ask(prompt: str, secret: bool = False) -> str | None:
    """A console answer, or None when nobody can answer (or input ended)."""
    if not _interactive():
        return None
    try:
        return getpass.getpass(prompt) if secret else input(prompt)
    except (EOFError, OSError):
        return None


def _preselect_lang(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def _localize_argparse() -> None:
    """argparse's own labels go through gettext; route them to lang files."""
    table = {
        "usage: ": t("cli_usage"),
        "positional arguments": t("cli_positional"),
        "options": t("cli_options"),
        "show this help message and exit": t("cli_help"),
    }
    argparse._ = lambda s: table.get(s, s)  # type: ignore[attr-defined]


def build_parser() -> argparse.ArgumentParser:
    _localize_argparse()
    p = argparse.ArgumentParser(prog="scan-pdf-split", description=t("cli_desc"))
    p.add_argument("inputs", nargs="*", help=t("cli_inputs"))
    lo, hi = split.POSITION_RANGE
    p.add_argument("--position", type=float, default=split.DEFAULT_POSITION, metavar=f"{lo:g}-{hi:g}",
                   help=t("cli_position"))
    lo, hi = split.OVERLAP_RANGE
    p.add_argument("--overlap", type=float, default=0.0, metavar=f"{lo:g}-{hi:g}", help=t("cli_overlap"))
    p.add_argument("--direction", choices=split.DIRECTIONS, default="ltr", help=t("cli_direction"))
    p.add_argument("--pages", choices=split.SELECTS, default="auto", help=t("cli_pages"))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--auto", dest="auto_detect", action="store_true", default=True, help=t("cli_auto"))
    g.add_argument("--no-auto", dest="auto_detect", action="store_false", help=t("cli_no_auto"))
    p.add_argument("--password", help=t("cli_password"))
    p.add_argument("-y", "--yes", action="store_true", help=t("cli_yes"))
    p.add_argument("--lang", choices=i18n.LANGS, help=t("cli_lang"))
    p.add_argument("--gui", action="store_true", help=t("cli_gui"))
    return p


def run_cli(args: argparse.Namespace) -> int:
    files = split.collect_pdfs(args.inputs)
    missing = [p for p in args.inputs if not os.path.exists(p)]
    for p in missing:
        print(t("err_open_failed", name=p), file=sys.stderr)
    if not files:
        print(t("cli_no_input"), file=sys.stderr)
        return 2
    opts = split.Options(position=args.position, overlap=args.overlap, direction=args.direction,
                         select=args.pages, auto_detect=args.auto_detect).validated()
    failures = len(missing)
    processed = 0
    for idx, path in enumerate(files, 1):
        name = os.path.basename(path)
        password = args.password
        try:
            try:
                pages, scanned = split.inspect_pdf(path, password)
            except split.PasswordRequired:
                if args.password:                    # a password was given and it is wrong
                    print(t("err_wrong_password", name=name))
                    failures += 1
                    continue
                print(f"{name}: {t('err_password')}")
                password = None if args.yes else _ask(t("cli_password_prompt", name=name), secret=True)
                if password is None:
                    print(t("log_skipped", name=name))
                    failures += 1
                    continue
                try:
                    pages, scanned = split.inspect_pdf(path, password)
                except split.PasswordRequired:
                    print(t("err_wrong_password", name=name))
                    failures += 1
                    continue
        except split.EmptyDocument:
            print(t("err_empty_pdf", name=name), file=sys.stderr)
            failures += 1
            continue
        except Exception:
            print(t("err_open_failed", name=name), file=sys.stderr)
            failures += 1
            continue
        if not scanned and not args.yes:
            answer = (_ask(f"{name}: {t('warn_text_pdf')}{t('cli_confirm_hint')}") or "").strip().lower()
            if answer not in ("y", "yes"):
                print(t("log_skipped", name=name))
                continue
        print(t("cli_processing", index=idx, total=len(files), name=name, pages=pages))

        def progress(page: int, total: int) -> None:
            print("\r" + t("cli_progress", page=page, pages=total), end="", flush=True)

        def page_failed(page: int, exc: Exception) -> None:
            print("\n" + t("log_page_failed", name=name, page=page, error=str(exc)))

        try:
            result = split.process_pdf(path, opts, password=password, progress=progress, page_failed=page_failed)
        except KeyboardInterrupt:
            print("\n" + t("status_cancelled"))
            return 130
        except split.EmptyResult:
            print("\n" + t("err_empty_result", name=name), file=sys.stderr)
            failures += 1
            continue
        except Exception as exc:  # one bad file must not end the batch
            print("\n" + t("err_file_failed", name=name, error=exc), file=sys.stderr)
            failures += 1
            continue
        print("\n" + t("log_saved", path=result.output_path))
        print(t("log_summary", pages=result.pages, out_pages=result.out_pages,
                split=result.split_pages, detected=result.detected_pages))
        if result.failed_pages:
            print(t("msg_failed_pages", count=len(result.failed_pages)))
        processed += 1
    print(t("msg_done", count=processed))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    i18n.init(_preselect_lang(argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lang:
        i18n.set_lang(args.lang, persist=False)
    # A windowed exe has no console, so dropping files on it opens the GUI
    # with those files loaded instead of running the CLI into nowhere.
    headless = getattr(sys, "frozen", False) and sys.stdout is None
    if args.gui or headless or not args.inputs:
        import gui

        gui.launch(args.inputs or None)
        return 0
    try:
        return run_cli(args)
    except KeyboardInterrupt:
        print("\n" + t("status_cancelled"))
        return 130


if __name__ == "__main__":
    sys.exit(main())
