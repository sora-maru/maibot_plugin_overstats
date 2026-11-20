from pathlib import Path

import asyncio
import base64
import importlib
import json
import sys
import tomllib

import pytest
import pytest_asyncio
from aiohttp import web
from maibot_sdk.context import PluginContext, PluginPaths

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "maibot_plugin_overstats",
    ROOT / "__init__.py",
    submodule_search_locations=[str(ROOT)],
)
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
commands = importlib.import_module("maibot_plugin_overstats.commands")
client_mod = importlib.import_module("maibot_plugin_overstats.client")
plugin_mod = importlib.import_module("maibot_plugin_overstats.plugin")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1WQAAAAASUVORK5CYII="
)


@pytest_asyncio.fixture
async def setup(tmp_path):
    calls = []
    sends = []

    async def backend(request):
        payload = await request.json() if request.method == "POST" else {}
        calls.append((request.path, payload))
        if request.path.endswith("today/image"):
            return web.json_response(
                {"error": "summary_empty", "message": "today empty"}, status=404
            )
        if request.path.endswith("/detail/replies"):
            return web.json_response(
                {
                    "replies": [
                        {"type": "image", "base64": base64.b64encode(PNG).decode()},
                        "/assets/card.png",
                    ]
                }
            )
        if request.path.endswith("/bad"):
            return web.json_response({"message": "not an image"})
        if request.path == "/huge":
            return web.Response(body=b"x" * 100)
        if request.path == "/healthz":
            return web.json_response({"ok": True})
        if request.path.endswith("/ow-guess/replies"):
            return web.json_response(
                {
                    "ok": True,
                    "stages": [
                        {"replies": [{"type": "text", "data": "题目一"}]},
                        {"replies": [{"type": "text", "data": "提示二"}]},
                    ],
                    "reveal_replies": [{"type": "text", "data": "答案三"}],
                }
            )
        return web.Response(body=PNG, content_type="image/png")

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", backend)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async def rpc(method, plugin_id, payload, **kwargs):
        sends.append({"payload": payload})
        return {"success": True, "sent": True}

    plugin = plugin_mod.create_plugin()
    plugin.set_plugin_config(
        {
            "plugin": {"config_version": "1.0.0"},
            "backend": {"api_url": f"http://127.0.0.1:{port}"},
            "access": {"admin_users": ["qq:admin"]},
        }
    )
    ctx = PluginContext(
        "local.overstats",
        rpc_call=rpc,
        paths=PluginPaths(tmp_path / "data", tmp_path / "runtime"),
    )
    plugin._set_context(ctx)
    await plugin.on_load()
    try:
        yield plugin, calls, sends
    finally:
        await plugin.on_unload()
        await runner.cleanup()


async def command(plugin, text, user="42", stream="s1"):
    return await plugin.command(
        stream_id=stream, platform="qq", user_id=user, group_id="g", text=text
    )


async def drain(plugin):
    while plugin.tasks:
        await asyncio.gather(*tuple(plugin.tasks))


def texts(sends):
    return [
        v["payload"]["args"]["text"]
        for v in sends
        if "text" in v.get("payload", {}).get("args", {})
    ]


def images(sends):
    return [v for v in sends if v.get("payload", {}).get("capability") == "send.image"]


@pytest.mark.parametrize(
    "cmd,args,endpoint,payload",
    [
        (
            "大神数据",
            ["竞技", "Other#1234"],
            "/dashen-profile/image",
            {"bnet_id": "Other#1234", "mode": "competitive"},
        ),
        (
            "赛季总结",
            ["24"],
            "/dashen-summary/season/image",
            {"bnet_id": "Self#1234", "season": 24},
        ),
        (
            "同玩查询",
            ["A#1234", "B#1234"],
            "/dashen-sameplay/image",
            {"player1_bnet_id": "A#1234", "player2_bnet_id": "B#1234"},
        ),
        (
            "同玩查询",
            ["B#1234"],
            "/dashen-sameplay/image",
            {"player1_bnet_id": "Self#1234", "player2_bnet_id": "B#1234"},
        ),
        (
            "省榜",
            ["北京", "开放"],
            "/dashen-rank-leaderboard/image",
            {"province": "北京", "role": "open"},
        ),
        (
            "绝活榜",
            ["北京", "安娜", "开放"],
            "/dashen-hero-leaderboard/image",
            {"province": "北京", "hero": "安娜", "mode": "open"},
        ),
        (
            "ow百科",
            ["安娜", "快速", "怎么玩"],
            "/ow-hero-wiki/image",
            {"hero": "安娜", "question": "快速 怎么玩"},
        ),
        (
            "开庭",
            ["2"],
            "/dashen-court/image",
            {"bnet_id": "Self#1234", "index": 1, "use_db": False},
        ),
        (
            "是区吗结果",
            [],
            "/dashen-shiqu/image",
            {"bnet_id": "Last#1234", "use_db": True},
        ),
    ],
)
def test_api_mapping(cmd, args, endpoint, payload):
    query = commands.build_query(cmd, args, "Self#1234", "Last#1234")
    assert (query.endpoint, query.payload) == (endpoint, payload)


