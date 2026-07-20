# 通知配置

## 桌面通知

默认开启。首次运行后在系统通知设置中允许 ETF Plan Assistant 显示通知。锁屏可运行，但电脑不得睡眠。

## 邮件

非敏感配置进入 SQLite：

- `notification.email.enabled=true`
- `smtp.host`
- `smtp.port`
- `smtp.username`
- `smtp.sender`
- `smtp.recipient`
- `smtp.use_ssl`

密码使用交互式命令写入系统凭证库：

```bash
etf-assistant set-secret smtp.password
```

不要把密码作为命令行参数传递，以免进入 shell 历史。

## 微信

V1 支持企业微信机器人兼容 Webhook。系统不模拟登录个人微信，也不保存微信密码。

```bash
etf-assistant set-secret wechat.webhook
```

启用 `notification.wechat.enabled=true` 后，先执行测试消息再启用14:50任务。

## 送达规则

任一渠道发送成功，事件状态变为 `NOTIFIED` 并占用本周提醒次数。用户不需要回复邮件或微信；实际执行状态保持 `UNKNOWN`，但不影响后续判断。

