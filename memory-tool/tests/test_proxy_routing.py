import json
import sqlite3
import uuid

import httpx
import pytest

from memory_tool.archive import Archive
from memory_tool.proxy_routing import CodexRouter, thread_id
from test_proxy import completed, compactable_request, post, proxy, running, upstream


@pytest.fixture
def registry(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    sessions = [str(uuid.uuid4()) for _ in range(3)]
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,cwd TEXT)")
        db.executemany(
            "INSERT INTO threads VALUES(?,?)",
            [(s, str(tmp_path / f"project-{i // 2}")) for i, s in enumerate(sessions)],
        )
    path = tmp_path / "memory.sqlite"
    archive = Archive(path)
    archive.register("main", str(tmp_path / "project-0"))
    archive.register("other", str(tmp_path / "project-1"))
    archive.close()
    return home, path, sessions


def metadata(session):
    return {
        "x-codex-turn-metadata": json.dumps(
            {"session_id": session, "thread_id": session}
        )
    }


def test_router_isolates_threads_projects_and_bounds_cache(registry):
    home, path, sessions = registry
    router = CodexRouter(path, home, lambda c, s: object(), max_cached=2)
    routes = [router.resolve(metadata(s), b"{}") for s in sessions]
    assert [r[0] for r in routes] == ["main", "main", "other"]
    assert [r[1] for r in routes] == sessions
    assert len({id(r[2]) for r in routes}) == 3
    assert len(router.cache) == 2
    assert router.resolve(metadata(sessions[2]), b"{}")[2] is routes[2][2]
    assert router.resolve(metadata(sessions[0]), b"{}")[2] is not routes[0][2]


def test_metadata_body_and_header_must_agree(registry):
    _, _, sessions = registry
    body = json.dumps(
        {"client_metadata": {**metadata(sessions[0]), "thread_id": sessions[0]}}
    ).encode()
    assert thread_id({}, body) == sessions[0]
    assert thread_id(metadata(sessions[0]), body) == sessions[0]
    assert thread_id(metadata(sessions[1]), body) is None


@pytest.mark.parametrize(
    "headers,body",
    [
        ({}, b"{}"),
        ({"session_id": "not-a-uuid"}, b"{}"),
        ({"session_id": str(uuid.uuid4())}, b"{}"),
        ({"x-codex-turn-metadata": "broken"}, b"{}"),
        ({}, b'{"client_metadata":{},"client_metadata":{}}'),
        ({}, b"[]"),
    ],
)
def test_unknown_identity_is_not_archived_under_default_chat(registry, headers, body):
    home, path, _ = registry
    router = CodexRouter(path, home, lambda c, s: object())
    assert router.resolve(headers, body) is None
    assert router.status()["unrouted_requests"] == 1


def test_project_binding_survives_restart_and_rejects_changed_project(registry):
    home, path, sessions = registry
    router = CodexRouter(path, home, lambda c, s: object())
    assert router.resolve(metadata(sessions[0]), b"{}")[0] == "main"
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute(
            "UPDATE threads SET cwd=? WHERE id=?",
            (str(home.parent / "project-1"), sessions[0]),
        )
    restarted = CodexRouter(path, home, lambda c, s: object())
    assert restarted.resolve(metadata(sessions[0]), b"{}") is None


def test_hook_identity_can_route_before_native_registry_insert(registry):
    home, path, _ = registry
    session = str(uuid.uuid4())
    archive = Archive(path)
    archive.operation(f"hook:{session}:SessionStart", "main", session, "prepared", {})
    archive.close()
    router = CodexRouter(path, home, lambda c, s: object())
    assert router.resolve(metadata(session), b"{}")[:2] == ("main", session)


def test_http_routing_compaction_isolated_and_native_compact_passes_through(registry):
    home, path, sessions = registry
    server, received = upstream(b'{"status":"completed","output":[]}')
    with (
        running(server) as origin,
        running(
            proxy(
                origin,
                path,
                "openai",
                codex_home=home,
                compact_at=14000,
                keep_turns=1,
                summary_tree=True,
            )
        ) as url,
    ):
        for session, label in (
            (sessions[0], "PROJECT_ZERO"),
            (sessions[2], "PROJECT_ONE"),
        ):
            request = compactable_request("openai").replace(
                b"long original output", label.encode()
            )
            # Retain enough data to cross the threshold after changing the label.
            request = request.replace(
                label.encode(), (label + " original output").encode()
            )
            response = post(url, request, path="/responses", **metadata(session))
            assert response.status_code == 200
        events, operations, _ = completed(path, count=2)
        assert {r["chat"] for r in operations} == {"main", "other"}
        assert {r["thread"] for r in operations} == {sessions[0], sessions[2]}
        assert all(r["detail"]["compaction"]["applied"] for r in operations)
        assert "PROJECT_ONE" not in "".join(r["text"] for r in events)
        assert b"PROJECT_ONE" not in received[0][2]
        assert b"PROJECT_ZERO" not in received[1][2]
        raw = b'{"input":[],"opaque":"UNCHANGED"}'
        assert post(url, raw, path="/responses").status_code == 200
        assert (
            post(
                url, raw, path="/responses/compact", **metadata(sessions[0])
            ).status_code
            == 200
        )
        assert received[-1][0] == "/responses/compact"
        assert received[-1][2] == received[-2][2] == raw
        health = httpx.get(url + "/health", trust_env=False).json()
        assert health["routed_requests"] == 2 and health["unrouted_requests"] == 1
        assert len(completed(path, count=2)[1]) == 2