def test_parsing_and_boundaries():
    assert commands.parse('/单局 "Space Name#1234" 20 锐评关')[0] == "单局详细"
    assert commands.parse("普通聊天内容") is None
    assert commands.parse("/今日总结abc") is None
    assert commands.parse("/绑定 Hero＃12345")[1] == ["Hero#12345"]
    with pytest.raises(ValueError):
        commands.parse('/绑定 "bad')
    for cmd, args in [
        ("单局详细", ["0"]),
        ("单局详细", ["21"]),
        ("快速强度", ["13"]),
        ("开庭", []),
        ("历史段位", ["24", "22"]),
    ]:
        with pytest.raises(ValueError):
            commands.build_query(cmd, args, "Self#1234")
    query = commands.build_query(
        "单局详细", ["20", "锐评关", "全员关"], "Self#1234", analyze=True
    )
    assert query.payload["index"] == "19"
    assert (
        query.payload["analyze"] is False and query.payload["show_all_heroes"] is False
    )


@pytest.mark.asyncio
async def test_binding_persists_and_platform_isolation(setup):
    plugin, calls, sends = setup
    await command(plugin, "/绑定 Self#1234")
    assert plugin.store.get("bindings", "qq:42") == "Self#1234"
    store = plugin_mod.StateStore(plugin.ctx.paths.data_dir)
    await store.load()
    assert store.get("bindings", "qq:42") == "Self#1234"
    assert store.get("bindings", "other:42") is None
    await command(plugin, "/解绑")
    assert plugin.store.get("bindings", "qq:42") is None
    assert not calls


@pytest.mark.asyncio
async def test_today_falls_back_to_yesterday(setup):
    plugin, calls, sends = setup
    assert await command(plugin, "/今日总结 Self#1234") == (True, "", 2)
    await drain(plugin)
    assert [p for p, _ in calls] == [
        "/api/v2/dashen-summary/today/image",
        "/api/v2/dashen-summary/yesterday/image",
    ]
    assert images(sends)


@pytest.mark.asyncio
async def test_multi_image_and_cache_isolation(setup):
    plugin, calls, sends = setup
    await command(plugin, "/单局详细 2 Self#1234")
    await drain(plugin)
    assert len(images(sends)) == 2
    assert calls[0][1]["index"] == "1"
    await command(plugin, "/获取图片", user="43")
    await drain(plugin)
    assert len(images(sends)) == 2
    await command(plugin, "/获取图片", stream="s2")
    await drain(plugin)
    assert len(images(sends)) == 2
    await command(plugin, "/获取图片")
    await drain(plugin)
    assert len(images(sends)) == 4


