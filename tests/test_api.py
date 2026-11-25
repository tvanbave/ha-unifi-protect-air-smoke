"""Exercise the real HTTP/WebSocket client against a fake console."""

from __future__ import annotations

import json
import struct

from aiohttp import web
import pytest

from custom_components.unifi_protect_air_smoke.api import InvalidAuth, ProtectClient


def _frame(obj) -> bytes:
    raw = json.dumps(obj).encode()
    return struct.pack("!bbbbi", 1, 1, 0, 0, len(raw)) + raw


@pytest.fixture
async def console(socket_enabled, aiohttp_server):
    state = {"logins": 0, "expire_next": False, "events_query": None}

    async def login(request):
        body = await request.json()
        if body["password"] != "pw":
            return web.Response(status=401)
        state["logins"] += 1
        resp = web.json_response({})
        resp.set_cookie("TOKEN", f"t{state['logins']}")
        return resp

    def authed(request):
        return request.cookies.get("TOKEN") == f"t{state['logins']}"

    async def sensors(request):
        if state["expire_next"]:
            state["expire_next"] = False
            return web.Response(status=401)
        if not authed(request):
            return web.Response(status=401)
        return web.json_response([{"id": "s1"}, "junk"])

    async def bootstrap(request):
        if not authed(request):
            return web.Response(status=401)
        return web.json_response({"nvr": {"mac": "AABBCCDDEEFF"}, "lastUpdateId": "u1"})

    async def events(request):
        state["events_query"] = request.query.getall("types")
        return web.json_response([{"id": "e1"}])

    async def ws(request):
        assert authed(request)
        assert request.query["lastUpdateId"] == "u1"
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        await sock.send_bytes(_frame({"action": "update", "id": "s1"}) + _frame({"x": 1}))
        await sock.close()
        return sock

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/proxy/protect/api/sensors", sensors)
    app.router.add_get("/proxy/protect/api/bootstrap", bootstrap)
    app.router.add_get("/proxy/protect/api/events", events)
    app.router.add_get("/proxy/protect/ws/updates", ws)
    server = await aiohttp_server(app)
    return server, state


def _client(server, password="pw") -> ProtectClient:
    client = ProtectClient("127.0.0.1", server.port, "ha", password, False)
    client._base = f"http://127.0.0.1:{server.port}"
    client._ws_base = f"ws://127.0.0.1:{server.port}"
    return client


async def test_login_poll_and_relogin(console) -> None:
    server, state = console
    client = _client(server)
    try:
        assert await client.get_sensors() == [{"id": "s1"}]
        state["expire_next"] = True
        assert await client.get_sensors() == [{"id": "s1"}]
        assert state["logins"] == 2
        boot = await client.get_bootstrap()
        assert boot["lastUpdateId"] == "u1"
        assert await client.get_events(["sensorAlarm", "sensorVape"], 1, 2) == [{"id": "e1"}]
        assert state["events_query"] == ["sensorAlarm", "sensorVape"]
        got = [item async for item in client.stream_updates("u1")]
        assert got == [({"action": "update", "id": "s1"}, {"x": 1})]
    finally:
        await client.close()


async def test_bad_password(console) -> None:
    server, _ = console
    client = _client(server, password="nope")
    try:
        with pytest.raises(InvalidAuth):
            await client.get_sensors()
    finally:
        await client.close()
