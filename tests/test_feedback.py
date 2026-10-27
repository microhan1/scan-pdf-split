"""The "Send feedback" link: it only opens the tool page's feedback box in the browser.
The browser is always faked, settings live in a temporary folder, and nothing may leave
the machine - conftest blocks urlopen, and the socket layer is watched here as well."""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.parse
import webbrowser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import update_check as uc  # noqa: E402
from version import APP_VERSION  # noqa: E402

KO = "https://chaekgalpi.co.kr/tools/scanpdfsplit?utm_source=app&utm_medium=feedback&v=0.2.1#feedback"
EN = "https://chaekgalpi.co.kr/en/tools/scanpdfsplit?utm_source=app&utm_medium=feedback&v=0.2.1#feedback"


def v_of(url: str) -> list[str] | None:
    """The v value(s) as a browser and the server would read them back."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.parse_qs(parts.query, keep_blank_values=True).get("v")


# ------------------------------------------------------------ a. the address
@pytest.mark.parametrize("lang, expect", [("ko", KO), ("en", EN), ("ja", EN), ("zh-CN", EN)])
def test_address_per_language(lang, expect):
    assert uc.feedback_url("scanpdfsplit", "0.2.1", lang) == expect


@pytest.mark.parametrize("lang", [None, "", "fr", "de", "zh-TW", "english"])
def test_unknown_language_gets_the_english_page(lang):
    """Series rule of 2026-10-27: only ko... is Korean; no language means the default (en)."""
    assert uc.feedback_url("scanpdfsplit", "0.2.1", lang) == EN


@pytest.mark.parametrize("lang", ["EN", "En", "JA", "zh-cn", "ZH-CN"])
def test_language_code_case_does_not_matter(lang):
    assert uc.feedback_url("scanpdfsplit", "0.2.1", lang) == EN


@pytest.mark.parametrize("lang", ["ko", "en", "ja", "zh-CN", None, "xx"])
@pytest.mark.parametrize("version", ["0.2.1", "", None, "a b&c#d"])
def test_address_shape(lang, version):
    url = uc.feedback_url(uc.SLUG, version, lang)
    assert url.startswith("https://chaekgalpi.co.kr/") and url.startswith(uc.SITE)
    assert url.endswith("#feedback") and url.count("#") == 1
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["utm_source"] == ["app"] and query["utm_medium"] == ["feedback"]
    assert urllib.parse.urlsplit(url).path in ("/tools/scanpdfsplit", "/en/tools/scanpdfsplit")
    assert urllib.parse.urlsplit(url).fragment == "feedback"


# ------------------------------------------------------------ b. the version
@pytest.mark.parametrize("version", ["0.2.1", "v0.2.1", "1.0.0-beta+3", APP_VERSION, f"v{APP_VERSION}"])
def test_version_is_carried_unchanged(version):
    url = uc.feedback_url(uc.SLUG, version, "ko")
    assert v_of(url) == [version]


def test_plus_is_encoded_so_it_does_not_read_back_as_a_space():
    url = uc.feedback_url(uc.SLUG, "1.0.0-beta+3", "en")
    assert "v=1.0.0-beta%2B3#feedback" in url


@pytest.mark.parametrize("version", ["", None, "   ", "\t"])
def test_empty_version_leaves_v_out(version):
    url = uc.feedback_url(uc.SLUG, version, "ko")
    assert v_of(url) is None and "v=" not in url
    assert url == "https://chaekgalpi.co.kr/tools/scanpdfsplit?utm_source=app&utm_medium=feedback#feedback"


@pytest.mark.parametrize("version", [
    "0.2 beta", "1&utm_source=evil", "1#top", "버전1", " 0.2.1 ", "a=b", "1/2", "%41", "?x", "0.2.1\n", "ü😀",
])
def test_odd_versions_are_encoded_and_cannot_break_the_address(version):
    url = uc.feedback_url(uc.SLUG, version, "ja")
    parts = urllib.parse.urlsplit(url)
    assert parts.fragment == "feedback" and parts.path == "/en/tools/scanpdfsplit"
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    assert query == {"utm_source": ["app"], "utm_medium": ["feedback"], "v": [version]}
    assert all(ord(c) < 128 and not c.isspace() for c in url)


# ------------------------------------------------------------ the window
class NoSockets:
    """Records any attempt to open a connection or look up a name."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, name):
        def blocked(*args, **kwargs):
            self.calls.append(name)
            raise OSError(f"network blocked in tests: {name}")
        return blocked


