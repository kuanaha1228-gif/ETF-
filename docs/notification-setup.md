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

可直接在桌面应用“系统设置”中填写并发送测试邮件。授权码会写入系统凭证库，也可使用交互式命令配置：

```bash
etf-assistant set-secret smtp.password
```

不要把密码作为命令行参数传递，以免进入 shell 历史。

## 送达规则

任一渠道发送成功，事件状态变为 `NOTIFIED` 并占用本周提醒次数。用户不需要回复邮件；实际执行状态保持 `UNKNOWN`，但不影响后续判断。
