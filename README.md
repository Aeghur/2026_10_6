# AD9226、DAC904 与 MSPM0 自动调相系统

本项目实现单正弦信号的自动频率跟踪和可配置滞后相位输出：

```text
AD9226 25 MSPS → FPGA抽取采集 → MSPM0单峰FFT
       │                              ↓ 频率、目标相位
       └→ FPGA 8192点可变延迟 → 标定映射 → DAC904 25 MSPS

PC 1 Mbaud ↔ MSPM0 ↔ FPGA 2 Mbaud
```

输入范围限定为1～100 kHz单正弦。PC不直接连接FPGA。

## 工作过程

1. MSPM0后台持续请求FPGA采集。
2. FPGA将25 MSPS数据每64点保留1点，返回4096点。
3. MSPM0执行Hann窗4096点Q15 FFT，在1～100 kHz内选择最强单峰并做三点抛物线插值。
4. MSPM0根据频率、目标滞后角和固定链路延迟计算附加延迟点数。
5. MSPM0通过 `0x03` 命令更新FPGA延迟RAM。
6. FPGA继续以25 MSPS输出延迟后的信号，不因FFT和串口传输而中断。
7. PC请求测量时，MSPM0返回最近一次完整结果。

默认目标为滞后90°，PC界面可设置 `0～359.99°`。

## 关键参数

| 参数 | 数值 |
|---|---:|
| ADC/DAC采样率 | 25 MHz |
| FFT抽取倍数 | 64 |
| FFT采样率 | 390625 S/s |
| FFT长度 | 4096 |
| 频率分辨率 | 约95.37 Hz |
| 分析范围 | 1～100 kHz |
| 延迟RAM | 8192 × 12 bit |
| 最大附加延迟 | 327.64 µs |
| 100 kHz整数延迟相位步进 | 1.44° |

FFT插值用于提高稳定单频信号的频率估计精度。由于抽取前没有抗混叠滤波，本方案只适用于已经限定为1～100 kHz单正弦的输入。

当前上板测试的已知问题：输入频率改变并重新锁定后，输出相位仍可能存在小于3°的残余误差。误差主要来自25 MHz整数采样点量化、FFT频率估计偏差、模拟链路群延迟和示波器测量波动，后续可通过分频段相位补偿表进一步校准。

## FPGA工程

Quartus工程为 `ads805.qpf`，器件为 `EP4CE6F17C8`。

| 文件 | 功能 |
|---|---|
| `ad9226_capture.v` | 25 MSPS ADC采集 |
| `variable_sample_delay.v` | 8192点整数可变延迟RAM |
| `adc_dac_voltage_mapper.v` | 零点、增益、反相和饱和映射 |
| `dac904_output.v` | DAC904锁存时序 |
| `ads805.v` | 抽取缓存、协议和数据链路顶层 |

当前电压标定参数：

```text
ADC_ZERO_CODE = 2104
DAC_ZERO_CODE = 8279
DAC_GAIN_Q16  = 318171
DAC_INVERT    = 0
```

这组参数来自2026-10-02修复DAC904模拟输出级后的实测结果：静态码
`0/8192/16383`分别输出`-950/-10/+930 mV`，三点线性拟合得到
`114.753 uV/code`和零电压码`8278.81`。ADC采样文件
`data/测量.csv`的4096点平均码为`2103.899`，因此中心码取`2104`。
10 kHz下输入、输出原Vpp分别为`491.8 mV`和`765.5 mV`，故按
`495241 * 491.8 / 765.5`将Q16增益修正为`318171`。修复后DAC输出
随DAC码增大而升高，因此关闭数字反相。

注意：`data/测量.csv`本身含约20.027 kHz的明显交流分量（码值范围
1647～2558），并非静止的无输入波形；这里只利用覆盖多个周期后的平均值
标定直流中心。若要进一步压低零点误差，应断开信号源和DAC回授后重新采集。