@pytest.mark.asyncio
async def test_real_1316_event_shape_and_target(setup):
    plugin, calls, sends = setup
    await command(plugin, "/大神对局 Other#1234")
    await drain(plugin)
    message = {
        "session_id": "s1",
        "platform": "qq",
        "processed_plain_text": "2",
        "message_info": {
            "user_info": {"user_id": "42"},
            "group_info": {"group_id": "g"},
        },
    }
    result = await plugin.shortcuts(message=message)
    assert (
        result["modified_kwargs"]["message"]["processed_plain_text"]
        == "/单局详细 2 Other#1234"
    )
    await command(plugin, result["modified_kwargs"]["message"]["processed_plain_text"])
    await drain(plugin)
    assert calls[-2][1]["bnet_id"] == "Other#1234"
    assert calls[-2][1]["index"] == "1"
    message["processed_plain_text"] = "今日总结"
    assert await plugin.shortcuts(message=message) == {"action": "continue"}
    message["processed_plain_text"] = "商店"
    assert await plugin.shortcuts(message=message) == {"action": "continue"}
    await command(plugin, "商店")
    await drain(plugin)
    assert calls[-1][0] == "/api/v2/ow-shop/image"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,data,flag",
    [
        (
            "@麦麦",
            {"target_user_id": "123456", "target_user_nickname": "麦麦"},
            "is_mentioned",
        ),
        (
            "@群里的 麦麦",
            {
                "target_user_id": "123456",
                "target_user_cardname": "群里的 麦麦",
                "target_user_nickname": "旧昵称",
            },
            "is_mentioned",
        ),
        ("@123456", {"target_user_id": "123456"}, "is_at"),
        ("@Bot", None, "is_mentioned"),
    ],
)
@pytest.mark.parametrize("slash", ["", "/", "／"])
async def test_mention_command_binding(setup, prefix, data, flag, slash):
    plugin, calls, sends = setup
    message = {
        "session_id": "s1",
        "platform": "qq",
        flag: True,
        "processed_plain_text": f"{prefix} {slash}绑定 Hero#123",
        "message_info": {
            "user_info": {"user_id": "42"},
            "group_info": {"group_id": "g"},
        },
    }
    if data:
        message["raw_message"] = [
            {"type": "at", "data": data},
            {"type": "text", "data": f" {slash}绑定 Hero#123"},
        ]
    result = await plugin.shortcuts(message=message)
    cleaned = result["modified_kwargs"]["message"]["processed_plain_text"]
    assert cleaned == slash + "绑定 Hero#123"
    assert message["processed_plain_text"].startswith("@")
    await command(plugin, cleaned)
    assert plugin.store.get("bindings", "qq:42") == "Hero#123"
    assert not calls


@pytest.mark.asyncio
async def test_mention_keeps_non_commands_and_inner_arguments(setup):
    plugin, calls, sends = setup
    base = {
        "session_id": "s1",
        "platform": "qq",
        "is_mentioned": True,
        "message_info": {
            "user_info": {"user_id": "42"},
            "group_info": {"group_id": "g"},
        },
        "raw_message": [{"type": "at", "data": {"target_user_nickname": "麦麦"}}],
    }
    assert await plugin.shortcuts(
        message={**base, "processed_plain_text": "@麦麦 今天想看看今日总结呢"}
    ) == {"action": "continue"}
    assert await plugin.shortcuts(
        message={
            **base,
            "is_mentioned": False,
            "processed_plain_text": "@其他人 绑定 Hero#123",
        }
    ) == {"action": "continue"}
    result = await plugin.shortcuts(
        message={**base, "processed_plain_text": "@麦麦 ow百科 安娜 @队友 怎么玩"}
    )
    assert (
        result["modified_kwargs"]["message"]["processed_plain_text"]
        == "ow百科 安娜 @队友 怎么玩"
    )
    config = plugin.get_plugin_config_data()
    config["chat"]["nickname"] = "文字昵称"
    plugin.set_plugin_config(config)
    result = await plugin.shortcuts(
        message={
            **base,
            "is_mentioned": False,
            "processed_plain_text": "@文字昵称 绑定 Hero#123",
        }
    )
    assert (
        result["modified_kwargs"]["message"]["processed_plain_text"] == "绑定 Hero#123"
    )


def test_chinese_configuration_labels():
    schema = plugin_mod.create_plugin().build_config_schema()
    for section in schema["sections"].values():
        assert any("\u4e00" <= c <= "\u9fff" for c in section["title"])
        for field in section["fields"].values():
            assert any("\u4e00" <= c <= "\u9fff" for c in field["label"])
    assert (
        schema["sections"]["backend"]["fields"]["auth_password"]["input_type"]
        == "password"
    )


@pytest.mark.asyncio
async def test_mention_number_shortcut_and_direct_command_context(setup):
    plugin, calls, sends = setup
    import time

    plugin.recent[("qq:42", "s1")] = ("Other#1234", time.time() + 300)
    message = {
        "session_id": "s1",
        "platform": "qq",
        "is_mentioned": True,
        "processed_plain_text": "@Bot 2",
        "message_info": {
            "user_info": {"user_id": "42"},
            "group_info": {"group_id": "g"},
        },
        "raw_message": [{"type": "at", "data": {"target_user_nickname": "Bot"}}],
    }
    result = await plugin.shortcuts(message=message)
    assert (
        result["modified_kwargs"]["message"]["processed_plain_text"]
        == "/单局详细 2 Other#1234"
    )
    await plugin.command(
        stream_id="s1",
        platform="qq",
        user_id="42",
        group_id="g",
        text="@Bot /绑定 Hero#123",
        message=message,
    )
    assert plugin.store.get("bindings", "qq:42") == "Hero#123"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("绑定 Hero#1234", "绑定"),
        ("今日总结", "今日总结"),
        ("单局详细 1", "单局详细"),
        ("／今日总结", "今日总结"),
    ],
)
def test_commands_without_slash(text, expected):
    assert commands.parse(text)[0] == expected
    assert commands.parse("今天想看看今日总结呢") is None


