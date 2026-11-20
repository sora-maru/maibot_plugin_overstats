"""Account-aware mentions and compatibility with the retained SDK 2.16 API."""

from test_overstats import command, drain, plugin_mod

import asyncio

import pytest


def at(target, nickname="Bot", cardname=""):
    return {
        "type": "at",
        "data": {
            "target_user_id": target,
            "target_user_nickname": nickname,
            "target_user_cardname": cardname,
        },
    }


def payload(text, segments):
    return {
        "session_id": "s1",
        "platform": "qq",
        "account_id": "bot-1",
        "scope": "group",
        "processed_plain_text": text,
        "is_mentioned": True,
        "raw_message": segments,
        "message_info": {
            "user_info": {"user_id": "42"},
            "group_info": {"group_id": "g"},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,segments",
    [
        ("@Bot @Bot", [at("bot-1"), at("bot-1")]),
        (
            "@Group Card @Nickname",
            [at("bot-1", "Nickname", "Group Card"), at("bot-1", "Nickname")],
        ),
        ("@Bot", [{"type": "text", "data": "  "}, at("bot-1")]),
    ],
)
async def test_multiple_self_mentions(setup, prefix, segments):
    plugin, calls, sends = setup
    msg = payload(
        prefix + " /绑定 Hero#123",
        [*segments, {"type": "text", "data": " /绑定 Hero#123"}],
    )
    result = await plugin.shortcuts(message=msg)
    cleaned = result["modified_kwargs"]["message"]["processed_plain_text"]
    assert cleaned == "/绑定 Hero#123"
    assert result["modified_kwargs"]["message"]["raw_message"] == msg["raw_message"]
    # The Command RPC may still carry the original display text in MaiBot 1.4.7.
    await plugin.command(
        stream_id="s1",
        platform="qq",
        user_id="42",
        group_id="g",
        text=msg["processed_plain_text"],
        message=msg,
        matched_groups={"command": "绑定", "args": "Hero#123"},
    )
    assert plugin.store.get("bindings", "qq:42") == "Hero#123"
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,segments",
    [
        ("@Other @Bot 绑定 Hero#123", [at("other", "Other"), at("bot-1")]),
        ("@Bot @Other 绑定 Hero#123", [at("bot-1"), at("other", "Other")]),
        ("@Bot 绑定 Hero#123", [at("bot-2")]),
    ],
)
async def test_other_account_mentions_stay_in_text(setup, text, segments):
    plugin, calls, sends = setup
    msg = payload(text, segments)
    assert await plugin.shortcuts(message=msg) == {"action": "continue"}
    await plugin.command(
        stream_id="s1",
        platform="qq",
        user_id="42",
        group_id="g",
        text=text,
        message=msg,
    )
    assert plugin.store.get("bindings", "qq:42") is None
    assert not calls


@pytest.mark.asyncio
async def test_account_id_identifies_self_without_flags(setup):
    plugin, calls, sends = setup
    msg = payload("@Bot 绑定 Hero#123", [at("bot-1")])
    msg["is_mentioned"] = False
    result = await plugin.shortcuts(message=msg)
    assert (
        result["modified_kwargs"]["message"]["processed_plain_text"] == "绑定 Hero#123"
    )


@pytest.mark.asyncio
async def test_legacy_state_is_retained(setup):
    plugin, calls, sends = setup
    await command(plugin, "绑定 Hero#123")
    await plugin.store.put("chats", "s1", {"prompt": False})
    await plugin.store.put("last_targets", "qq:42:是区吗", "Other#456")
    config = plugin.get_plugin_config_data()
    ctx = plugin.ctx
    await plugin.on_unload()
    restored = plugin_mod.create_plugin()
    restored.set_plugin_config(config)
    restored._set_context(ctx)
    await restored.on_load()
    try:
        await command(restored, "大神数据")
        await drain(restored)
        assert calls[-1][1]["bnet_id"] == "Hero#123"
        assert restored.store.get("chats", "s1") == {"prompt": False}
        assert restored.store.get("last_targets", "qq:42:是区吗") == "Other#456"
    finally:
        await restored.on_unload()


@pytest.mark.asyncio
async def test_cancel_before_first_task_step_releases_slots(setup):
    plugin, calls, sends = setup
    assert plugin._start(
        plugin_mod.Sender("s1", "qq", "42", "g"), asyncio.sleep(30), cmd="是区吗"
    )
    await plugin._stop_jobs()
    assert not plugin.active and not plugin.ai_executing
    assert plugin.pools == {"light": 0, "heavy": 0, "weekly": 0}
