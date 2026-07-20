# 开发指南

## 架构

```text
UI（待接入）
  ↓
应用服务：DailyCheckService / BackupService
  ↓
领域层：Plan / Strategy / StrategyLevel / TriggerDecision
  ↓
SQLite Repository

外部能力全部通过 providers 适配：
MarketProvider / NotificationProvider / CredentialStore / SchedulerProvider
```

UI 不直接访问 AKShare、SMTP、Webhook 或 SQLite 表。业务计算必须通过领域层和应用服务完成。

## 运行测试

```bash
python -m unittest discover -s tests -v
```

测试不得请求真实行情、发送真实邮件或微信消息。使用 `StaticMarketProvider` 和 `RecordingNotifier`。

## 数据库规则

- 使用 UUID 作为跨环境稳定主键；
- 金额、倍数和百分比以十进制字符串保存，禁止用浮点数；
- 所有历史事件保存策略和金额快照；
- 修改策略创建新版本，不更新旧版本；
- 数据库连接必须及时关闭，保证 Windows 可以备份和替换文件。

## 新增行情源

实现：

```python
class MarketProvider(Protocol):
    def latest_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    def completed_closes(self, symbol: str, count: int = 20) -> list[Decimal]: ...
```

业务代码不得依赖行情源特有字段。

## 新增通知渠道

实现：

```python
class NotificationProvider(Protocol):
    name: str
    def send(self, title: str, body: str) -> None: ...
```

每个渠道独立记录结果；单渠道失败不得阻止其他渠道。

