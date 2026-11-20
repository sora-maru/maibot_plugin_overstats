"""Optional tests against unmodified MaiBot source, not a simulated Host.

Set MAIBOT_TEST_SOURCE to a MaiBot 1.4.7 checkout. No bot credentials, QQ
connection or live Overstats backend are needed. The SDK send RPC is simulated.
"""

from pathlib import Path
from types import SimpleNamespace

import ast
import __future__
import asyncio
import importlib
import importlib.metadata
import os
import tomllib

import pytest
from test_compat import at, payload
from test_overstats import ROOT, plugin_mod

SOURCE = os.environ.get("MAIBOT_TEST_SOURCE", "")
pytestmark = pytest.mark.skipif(
    not SOURCE, reason="set MAIBOT_TEST_SOURCE for real Host tests"
)


def source_function(path, name, namespace):
    """Execute a selected, unchanged function without booting the whole bot."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    exec(
        compile(module, str(path), "exec", flags=__future__.annotations.compiler_flag),
        namespace,
    )
    return namespace[name]


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = Path(SOURCE).resolve()
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    validator_module = importlib.import_module(
        "src.plugin_runtime.runner.manifest_validator"
    )
    components = importlib.import_module("src.plugin_runtime.host.component_registry")
    hooks = importlib.import_module("src.plugin_runtime.host.hook_spec_registry")
    loader = importlib.import_module("src.plugin_runtime.runner.plugin_loader")
    tasks = importlib.import_module("src.plugin_runtime.runner.task_registry")
    sdk_path = Path(importlib.import_module("maibot_sdk").__file__).resolve().parents[1]
    sdk_version = (
        tomllib.loads((sdk_path / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["version"]
        if (sdk_path / "pyproject.toml").exists()
        else importlib.metadata.version("maibot-plugin-sdk")
    )
    if (sdk_path / "pyproject.toml").exists():
        monkeypatch.setenv("MAIBOT_PLUGIN_SDK_PATH", str(sdk_path))
    runner = importlib.import_module("src.plugin_runtime.runner.runner_main")
    return SimpleNamespace(
        root=root,
        version=metadata["project"]["version"],
        sdk_version=sdk_version,
        validator=validator_module.ManifestValidator,
        Registry=components.ComponentRegistry,
        HookSpecRegistry=hooks.HookSpecRegistry,
        HookSpec=hooks.HookSpec,
        Loader=loader.PluginLoader,
        Runner=runner.PluginRunner,
        Tasks=tasks.PluginTaskRegistry,
    )


def validator(host, host_version=None):
    return host.validator(
        host_version=host_version or host.version,
        sdk_version=host.sdk_version,
        project_root=host.root,
        log_errors=False,
    )


@pytest.mark.parametrize("version", ["1.3.16", "1.4.7"])
def test_manifest_with_real_host_validator(host, version):
    check = validator(host, version)
    manifest = check.load_from_plugin_path(ROOT)
    assert manifest is not None, check.errors
    assert not check.errors and not check.warnings
    assert manifest.id == "local.overstats" and manifest.plugin_type == "game"


@pytest.mark.asyncio
async def test_real_host_loader_and_hook_registration(host):
    check = validator(host)
    manifest = check.load_from_plugin_path(ROOT)
    assert manifest is not None, check.errors
    loader = host.Loader(host_version=host.version)
    meta = loader.load_candidate(manifest.id, (ROOT, manifest, ROOT / "plugin.py"))
    assert meta is not None
    instance = meta.instance
    instance.set_plugin_config(instance.build_default_config())
    assert instance.config.backend.api_url.endswith("/api/v2")
    runner = host.Runner.__new__(host.Runner)
    declaration = await runner._build_plugin_registration_payload(meta)
    specs = host.HookSpecRegistry()
    schema = importlib.import_module("src.plugin_runtime.hook_schema_utils")
    register = source_function(
        host.root / "src/chat/message_receive/bot.py",
        "register_chat_hook_specs",
        {"HookSpec": host.HookSpec, "build_object_schema": schema.build_object_schema},
    )
    register(specs)
    registry = host.Registry(hook_spec_registry=specs)
    assert (
        registry.register_plugin_components(
            meta.plugin_id, declaration.model_dump()["components"]
        )
        == 2
    )
    assert meta.component_handlers == {
        "overstats": "command",
        "overstats_shortcuts": "shortcuts",
    }
    for text in ("绑定 Hero#123", "/绑定 Hero#123", "今日总结", "单局详细 1"):
        match = registry.find_command_by_text(text)
        assert match is not None
        assert match[0].plugin_id == "local.overstats"
    assert registry.find_command_by_text("日常聊天中提到今日总结") is None


@pytest.mark.asyncio
async def test_host_native_mention_matching_and_plugin_rpc(setup, host):
    plugin, calls, sends = setup

    class At:
        def __init__(self, target, name):
            self.target_user_id = target
            self.target_user_nickname = name
            self.target_user_cardname = ""

    class Text:
        def __init__(self, text):
            self.text = text

    native = source_function(
        host.root / "src/chat/message_receive/message.py",
        "get_command_text",
        {"AtComponent": At, "TextComponent": Text},
    )
    text = "@Group Card @Bot 绑定 Hero#123"
    display = SimpleNamespace(
        processed_plain_text=text,
        account_id="bot-1",
        raw_message=SimpleNamespace(
            components=[
                At("bot-1", "Group Card"),
                At("bot-1", "Bot"),
                Text(" 绑定 Hero#123"),
            ]
        ),
    )
    normalized = native(display)
    assert normalized == "绑定 Hero#123"
    registry = host.Registry()
    check = validator(host)
    manifest = check.load_from_plugin_path(ROOT)
    loader = host.Loader(host_version=host.version)
    meta = loader.load_candidate(manifest.id, (ROOT, manifest, ROOT / "plugin.py"))
    meta.instance = plugin
    runner = host.Runner.__new__(host.Runner)
    declaration = await runner._build_plugin_registration_payload(meta)
    registry.register_plugin_components(
        "local.overstats", declaration.model_dump()["components"]
    )
    _, groups = registry.find_command_by_text(normalized)
    original = payload(
        text,
        [
            at("bot-1", "Group Card"),
            at("bot-1"),
            {"type": "text", "data": " 绑定 Hero#123"},
        ],
    )
    # This matches the Host's RPC contract: original text plus normalized captures.
    await plugin.command(
        stream_id="s1",
        platform="qq",
        user_id="42",
        group_id="g",
        text=text,
        message=original,
        matched_groups=groups,
    )
    assert plugin.store.get("bindings", "qq:42") == "Hero#123"
    assert display.processed_plain_text == text


@pytest.mark.asyncio
async def test_host_task_registry_cancels_background_query(setup, host):
    plugin, calls, sends = setup
    registry = host.Tasks()
    registry.activate("local.overstats")
    with registry.task_factory():
        with registry.scope("local.overstats"):
            assert plugin._start(
                plugin_mod.Sender("s1", "qq", "42", "g"),
                asyncio.sleep(30),
                cmd="是区吗",
            )
        registry.begin_stop(["local.overstats"])
        await registry.cancel_and_wait("local.overstats")
        await asyncio.sleep(0)
    assert not plugin.tasks and not plugin.active and not plugin.ai_executing
    assert plugin.pools == {"light": 0, "heavy": 0, "weekly": 0}
