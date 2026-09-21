"""tkinter GUI for scan-pdf-split."""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import i18n
import split
from i18n import t
from split import Options

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_DND = False

PREVIEW_W, PREVIEW_H = 640, 460
LINE_COLOR = "#e5484d"
POSITION_SLIDER = (30.0, 70.0)       # the slider; the CLI accepts split.POSITION_RANGE
DEFAULT_PAGE_SECONDS = 0.02          # used for the large-file estimate before any preview


def _number(value, default: float) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok else default


def _saved_options(settings: dict) -> Options:
    """Options from settings.json, trusting nothing about its types. The file
    sits beside the exe where anyone can edit it; one wrong value must not
    stop the window from opening."""
    raw = settings.get("options")
    raw = raw if isinstance(raw, dict) else {}
    d = Options()
    auto = raw.get("auto_detect")
    direction, select = raw.get("direction"), raw.get("select")
    opts = Options(position=_number(raw.get("position"), d.position),
                   overlap=_number(raw.get("overlap"), d.overlap),
                   direction=direction if isinstance(direction, str) else d.direction,
                   select=select if isinstance(select, str) else d.select,
                   auto_detect=auto if isinstance(auto, bool) else d.auto_detect).validated()
    # the slider cannot show a line outside its own range
    opts.position = min(POSITION_SLIDER[1], max(POSITION_SLIDER[0], opts.position))
    return opts