@pytest.mark.asyncio
async def test_basic_auth_covers_image_health_and_download(setup):
    plugin, calls, sends = setup
    headers = []

    async def protected(request):
        headers.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization") != "Basic dGVzdDpwYXNz":
            return web.Response(status=401)
        if request.path == "/healthz":
            return web.json_response({"ok": True})
        return web.Response(body=PNG)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", protected)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    client = client_mod.OverstatsClient(url, username="test", password="pass")
    empty = client_mod.OverstatsClient(url)
    try:
        assert await client.image("/card") == PNG
        assert "true" in await client.health()
        assert await client.reply_image("/card.png") == PNG
        assert headers == ["Basic dGVzdDpwYXNz"] * 3
        with pytest.raises(client_mod.BackendError, match="验证"):
            await empty.image("/card")
    finally:
        await client.close()
        await empty.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_whitelist_review_limits_and_prompt(setup):
    plugin, calls, sends = setup
    config = plugin.get_plugin_config_data()
    config["ai"].update(match_review=True, review_daily_limit=1)
    config["chat"]["daily_first_prompt_only"] = True
    plugin.set_plugin_config(config)
    for _ in range(2):
        await command(plugin, "单局详细 1 Self#1234")
        await drain(plugin)
    queries = [data for path, data in calls if path.endswith("/detail/replies")]
    assert [q["analyze"] for q in queries] == [True, False]
    assert len([t for t in texts(sends) if t.startswith("⏳")]) == 1
    config["access"]["user_whitelist"] = ["qq:42"]
    plugin.set_plugin_config(config)
    await command(plugin, "单局详细 1 Self#1234")
    await drain(plugin)
    assert [data for path, data in calls if path.endswith("/detail/replies")][-1][
        "analyze"
    ] is True
    await command(plugin, "维护 测试维护", user="admin")
    await command(plugin, "商店")
    await drain(plugin)
    assert calls[-1][0].endswith("/ow-shop/image")


@pytest.mark.asyncio
async def test_backend_routing_and_independent_pools(setup):
    plugin, calls, sends = setup
    config = plugin.get_plugin_config_data()
    config["backend"].update(
        api_url_2="http://localhost:18081",
        shiqu_dedicated_enabled=True,
        privileged_backend_enabled=True,
        privileged_backend_url="http://localhost:18082",
        max_concurrent=0,
    )
    config["concurrency"].update(light_max=1, heavy_max=1)
    plugin.set_plugin_config(config)
    await plugin.on_config_update("self", config, "1.0.0")
    sender = plugin_mod.Sender("s", "qq", "admin")
    assert (
        plugin._select_client(sender, "是区吗").base_url
        == "http://localhost:18081/api/v2"
    )
    assert (
        plugin._select_client(sender, "是区吗结果").base_url
        == "http://localhost:18081/api/v2"
    )
    assert (
        plugin._select_client(sender, "商店").base_url
        == "http://localhost:18082/api/v2"
    )
    assert plugin._start(sender, asyncio.sleep(10), cmd="商店")
    assert plugin._start(
        plugin_mod.Sender("s", "qq", "other"), asyncio.sleep(10), cmd="开庭"
    )
    assert not plugin._start(
        plugin_mod.Sender("s", "qq", "third"), asyncio.sleep(0), cmd="商店"
    )
    await plugin._stop_jobs()
    assert plugin.pools == {"light": 0, "heavy": 0, "weekly": 0}


@pytest.mark.asyncio
async def test_ai_off_permissions_and_cooldown(setup):
    plugin, calls, sends = setup
    await command(plugin, "/是区吗 Self#1234")
    assert not calls
    config = plugin.get_plugin_config_data()
    config["ai"].update(enabled=True, normal_users=True)
    plugin.set_plugin_config(config)
    await command(plugin, "/是区吗 Self#1234")
    await drain(plugin)
    assert len(calls) == 1
    await command(plugin, "/是区吗 Self#1234")
    assert len(calls) == 1
    await command(plugin, "/是区吗结果")
    await drain(plugin)
    assert calls[-1][1] == {"bnet_id": "Self#1234", "use_db": True}
    await command(plugin, "/维护 测试", user="42")
    assert plugin.store.data["maintenance"] == ""


