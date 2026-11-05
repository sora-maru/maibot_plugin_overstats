"""Chinese configuration UI for MaiBot SDK 2.16; existing TOML keys stay valid."""

from typing import Literal

from maibot_sdk import Field, PluginConfigBase


def option(label: str, **kwargs):
    return Field(json_schema_extra={"label": label}, **kwargs)


class PluginSection(PluginConfigBase):
    """插件总开关。升级时保留配置版本和原有配置。"""

    __ui_label__ = "插件设置"
    __ui_order__ = 0
    enabled: bool = option(
        "启用插件", default=True, description="关闭后停止响应守望先锋查询。"
    )
    config_version: str = option(
        "配置版本", default="1.0.0", description="用于识别配置格式，保持默认即可。"
    )


class BackendSection(PluginConfigBase):
    """填写已经部署好的 Overstats 地址；只有一个后端时，其余地址留空。"""

    __ui_label__ = "后端连接与分流"
    __ui_order__ = 1
    api_url: str = option(
        "Overstats 后端地址",
        default="http://127.0.0.1:18080/api/v2",
        description="填写麦麦可以访问的接口地址，仅填主机端口时自动补 /api/v2。",
    )
    auth_username: str = option(
        "后端验证账号（可留空）",
        default="",
        description="用于 HTTP Basic Auth，即浏览器弹出账号密码框的验证。留空表示不启用。所有配置的后端节点共用，请只填写可信节点。",
    )
    auth_password: str = Field(
        default="",
        description="对应 HTTP Basic Auth 密码；无验证时留空。网页登录表单、验证码或 Cloudflare 验证不能仅靠此处账号密码通过。",
        json_schema_extra={"label": "后端验证密码（可留空）", "input_type": "password"},
    )
    api_url_2: str = option(
        "第二个后端地址",
        default="",
        description="可选，用于是区吗专属分流；没有第二个后端时留空。",
    )
    api_url_3: str = option(
        "第三个后端地址",
        default="",
        description="可选，用于是区吗专属分流；没有第三个后端时留空。",
    )
    shiqu_dedicated_enabled: bool = option(
        "是区吗使用专属后端",
        default=False,
        description="开启后，是区吗生成及结果取图使用下面选择的节点。",
    )
    shiqu_dedicated_address: Literal["主地址", "第二个地址", "第三个地址"] = option(
        "是区吗专属地址",
        default="第二个地址",
        description="对应地址留空时回退主地址；优先于白名单分流。",
    )
    privileged_backend_enabled: bool = option(
        "管理员和白名单使用独立后端",
        default=False,
        description="开启后，管理员、白名单个人或群的其他查询使用独立节点。",
    )
    privileged_backend_url: str = option(
        "管理员和白名单后端地址",
        default="",
        description="可选，留空回退主地址；结果取图与生成请求使用相同分流规则。",
    )
    max_response_mb: int = option(
        "单次响应大小上限（MB）",
        default=20,
        ge=1,
        le=100,
        description="超过上限的图片或响应会被拒绝，避免占用过多内存。",
    )
    request_timeout: int = option(
        "普通查询超时（秒）",
        default=600,
        ge=10,
        le=1800,
        description="普通查询最长等待时间；周度总结至少等待 900 秒。",
    )
    ai_timeout: int = option(
        "AI 分析超时（秒）",
        default=1800,
        ge=10,
        le=3600,
        description="是区吗、开庭最长等待时间；超时不自动重新生成。",
    )
    max_concurrent: int = option(
        "全部查询并发上限",
        default=4,
        ge=0,
        le=100,
        description="保留上一版设置：同时执行的全部查询上限，0 表示不限；还会受轻重指令池限制。",
    )
    per_user_max: int = option(
        "每人同时查询上限",
        default=1,
        ge=0,
        le=20,
        description="单个用户在全部聊天中的并发上限，0 表示不限。",
    )


class ConcurrencySection(PluginConfigBase):
    """并发数是同时执行的任务数量，超过上限直接提示繁忙，不排队。"""

    __ui_label__ = "查询并发与限流"
    __ui_order__ = 2
    per_user_enabled: bool = option(
        "启用每人并发限制",
        default=True,
        description="关闭后不限制单人并发，但总量和轻重指令池仍生效。",
    )
    light_max: int = option(
        "普通指令并发上限",
        default=0,
        ge=0,
        le=100,
        description="战绩、榜单等普通查询同时执行数量，0 表示不限。",
    )
    heavy_max: int = option(
        "AI 重指令并发上限",
        default=10,
        ge=0,
        le=100,
        description="仅是区吗和开庭的新生成任务，0 表示不限；结果取图属于普通指令。",
    )
    weekly_max: int = option(
        "周度总结并发上限",
        default=2,
        ge=0,
        le=100,
        description="周度总结较耗资源，额外限制同时执行数量，0 表示不限。",
    )