class App:
    def __init__(self, initial_files: list[str] | None = None) -> None:
        self.root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
        self.root.geometry("1120x800")
        self.root.minsize(960, 680)

        self.files: list[str] = []
        self.passwords: dict[str, str] = {}
        self.page_counts: dict[str, int] = {}
        self.overrides: dict[str, dict[int, dict]] = {}
        self.outputs: list[str] = []
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.preview_job: str | None = None
        self.preview_gen = 0
        self.preview_file: str | None = None
        self.preview_index = 0
        self._preview: tuple[Image.Image, split.PagePlan, list[int | None]] | None = None
        self._orig_geom: tuple[int, int, int, int] | None = None
        self._photos: list[ImageTk.PhotoImage] = []
        self._texts: list[tuple[tk.Misc, str, str]] = []
        self._page_seconds = 0.0
        self._status_key = ""
        self._status_kwargs: dict = {}
        self._count_kwargs: dict | None = None
        self._line_key = ""
        self._line_kwargs: dict = {}
        self._dragging = False
        self._closing = False
        self._running = False

        saved = _saved_options(i18n.load_settings())
        self.var_position = tk.DoubleVar(value=saved.position)
        self.var_overlap = tk.DoubleVar(value=saved.overlap)
        self.var_direction = tk.StringVar(value=saved.direction)
        self.var_select = tk.StringVar(value=saved.select)
        self.var_auto = tk.BooleanVar(value=saved.auto_detect)
        self.var_page_mode = tk.StringVar(value="")
        self.var_lang = tk.StringVar(value=i18n.LANG_NAMES[i18n.current_lang()])

        self._build()
        self._apply_texts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if initial_files:
            self.root.after(100, lambda: self.add_paths(initial_files))

    # ------------------------------------------------------------ building
    def _reg(self, widget: tk.Misc, key: str, attr: str = "text") -> tk.Misc:
        self._texts.append((widget, key, attr))
        return widget

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # ---- header
        head = ttk.Frame(root, padding=(12, 10, 12, 4))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self._reg(ttk.Label(head, font=("", 15, "bold")), "app_title").grid(row=0, column=0, sticky="w")
        self._reg(ttk.Label(head), "lbl_language").grid(row=0, column=1, padx=(0, 6))
        self.cmb_lang = ttk.Combobox(
            head, state="readonly", width=10, textvariable=self.var_lang,
            values=[i18n.LANG_NAMES[c] for c in i18n.LANGS],
        )
        self.cmb_lang.grid(row=0, column=2)
        self.cmb_lang.bind("<<ComboboxSelected>>", self._on_lang)

        # ---- files
        files = ttk.LabelFrame(root, padding=8)
        self._reg(files, "lbl_files")
        files.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        files.columnconfigure(0, weight=1)
        self.lbl_drop = ttk.Label(files, anchor="center", relief="groove", padding=6, foreground="#555")
        self._reg(self.lbl_drop, "drop_hint")
        self.lbl_drop.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.lst_files = tk.Listbox(files, height=4, activestyle="none", exportselection=False)
        self.lst_files.grid(row=1, column=0, sticky="nsew")
        self.lst_files.bind("<<ListboxSelect>>", self._on_file_select)
        btns = ttk.Frame(files)
        btns.grid(row=1, column=1, sticky="ns", padx=(8, 0))
        self.btn_add = self._reg(ttk.Button(btns, command=self._add_files_dialog), "btn_add_files")
        self.btn_add.pack(fill="x")
        self.btn_add_dir = self._reg(ttk.Button(btns, command=self._add_folder_dialog), "btn_add_folder")
        self.btn_add_dir.pack(fill="x", pady=4)
        self.btn_clear = self._reg(ttk.Button(btns, command=self.clear_files), "btn_clear")
        self.btn_clear.pack(fill="x")
        if _HAS_DND:
            for w in (root, self.lbl_drop, self.lst_files):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        # ---- middle: options | preview
        mid = ttk.Frame(root)
        mid.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)

        opts = ttk.Frame(mid, width=250)
        opts.grid(row=0, column=0, sticky="nsw", padx=(0, 12))

        f_dir = self._reg(ttk.LabelFrame(opts, padding=6), "opt_direction")
        f_dir.pack(fill="x", pady=(0, 6))
        for i, d in enumerate(split.DIRECTIONS):
            rb = ttk.Radiobutton(f_dir, variable=self.var_direction, value=d, command=self._on_option)
            self._reg(rb, f"dir_{d}")
            rb.grid(row=i, column=0, sticky="w")

        f_sel = self._reg(ttk.LabelFrame(opts, padding=6), "opt_pages")
        f_sel.pack(fill="x", pady=(0, 6))
        for i, s in enumerate(split.SELECTS):
            rb = ttk.Radiobutton(f_sel, variable=self.var_select, value=s, command=self._on_option)
            self._reg(rb, f"select_{s}")
            rb.grid(row=i, column=0, sticky="w")

        f_pos = self._reg(ttk.LabelFrame(opts, padding=6), "opt_position")
        f_pos.pack(fill="x", pady=(0, 6))
        f_pos.columnconfigure(0, weight=1)
        self._reg(ttk.Checkbutton(f_pos, variable=self.var_auto, command=self._on_option),
                  "opt_auto").grid(row=0, column=0, columnspan=2, sticky="w")
        self.scl_position = ttk.Scale(f_pos, from_=POSITION_SLIDER[0], to=POSITION_SLIDER[1], orient="horizontal",
                                      length=200, variable=self.var_position, command=self._on_position_slide)
        self.scl_position.grid(row=1, column=0, sticky="ew")
        self.lbl_position = ttk.Label(f_pos, width=6, anchor="e")
        self.lbl_position.grid(row=1, column=1, sticky="e")
        self.lbl_position_hint = ttk.Label(f_pos, foreground="#666", wraplength=230, justify="left")
        self._reg(self.lbl_position_hint, "lbl_position_hint")
        self.lbl_position_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        f_ov = self._reg(ttk.LabelFrame(opts, padding=6), "opt_overlap")
        f_ov.pack(fill="x", pady=(0, 6))
        f_ov.columnconfigure(0, weight=1)
        self.scl_overlap = ttk.Scale(f_ov, from_=split.OVERLAP_RANGE[0], to=split.OVERLAP_RANGE[1],
                                     orient="horizontal", length=200, variable=self.var_overlap,
                                     command=self._on_overlap_slide)
        self.scl_overlap.grid(row=0, column=0, sticky="ew")
        self.lbl_overlap = ttk.Label(f_ov, width=6, anchor="e")
        self.lbl_overlap.grid(row=0, column=1, sticky="e")
        self.lbl_overlap_hint = ttk.Label(f_ov, foreground="#666", wraplength=230, justify="left")
        self._reg(self.lbl_overlap_hint, "lbl_overlap_hint")
        self.lbl_overlap_hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.lbl_count = ttk.Label(opts, font=("", 10, "bold"))
        self.lbl_count.pack(anchor="w", pady=(4, 0))

        # preview
        prev = ttk.Frame(mid)
        prev.grid(row=0, column=1, sticky="nsew")
        prev.columnconfigure(0, weight=3)
        prev.columnconfigure(1, weight=2)
        prev.rowconfigure(2, weight=1)

        nav = ttk.Frame(prev)
        nav.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.btn_prev = self._reg(ttk.Button(nav, width=10, command=lambda: self._go_page(-1)), "btn_prev")
        self.btn_prev.pack(side="left")
        self.lbl_page = ttk.Label(nav, width=14, anchor="center")
        self.lbl_page.pack(side="left", padx=4)
        self.btn_next = self._reg(ttk.Button(nav, width=10, command=lambda: self._go_page(1)), "btn_next")
        self.btn_next.pack(side="left")
        self.btn_reset_page = self._reg(ttk.Button(nav, command=self._reset_page), "btn_reset_page")
        self.btn_reset_page.pack(side="right")
        self.rb_modes = []
        for m in reversed(split.PAGE_MODES):
            rb = ttk.Radiobutton(nav, variable=self.var_page_mode, value=m, command=self._on_page_mode)
            self._reg(rb, f"mode_{m}")
            rb.pack(side="right", padx=(0, 10))
            self.rb_modes.append(rb)

        self._reg(ttk.Label(prev, anchor="center"), "preview_original").grid(row=1, column=0, sticky="ew")
        self._reg(ttk.Label(prev, anchor="center"), "preview_result").grid(row=1, column=1, sticky="ew")
        self.cv_orig = tk.Canvas(prev, bg="#d9d9d9", highlightthickness=1, highlightbackground="#999",
                                 cursor="sb_h_double_arrow")
        self.cv_res = tk.Canvas(prev, bg="#d9d9d9", highlightthickness=1, highlightbackground="#999")
        self.cv_orig.grid(row=2, column=0, sticky="nsew", padx=(0, 4))
        self.cv_res.grid(row=2, column=1, sticky="nsew", padx=(4, 0))
        self.lbl_line = ttk.Label(prev, anchor="w", foreground="#555")
        self.lbl_line.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.lbl_preview_msg = ttk.Label(prev, anchor="e", foreground="#555")
        self.lbl_preview_msg.grid(row=3, column=1, sticky="ew", pady=(4, 0))
        self.cv_orig.bind("<Configure>", lambda e: self._redraw_preview())
        self.cv_res.bind("<Configure>", lambda e: self._redraw_preview())
        self.cv_orig.bind("<ButtonPress-1>", self._on_line_press)
        self.cv_orig.bind("<B1-Motion>", self._on_line_drag)
        self.cv_orig.bind("<ButtonRelease-1>", self._on_line_release)
        self.cv_orig.bind("<Double-Button-1>", self._on_line_reset)

        # ---- log
        logf = self._reg(ttk.LabelFrame(root, padding=4), "lbl_log")
        logf.grid(row=3, column=0, sticky="ew", padx=12, pady=4)
        logf.columnconfigure(0, weight=1)
        self.txt_log = tk.Text(logf, height=4, state="disabled", wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(logf, command=self.txt_log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=sb.set)

        # ---- bottom
        bot = ttk.Frame(root, padding=(12, 4, 12, 10))
        bot.grid(row=4, column=0, sticky="ew")
        bot.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bot, mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        self.lbl_status = ttk.Label(bot)
        self.lbl_status.grid(row=1, column=0, sticky="w")
        self.btn_open = self._reg(ttk.Button(bot, command=self.open_result, state="disabled"), "btn_open_result")
        self.btn_open.grid(row=1, column=1, padx=4)
        self.btn_cancel = self._reg(ttk.Button(bot, command=self.cancel, state="disabled"), "btn_cancel")
        self.btn_cancel.grid(row=1, column=2, padx=4)
        self.btn_run = self._reg(ttk.Button(bot, command=self.run), "btn_run")
        self.btn_run.grid(row=1, column=3, padx=(4, 0))

        self._update_value_labels()
        self._update_nav()
        self._set_status("status_ready")

    def _apply_texts(self) -> None:
        self.root.title(t("app_title"))
        for widget, key, attr in self._texts:
            try:
                widget.configure(**{attr: t(key)})
            except tk.TclError:
                pass
        if self._preview is None:
            self.lbl_preview_msg.configure(text=t("preview_empty") if not self.files else "")
        self._update_nav()
        self._update_count()
        if self._line_key:
            self.lbl_line.configure(text=t(self._line_key, **self._line_kwargs))
        if self._status_key:
            self._set_status(self._status_key, **self._status_kwargs)
        self._redraw_preview()

    # ------------------------------------------------------------ helpers
    def _set_status(self, key: str, **kwargs) -> None:
        self._status_key, self._status_kwargs = key, kwargs
        self.lbl_status.configure(text=t(key, **kwargs))

    def _set_line(self, key: str, **kwargs) -> None:
        self._line_key, self._line_kwargs = key, kwargs
        self.lbl_line.configure(text=t(key, **kwargs) if key else "")

    def log(self, key: str, **kwargs) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", t(key, **kwargs) + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def options(self) -> Options:
        return Options(position=round(float(self.var_position.get()), 1),
                       overlap=round(float(self.var_overlap.get()), 1),
                       direction=self.var_direction.get(), select=self.var_select.get(),
                       auto_detect=bool(self.var_auto.get())).validated()

    def _save_options(self) -> None:
        settings = i18n.load_settings()
        settings["options"] = dataclasses.asdict(self.options())
        i18n.save_settings(settings)

    def _on_lang(self, _event=None) -> None:
        index = self.cmb_lang.current()
        if index >= 0:
            i18n.set_lang(i18n.LANGS[index])
        self._apply_texts()

    def _update_value_labels(self) -> None:
        self.lbl_position.configure(text=f"{self.var_position.get():.1f}%")
        ov = float(self.var_overlap.get())
        self.lbl_overlap.configure(text=f"{ov:+.1f}%" if abs(ov) >= 0.05 else "0.0%")

    def _on_position_slide(self, _v=None) -> None:
        self.var_position.set(round(float(self.scl_position.get()) * 2) / 2)    # 0.5% steps
        self._update_value_labels()
        self._schedule_preview()

    def _on_overlap_slide(self, _v=None) -> None:
        self.var_overlap.set(round(float(self.scl_overlap.get()) * 10) / 10)   # 0.1% steps
        self._update_value_labels()
        self._redraw_with_local_plan()

    def _on_option(self) -> None:
        self._schedule_preview()

    # ------------------------------------------------------------ files
    def _on_drop(self, event) -> None:
        if self._running:
            return
        paths = self.root.tk.splitlist(event.data)
        self.add_paths(list(paths))

    def _add_files_dialog(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[(t("file_dialog_pdf"), "*.pdf")])
        if paths:
            self.add_paths(list(paths))

    def _add_folder_dialog(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        for p in paths:
            if not os.path.exists(p):
                self.log("err_open_failed", name=p)
        found = split.collect_pdfs(paths)
        if not found:
            self.log("err_no_pdf_found")
            return
        first_new = None
        for path in found:
            if path in self.files:
                continue
            name = os.path.basename(path)
            password = None
            try:
                try:
                    pages, scanned = split.inspect_pdf(path)
                except split.PasswordRequired:
                    password = simpledialog.askstring(t("err_password"), t("dlg_password_prompt", name=name),
                                                      show="*", parent=self.root)
                    if password is None:
                        self.log("log_skipped", name=name)
                        continue
                    try:
                        pages, scanned = split.inspect_pdf(path, password)
                    except split.PasswordRequired:
                        messagebox.showerror(t("dlg_error"), t("err_wrong_password", name=name), parent=self.root)
                        self.log("log_skipped", name=name)
                        continue
            except split.EmptyDocument:
                messagebox.showerror(t("dlg_error"), t("err_empty_pdf", name=name), parent=self.root)
                self.log("err_empty_pdf", name=name)
                continue
            except Exception:
                messagebox.showerror(t("dlg_error"), t("err_open_failed", name=name), parent=self.root)
                self.log("log_skipped", name=name)
                continue
            if not scanned and not messagebox.askyesno(
                    t("dlg_confirm"), f"{name}\n{t('warn_text_pdf')}", parent=self.root):
                self.log("log_skipped", name=name)
                continue
            self.files.append(path)
            if password:
                self.passwords[path] = password
            self.page_counts[path] = pages
            self.lst_files.insert("end", f"{name}  ({t('unit_pages', pages=pages)})")
            self.log("log_added", name=name, pages=pages)
            if first_new is None:
                first_new = len(self.files) - 1
        if self.preview_file is None and self.files:
            self._select_file(first_new if first_new is not None else 0)

    def clear_files(self) -> None:
        if self._running:
            return
        self.files.clear()
        self.passwords.clear()
        self.page_counts.clear()
        self.overrides.clear()
        self.lst_files.delete(0, "end")
        self.preview_file = None
        self.preview_index = 0
        self.preview_gen += 1
        self._preview = None
        self._orig_geom = None
        self._count_kwargs = None
        self._photos.clear()
        self.cv_orig.delete("all")
        self.cv_res.delete("all")
        self._set_line("")
        self.lbl_preview_msg.configure(text=t("preview_empty"))
        self._update_nav()
        self._update_count()

    def _on_file_select(self, _event=None) -> None:
        sel = self.lst_files.curselection()
        if sel:
            self._select_file(sel[0], from_list=True)

    def _select_file(self, index: int, from_list: bool = False) -> None:
        if not (0 <= index < len(self.files)):
            return
        if not from_list:
            self.lst_files.selection_clear(0, "end")
            self.lst_files.selection_set(index)
            self.lst_files.see(index)
        path = self.files[index]
        if path != self.preview_file:
            self.preview_file = path
            self.preview_index = self._first_spread_guess(path)
        self._schedule_preview(delay=0)

    def _first_spread_guess(self, path: str) -> int:
        """Open on the first page likely to be a spread, not on a cover."""
        return 1 if self.page_counts.get(path, 0) > 1 else 0

    # ------------------------------------------------------------ page navigation
    def _page_override(self, create: bool = False) -> dict | None:
        if self.preview_file is None:
            return None
        per_file = self.overrides.setdefault(self.preview_file, {}) if create else self.overrides.get(self.preview_file, {})
        if create:
            return per_file.setdefault(self.preview_index, {})
        return per_file.get(self.preview_index)

    def _drop_empty_override(self) -> None:
        per_file = self.overrides.get(self.preview_file or "", {})
        if per_file.get(self.preview_index) == {}:
            del per_file[self.preview_index]

    def _go_page(self, step: int) -> None:
        if self.preview_file is None:
            return
        total = self.page_counts.get(self.preview_file, 1)
        new = max(0, min(total - 1, self.preview_index + step))
        if new != self.preview_index:
            self.preview_index = new
            self._schedule_preview(delay=0)

    def _update_nav(self) -> None:
        has = self.preview_file is not None and not self._running
        total = self.page_counts.get(self.preview_file or "", 0)
        self.lbl_page.configure(text=t("lbl_page_of", page=self.preview_index + 1, pages=total) if total else "")
        self.btn_prev.configure(state="normal" if has and self.preview_index > 0 else "disabled")
        self.btn_next.configure(state="normal" if has and self.preview_index < total - 1 else "disabled")
        for rb in self.rb_modes:
            rb.configure(state="normal" if has else "disabled")
        ov = self._page_override()
        self.btn_reset_page.configure(state="normal" if has and ov else "disabled")

    def _on_page_mode(self) -> None:
        mode = self.var_page_mode.get()
        if mode not in split.PAGE_MODES or self.preview_file is None:
            return
        self._page_override(create=True)["mode"] = mode
        self._schedule_preview(delay=0)

    def _reset_page(self) -> None:
        per_file = self.overrides.get(self.preview_file or "", {})
        per_file.pop(self.preview_index, None)
        self._schedule_preview(delay=0)

    # ------------------------------------------------------------ split line dragging
    def _x_to_position(self, x: int) -> float | None:
        if not self._orig_geom:
            return None
        ox, _oy, w, _h = self._orig_geom
        frac = (x - ox) / max(w, 1)
        lo, hi = split.POSITION_RANGE
        return round(min(hi, max(lo, frac * 100)), 1)

    def _on_line_press(self, event) -> None:
        if self._running or not self._preview or self._preview[1].mode != "split":
            return
        self._dragging = True
        self._on_line_drag(event)

    def _on_line_drag(self, event) -> None:
        if not self._dragging or not self._preview:
            return
        pos = self._x_to_position(event.x)
        if pos is None:
            return
        image, plan, first_out = self._preview
        self._preview = (image, dataclasses.replace(plan, position=pos / 100, detected=False), first_out)
        self._set_line("lbl_line_manual", pos=f"{pos:.1f}%")
        self._redraw_preview()

    def _on_line_release(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        pos = self._x_to_position(event.x)
        if pos is not None:
            self._page_override(create=True)["position"] = pos
        self._update_nav()

    def _on_line_reset(self, _event=None) -> None:
        ov = self._page_override()
        if ov and "position" in ov:
            del ov["position"]
            self._drop_empty_override()
            self._schedule_preview(delay=0)

    # ------------------------------------------------------------ preview
    def _schedule_preview(self, delay: int = 350) -> None:
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
        self._update_nav()
        self.preview_job = self.root.after(delay, self._start_preview)

    def _start_preview(self) -> None:
        self.preview_job = None
        path = self.preview_file
        if path is None:
            return
        opts = self.options()
        index = self.preview_index
        override = dict(self._page_override() or {})
        overrides = {k: dict(v) for k, v in self.overrides.get(path, {}).items()}
        password = self.passwords.get(path)
        self.preview_gen += 1
        gen = self.preview_gen
        self.lbl_preview_msg.configure(text=t("preview_loading"))

        def work() -> None:
            if gen != self.preview_gen:  # a newer request already superseded this one
                return
            try:
                doc = split.open_pdf(path, password)
                try:
                    t0 = time.perf_counter()
                    plan = split.plan_page(doc[index], opts, override)
                    secs = time.perf_counter() - t0
                    image = split.render_preview(doc, index, (PREVIEW_W * 2, PREVIEW_H * 2))
                    # output page numbers and count: modes only, no detection needed
                    modes = []
                    for i, page in enumerate(doc):
                        m = overrides.get(i, {}).get("mode")
                        modes.append(split.PagePlan(m if m in split.PAGE_MODES else split.default_mode(page, opts), 0.5))
                    first_out = split.page_map(modes)
                    out_pages = sum(2 if p.mode == "split" else 1 if p.mode == "whole" else 0 for p in modes)
                finally:
                    doc.close()
                self.root.after(0, lambda: self._preview_done(gen, image, plan, first_out, out_pages, secs))
            except Exception as exc:  # pragma: no cover - UI feedback only
                self.root.after(0, lambda e=exc: self._preview_failed(gen, e))

        threading.Thread(target=work, daemon=True).start()

    def _preview_failed(self, gen: int, exc: Exception) -> None:
        if gen == self.preview_gen and not self._closing:
            self.lbl_preview_msg.configure(text=str(exc))

    def _preview_done(self, gen: int, image, plan, first_out, out_pages: int, secs: float) -> None:
        if gen != self.preview_gen or self._closing:
            return
        self._preview = (image, plan, first_out)
        if plan.mode == "split" and plan.detected:
            self._page_seconds = secs
        self.var_page_mode.set(plan.mode)
        self._count_kwargs = {"pages": len(first_out), "out_pages": out_pages}
        self._update_count()
        self.lbl_preview_msg.configure(text="")
        self._describe_line(plan)
        self._update_nav()
        self._redraw_preview()

    def _update_count(self) -> None:
        if self._count_kwargs:
            self.lbl_count.configure(text=t("lbl_count", **self._count_kwargs))
        else:
            self.lbl_count.configure(text="")

    def _describe_line(self, plan) -> None:
        if plan.mode != "split":
            self._set_line("preview_skipped" if plan.mode == "skip" else "lbl_line_whole")
            return
        pos = f"{plan.position * 100:.1f}%"
        ov = self._page_override() or {}
        if "position" in ov:
            self._set_line("lbl_line_manual", pos=pos)
        elif plan.detected:
            self._set_line("lbl_line_auto", pos=pos)
        else:
            self._set_line("lbl_line_default", pos=pos)

    def _redraw_with_local_plan(self) -> None:
        """Overlap changes only the cut, not the line: redraw without re-rendering."""
        self._redraw_preview()
        self._schedule_preview()

    def _redraw_preview(self) -> None:
        if not self._preview:
            return
        image, plan, first_out = self._preview
        self._photos.clear()
        overlap = float(self.var_overlap.get()) / 100.0
        direction = self.var_direction.get()

        # original with the split line
        cv = self.cv_orig
        cw, ch = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        scale = min((cw - 16) / image.width, (ch - 16) / image.height, 1.0)
        w, h = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        shown = image.resize((w, h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(shown)
        self._photos.append(photo)
        cv.delete("all")
        ox, oy = (cw - w) // 2, (ch - h) // 2
        cv.create_image(ox, oy, image=photo, anchor="nw")
        self._orig_geom = (ox, oy, w, h)
        if plan.mode == "split":
            x = ox + plan.position * w
            if abs(overlap) > 1e-6:
                a, b = sorted((plan.position - overlap, plan.position + overlap))
                cv.create_rectangle(ox + a * w, oy, ox + b * w, oy + h, outline="",
                                    fill=LINE_COLOR if overlap > 0 else "#222", stipple="gray25")
            cv.create_line(x, oy - 4, x, oy + h + 4, fill=LINE_COLOR, width=2)
        elif plan.mode == "skip":
            cv.create_rectangle(ox, oy, ox + w, oy + h, outline="", fill="#ffffff", stipple="gray50")

        # result: the pages it turns into, in output order, with their numbers
        parts = split.split_preview(image, dataclasses.replace(plan), overlap, direction)
        cv = self.cv_res
        cv.delete("all")
        cw, ch = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        if not parts:
            return
        gap, label_h = 12, 20
        total_w = sum(p.width for p in parts)
        max_h = max(p.height for p in parts)
        scale = min((cw - 16 - gap * (len(parts) - 1)) / total_w, (ch - 16 - label_h) / max_h, 1.0)
        x = (cw - (total_w * scale + gap * (len(parts) - 1))) / 2
        first = first_out[self.preview_index] if self.preview_index < len(first_out) else None
        for k, part in enumerate(parts):
            pw, ph = max(1, int(part.width * scale)), max(1, int(part.height * scale))
            photo = ImageTk.PhotoImage(part.resize((pw, ph), Image.LANCZOS))
            self._photos.append(photo)
            y = (ch - label_h - ph) / 2
            cv.create_image(x, y, image=photo, anchor="nw")
            cv.create_rectangle(x, y, x + pw, y + ph, outline="#888")
            if first is not None:
                cv.create_text(x + pw / 2, y + ph + 11, text=str(first + k), fill="#333")
            x += pw + gap

    # ------------------------------------------------------------ running
    def run(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo(t("app_title"), t("msg_no_files"), parent=self.root)
            return
        opts = self.options()
        self._save_options()
        per_page = self._page_seconds or DEFAULT_PAGE_SECONDS
        for path in self.files:
            pages = self.page_counts[path]
            if pages >= split.LARGE_PAGE_COUNT:
                minutes = max(1, int(round(pages * per_page / 60)))
                if not messagebox.askyesno(t("dlg_confirm"), t("msg_large_file", name=os.path.basename(path),
                                                                pages=pages, minutes=minutes), parent=self.root):
                    return
        self.cancel_event.clear()
        self.outputs = []
        total_pages = sum(self.page_counts[p] for p in self.files)
        self.progress.configure(maximum=max(total_pages, 1), value=0)
        self._set_controls(running=True)
        files = list(self.files)
        overrides = {p: {k: dict(v) for k, v in self.overrides.get(p, {}).items()} for p in files}

        def work() -> None:
            done_pages = 0
            count = 0
            failed_total = 0
            for idx, path in enumerate(files, 1):
                name = os.path.basename(path)
                base = done_pages

                def progress(page: int, pages: int, _base=base, _idx=idx, _name=name) -> None:
                    self.root.after(0, lambda: self._on_progress(_base + page, _idx, len(files), page, pages, _name))

                def page_failed(page: int, exc: Exception, _name=name) -> None:
                    self.root.after(0, lambda: self.log("log_page_failed", name=_name, page=page, error=str(exc)))

                try:
                    result = split.process_pdf(path, opts, password=self.passwords.get(path),
                                               progress=progress, cancel=self.cancel_event,
                                               page_failed=page_failed, overrides=overrides.get(path))
                except split.Cancelled:
                    self.root.after(0, lambda _n=name: self.log("log_cancelled", name=_n))
                    self.root.after(0, lambda: self._finished(cancelled=True, count=count, failed=failed_total))
                    return
                except split.EmptyResult:
                    self.root.after(0, lambda _n=name: self.log("err_empty_result", name=_n))
                    done_pages += self.page_counts[path]
                    self.root.after(0, lambda _d=done_pages: self.progress.configure(value=_d))
                    continue
                except Exception as exc:  # one bad file must not end the batch
                    self.root.after(0, lambda _n=name, _e=exc: self.log("err_file_failed", name=_n, error=str(_e)))
                    done_pages += self.page_counts[path]
                    self.root.after(0, lambda _d=done_pages: self.progress.configure(value=_d))
                    continue
                count += 1
                failed_total += len(result.failed_pages)
                done_pages += result.pages
                self.outputs.append(result.output_path)
                self.root.after(0, lambda _r=result: self._log_result(_r))
            self.root.after(0, lambda: self._finished(cancelled=False, count=count, failed=failed_total))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _log_result(self, result: split.FileResult) -> None:
        self.log("log_saved", path=result.output_path)
        self.log("log_summary", pages=result.pages, out_pages=result.out_pages,
                 split=result.split_pages, detected=result.detected_pages)

    def _on_progress(self, done: int, file_idx: int, files: int, page: int, pages: int, name: str) -> None:
        self.progress.configure(value=done)
        self._set_status("status_processing", file=file_idx, files=files, page=page, pages=pages, name=name)

    def _finished(self, cancelled: bool, count: int, failed: int) -> None:
        self._set_controls(running=False)
        if cancelled or self._closing:
            self._set_status("status_cancelled")
            return
        self._set_status("status_done")
        self.progress.configure(value=self.progress["maximum"])
        if self.outputs:
            self.btn_open.configure(state="normal")
        msg = t("msg_done", count=count)
        if failed:
            msg += "\n" + t("msg_failed_pages", count=failed)
        messagebox.showinfo(t("app_title"), msg, parent=self.root)

    def cancel(self) -> None:
        self.cancel_event.set()
        self.btn_cancel.configure(state="disabled")

    def _set_controls(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        for w in (self.btn_run, self.btn_add, self.btn_add_dir, self.btn_clear):
            w.configure(state=state)
        self.cmb_lang.configure(state="disabled" if running else "readonly")
        self.btn_cancel.configure(state="normal" if running else "disabled")
        if running:
            self.btn_open.configure(state="disabled")
        self._update_nav()

    def open_result(self) -> None:
        if not self.outputs:
            return
        target = self.outputs[0]
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", target])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(target)])
        except OSError:
            pass

    def _on_close(self) -> None:
        self._closing = True
        self.cancel_event.set()
        try:
            self._save_options()
        except Exception:
            pass
        self._close_when_idle()

    def _close_when_idle(self) -> None:
        """Wait for the worker before tearing down. It is a daemon thread, so
        exiting under it mid-save would leave a stray .part file behind. The
        cancel flag stops it at the next page; a save already running is
        allowed to finish."""
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._close_when_idle)
            return
        self.root.destroy()

    def mainloop(self) -> None:
        self.root.mainloop()


def launch(initial_files: list[str] | None = None) -> None:
    App(initial_files).mainloop()
