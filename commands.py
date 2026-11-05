"""Command parsing and API mapping, adapted from astrbot_plugin_overstats v2.10.0."""

from dataclasses import dataclass

import re
import shlex


ALIASES = {
    "绑定": ("大神绑定",),
    "解绑": (),
    "我的绑定": (),
    "owhelp": ("ow菜单", "ow帮助", "OW帮助", "快速指南", "快捷指令"),
    "所有指令": ("别称",),
    "今日总结": ("今日", "今日数据"),
    "昨日总结": ("昨日", "昨日数据", "昨天数据"),
    "周度总结": ("本周总结", "本周数据", "本周"),
    "赛季总结": ("赛季报告", "ow赛季总结"),
    "大神数据": ("详情卡片", "战绩查询", "数据"),
    "大神对局": ("最近对局", "战绩", "对局"),
    "单局详细": ("单局", "单局详情"),
    "历史段位": ("历届段位",),
    "同玩查询": ("开黑胜率",),
    "快速强度": ("快速强度指数",),
    "竞技强度": ("竞技强度指数",),
    "快速英雄云图": ("快速云图",),
    "竞技英雄云图": ("竞技云图",),
    "威能": (),
    "ow英雄": (),
    "获取段位分布": (),
    "banpick": ("全英雄排行",),
    "商店": ("ow商店",),
    "皮肤搜索": (),
    "mappick": (),
    "ow赛事": ("赛事",),
    "ow活动": ("活动",),
    "ow更新": ("版本更新",),
    "省榜": ("排行",),
    "绝活榜": ("英雄省榜",),
    "ow百科": ("英雄百科", "ow英雄百科", "百科"),
    "英雄对比": ("ow英雄对比",),
    "ow猜猜": ("猜猜", "ow猜"),
    "是区吗": ("ow是区吗",),
    "是区吗结果": ("ow是区吗结果",),
    "开庭": ("ow开庭",),
    "开庭结果": ("ow开庭结果",),
    "ow连接测试": ("owAI检测", "AI检测"),
    "群设置": (),
    "维护": (),
    "获取图片": ("取图", "ow取图", "ow获取图片"),
}
LOOKUP = {alias: cmd for cmd, aliases in ALIASES.items() for alias in (cmd, *aliases)}
NAMES = "|".join(re.escape(s) for s in sorted(LOOKUP, key=len, reverse=True))
COMMAND_PATTERN = rf"^[/／]?(?P<command>{NAMES})(?:\s+(?P<args>[\s\S]*))?\s*$"
DIRECT_PATTERN = rf"^(?P<command>{NAMES})(?:\s+(?P<args>[\s\S]*))?\s*$"
BATTLETAG = re.compile(r"^[^\s#＃]{1,32}[#＃][0-9]{3,12}$")
MODES = {
    "快速": "quick",
    "quick": "quick",
    "竞技": "competitive",
    "competitive": "competitive",
}
MMR = dict(
    zip(
        ("青铜", "白银", "黄金", "白金", "钻石", "大师", "宗师", "英杰", "全部"),
        (
            "Bronze",
            "Silver",
            "Gold",
            "Platinum",
            "Diamond",
            "Master",
            "Grandmaster",
            "Champion",
            "all",
        ),
    )
)
HELP = """Overstats 守望先锋查询
以下所有指令均可省略开头的 /，例如直接发送 今日总结。
/绑定 Player#12345　/我的绑定　/解绑
/今日总结　/昨日总结　/周度总结　/赛季总结 [赛季]
/大神数据 [战网ID] [快速/竞技]　/大神对局 [战网ID]
/单局详细 1 [战网ID] [锐评关] [全员关]
/历史段位 [战网ID] [起始赛季] [终止赛季]
/同玩查询 [自己的ID] 对方ID
/快速强度 [战网ID] [3~12]　/竞技强度 [战网ID] [3~12]
/快速英雄云图 [战网ID] [赛季]　/竞技英雄云图 [战网ID] [赛季]
/威能 英雄　/ow英雄 英雄 [快速/竞技] [段位]
/获取段位分布 [模式] [段位]　/banpick [模式] [段位]
/商店　/ow赛事　/ow活动　/mappick　/ow更新 [latest/small/big]
/省榜 省份 tank/dps/healer/open　/绝活榜 省份 英雄 [开放]
/ow百科 英雄 [问题]　/英雄对比 [自己ID] 对方ID [英雄] [模式] [赛季]
/是区吗 [战网ID] [1~25]　/开庭 序号 [战网ID]
/是区吗结果　/开庭结果　/获取图片
/ow猜猜 [地图局部/地图模糊/威能/头像/英雄描述/技能描述/技能名/颜色]
/所有指令 查看别称；/群设置 查看本聊天设置。
省略战网ID时使用绑定；含空格的名称请用双引号包住。
AI 分析需管理员在配置中启用，功能也取决于后端版本。"""


