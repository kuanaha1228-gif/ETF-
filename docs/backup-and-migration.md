# 备份与迁移

## 完整导出

```bash
etf-assistant export ~/Desktop/etf-assistant.etfbak
etf-assistant validate-backup ~/Desktop/etf-assistant.etfbak
```

迁移包包含计划、策略版本、历史事件、通知状态、执行记录和非敏感设置。SMTP 密码和微信 Webhook 不会导出。

## 整库恢复

适合 B 环境为空或完全替换：

```bash
etf-assistant restore ~/Desktop/etf-assistant.etfbak
```

恢复前会备份 B 环境原数据库。校验或替换失败时不会破坏原数据。

## 合并导入

保留本机冲突配置：

```bash
etf-assistant merge ~/Desktop/etf-assistant.etfbak --conflict local
```

采用导入包中的冲突配置：

```bash
etf-assistant merge ~/Desktop/etf-assistant.etfbak --conflict import
```

UUID 相同的事件只导入一次。通知历史不会重新发送，已触发档位能够继续衔接。

## 导入后

在新设备重新配置：

- macOS/Windows 桌面通知权限；
- SMTP 密码或授权码；
- 微信 Webhook；
- 14:50 系统调度任务。

