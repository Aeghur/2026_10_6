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
