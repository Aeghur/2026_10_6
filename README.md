# AD9226、DAC904 与 MSPM0 测量系统

本项目实现以下固定链路：

```text
AD9226 25 MSPS → Cyclone IV FPGA → MSPM0G3519 → PC
                         ↓
                    DAC904 25 MSPS
```

PC不再直接连接FPGA。FPGA只负责实时采样、ADC到DAC映射和有限缓存；MSPM0负责协议桥接、FFT计算和结果上传；PC负责显示时域、频谱和三个峰值。

## 当前功能

- AD9226以25 MSPS连续采样，FPGA读取12位直二进制码和OTR。
- FPGA丢弃上电后的前8个流水线字。
- ADC数据经过零点、Q16增益、反相和饱和处理后实时送往14位DAC904。
- FPGA每16点保留1点，缓存4096点，等效采样率1.5625 MS/s。
- MSPM0校验FPGA帧和CRC，执行4096点Q15实数FFT及Hann窗处理。
- MSPM0在10～500 kHz内搜索三个谱峰并进行抛物线插值。
- PC界面显示4096点时域波形、0～500 kHz频谱、三个峰值、OTR、DAC削顶和CRC状态。
- FPGA通信超时、格式错误或CRC错误会由MSPM0立即返回错误帧。

## 工程组成

### FPGA

Quartus工程：`ads805.qpf`，目标器件：`EP4CE6F17C8`。

| 文件 | 功能 |
|---|---|
| `ad9226_capture.v` | 生成25 MHz采样时钟并读取ADC |
| `adc_dac_voltage_mapper.v` | ADC到DAC定点标定映射 |
| `dac904_output.v` | DAC904数据与锁存时序 |
| `uart_rx.v`、`uart_tx.v` | 2 Mbaud FPGA/MSPM0串口 |
| `ads805.v` | 4096点抽取缓存、命令解析和帧发送 |

当前标定参数为：

```text
ADC_ZERO_CODE = 2100
DAC_ZERO_CODE = 7237
DAC_GAIN_Q16  = 495241
DAC_INVERT    = 1
```

必须在Quartus中重新编译后再下载 `output_files/ads805.sof`。仓库现有SOF生成于本次2 Mbaud修改之前，不能用于新的MSPM0链路。

### MSPM0

Keil工程位于：

```text
KeyBoardpro/Project/empty.uvprojx
```

新增的应用文件：

| 文件 | 功能 |
|---|---|
| `msp_uart_link.c/.h` | 初始化UART1和UART4及对应引脚 |
| `fpga_capture.c/.h` | 请求FPGA采集、接收4096点并检查CRC |
| `fft_analyzer.c/.h` | Hann窗、4096点Q15 FFT和三峰插值 |
| `pc_link.c/.h` | 接收PC命令并返回结果或错误帧 |
| `measurement_config.h` | 采样率、FFT点数和频谱范围 |
| `main.c` | PC请求→FPGA采集→FFT→PC结果主流程 |

工程已经加入CMSIS-DSP头文件路径和Cortex-M0+预编译库。固件只进入普通SLEEP，不使用会关闭SRAM的深度低功耗模式。

### PC

安装依赖并启动：

```powershell
python -m pip install -r requirements.txt
python scope.py
```

选择连接MSPM0 PB4/PB5的USB-TTL串口，点击“连接”和“单次采集”。串口参数为1,000,000 baud、8N1。

原来的 `fpga_raw_scope.py` 和PC直连FPGA的 `0x02/0x82` 原始采集接口已经删除。

## 接线

所有数字设备使用3.3 V电平并共地。

| 连接 | 发送端 | 接收端 | 波特率 |
|---|---|---|---:|
| FPGA→MSPM0 | FPGA R13 / TX | MSPM0 PB11 / UART4_RX | 2 Mbaud |
| MSPM0→FPGA | MSPM0 PB10 / UART4_TX | FPGA T13 / RX | 2 Mbaud |
| MSPM0→PC | MSPM0 PB4 / UART1_TX | USB-TTL RX | 1 Mbaud |
| PC→MSPM0 | USB-TTL TX | MSPM0 PB5 / UART1_RX | 1 Mbaud |

AD9226和DAC904的详细引脚分配以 `ads805.qsf` 为准。AD9226的AVDD接5 V、DRVDD接3.3 V，MODE必须配置为直二进制输出。

## 测试

Python协议测试：

```powershell
python -m unittest -v test_protocol.py
```

FPGA顶层仿真：

```powershell
iverilog -g2012 -o tb.out ad9226_capture.v adc_dac_voltage_mapper.v dac904_output.v uart_tx.v uart_rx.v ads805.v tb_ads805.v
vvp tb.out
```

完整协议定义和排错方式见 `FFT_README.md`。

## 已知限制

- 16倍抽取前没有数字抗混叠FIR，输入存在高频分量时可能发生混叠。
- 当前是PC触发的单次4096点测量，不支持连续流式传输。
- 电压标定针对现有模拟前端和高阻示波器负载，硬件条件变化后需重新运行 `voltage_calibration.py`。
- PC端显示ADC码和相对频谱幅值，尚未把ADC码自动换算为绝对电压。