@pytest.fixture
def env(tmp_path, monkeypatch, no_update_server):
    """A real App with temporary settings, a recorded browser and a recorded warning box."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"lang": "en", "options": {"position": 55.0}}), encoding="utf-8")
    monkeypatch.setattr(i18n, "SETTINGS_PATH", str(settings))
    sockets = NoSockets()
    monkeypatch.setattr(socket.socket, "connect", sockets("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", sockets("socket.connect_ex"))
    monkeypatch.setattr(socket, "create_connection", sockets("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", sockets("socket.getaddrinfo"))
    i18n.init("en")
    import gui

    state = {"opened": [], "result": True, "warnings": [], "settings": settings, "gui": gui,
             "sockets": sockets, "urlopen": no_update_server}

    def fake_open(url, *args, **kwargs):
        state["opened"].append(url)
        result = state["result"]
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(gui.webbrowser, "open", fake_open)
    monkeypatch.setattr(gui.messagebox, "showwarning",
                        lambda title, message, **kw: state["warnings"].append((title, message)))
    app = gui.App()
    app.root.update()
    state["app"] = app
    yield state
    # g. nothing reached the network: no urlopen (conftest) and no socket at all
    assert no_update_server.requests == []
    assert sockets.calls == []
    try:
        for job in app.root.tk.splitlist(app.root.tk.call("after", "info")):
            app.root.after_cancel(job)                  # the pending update check, so Tcl does not complain
        app._on_close()
        app.root.update()
    except Exception:
        pass
    import gc

    gc.collect()


def switch(app, lang):
    app.cmb_lang.current(i18n.LANGS.index(lang))
    app._on_lang()
    app.root.update()


def url_for(lang):
    return uc.feedback_url("scanpdfsplit", APP_VERSION, lang)


# ------------------------------------------------------------ c. the click
def test_link_sits_in_the_header_next_to_update_check(env):
    app = env["app"]
    assert app.lnk_feedback.master is app.chk_update.master
    assert app.lnk_feedback.grid_info()["column"] > app.chk_update.grid_info()["column"]
    assert app.lnk_feedback.winfo_ismapped() and app.lnk_feedback.cget("text") == "Send feedback"


def test_click_opens_the_feedback_page_once(env):
    env["app"]._send_feedback()
    assert env["opened"] == [url_for("en")]
    assert env["opened"][0] == (f"https://chaekgalpi.co.kr/en/tools/scanpdfsplit?utm_source=app&utm_medium=feedback"
                                f"&v={urllib.parse.quote(APP_VERSION, safe='')}#feedback")
    assert env["warnings"] == []


def test_a_real_mouse_click_on_the_link_runs_the_same_callback(env):
    app = env["app"]
    app.lnk_feedback.event_generate("<Button-1>", x=2, y=2)
    app.root.update()
    assert env["opened"] == [url_for("en")]


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_click_after_switching_language_uses_the_new_address(env, lang):
    app = env["app"]
    switch(app, lang)
    app._send_feedback()
    assert env["opened"] == [url_for(lang)]


def test_switching_back_and_forth_follows_the_current_language(env):
    app = env["app"]
    seen = []
    for lang in ("ko", "en", "ja", "ko", "zh-CN"):
        switch(app, lang)
        app._send_feedback()
        seen.append(env["opened"][-1])
    assert seen == [url_for(l) for l in ("ko", "en", "ja", "ko", "zh-CN")]
    assert "/en/" not in seen[0] and "/en/" not in seen[3]


# ------------------------------------------------------------ d. no browser
@pytest.mark.parametrize("result", [False, None, webbrowser.Error("no runnable browser"),
                                    OSError("ShellExecute failed"), RuntimeError("anything else")],
                         ids=["false", "none", "webbrowser.Error", "OSError", "RuntimeError"])
def test_no_browser_shows_the_address_and_leaves_the_app_as_it_was(env, result):
    app = env["app"]
    env["result"] = result
    settings_before = env["settings"].read_text(encoding="utf-8")
    status_before = app.lbl_status.cget("text")
    title_before = app.root.title()
    app._send_feedback()                               # must not raise
    app.root.update()
    url = url_for("en")
    assert env["opened"] == [url]
    assert len(env["warnings"]) == 1 and url in env["warnings"][0][1]
    assert "Could not open your browser" in env["warnings"][0][1]
    assert url in app.txt_log.get("1.0", "end")        # also in the log, where it can be selected
    assert app.lbl_status.cget("text") == status_before and app.root.title() == title_before
    assert env["settings"].read_text(encoding="utf-8") == settings_before
    assert app.files == [] and not app._running and str(app.btn_run.cget("state")) != "disabled"
    # and the next click still works
    env["result"] = True
    app._send_feedback()
    assert env["opened"] == [url, url] and len(env["warnings"]) == 1


@pytest.mark.parametrize("lang, words", [("ko", "브라우저를 열지 못했습니다"), ("en", "Could not open your browser"),
                                         ("ja", "ブラウザを開けませんでした"), ("zh-CN", "无法打开浏览器")])
def test_no_browser_message_is_in_the_screen_language(env, lang, words):
    app = env["app"]
    switch(app, lang)
    env["result"] = False
    app._send_feedback()
    title, message = env["warnings"][0]
    assert words in message and message.endswith(url_for(lang)) and title == i18n.t("app_title")


# ------------------------------------------------------------ e. the words
NEW_KEYS = ("btn_feedback", "err_feedback_open")
LINK_TEXT = {"ko": "의견 보내기", "en": "Send feedback", "ja": "ご意見を送る", "zh-CN": "发送反馈"}


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_new_keys_exist_in_every_language(lang):
    with open(os.path.join(ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
        strings = json.load(f)
    for key in NEW_KEYS:
        assert isinstance(strings.get(key), str) and strings[key].strip(), (lang, key)
    assert strings["btn_feedback"] == LINK_TEXT[lang]
    assert "{url}" in strings["err_feedback_open"]


def test_every_language_has_the_same_keys():
    keys = {}
    for lang in i18n.LANGS:
        with open(os.path.join(ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
            keys[lang] = set(json.load(f))
    assert all(k == keys["en"] for k in keys.values()), {l: keys["en"] ^ k for l, k in keys.items()}


def test_link_text_follows_the_language_switch(env):
    app = env["app"]
    for lang in ("ko", "ja", "zh-CN", "en", "ko"):
        switch(app, lang)
        assert app.lnk_feedback.cget("text") == LINK_TEXT[lang]


# ------------------------------------------------------------ f. update check untouched
def test_feedback_leaves_the_update_check_alone(env):
    app = env["app"]
    prefs_before = dict(vars(app.update_prefs))
    app._send_feedback()
    assert vars(app.update_prefs) == prefs_before and app.var_update_check.get() is True
    assert not app.frm_update.winfo_manager() and app._update_info is None
    app.var_update_check.set(False)
    app._on_update_toggle()                             # the checkbox still saves as before
    saved = json.loads(env["settings"].read_text(encoding="utf-8"))
    assert saved["update_check"] is False and saved["options"] == {"position": 55.0}
    app._send_feedback()
    assert env["opened"] == [url_for("en")] * 2


def test_update_notice_and_feedback_open_their_own_pages(env):
    app = env["app"]
    info = uc.UpdateInfo("9.9.9", APP_VERSION, "https://chaekgalpi.co.kr/tools/scanpdfsplit?utm_source=app&utm_medium=update")
    app._on_update_result(info, answered=False)
    app._send_feedback()
    app._update_open()
    assert env["opened"] == [url_for("en"), info.url]


# ------------------------------------------------------------ g. the watch itself
def test_the_socket_watch_really_records(env):
    """Without this, "no socket calls" after every window test could be vacuous."""
    with pytest.raises(OSError):
        socket.create_connection(("chaekgalpi.co.kr", 443), timeout=0.1)
    s = socket.socket()
    try:
        with pytest.raises(OSError):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()
    assert env["sockets"].calls == ["socket.create_connection", "socket.connect"]
    env["sockets"].calls.clear()                        # recorded on purpose here


def test_feedback_module_does_not_reach_the_network(no_update_server):
    """feedback_url only builds a string."""
    for lang in (*i18n.LANGS, None):
        uc.feedback_url(uc.SLUG, APP_VERSION, lang)
    assert no_update_server.requests == []