class ChatSection(PluginConfigBase):
    """配置指令触发和查询提示，也可由插件管理员按聊天单独设置。"""

    __ui_label__ = "指令与聊天提示"
    __ui_order__ = 3
    auto_bind: bool = option(
        "直接发送战网ID自动绑定",
        default=False,
        description="开启后，发送完整的 Player#12345 即可绑定。",
    )
    number_shortcut: bool = option(
        "数字快捷查看单局",
        default=True,
        description="查询对局后五分钟内，发送 1~20 可查看上次查询玩家的对应单局。",
    )
    shortcuts_require_mention: bool = option(
        "群聊快捷入口必须艾特机器人",
        default=False,
        description="限制直接发送战网ID和数字快捷入口；完整命令带不带 / 都可直接识别。",
    )
    nickname: str = option(
        "文字艾特昵称",
        default="",
        description="可选。支持“@此昵称 今日总结”；留空只识别宿主标记的真实艾特。",
    )
    status_prompt: bool = option(
        "显示开始查询提示",
        default=True,
        description="查询开始时发送等待提示；可用 /群设置 提示 开|关 单独覆盖。",
    )
    daily_first_prompt_only: bool = option(
        "每天只显示首次等待提示",
        default=False,
        description="同一用户在同一聊天每天只提示一次；失败后下次查询仍会提示。",
    )
    error_append_hint: bool = option(
        "错误后追加操作提示",
        default=False,
        description="在错误信息后追加查看帮助或补取图片的提示。",
    )
    guess_interval: int = option(
        "猜猜提示间隔（秒）",
        default=15,
        ge=1,
        le=60,
        description="猜猜每阶段之间以及揭晓答案前的等待时间。",
    )
    cache_minutes: int = option(
        "图片缓存有效期（分钟）",
        default=30,
        ge=1,
        le=1440,
        description="/获取图片 可补取最近一组图片，缓存按用户和聊天隔离。",
    )
    max_cache_mb: int = option(
        "图片缓存总量上限（MB）",
        default=100,
        ge=20,
        le=1000,
        description="临时图片磁盘占用上限，超限时清理旧文件。",
    )
    save_image_locally: bool = option(
        "保存图片到本地以便补取",
        default=True,
        description="关闭后直接发送内存中的图片，减少磁盘写入，但新图片无法用 /获取图片 补取。",
    )


class AccessSection(PluginConfigBase):
    """管理员可以修改聊天设置、维护状态并检测连接。白名单没有管理权限。"""

    __ui_label__ = "管理员、白名单与访问范围"
    __ui_order__ = 4
    admin_users: list[str] = option(
        "插件管理员",
        default_factory=list,
        description="每项填写 平台:用户ID，例如 qq:123456789；同时信任麦麦的本地操作员。",
    )
    user_whitelist: list[str] = option(
        "个人白名单",
        default_factory=list,
        description="例如 qq:123456789；可绕过维护限制，使用 AI 白名单冷却并免单局锐评每日限次。",
    )
    group_whitelist: list[str] = option(
        "群白名单",
        default_factory=list,
        description="例如 qq:987654321；群内成员享有白名单查询待遇，不会获得管理权限。",
    )
    allowed_groups: list[str] = option(
        "允许使用的群",
        default_factory=list,
        description="每项填写 平台:群ID，例如 qq:987654321；留空允许所有群，白名单也受此范围限制。",
    )
    allow_private: bool = option(
        "允许私聊查询", default=True, description="关闭后所有私聊都不响应，包括管理员。"
    )


class AISection(PluginConfigBase):
    """AI 凭据与模型仍由已有 Overstats 后端管理，这里只控制使用权限和次数。"""

    __ui_label__ = "AI 分析与每日次数"
    __ui_order__ = 5
    enabled: bool = option(
        "启用是区吗和开庭",
        default=False,
        description="总开关。开启后管理员与白名单可用，普通用户还需要下方开关。",
    )
    normal_users: bool = option(
        "允许普通用户使用 AI 分析",
        default=False,
        description="仅控制是区吗和开庭；单局锐评由下方独立开关控制。",
    )
    cooldown_seconds: int = option(
        "普通用户冷却时间（秒）",
        default=14400,
        ge=0,
        le=604800,
        description="普通用户每类 AI 生成成功后的冷却时间，默认四小时；0 表示不限。",
    )
    whitelist_cooldown_seconds: int = option(
        "白名单冷却时间（秒）",
        default=1800,
        ge=0,
        le=604800,
        description="白名单每类 AI 生成成功后的冷却时间，默认半小时。",
    )
    admin_cooldown_seconds: int = option(
        "管理员冷却时间（秒）",
        default=0,
        ge=0,
        le=604800,
        description="管理员每类 AI 分析的冷却时间，默认 0 不限。",
    )
    match_count: int = option(
        "是区吗默认分析局数",
        default=12,
        ge=1,
        le=25,
        description="未指定局数时发送给后端的局数；更多局数可能增加后端 AI 用量。",
    )
    custom_count_privileged_only: bool = option(
        "仅管理员和白名单可自定义局数",
        default=True,
        description="普通用户指定局数时忽略该数字，仍使用默认局数。",
    )
    match_review: bool = option(
        "启用单局 AI 锐评",
        default=False,
        description="开启后单局详细会请求后端锐评；追加“锐评关”可按次关闭。",
    )
    review_limit_enabled: bool = option(
        "限制普通用户每日锐评次数",
        default=True,
        description="超过次数后仍生成单局图片，但不再调用 AI 锐评；管理员和白名单不限。",
    )
    review_daily_limit: int = option(
        "每日锐评次数上限",
        default=3,
        ge=0,
        le=1000,
        description="每个普通用户每天允许请求锐评的次数；0 表示普通用户只出图不锐评。",
    )
    review_reset_hour: int = option(
        "每日次数重置时刻（北京时间）",
        default=4,
        ge=0,
        le=23,
        description="按北京时间每天此整点开始新一天，默认凌晨四点。",
    )


class OverstatsConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    backend: BackendSection = Field(default_factory=BackendSection)
    concurrency: ConcurrencySection = Field(default_factory=ConcurrencySection)
    chat: ChatSection = Field(default_factory=ChatSection)
    access: AccessSection = Field(default_factory=AccessSection)
    ai: AISection = Field(default_factory=AISection)