修改后必须重新执行Quartus全编译。仓库旧的 `output_files/ads805.sof` 不包含自动调相功能，不能继续使用。

## MSPM0工程

Keil工程：`KeyBoardpro/Project/empty.uvprojx`。

| 文件 | 功能 |
|---|---|
| `msp_uart_link.c/.h` | 初始化PC UART1和FPGA UART4 |
| `fpga_capture.c/.h` | FPGA采集与延迟配置命令 |
| `fft_analyzer.c/.h` | 单峰FFT频率估计 |
| `pc_link.c/.h` | PC采集请求、目标相位设置和结果返回 |
| `measurement_config.h` | 频率范围、采样率、延迟深度和固定延迟 |
| `main.c` | 自动跟踪和延迟计算主循环 |

`MEASUREMENT_FIXED_LATENCY_SAMPLES` 当前初值为10。它代表未启用附加延迟时，从ADC模拟输入到DAC模拟输出的等效25 MSPS采样点数。首次上板必须用示波器测量后修正该值，否则目标相位会存在固定偏差。

## PC界面

```powershell
python -m pip install -r requirements.txt
python scope.py
```

选择MSPM0串口，填写目标滞后角，连接后点击“单次采集”。界面显示：

- 4096点抽取时域波形；
- 0～100 kHz频谱；
- 自动测得的单频频率；
- OTR、DAC削顶、CRC和调相范围状态。

独立DPLL/DDS工程建议改用实时诊断脚本，它不会显示占位波形，而是直接读取
FPGA的锁定状态和环路相位误差：

```powershell
python dpll_control.py --port COM6 --phase 0
```

将`COM6`替换为实际USB-TTL串口；用`--phase 90`、`180`、`270`分别测试
相位控制，按`Ctrl+C`退出。该诊断命令要求烧录
`dpll_dds_fpga/mspm0/Keil_DPLL/Output/dpll_dds_mspm0.hex`。

## 接线

所有数字设备使用3.3 V并共地。

| 连接 | 发送端 | 接收端 | 波特率 |
|---|---|---|---:|
| FPGA→MSPM0 | FPGA R13 | MSPM0 PB11/UART4_RX | 2 Mbaud |
| MSPM0→FPGA | MSPM0 PB10/UART4_TX | FPGA T13 | 2 Mbaud |
| MSPM0→PC | MSPM0 PB4/UART1_TX | USB-TTL RX | 1 Mbaud |
| PC→MSPM0 | USB-TTL TX | MSPM0 PB5/UART1_RX | 1 Mbaud |

## 验证

```powershell
python -m unittest -v test_protocol.py

iverilog -g2012 -o tb_delay.out variable_sample_delay.v tb_variable_sample_delay.v
vvp tb_delay.out

iverilog -g2012 -o tb.out ad9226_capture.v variable_sample_delay.v adc_dac_voltage_mapper.v dac904_output.v uart_tx.v uart_rx.v ads805.v tb_ads805.v
vvp tb.out
```

## 调相范围限制

算法按照下式计算总延迟：

```text
目标总延迟 = (k + 目标角度/360°) / 测得频率
附加延迟   = 目标总延迟 - 固有延迟
```

选择最小的 `k` 使附加延迟非负。如果精确结果超过8191点，MSPM0设置flags bit 2，并将附加延迟安全地回到0，避免继续使用上一个频率的大延迟值。

滞后90°在1 kHz时约需6250个总采样点，处于支持范围。严格0°同相在1 kHz时需要接近一个完整周期，即约25000点，超出片内RAM；当前约在3.05 kHz以上才能覆盖严格0°整周期补偿。3.05 kHz以下设置0°时会退回零附加延迟，此时只剩固有链路延迟：按初值10点计算，1 kHz约滞后0.144°、3 kHz约滞后0.432°。若低频也必须严格补偿到0°，需要外部RAM、降低延迟通道采样率，或改为锁相后重新合成正弦。
