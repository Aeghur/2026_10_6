# PC 图形波形控制

在本目录运行：

```powershell
python -m pip install -r requirements.txt
python scope.py
```

选择或输入 COM 端口，点击“连接”，填写参数后点击“应用输出”。连接不会自动下发参数。

- 正弦波、双向锯齿和固定100 Hz快降脉冲，与 Radxa 的 `set_waveform()` 使用相同接口。
- 最终频率 250～210000 Hz，支持 0.001 Hz；二倍频在 PC 上计算后下发。
- 快降脉冲固定10 ms周期，下降时间可设1～9999 us；其余时间保持负峰值，下降开始时瞬间升至正峰值，8/8档约4 Vpp。
- 滞后相位 0～359.99°；幅度为 2/4/6/8 相对峰值档位，不是伏特值。
- 串口操作在后台执行，等待确认时禁止重复操作。
- 成功状态需要 FPGA 端到端确认，并检查波形、方向、频率、相位与幅度。
- 超时或通信错误后显示未确认并断开；重新连接后可再次下发。
- 断开或关闭窗口不会停止波形；协议没有停止输出命令。

使用支持 1 Mbaud 的 **3.3 V USB-TTL**：TX 接 MSPM0 PB5，RX 接 PB4，GND 共地。
PC 严格沿用原分配：MSPM0 PB4 为 TX，PB5 为 RX。
当前固件默认保留 Radxa（HOST_UART_USE_RADXA_PINS=1）。此直连窗口仅适用于宏设为0并重新编译烧录的PC固件。
通过现有Radxa接线控制请改用 `python radxa_scope.py`，见 [网络窗口说明](RADXA_NETWORK_GUI.md)，无需改线或烧录。
Radxa 的 PB21/PB22 配置仅在该宏设为 1 时启用，不能与 PC 默认配置混用。

不依赖 T14 或 ADC 输入即可自由输出。MSPM0 和 FPGA 应配套支持当前 Q38 自由运行协议；
反向锯齿需要支持 waveform=2 的固件。实际模拟幅度、频率与相位仍用示波器测量。
详细协议见 [Radxa 波形控制协议](radxa/WAVEFORM_CONTROL_PROTOCOL.md)。

## 微小频率误差标定

当输入信号源与DDS输出存在微小频差、需要人工观察定相时，运行：

```powershell
python radxa_frequency_calibration.py
```

该窗口默认采用与 `radxa_scope.py` 相同的控制链：
`PC → SSH → Radxa → /dev/ttyS4 → MSPM0 → FPGA`。默认地址为
`192.168.137.2`，不需要PC直连USB-TTL，也不改变现有Radxa接线。若程序直接运行在
连接MSPM0串口的设备上，才使用 `python frequency_calibration.py`。

标定窗口默认不等待FPGA数字回执：Radxa确认整帧已写入 `/dev/ttyS4` 后即可进行下一次
人工微调，最终仍由操作者观察定相并点击保留。需要排查数字链路时，可在连接前勾选
“等待 FPGA 数字回执”；勾选后每次调整都会等待MSPM0/FPGA端到端确认。

连接串口后，在“输入信号源频率”中填写基准频率并点击“从信号源频率开始”。
随后选择 `1 / 0.1 / 0.01 / 0.001 Hz` 步进，用“减小/增大”逐次调整；默认只确认
Radxa串口写入完成，不等待FPGA回执。观察到定相后点击“已定相：确认并保留”，程序会
把信号源频率、最终标定频率、频差、ppm、相位和幅度追加到
`frequency_calibration.csv`，同时显示在窗口表格中。也可用“另存表格”导出本次记录。

频率计算使用十进制定点数，最小步进与协议的 `frequency_mHz` 精度一致，不会因连续
点击产生浮点累积误差。输出范围仍为250～210000 Hz；CSV使用UTF-8 BOM，可直接用
Excel打开。可用 `--table 路径.csv` 指定自动保存位置。