@pytest.mark.asyncio
async def test_no_retry_invalid_image_and_size_limit(setup):
    plugin, calls, sends = setup
    with pytest.raises(client_mod.BackendError):
        await plugin.client.image("/today/image", {})
    assert len(calls) == 1
    with pytest.raises(client_mod.BackendError, match="有效"):
        await plugin.client.image("/bad", {})
    with pytest.raises(client_mod.BackendError, match="来源"):
        await plugin.client.reply_image("https://example.com/x.png")
    plugin.client.max_bytes = 20
    with pytest.raises(client_mod.BackendError, match="限制"):
        await plugin.client.request(
            plugin.client.root_url + "/huge", absolute=True, method="GET"
        )


@pytest.mark.asyncio
async def test_admission_and_unload_cancellation(setup):
    plugin, calls, sends = setup
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    sender = plugin_mod.Sender("s1", "qq", "42", "g")
    assert plugin._start(sender, slow())
    await started.wait()
    assert not plugin._start(sender, asyncio.sleep(0))
    assert plugin.active == {"qq:42": 1}
    await plugin._stop_jobs()
    assert cancelled.is_set() and not plugin.tasks and not plugin.active


def test_sdk_component_metadata_and_sample_config():
    plugin = plugin_mod.create_plugin()
    entries = plugin.get_components()
    assert {e["name"] for e in entries} == {"overstats", "overstats_shortcuts"}
    sample = tomllib.loads((ROOT / "config.toml.example").read_text(encoding="utf-8"))
    plugin.set_plugin_config(sample)
    assert plugin.config.backend.api_url.endswith("/api/v2")
    manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sdk"]["min_version"] == "2.16.0"


@pytest.mark.asyncio
async def test_raw_jpeg_base64_and_data_uri(setup):
    plugin, calls, sends = setup
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 120
    encoded = base64.b64encode(jpeg).decode()
    assert encoded.startswith("/")
    assert await plugin.client.reply_image(encoded) == jpeg
    assert await plugin.client.reply_image({"type": "image", "base64": encoded}) == jpeg
    assert (
        await plugin.client.reply_image(
            "data:image/png;base64," + base64.b64encode(PNG).decode()
        )
        == PNG
    )
    assert not calls


def test_real_runner_import_style():
    spec = importlib.util.spec_from_file_location(
        "overstats_runner_test",
        ROOT / "plugin.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.create_plugin().config_model is module.OverstatsConfig


@pytest.mark.asyncio
async def test_guess_stages_and_reveal(setup, monkeypatch):
    plugin, calls, sends = setup
    original_sleep = asyncio.sleep

    async def fast_sleep(delay):
        await original_sleep(0)

    monkeypatch.setattr(plugin_mod.asyncio, "sleep", fast_sleep)
    await command(plugin, "/ow猜猜 头像")
    assert "s1" in plugin.guessing
    await drain(plugin)
    assert texts(sends) == ["题目一", "提示二", "答案三"]
    assert "s1" not in plugin.guessing
    assert calls[0][1] == {"question_type": "头像"}
    await command(plugin, "/ow猜猜 音乐")
    assert "s1" not in plugin.guessing


@pytest.mark.asyncio
async def test_backend_switch_cancels_active_jobs(setup):
    plugin, calls, sends = setup
    old_client = plugin.client
    config = plugin.get_plugin_config_data()
    config["backend"]["api_url"] = "http://localhost:18080/api/v2"
    plugin.set_plugin_config(config)
    await plugin.on_config_update("self", config, "1.0.0")
    assert old_client.session.closed
    assert plugin.client.base_url == config["backend"]["api_url"]


@pytest.mark.asyncio
async def test_maintenance_access_and_health(setup):
    plugin, calls, sends = setup
    await command(plugin, "/ow连接测试")
    assert not calls
    await command(plugin, "/ow连接测试", user="admin")
    await drain(plugin)
    assert calls[0][0] == "/healthz"
    await command(plugin, "/维护 正在维护", user="admin")
    await command(plugin, "/商店")
    assert len(calls) == 1
    assert texts(sends)[-1] == "正在维护"
    await command(plugin, "/绑定 Self#1234")
    assert plugin.store.get("bindings", "qq:42") == "Self#1234"
    await command(plugin, "/维护 取消", user="admin")
    config = plugin.get_plugin_config_data()
    config["access"]["allowed_groups"] = ["qq:other"]
    plugin.set_plugin_config(config)
    await command(plugin, "/商店")
    assert len(calls) == 1
