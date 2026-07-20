# macOS 安装与运行

## 运行条件

- MacBook 保持开盖并连接电源；
- 屏幕可以关闭和锁定；
- 系统不能进入睡眠；
- 当前用户保持登录；
- 14:50 前后保持联网。

## 开发版安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[market,desktop]"
etf-assistant init
etf-assistant install-scheduler
```

## 检查调度

```bash
launchctl list | grep etfplanassistant
cat ~/Library/LaunchAgents/com.etfplanassistant.daily-check.plist
```

## 删除调度

```bash
etf-assistant remove-scheduler
```

正式 DMG 的安装、公证和 Gatekeeper 截图将在 UI 接入并完成原生构建后补充。

