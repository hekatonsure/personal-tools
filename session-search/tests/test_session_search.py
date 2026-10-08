import orjson
from session_search import sessions
from session_search.sessions import windows, TURN_CHARS, MAX_WINDOWS
from session_search.search import terms
from session_search.cli import _snippet, _fit
from session_search.embed import passages, PASSAGE
from session_search.jev import redact


def _jsonl(path, rows):
    path.write_bytes(b"\n".join(map(orjson.dumps, rows)) + b"\n\x00\x00\x00\n")  # trailing NUL run, as in crash-truncated logs
    return path


def test_parse_claude(tmp_path):
    msg = lambda t, content, **kw: {"type": t, "message": {"content": content}, "timestamp": "2026-09-01T00:00:00Z", "cwd": "/w", **kw}
    s = sessions.parse_claude(_jsonl(tmp_path / "abc.jsonl", [
        {"type": "ai-title", "aiTitle": "Tab colors"},
        msg("user", "<command-message>catchup</command-message>\n<command-name>/catchup</command-name>"),
        msg("user", "make tabs <system-reminder>ignore me</system-reminder>orange"),
        msg("assistant", [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "Done."}]),
        msg("user", [{"type": "tool_result", "content": "noise"}]),
        msg("assistant", [{"type": "text", "text": "Also blue."}]),
        msg("user", "subagent prompt", isSidechain=True),
        msg("user", "Caveat: local command output follows", isMeta=True),
    ]))
    assert (s.id, s.title, s.cwd) == ("abc", "Tab colors", "/w")
    assert [(t.user, t.assistant) for t in s.turns] == [("/catchup", []), ("make tabs orange", ["Done.", "Also blue."])]


def test_parse_codex_skips_injected_and_subagents(tmp_path):
    item = lambda role, text: {"type": "response_item", "timestamp": "t", "payload": {"type": "message", "role": role,
                               "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}
    meta = lambda **kw: {"type": "session_meta", "payload": {"id": "s1", "cwd": "/w", **kw}}
    rows = [item("user", "# AGENTS.md instructions\n..."), item("user", "<environment_context>x</environment_context>"),
            item("developer", "rules"), item("user", "fix the build"), item("assistant", "Fixed.")]
    s = sessions.parse_codex(_jsonl(tmp_path / "a.jsonl", [meta(source="cli")] + rows), {"s1": "Build fix"})
    assert s.title == "Build fix" and [(t.user, t.assistant) for t in s.turns] == [("fix the build", ["Fixed."])]
    guardian = sessions.parse_codex(_jsonl(tmp_path / "b.jsonl", [meta(source={"subagent": {"other": "guardian"}})] + rows), {})
    assert guardian.turns == []


def test_card_drops_bare_commands():
    card = sessions.card_text("", "/w", ["/clear", "/catchup", "why is wifi flaky"], "It was power saving.")
    assert "/clear" not in card and "why is wifi flaky" in card and "final reply: It was power saving." in card


def test_windows_cover_long_replies():
    assert windows("USER: hi\nASSISTANT: short") == ["USER: hi\nASSISTANT: short"]
    reply = "".join(f"{i:05d} " for i in range(4000))  # 24k chars
    ws = windows(f"USER: q\nASSISTANT: {reply}")
    assert 1 < len(ws) <= MAX_WINDOWS and all(w.startswith("USER: q\nASSISTANT (part ") for w in ws)
    assert "03999" in ws[-1], "last window must reach the end of the reply"
    assert all(len(w) <= TURN_CHARS for w in ws)


def test_redact():
    text = ("key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 and ghp_" + "a" * 36 +
            " Authorization: Bearer abcdefghijklmnop1234 password=hunter2hunter2 TYPESAFE_API_KEY='jv_live_0123456789abcdef'")
    out = redact(text)
    assert all(s not in out for s in ("sk-ant", "ghp_", "abcdefghijklmnop1234", "hunter2", "jv_live")), out
    assert "password=[REDACTED]" in out
    assert redact("plain prose about tokens and passwords") == "plain prose about tokens and passwords"


def test_terms_drop_stopwords():
    assert terms("where did we decide the cache backend") == ["decide", "cache", "backend"]


def test_passages_cover_window():
    text = "x" * 4500
    ps = passages(text)
    assert ps[0][0] == 0 and ps[-1][0] + len(ps[-1][1]) == len(text) and all(len(c) <= PASSAGE for _, c in ps)
    assert passages("short") == [(0, "short")]


def test_snippet_centres_on_term_in_best_passage():
    window = "USER: q\nASSISTANT: " + "filler " * 400 + "the cache backend is sqlite now" + " tail" * 50
    snip = _snippet(window, window.index("filler " * 10, 2000), ["backend"], 80)
    assert "backend" in snip and snip.startswith("…")
    assert not _snippet("USER: q\nASSISTANT: answer", 0, [], 80).startswith("USER")


def test_fit_keeps_best_session():
    out = [{"id": str(i), "x": "y" * 400} for i in range(10)]
    assert len(_fit(out, 300)) < 10 and _fit(out, 1)[0]["id"] == "0" and _fit(out, None) == out


def test_exact_repeat_reuses_locally(tmp_path, monkeypatch):
    import asyncio, time, types
    from session_search import search
    def unexpected_jev(*args, **kwargs):
        raise AssertionError("Exact cached queries must not authenticate or call Jev")
    monkeypatch.setattr(search, "Jev", unexpected_jev)
    db = sessions.connect(tmp_path / "i.db")
    args = types.SimpleNamespace(query="Cache  Backend choice", source=None, since=None, cwd=None, exclude=None, depth=12,
                                 windows=48, excerpts=2, exhaustive=False, verbose=False)
    db.execute("insert into searches values(?,?,?,?)", (time.time(), "cache backend choice", search.filters_key(args), orjson.dumps([{"id": "x"}])))
    assert asyncio.run(search.equivalent(db, args))[1:2] == (1.0,)
    args.depth = 32  # different filters → nothing eligible, still no network
    assert asyncio.run(search.equivalent(db, args)) is None
