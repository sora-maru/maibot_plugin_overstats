# 本地验证

从仓库根目录运行，Python >= 3.12。测试不会连接 QQ，不使用实际部署的后端或真实账号密码。

```text
python -m pip install -r requirements.txt -r tests/requirements.txt
python -m pytest
```

不配置 Host 源码时，执行 54 项插件回归测试；5 项真实 Host 检查明确跳过。覆盖绑定、查询参数、多图、缓存、Basic 认证、白名单、并发、AI 次数、猜猜、艾特和旧数据保留。

## 验证 MaiBot 1.4.7

将官方源码检出到固定版本，再指定 `MAIBOT_TEST_SOURCE`。测试中会读取并导入该目录的清单校验器、插件加载器、Runner 注册载荷构造器、Host 注册器和任务管理器，不启动完整 Bot 进程或修改主程序源码。

```powershell
git clone --depth 1 --branch v1.4.7 https://github.com/Mai-with-u/MaiBot.git ../MaiBot-1.4.7
python -m pip install "maibot-plugin-sdk==2.23.0" "aiohttp>=3.14.3,<4"
$env:MAIBOT_TEST_SOURCE = (Resolve-Path ../MaiBot-1.4.7).Path
python -m pytest
```

如果直接使用 SDK 源码，可将其根目录加入 `PYTHONPATH`，测试会自动设置本地 SDK 路径供 Runner 读取版本。

原生艾特文本处理和入站 Hook 规格注册函数依赖完整业务导入。测试按 AST 提取这两个未修改的官方函数独立调用，其余加载/注册/任务类直接导入原实现。消息对象及发送 RPC 使用测试数据；这不是 QQ 适配器或真实平台上传验收。

## 验证旧 SDK

在独立虚拟环境安装 `maibot-plugin-sdk==2.16.0` 后，清空 `MAIBOT_TEST_SOURCE` 再运行全部测试。可与旧版 aiohttp 3.13.2 / Pydantic 2.12.4 验证，避免修改实际 MaiBot 运行环境的依赖。
