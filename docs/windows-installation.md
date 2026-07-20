# Windows 安装与运行

## 开发版安装

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[market,desktop]"
etf-assistant init
etf-assistant install-scheduler
```

## 检查任务

```powershell
schtasks /Query /TN "ETF Plan Assistant Daily Check" /V /FO LIST
```

任务设置为工作日14:50运行、需要网络、允许唤醒、最长5分钟。同一时间只允许一个实例；若睡眠后15:00才启动，程序会拒绝生成当天投入提醒。

## 删除任务

```powershell
etf-assistant remove-scheduler
```

正式 EXE 安装和 SmartScreen 说明将在 UI 接入并完成签名方案后补充。

