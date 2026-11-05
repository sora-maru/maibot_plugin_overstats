"""MaiBot 1.3.16 / SDK 2.16.0 Overstats bridge.

Original feature/API mapping: Zzzzzzouhang/astrbot_plugin_overstats (AGPL-3.0).
Port: 2026-11-05. All backend deployment/management code is intentionally absent.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncio
import base64
import contextvars
import logging
import random
import re
import time
import uuid

import aiohttp
from maibot_sdk import Command, HookHandler, MaiBotPlugin

from .client import BackendError, OverstatsClient, normalize_url
from .config import OverstatsConfig
from .mentions import strip_bot_mention
from .commands import ALIASES, BATTLETAG, COMMAND_PATTERN, HELP, build_query, parse, tag
from .state import StateStore

logger = logging.getLogger(__name__)
_request_client = contextvars.ContextVar("overstats_client", default=None)


@dataclass(frozen=True)
class Sender:
    stream: str
    platform: str
    user: str
    group: str = ""
    local_operator: bool = False

    @property
    def key(self):
        return f"{self.platform}:{self.user}"

    @property
    def cache_key(self):
        return self.key, self.stream


class OverstatsPlugin(MaiBotPlugin):
    config_model = OverstatsConfig

    def __init__(self):
        super().__init__()
        self.client = None
        self.clients = {}
        self.pools = {"light": 0, "heavy": 0, "weekly": 0}
        self.prompt_days = {}
        self.ai_executing = set()
        self.tasks = set()
        self.active = {}
        self.cache = {}
        self.recent = {}
        self.guessing = set()
        self._cache_lock = asyncio.Lock()

    async def on_load(self):
        self.store = StateStore(self.ctx.paths.data_dir)
        await self.store.load()
        self.cache_dir = self.ctx.paths.runtime_dir / "images"
        await asyncio.to_thread(self.cache_dir.mkdir, parents=True, exist_ok=True)
        self._configure_clients()
        await self._prune_cache()

    async def _stop_jobs(self):
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.active.clear()
        self.guessing.clear()
        self.ai_executing.clear()
        self.pools = {"light": 0, "heavy": 0, "weekly": 0}

    async def on_unload(self):
        await self._stop_jobs()
        for client in self.clients.values():
            await client.close()

    async def on_config_update(self, scope: str, config_data: dict, version: str):
        if scope != "self":
            return
        urls = self._backend_urls()
        if not self.config.plugin.enabled:
            await self._stop_jobs()
        if set(urls) != set(self.clients) or any(
            c.max_bytes != self.config.backend.max_response_mb * 1024 * 1024
            or c.auth_credentials
            != (self.config.backend.auth_username, self.config.backend.auth_password)
            for c in self.clients.values()
        ):
            # Cancel active jobs before closing the shared client on a backend switch.
            await self._stop_jobs()
            for client in self.clients.values():
                await client.close()
            self._configure_clients()
        else:
            self.client = self.clients[urls[0]]

    @property
    def client(self):
        return _request_client.get() or self._client

    @client.setter
    def client(self, value):
        self._client = value

    def _backend_urls(self):
        backend = self.config.backend
        normalize_url(backend.api_url)
        if ":" in backend.auth_username:
            raise ValueError("后端验证账号不能包含冒号")
        return [
            normalize_url(url)
            for url in (
                backend.api_url,
                backend.api_url_2,
                backend.api_url_3,
                backend.privileged_backend_url,
            )
            if url.strip()
        ]

    def _configure_clients(self):
        backend = self.config.backend
        self.clients = {
            url: OverstatsClient(
                url,
                backend.max_response_mb,
                username=backend.auth_username,
                password=backend.auth_password,
            )
            for url in dict.fromkeys(self._backend_urls())
        }
        self.client = self.clients[normalize_url(self.config.backend.api_url)]

    def _select_client(self, sender, cmd):
        backend = self.config.backend
        url = backend.api_url
        if backend.privileged_backend_enabled and self._privileged(sender):
            url = backend.privileged_backend_url.strip() or url
        if cmd in {"是区吗", "是区吗结果"} and backend.shiqu_dedicated_enabled:
            url = {
                "主地址": backend.api_url,
                "第二个地址": backend.api_url_2,
                "第三个地址": backend.api_url_3,
            }[backend.shiqu_dedicated_address].strip() or backend.api_url
        return self.clients[normalize_url(url)]

    def _admin(self, sender: Sender):
        return sender.local_operator or sender.key in self.config.access.admin_users

    def _privileged(self, sender):
        return (
            self._admin(sender)
            or sender.key in self.config.access.user_whitelist
            or bool(
                sender.group
                and f"{sender.platform}:{sender.group}"
                in self.config.access.group_whitelist
            )
        )

    def _cooldown(self, sender):
        if self._admin(sender):
            return self.config.ai.admin_cooldown_seconds
        if self._privileged(sender):
            return self.config.ai.whitelist_cooldown_seconds
        return self.config.ai.cooldown_seconds

    def _day(self, reset_hour=0):
        return (
            (datetime.now(timezone(timedelta(hours=8))) - timedelta(hours=reset_hour))
            .date()
            .isoformat()
        )

    def _allowed(self, sender: Sender):
        access = self.config.access
        return (
            (
                not access.allowed_groups
                or f"{sender.platform}:{sender.group}" in access.allowed_groups
            )
            if sender.group
            else access.allow_private
        )

    async def _text(self, sender: Sender, text: str):
        settings = self.store.get("chats", sender.stream, {})
        if text.startswith("❌") and settings.get(
            "error_hint", self.config.chat.error_append_hint
        ):
            text += "\n发送 owhelp 查看用法；已生成的图片可用 获取图片 补取。"
        sent = await self.ctx.send.text(text, sender.stream)
        if not sent:
            raise RuntimeError("MaiBot 文本发送失败")

    @Command(
        "overstats", description="守望先锋 Overstats 查询", pattern=COMMAND_PATTERN
    )
    async def command(self, stream_id: str = "", **kwargs):
        if not self.config.plugin.enabled:
            return True, "", 0
        sender = Sender(
            stream_id,
            str(kwargs.get("platform") or ""),
            str(kwargs.get("user_id") or ""),
            str(kwargs.get("group_id") or ""),
            kwargs.get("is_local_operator") is True,
        )
        if not sender.stream or not sender.user or not sender.platform:
            return False, "缺少宿主消息身份，无法查询", 2
        if not self._allowed(sender):
            return True, "", 2
        text = str(kwargs.get("text") or "")
        text, _ = strip_bot_mention(
            text, kwargs.get("message") or {}, self.config.chat.nickname
        )
        # Host 1.3.16 supplies both text and named regex groups.
        groups = kwargs.get("matched_groups") or {}
        if not text and groups.get("command"):
            text = "/" + groups["command"] + " " + str(groups.get("args") or "")
        await self._dispatch(sender, text)
        return True, "", 2

    @HookHandler(
        "chat.receive.after_process",
        name="overstats_shortcuts",
        description="OW 可选直发指令与数字快捷查询",
    )
    async def shortcuts(self, message=None, stream_id: str = "", **kwargs):
        if not self.config.plugin.enabled or not isinstance(message, dict):
            return {"action": "continue"}
        # 1.3.16 no longer dispatches ON_MESSAGE. Rewrite through the live receive Hook;
        # the host then registers the session before entering the normal Command chain.
        info = message.get("message_info") or {}
        user = info.get("user_info") or {}
        group = info.get("group_info") or {}
        sender = Sender(
            str(message.get("session_id") or stream_id),
            str(message.get("platform") or ""),
            str(user.get("user_id") or ""),
            str(group.get("group_id") or ""),
        )
        text = str(message.get("processed_plain_text") or "").strip()
        text, stripped_mention = strip_bot_mention(
            text, message, self.config.chat.nickname
        )
        if (
            not sender.stream
            or not sender.user
            or not sender.platform
            or not self._allowed(sender)
        ):
            return {"action": "continue"}
        rewritten = ""
        if stripped_mention and re.fullmatch(COMMAND_PATTERN, text):
            rewritten = text
        elif (
            self.config.chat.shortcuts_require_mention
            and sender.group
            and not (
                stripped_mention or message.get("is_mentioned") or message.get("is_at")
            )
        ):
            return {"action": "continue"}
        elif self.config.chat.auto_bind and BATTLETAG.fullmatch(text):
            rewritten = "/绑定 " + text
        elif (
            self.config.chat.number_shortcut
            and re.fullmatch(r"(?:[1-9]|1[0-9]|20)", text)
            and sender.stream not in self.guessing
        ):
            recent = self.recent.get(sender.cache_key)
            if recent and recent[1] > time.time():
                rewritten = f"/单局详细 {text} {recent[0]}"
        if not rewritten:
            return {"action": "continue"}
        updated = dict(message)
        updated["processed_plain_text"] = rewritten
        return {"action": "continue", "modified_kwargs": {"message": updated}}

    @staticmethod
    def _direct_pattern():
        from .commands import DIRECT_PATTERN

        return DIRECT_PATTERN

    async def _dispatch(self, sender: Sender, text: str):
        try:
            parsed = parse(text)
            if not parsed:
                return
            cmd, args = parsed
            if cmd in {"owhelp", "所有指令"}:
                content = (
                    HELP
                    if cmd == "owhelp"
                    else "\n".join(
                        f"/{c}：{'、'.join(a) or '无别称'}" for c, a in ALIASES.items()
                    )
                )
                await self._text(sender, content)
                return
            if cmd in {"绑定", "解绑", "我的绑定"}:
                if cmd == "绑定":
                    if len(args) != 1:
                        raise ValueError("用法：/绑定 Player#12345")
                    value = tag(args[0])
                    await self.store.put("bindings", sender.key, value)
                    await self._text(sender, f"已绑定战网账号：{value}")
                elif cmd == "解绑":
                    await self.store.put("bindings", sender.key, None)
                    await self._text(sender, "已解除绑定")
                else:
                    await self._text(
                        sender,
                        "当前绑定："
                        + self.store.get("bindings", sender.key, "尚未绑定"),
                    )
                return
            if cmd == "群设置":
                settings = dict(self.store.get("chats", sender.stream, {}))
                if args:
                    if not self._admin(sender):
                        raise ValueError(
                            "更改聊天设置需要插件管理员权限（access.admin_users）"
                        )
                    if (
                        len(args) != 2
                        or args[0] not in {"提示", "首次提示", "追加提示"}
                        or args[1] not in {"开", "关"}
                    ):
                        raise ValueError(
                            "用法：群设置 提示|首次提示|追加提示 开|关；所有指令已默认支持省略 /"
                        )
                    settings[
                        {
                            "提示": "prompt",
                            "首次提示": "daily_prompt",
                            "追加提示": "error_hint",
                        }[args[0]]
                    ] = args[1] == "开"
                    await self.store.put("chats", sender.stream, settings)
                await self._text(
                    sender,
                    f"本聊天：指令可省略 /；等待提示={'开' if settings.get('prompt', self.config.chat.status_prompt) else '关'}；每天首次提示={'开' if settings.get('daily_prompt', self.config.chat.daily_first_prompt_only) else '关'}；追加提示={'开' if settings.get('error_hint', self.config.chat.error_append_hint) else '关'}",
                )
                return
            if cmd == "维护":
                if not self._admin(sender):
                    raise ValueError("维护命令需要插件管理员权限")
                if not args:
                    raise ValueError("用法：/维护 内容 或 /维护 取消")
                content = "" if args == ["取消"] else " ".join(args)
                await self.store.put("maintenance", None, content)
                await self._text(
                    sender, "维护已关闭" if not content else "维护已开启：" + content
                )
                return
            if cmd == "获取图片":
                self._start(sender, self._cached(sender))
                return
            if self.store.data["maintenance"] and not self._privileged(sender):
                await self._text(sender, self.store.data["maintenance"])
                return
            if cmd == "ow连接测试":
                if not self._admin(sender):
                    raise ValueError("连接测试需要插件管理员权限")
                self._start(sender, self._health(sender))
                return
            if cmd == "ow猜猜":
                if args and " ".join(args) not in {
                    "地图局部",
                    "地图模糊",
                    "威能",
                    "头像",
                    "英雄描述",
                    "技能描述",
                    "技能名",
                    "颜色",
                }:
                    raise ValueError(
                        "支持题型：地图局部、地图模糊、威能、头像、英雄描述、技能描述、技能名、颜色；音频题型暂不支持"
                    )
                if sender.stream in self.guessing:
                    raise ValueError("本聊天已有猜猜正在进行")
                if self._start(sender, self._guess(sender, args)):
                    self.guessing.add(sender.stream)
                return
            base_cmd = cmd.removesuffix("结果")
            if base_cmd in {"是区吗", "开庭"}:
                if not self.config.ai.enabled or not (
                    self.config.ai.normal_users or self._privileged(sender)
                ):
                    raise ValueError("AI 分析尚未启用或仅对插件管理员开放")
                until = self.store.get("cooldowns", sender.key + ":" + base_cmd, 0)
                if not cmd.endswith("结果") and until > time.time():
                    raise ValueError(
                        f"该 AI 分析冷却中，剩余 {int(until - time.time()) + 1} 秒"
                    )
                if not cmd.endswith("结果") and sender.key in self.ai_executing:
                    raise ValueError("你已有一条是区吗或开庭正在执行，请等待完成")
                if cmd == "是区吗":
                    if (
                        self.config.ai.custom_count_privileged_only
                        and not self._privileged(sender)
                        and any(a.isdigit() for a in args)
                    ):
                        args = [a for a in args if not a.isdigit()]
                        await self._text(
                            sender,
                            "自定义局数仅对管理员和白名单开放，本次使用默认局数。",
                        )
                    if not any(a.isdigit() for a in args):
                        args = [*args, str(self.config.ai.match_count)]
            bound = self.store.get("bindings", sender.key, "")
            last = self.store.get("last_targets", sender.key + ":" + base_cmd, "")
            query = build_query(cmd, args, bound, last, self.config.ai.match_review)
            query.timeout = (
                self.config.backend.ai_timeout
                if base_cmd in {"是区吗", "开庭"}
                else max(
                    self.config.backend.request_timeout, 900 if cmd == "周度总结" else 0
                )
            )
            self._start(sender, self._query(sender, cmd, query), cmd=cmd)
        except (ValueError, BackendError) as exc:
            await self._text(sender, "❌ " + str(exc))

    def _start(self, sender: Sender, work, cmd=""):
        # Reserve slots synchronously, before creating the task, to avoid admission races.
        pool = "heavy" if cmd in {"是区吗", "开庭"} else "light"
        backend, limits = self.config.backend, self.config.concurrency
        pool_limit = limits.heavy_max if pool == "heavy" else limits.light_max
        if (
            backend.max_concurrent
            and sum(self.active.values()) >= backend.max_concurrent
            or limits.per_user_enabled
            and backend.per_user_max
            and self.active.get(sender.key, 0) >= backend.per_user_max
            or pool_limit
            and self.pools[pool] >= pool_limit
            or cmd == "周度总结"
            and limits.weekly_max
            and self.pools["weekly"] >= limits.weekly_max
        ):
            work.close()
            # This task is short lived and must also be cancelled on unload.
            task = asyncio.create_task(
                self._text(sender, "⏳ 查询繁忙或你已有查询正在执行，请稍后再试")
            )
            admitted = False
        else:
            self.active[sender.key] = self.active.get(sender.key, 0) + 1
            self.pools[pool] += 1
            if cmd == "周度总结":
                self.pools["weekly"] += 1
            if pool == "heavy":
                self.ai_executing.add(sender.key)
            task = asyncio.create_task(
                self._run(sender, work, self._select_client(sender, cmd))
            )
            admitted = True
        self.tasks.add(task)
        if admitted:

            def release(done):
                work.close()
                count = self.active.get(sender.key, 1) - 1
                if count:
                    self.active[sender.key] = count
                else:
                    self.active.pop(sender.key, None)
                self.pools[pool] -= 1
                if cmd == "周度总结":
                    self.pools["weekly"] -= 1
                if pool == "heavy":
                    self.ai_executing.discard(sender.key)

            task.add_done_callback(release)
        task.add_done_callback(self._finished)
        return admitted

    def _finished(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Overstats 任务失败：%s", task.exception())

    async def _run(self, sender, work, client):
        token = _request_client.set(client)
        try:
            await work
        except asyncio.CancelledError:
            raise
        except (BackendError, ValueError) as exc:
            await self._text(sender, "❌ " + str(exc))
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Overstats 网络连接或超时错误", exc_info=True)
            await self._text(
                sender,
                "❌ 后端连接失败或请求超时，请检查 backend.api_url；AI 请求不会自动重试，可稍后用结果指令取图",
            )
        except Exception:
            logger.exception("Overstats 查询或发送失败")
            await self._text(
                sender,
                "❌ 查询或发送失败，请查看 MaiBot 插件日志；已缓存图片可用 /获取图片 补取",
            )
        finally:
            if hasattr(work, "close"):
                work.close()
            _request_client.reset(token)

    async def _query(self, sender, cmd, query):
        settings = self.store.get("chats", sender.stream, {})
        daily_prompt = settings.get(
            "daily_prompt", self.config.chat.daily_first_prompt_only
        )
        day = self._day()
        prompt_token = (day, uuid.uuid4().hex)
        prompt_needed = settings.get("prompt", self.config.chat.status_prompt) and (
            not daily_prompt
            or self.prompt_days.get(sender.cache_key, (None,))[0] != day
        )
        if prompt_needed:
            await self._text(sender, f"⏳ 正在查询{cmd}，完成后会自动发送。")
            if daily_prompt:
                self.prompt_days = {
                    k: v for k, v in self.prompt_days.items() if v[0] == day
                }
                self.prompt_days[sender.cache_key] = prompt_token
        try:
            await self._perform_query(sender, cmd, query)
        except BaseException:
            if (
                prompt_needed
                and daily_prompt
                and self.prompt_days.get(sender.cache_key) == prompt_token
            ):
                self.prompt_days.pop(sender.cache_key, None)
            raise

    async def _perform_query(self, sender, cmd, query):
        ai = self.config.ai
        if (
            cmd == "单局详细"
            and query.payload["analyze"]
            and ai.review_limit_enabled
            and not self._privileged(sender)
        ):
            allowed = await self.store.reserve_review(
                sender.key, self._day(ai.review_reset_hour), ai.review_daily_limit
            )
            if not allowed:
                query.payload["analyze"] = False
                await self._text(
                    sender, "今日 AI 锐评次数已用完，本次仅生成单局图片，不调用锐评。"
                )
        if cmd == "皮肤搜索":
            await self._text(
                sender,
                "当前上游接口提供完整商店卡片，请在卡片中查看皮肤；不支持按关键词筛选。",
            )
        if cmd in {"是区吗", "开庭"}:
            await self.store.put(
                "last_targets", sender.key + ":" + cmd, query.payload["bnet_id"]
            )
        if query.kind == "replies":
            data = await self.client.post_json(
                query.endpoint, query.payload, query.timeout
            )
            replies = data.get("replies")
            if not isinstance(replies, list) or not replies:
                raise BackendError("单局详细未返回图片")
            await self._replies(sender, replies, cache=True)
        else:
            try:
                image = await self.client.image(
                    query.endpoint, query.payload, query.timeout
                )
            except BackendError as exc:
                if cmd == "今日总结" and exc.code == "summary_empty":
                    await self._text(sender, "今日无对局，尝试查询昨日总结…")
                    image = await self.client.image(
                        "/dashen-summary/yesterday/image", query.payload, query.timeout
                    )
                elif cmd == "ow活动" and exc.status in {404, 422}:
                    image = await self.client.image(
                        "/patch-notes/image", {"patch_kind": "latest"}
                    )
                else:
                    raise
            # Record successful AI generation before platform sending; a send failure must not re-generate.
            if cmd in {"是区吗", "开庭"}:
                await self.store.put(
                    "cooldowns",
                    sender.key + ":" + cmd,
                    time.time() + self._cooldown(sender),
                )
            await self._images(sender, [image])
        if cmd == "大神对局":
            self.recent[sender.cache_key] = (
                query.payload["bnet_id"],
                time.time() + 300,
            )

    async def _health(self, sender):
        await self._text(sender, "Overstats /healthz：\n" + await self.client.health())

    async def _replies(self, sender, replies, cache=False):
        images = []
        texts = []
        for item in replies[:30]:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("data") or item.get("text") or ""))
            elif (
                isinstance(item, str)
                or isinstance(item, dict)
                and (
                    item.get("type") == "image"
                    or any(
                        k in item
                        for k in ("url", "image", "src", "path", "file", "base64")
                    )
                )
            ):
                images.append(await self.client.reply_image(item))
        if not images and not any(texts):
            raise BackendError("后端未返回可发送的文本或图片")
        if cache and not images:
            raise BackendError("后端未返回单局图片")
        if images:
            await self._images(sender, images)
        for text in texts:
            if text:
                await self._text(sender, text[:4000])

    async def _images(self, sender, images):
        if not self.config.chat.save_image_locally:
            self.cache.pop(sender.cache_key, None)
            for image in images:
                if not await self.ctx.send.image(
                    base64.b64encode(image).decode("ascii"), sender.stream
                ):
                    raise RuntimeError("MaiBot 图片发送失败")
            return
        async with self._cache_lock:
            await self._images_locked(sender, images)

    async def _images_locked(self, sender, images):
        await self._prune_cache()
        paths = []
        for image in images:
            path = self.cache_dir / (uuid.uuid4().hex + ".img")
            await asyncio.to_thread(path.write_bytes, image)
            paths.append(path)
        self.cache[sender.cache_key] = (
            time.time() + self.config.chat.cache_minutes * 60,
            paths,
        )
        await self._send_paths(sender, paths)
        await self._prune_cache()

    async def _send_paths(self, sender, paths):
        for path in paths:
            data = await asyncio.to_thread(path.read_bytes)
            sent = await self.ctx.send.image(
                base64.b64encode(data).decode("ascii"), sender.stream
            )
            if not sent:
                raise RuntimeError("MaiBot 图片发送失败")

    async def _cached(self, sender):
        async with self._cache_lock:
            await self._prune_cache()
            cached = self.cache.get(sender.cache_key)
            if (
                not cached
                or cached[0] <= time.time()
                or not await asyncio.to_thread(
                    lambda: all(p.exists() for p in cached[1])
                )
            ):
                raise ValueError(
                    "本聊天没有你的有效缓存，请重新查询；缓存索引在插件重启后失效"
                )
            await self._send_paths(sender, cached[1])

    async def _prune_cache(self):
        cutoff = time.time() - self.config.chat.cache_minutes * 60
        limit = self.config.chat.max_cache_mb * 1024 * 1024

        def prune():
            files = sorted(
                ((p, p.stat()) for p in self.cache_dir.glob("*.img")),
                key=lambda v: v[1].st_mtime,
            )
            size = sum(s.st_size for _, s in files)
            for path, stat in files:
                if stat.st_mtime < cutoff or size > limit:
                    path.unlink(missing_ok=True)
                    size -= stat.st_size

        # Called on load or while holding _cache_lock, including through image sends.
        await asyncio.to_thread(prune)
        now = time.time()
        self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
        self.recent = {k: v for k, v in self.recent.items() if v[1] > now}

    async def _guess(self, sender, args):
        pool = (
            "地图局部",
            "地图模糊",
            "威能",
            "头像",
            "英雄描述",
            "技能描述",
            "技能名",
            "颜色",
        )
        kind = " ".join(args) if args else random.choice(pool)
        self.guessing.add(sender.stream)
        try:
            data = await self.client.post_json(
                "/ow-guess/replies", {"question_type": kind}, timeout=120
            )
            stages = data.get("stages") or [{"replies": data.get("replies", [])}]
            for index, stage in enumerate(stages[:8]):
                if index:
                    await asyncio.sleep(self.config.chat.guess_interval)
                await self._replies(sender, stage.get("replies") or [])
            await asyncio.sleep(self.config.chat.guess_interval)
            await self._replies(sender, data.get("reveal_replies") or [])
        finally:
            self.guessing.discard(sender.stream)


def create_plugin() -> OverstatsPlugin:
    return OverstatsPlugin()
