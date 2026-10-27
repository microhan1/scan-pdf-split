"""The "Send feedback" link, series follow-up of 2026-10-27: it is a Tab stop that Enter
and Space open, it shows a small note while the mouse is over it, and every language code
picks its page by one rule (ko... -> Korean page, anything else -> English page). The
browser is faked, settings live in a temporary folder and nothing may leave the machine.

Key presses: a generated key event only reaches a widget when the window has the
system's keyboard focus, and taking that focus would steal it from whoever is typing.
So the tests run the very script Tk binds to the key (what Tk runs when the key comes
in), and check the Tab order with tk_focusNext, which is what Tk's <Tab> binding uses."""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import update_check as uc  # noqa: E402
from version import APP_VERSION  # noqa: E402

KO_PAGE = "https://chaekgalpi.co.kr/tools/scanpdfsplit"
EN_PAGE = "https://chaekgalpi.co.kr/en/tools/scanpdfsplit"
TIPS = {"ko": "안 되는 것·불편한 것·바라는 기능을 알려 주세요(브라우저가 열립니다)",
        "en": "Tell us what's broken or missing (opens your browser)",
        "ja": "不具合・ご要望をお知らせください（ブラウザが開きます）",
        "zh-CN": "告诉我们问题或建议（将打开浏览器）"}


def page_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


# ------------------------------------------------------------ c. one language rule
KOREAN = ["ko", "ko-KR", "ko_KR", "KO", "Ko-kr", " ko "]
ENGLISH = ["en", "ja", "zh", "zh-CN", "zh_CN", "zh-TW", "fr", "de", "EN", "en-US", "ja-JP", "pt-BR", "xx", "english"]


@pytest.mark.parametrize("lang", KOREAN)
def test_korean_codes_get_the_korean_page(lang):
    assert uc.is_korean(lang)
    assert page_of(uc.feedback_url(uc.SLUG, APP_VERSION, lang)) == KO_PAGE


@pytest.mark.parametrize("lang", ENGLISH)
def test_every_other_code_gets_the_english_page(lang):
    assert not uc.is_korean(lang)
    assert page_of(uc.feedback_url(uc.SLUG, APP_VERSION, lang)) == EN_PAGE


@pytest.mark.parametrize("lang", [None, "", "   "])
def test_no_language_follows_the_default_screen_language(lang, monkeypatch):
    assert i18n.DEFAULT_LANG == "en"                    # this tool opens in English when it cannot tell
    assert page_of(uc.feedback_url(uc.SLUG, APP_VERSION, lang)) == EN_PAGE
    monkeypatch.setattr(i18n, "DEFAULT_LANG", "ko")     # and the rule really reads the default
    assert page_of(uc.feedback_url(uc.SLUG, APP_VERSION, lang)) == KO_PAGE


@pytest.mark.parametrize("lang", KOREAN + ENGLISH + [None, ""])
@pytest.mark.parametrize("version", [APP_VERSION, "", None])
def test_every_address_has_the_same_shape(lang, version):
    url = uc.feedback_url(uc.SLUG, version, lang)
    assert url.startswith("https://chaekgalpi.co.kr/") and url.endswith("#feedback")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["utm_source"] == ["app"] and query["utm_medium"] == ["feedback"]
    assert query.get("v") == ([version] if version else None)


URL_KO = "https://chaekgalpi.co.kr/tools/scanpdfsplit?utm_source=app&utm_medium=update"
URL_EN = "https://chaekgalpi.co.kr/en/tools/scanpdfsplit?utm_source=app&utm_medium=update"


@pytest.mark.parametrize("lang", KOREAN + ENGLISH + [None, ""])
def test_update_page_uses_the_same_rule(lang):
    info = uc.UpdateInfo("9.9.9", APP_VERSION, URL_KO, URL_EN)
    expected = URL_KO if lang is not None and lang.strip().lower().startswith("ko") else URL_EN
    assert uc.page_for(info, lang) == expected
    assert (uc.page_for(info, lang) == URL_KO) == uc.is_korean(lang)
    assert uc.page_for(uc.UpdateInfo("9.9.9", APP_VERSION, URL_KO), lang) == URL_KO   # no English page offered


# ------------------------------------------------------------ d. the READMEs
FEEDBACK_LINK = re.compile(r"https://chaekgalpi\.co\.kr/[^\s)\]>]*#feedback")


@pytest.mark.parametrize("name, expected", [
    ("README.md", "https://chaekgalpi.co.kr/tools/scanpdfsplit#feedback"),
    ("README.en.md", "https://chaekgalpi.co.kr/en/tools/scanpdfsplit#feedback"),
    ("README.ja.md", "https://chaekgalpi.co.kr/en/tools/scanpdfsplit#feedback"),
    ("README.zh-CN.md", "https://chaekgalpi.co.kr/en/tools/scanpdfsplit#feedback"),
])
def test_readme_feedback_links(name, expected):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        links = FEEDBACK_LINK.findall(f.read())
    assert links and all(link == expected for link in links), links