def parse(text: str, direct: bool = False) -> tuple[str, list[str]] | None:
    match = re.fullmatch(DIRECT_PATTERN if direct else COMMAND_PATTERN, text.strip())
    if not match:
        return None
    try:
        args = shlex.split(match.group("args") or "")
    except ValueError as exc:
        raise ValueError("参数引号未闭合，请检查输入") from exc
    return LOOKUP[match.group("command")], [s.replace("＃", "#") for s in args]


def tag(value: str) -> str:
    if not BATTLETAG.fullmatch(value):
        raise ValueError("战网ID格式错误，示例：Player#12345（严格区分大小写）")
    return value.replace("＃", "#")


@dataclass
class Query:
    endpoint: str
    payload: dict
    kind: str = "image"
    timeout: int = 600


def build_query(
    cmd: str, args: list[str], bound: str, last: str = "", analyze: bool = False
) -> Query:
    args = list(args)
    mode = "competitive" if cmd in {"ow英雄", "获取段位分布", "banpick"} else "quick"
    mmr = "all"
    show_all = True
    open_queue = False
    review = analyze
    positional = []
    for arg in args:
        # Encyclopedia questions are free text; preserve words such as 快速 and 竞技.
        if cmd == "ow百科":
            positional.append(arg)
        elif arg in MODES:
            mode = MODES[arg]
        elif cmd in {"ow英雄", "获取段位分布", "banpick"} and arg in {
            *MMR,
            *MMR.values(),
        }:
            mmr = MMR.get(arg, arg)
        elif arg in {"锐评", "锐评关", "全员关", "开放", "预设"} and not (
            cmd == "省榜" and arg == "开放"
        ):
            if arg == "锐评关":
                review = False
            if arg == "全员关":
                show_all = False
            if arg == "开放":
                open_queue = True
        else:
            positional.append(arg)
    ids = [tag(s) for s in positional if "#" in s]
    numbers = [int(s) for s in positional if s.isdigit()]
    words = [s for s in positional if "#" not in s and not s.isdigit()]

    def player() -> str:
        if len(ids) > 1 or words:
            raise ValueError("参数有误，请使用战网ID、模式或数字参数；/owhelp 查看用法")
        value = ids[0] if ids else bound
        if not value:
            raise ValueError("请先 /绑定 Player#12345，或在指令后指定战网ID")
        return value

    if cmd in {"今日总结", "昨日总结", "周度总结", "赛季总结"}:
        scopes = {
            "今日总结": "today",
            "昨日总结": "yesterday",
            "周度总结": "week",
            "赛季总结": "season",
        }
        payload = {"bnet_id": player()}
        if numbers and cmd == "赛季总结":
            payload["season"] = numbers[0]
        elif numbers:
            raise ValueError("该总结不需要数字参数")
        return Query(
            f"/dashen-summary/{scopes[cmd]}/image",
            payload,
            timeout=900 if cmd == "周度总结" else 600,
        )
    if cmd in {"大神数据", "大神对局"}:
        payload = {"bnet_id": player()}
        if cmd == "大神数据":
            payload["mode"] = mode
        return Query(
            "/dashen-profile/image" if cmd == "大神数据" else "/dashen-match/image",
            payload,
        )
    if cmd == "单局详细":
        index = numbers[0] if numbers else 1
        if not 1 <= index <= 20:
            raise ValueError("单局序号须为 1~20（1 为最近一局）")
        return Query(
            "/dashen-match/detail/replies",
            {
                "bnet_id": player(),
                "index": str(index - 1),
                "limit": "20",
                "include_fight": True,
                "include_previous_season": True,
                "show_all_heroes": show_all,
                "analyze": review,
            },
            kind="replies",
        )
    if cmd == "历史段位":
        payload = {"bnet_id": player()}
        if numbers:
            payload["start_season"] = numbers[0]
        if len(numbers) > 1:
            payload["end_season"] = numbers[1]
            if numbers[1] < numbers[0]:
                raise ValueError("终止赛季不能早于起始赛季")
        return Query("/dashen-rank-history/image", payload)
    if cmd == "同玩查询" or cmd == "英雄对比":
        players = ids if len(ids) == 2 else [bound, *ids]
        if len(players) != 2 or not all(players):
            raise ValueError("请提供两个战网ID，或绑定后提供对方ID")
        if cmd == "同玩查询":
            return Query(
                "/dashen-sameplay/image",
                {"player1_bnet_id": players[0], "player2_bnet_id": players[1]},
            )
        payload = {"player1": players[0], "player2": players[1], "mode": mode}
        if words:
            payload["hero"] = " ".join(words)
        if numbers:
            payload["season"] = numbers[0]
        return Query("/dashen-hero-compare/image", payload)
    if cmd in {"快速强度", "竞技强度"}:
        payload = {"bnet_id": player(), "include_previous_season": True}
        if numbers:
            if not 3 <= numbers[0] <= 12:
                raise ValueError("强度查询局数须为 3~12")
            payload["limit"] = numbers[0]
        kind = "quick" if cmd == "快速强度" else "competitive"
        return Query(f"/dashen-{kind}-strength/image", payload)
    if cmd in {"快速英雄云图", "竞技英雄云图"}:
        payload = {
            "bnet_id": player(),
            "mode": "quick" if cmd == "快速英雄云图" else "competitive",
            "include_previous_season": True,
        }
        if numbers:
            payload["season"] = str(numbers[0])
        return Query("/dashen-hero-treemap/image", payload)
    if cmd == "威能":
        if not positional:
            raise ValueError("用法：/威能 安娜")
        return Query("/ow-hero-perk/image", {"hero": " ".join(positional)})
    if cmd in {"ow英雄", "获取段位分布", "banpick"}:
        payload = {
            "view": "history" if cmd == "ow英雄" else "ranking",
            "game_mode": mode,
            "mmr": mmr,
        }
        if cmd == "ow英雄":
            if not positional:
                raise ValueError("用法：/ow英雄 安娜 [快速/竞技] [段位]")
            payload["hero"] = " ".join(positional)
        return Query("/ow-hero-pick-rate/image", payload)
    if cmd in {"商店", "皮肤搜索", "ow赛事"}:
        # Upstream's 皮肤搜索 also returns the shop card: there is no search endpoint.
        return Query("/ow-esports/image" if cmd == "ow赛事" else "/ow-shop/image", {})
    if cmd in {"ow活动", "mappick", "ow更新"}:
        kind = positional[0] if positional else "latest"
        if cmd == "ow活动":
            kind = "big"
        if kind not in {"latest", "small", "big"}:
            raise ValueError("更新类型支持 latest / small / big")
        return Query("/patch-notes/image", {"patch_kind": kind})
    if cmd in {"省榜", "绝活榜"}:
        if len(positional) < 2:
            raise ValueError("用法：/省榜 北京 tank 或 /绝活榜 北京 安娜 [开放]")
        province, target = positional[:2]
        if cmd == "省榜":
            target = {
                "坦克": "tank",
                "重装": "tank",
                "输出": "dps",
                "辅助": "healer",
                "support": "healer",
                "开放": "open",
            }.get(target, target)
            if target not in {"tank", "dps", "healer", "open"}:
                raise ValueError("位置支持 tank / dps / healer / open")
            return Query(
                "/dashen-rank-leaderboard/image", {"province": province, "role": target}
            )
        return Query(
            "/dashen-hero-leaderboard/image",
            {
                "province": province,
                "hero": target,
                "mode": "open" if open_queue else "preset",
            },
        )
    if cmd == "ow百科":
        if not positional:
            raise ValueError("用法：/ow百科 安娜 [问题]")
        payload = {"hero": positional[0]}
        if len(positional) > 1:
            payload["question"] = " ".join(positional[1:])
        return Query("/ow-hero-wiki/image", payload)
    if cmd in {"是区吗", "开庭", "是区吗结果", "开庭结果"}:
        cached = cmd.endswith("结果")
        payload = (
            {"bnet_id": last or bound, "use_db": cached}
            if cached
            else {"bnet_id": player(), "use_db": False}
        )
        if not payload["bnet_id"]:
            raise ValueError("还没有查询记录，请先绑定并发起查询")
        if not cached and cmd == "开庭":
            if not numbers or not 1 <= numbers[0] <= 20:
                raise ValueError("用法：/开庭 1 [战网ID]（序号 1~20）")
            payload["index"] = numbers[0] - 1
        if not cached and cmd == "是区吗" and numbers:
            if not 1 <= numbers[0] <= 25:
                raise ValueError("是区吗局数须为 1~25")
            payload["match_count"] = numbers[0]
        return Query(
            "/dashen-shiqu/image"
            if cmd.startswith("是区吗")
            else "/dashen-court/image",
            payload,
            timeout=1800,
        )
    raise ValueError("未识别的查询；/owhelp 查看指令")
