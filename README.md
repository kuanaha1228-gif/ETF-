# ETF Plan Assistant

本地运行的 ETF 定投与回撤提醒工具。它使用场内 ETF 行情作为信号，为对应的场外联接基金计算用户自己预设的计划金额；系统只提醒，不自动交易。

当前仓库已包含可运行的 PySide6 桌面界面，以及 Windows EXE 和 macOS DMG 自动构建流程。

## 已实现

- 每只基金独立配置回撤档位、补仓倍数和执行方式；
- 稳健低波、核心宽基、高波宽基、行业主题和完全自定义初始化模板；
- 策略版本和“立即生效/下一回撤周期生效”；
- 短期 20 日回撤与固定 `cycle_peak` 本轮回撤双基准；
- 持续补仓默认最多 4 周/4 倍，达到上限后暂停并等待用户确认；
- 同日幂等、同周提醒限制和通知送达闭环；
- SQLite 本地存储；
- macOS 桌面通知、Windows Toast 和可配置的 SMTP 邮件；
- `.etfbak` 完整导出、校验、恢复和幂等合并；
- macOS `launchd` 与 Windows 任务计划定义；
- AKShare 行情适配器和可替换接口；
- 自动化测试。
- 简约桌面界面：今日概览、计划管理、自定义策略、提醒历史、迁移和通知开关。
- 提醒历史支持记录已执行、未执行、忽略和延迟，并保留原始行情与金额快照。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate        # macOS
# .venv\Scripts\activate         # Windows PowerShell
python -m pip install -e ".[dev,market,desktop]"
python -m unittest discover -s tests -v
```

启动桌面界面：

```bash
etf-assistant-ui
# 或：python -m etf_assistant.gui
```

## 命令行快速验证

```bash
etf-assistant init

etf-assistant add-plan \
  --name "A500" \
  --purchase-code 022459 \
  --signal-code 159361 \
  --base-amount 600

etf-assistant list-plans
etf-assistant export ~/Desktop/etf-backup.etfbak
etf-assistant validate-backup ~/Desktop/etf-backup.etfbak
```

自定义恒生科技档位可传入 JSON：

```json
{
  "levels": [
    {"threshold": 12, "multiplier": 1.5},
    {"threshold": 20, "multiplier": 2.25}
  ]
}
```

```bash
etf-assistant add-plan \
  --name "恒生科技" \
  --purchase-code 012349 \
  --signal-code 513180 \
  --base-amount 300 \
  --strategy-json hengsheng-tech.json
```

## 文档

- [完整产品需求文档](docs/PRD.md)
- [开发指南](docs/development.md)
- [macOS 部署](docs/macos-installation.md)
- [Windows 部署](docs/windows-installation.md)
- [通知配置](docs/notification-setup.md)
- [备份与迁移](docs/backup-and-migration.md)
- [故障排查](docs/troubleshooting.md)

## 安全边界

- 不连接支付宝或券商交易账户；
- 不保存邮箱密码或授权码到 SQLite 或迁移包；
- 密钥进入 macOS Keychain 或 Windows Credential Manager；
- 场内 ETF 行情只用于规则计算，不代表场外基金最终净值；
- 不保证收益，不构成投资建议。
