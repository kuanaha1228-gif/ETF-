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

## 构建 DMG

```bash
bash scripts/build_macos.sh
```

输出文件为 `dist/ETFPlanAssistant-Core.dmg`，其中包含标准的 `ETF Plan Assistant.app`。
当前构建未进行 Apple Developer ID 签名或公证，首次打开时可能需要在“系统设置 → 隐私与安全性”中确认。