# ------------------------------------------------------------ the window
class NoSockets:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, name):
        def blocked(*args, **kwargs):
            self.calls.append(name)
            raise OSError(f"network blocked in tests: {name}")
        return blocked


@pytest.fixture
def env(tmp_path, monkeypatch, no_update_server):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"lang": "en"}), encoding="utf-8")
    monkeypatch.setattr(i18n, "SETTINGS_PATH", str(settings))
    sockets = NoSockets()
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                        (socket, "create_connection"), (socket, "getaddrinfo")):
        monkeypatch.setattr(owner, name, sockets(name))
    i18n.init("en")
    import gui

    monkeypatch.setattr(gui.Tooltip, "DELAY_MS", 50)
    state = {"opened": [], "result": True, "warnings": [], "gui": gui}

    def fake_open(url, *args, **kwargs):
        state["opened"].append(url)
        return state["result"]

    monkeypatch.setattr(gui.webbrowser, "open", fake_open)
    monkeypatch.setattr(gui.messagebox, "showwarning",
                        lambda title, message, **kw: state["warnings"].append((title, message)))
    app = gui.App()
    app.root.update()
    state["app"] = app
    yield state
    # f. nothing reached the network: no urlopen (conftest) and no socket at all
    assert no_update_server.requests == []
    assert sockets.calls == []
    try:
        for job in app.root.tk.splitlist(app.root.tk.call("after", "info")):
            app.root.after_cancel(job)
        app._on_close()
        app.root.update()
    except Exception:
        pass
    import gc

    gc.collect()


def wait(app, cond, timeout=3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


def press(widget, sequence: str):
    """Run the script Tk binds to `sequence` on this widget, as Tk does when the key
    arrives; return what it returned ("break" stops the class bindings)."""
    script = widget.bind(sequence)
    assert script, f"nothing bound to {sequence}"
    fields = dict.fromkeys("#bhkstwxyEXYD", "0")      # the numbers Tk would fill in
    fields["W"] = str(widget)
    script = re.sub(r"%([#a-zA-Z])", lambda m: fields.get(m.group(1), "??"), script)
    code = widget.tk.eval(f"catch {{{script}}} ::press_result")   # 3 is Tcl's code for break
    assert code in ("0", "3"), widget.tk.eval("set ::press_result")
    return "break" if code == "3" else ""


def switch(app, lang):
    app.cmb_lang.current(i18n.LANGS.index(lang))
    app._on_lang()
    app.root.update()


def url_for(lang):
    return uc.feedback_url(uc.SLUG, APP_VERSION, lang)


# ------------------------------------------------------------ a. keyboard
def test_link_is_a_tab_stop_right_after_the_update_checkbox(env):
    app = env["app"]
    link = app.lnk_feedback
    assert str(link.cget("takefocus")) in ("1", "true", "True")
    assert app.chk_update.tk_focusNext() is link       # Tab from the checkbox lands on the link
    assert link.tk_focusPrev() is app.chk_update       # and Shift+Tab goes back
    assert link.tk_focusNext() is not link              # Tab moves on from it


def test_tab_order_reaches_the_link_from_the_language_box(env):
    app = env["app"]
    w, seen = app.cmb_lang, []
    for _ in range(5):
        w = w.tk_focusNext()
        seen.append(w)
    assert app.chk_update in seen and app.lnk_feedback in seen
    assert seen.index(app.lnk_feedback) == seen.index(app.chk_update) + 1


@pytest.mark.parametrize("key", ["<Return>", "<KP_Enter>", "<space>"])
def test_enter_and_space_open_the_page_exactly_once(env, key):
    assert press(env["app"].lnk_feedback, key) == "break"
    assert env["opened"] == [url_for("en")]
    assert env["warnings"] == []


def test_each_key_opens_once_and_mouse_still_works(env):
    link = env["app"].lnk_feedback
    for key in ("<Return>", "<space>", "<KP_Enter>"):
        press(link, key)
    link.event_generate("<Button-1>", x=2, y=2)
    env["app"].root.update()
    assert env["opened"] == [url_for("en")] * 4


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_keyboard_uses_the_current_language(env, lang):
    switch(env["app"], lang)
    press(env["app"].lnk_feedback, "<Return>")
    assert env["opened"] == [url_for(lang)]


def test_keyboard_with_no_browser_shows_the_address(env):
    env["result"] = False
    press(env["app"].lnk_feedback, "<space>")
    assert len(env["warnings"]) == 1 and url_for("en") in env["warnings"][0][1]


def test_focus_shows_a_frame_that_does_not_move_the_header(env):
    app = env["app"]
    link = app.lnk_feedback
    size = (link.winfo_reqwidth(), link.winfo_reqheight())
    assert str(link.cget("relief")) == "flat" and int(str(link.cget("borderwidth"))) >= 1
    press(link, "<FocusIn>")
    assert str(link.cget("relief")) == "solid"
    assert (link.winfo_reqwidth(), link.winfo_reqheight()) == size
    press(link, "<FocusOut>")
    assert str(link.cget("relief")) == "flat"
    assert env["opened"] == []                          # focus alone opens nothing


def test_other_keys_do_nothing(env):
    link = env["app"].lnk_feedback
    for seq in ("<Key-a>", "<Escape>", "<Tab>"):
        assert not link.bind(seq)
    assert env["opened"] == []


# ------------------------------------------------------------ b. the note under the link
def hover(app):
    app.lnk_feedback.event_generate("<Enter>", x=2, y=2)
    # mapped is not enough: the label inside can still be width 1 for a moment
    return wait(app, lambda: app.tip_feedback.tip is not None and app.tip_feedback.tip.winfo_ismapped()
                and app.tip_feedback.tip.winfo_width() > 1 and app.tip_feedback.label.winfo_width() > 1)


def test_note_appears_on_hover_and_goes_on_leave(env):
    app = env["app"]
    tip = app.tip_feedback
    assert tip.tip is None
    assert hover(app)
    assert tip.label.cget("text") == TIPS["en"]
    app.lnk_feedback.event_generate("<Leave>", x=2, y=2)
    app.root.update()
    assert tip.tip is None
    assert hover(app)                                   # and again
    assert env["opened"] == []                          # hovering opens nothing


def test_leaving_before_the_delay_shows_nothing(env):
    app = env["app"]
    app.lnk_feedback.event_generate("<Enter>", x=2, y=2)
    app.lnk_feedback.event_generate("<Leave>", x=2, y=2)
    wait(app, lambda: False, timeout=0.3)
    assert app.tip_feedback.tip is None


def test_clicking_or_pressing_hides_the_note(env):
    app = env["app"]
    assert hover(app)
    app.lnk_feedback.event_generate("<Button-1>", x=2, y=2)
    app.root.update()
    assert app.tip_feedback.tip is None and env["opened"] == [url_for("en")]
    assert hover(app)
    press(app.lnk_feedback, "<Return>")
    assert app.tip_feedback.tip is None and len(env["opened"]) == 2


def test_note_is_a_plain_borderless_window_above_the_app(env):
    app = env["app"]
    assert hover(app)
    tip = app.tip_feedback.tip
    assert tip.wm_overrideredirect() and tip.winfo_toplevel() is tip
    assert tip.winfo_rooty() >= app.lnk_feedback.winfo_rooty() + app.lnk_feedback.winfo_height()


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_note_words_per_language(env, lang):
    app = env["app"]
    switch(app, lang)
    assert app.tip_feedback.text == TIPS[lang]
    assert hover(app)
    assert app.tip_feedback.label.cget("text") == TIPS[lang]


def test_language_files_carry_distinct_non_empty_notes():
    notes = {}
    for lang in i18n.LANGS:
        with open(os.path.join(ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
            notes[lang] = json.load(f)["tip_feedback"]
    assert notes == TIPS
    assert all(n.strip() for n in notes.values()) and len(set(notes.values())) == len(notes)


def test_switching_language_while_the_note_shows_changes_its_words(env):
    app = env["app"]
    assert hover(app)
    for lang in ("ko", "ja", "zh-CN", "en"):
        switch(app, lang)
        assert app.tip_feedback.tip is not None
        assert app.tip_feedback.label.cget("text") == TIPS[lang]


def test_closing_the_window_takes_the_note_with_it(env):
    app = env["app"]
    assert hover(app)
    tip = app.tip_feedback.tip
    app.lnk_feedback.destroy()
    app.root.update()
    assert app.tip_feedback.tip is None and not tip.winfo_exists()


# ------------------------------------------------------------ e. what was there stays
def test_update_check_and_its_page_untouched(env):
    app = env["app"]
    info = uc.UpdateInfo("9.9.9", APP_VERSION, URL_KO, URL_EN)
    app._on_update_result(info, answered=False)
    assert app.frm_update.winfo_manager()
    assert hover(app)
    press(app.lnk_feedback, "<Return>")
    app._update_open()
    assert env["opened"] == [url_for("en"), URL_EN]
    assert app.var_update_check.get() is True


# ------------------------------------------------------------ f. no network
def test_rule_and_note_do_not_touch_the_network(no_update_server):
    for lang in KOREAN + ENGLISH + [None, ""]:
        uc.feedback_url(uc.SLUG, APP_VERSION, lang)
        uc.is_korean(lang)
    assert no_update_server.requests == []
