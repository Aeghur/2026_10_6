# AD9226、DAC904 与 MSPM0 自动调相系统

本项目实现可配置频率、相位和幅度的正弦输出，并保留FPGA内的实时锁相与
流水线硬件：

```text
AD9226 25 MSPS → FPGA 8192点可变延迟 ──────┐
T14比较器方波 → 100ms门控测频 → 32位DDS频率字 → 六位数码管(Hz)
       └→ 上升沿相位参考 → 连续相位PI-DPLL → 正弦ROM ├→ 标定映射 → DAC904
PC/Radxa → MSPM0参数转发 → 自由运行DDS频率字 ────────┘

PC 1 Mbaud ↔ MSPM0 ↔ FPGA 2 Mbaud
```

ADC分析范围为0.9～110 kHz单正弦；T14比较器测频范围为1～100 kHz。
PC不直接连接FPGA。

## 工作过程

1. PC发送目标频率、目标滞后角和幅度档位。
2. MSPM0校验参数，换算32位DDS频率字和相位字并立即转发给FPGA。
3. FPGA自由运行DDS连续输出目标正弦波。
4. MSPM0返回轻量确认帧，表示命令已经校验并转发。

MSPM0不再采集4096点数据，也不再运行FFT或计算延迟。比较器锁相DDS仍由FPGA
独立测量T14并更新频率；8192点流水线RTL也继续保留，但当前MSPM0控制主循环
不再为它自动测频和计算延迟。

默认目标为滞后90°，上电默认输出模式为比较器锁相DDS。阶段一PC界面下发自由
运行DDS参数；键盘`D`可退出自由运行并切换比较器锁相DDS的1倍频/2倍频。
延迟RAM硬件仍在DDS模式下持续写入，但MSPM0不再自动更新流水线延迟。
DDS可选择输出幅度`2/4/6/8`，分别对应
ADC跟踪到的输入峰值包络的`2/8、4/8、6/8、8/8`，默认8；流水线模式不缩放。

DDS锁相模式的频率只由FPGA的T14比较器方波决定，不使用AD9226过零点或MSPM0
FFT。FPGA以50 MHz采样T14，经两级同步和4时钟周期滤波，
每100 ms计数上升沿，按10 Hz分辨率更新DDS频率字。有效范围为1～100 kHz；
无信号或明显超出范围时DAC回到标定零点，数码管显示`000000`。六位数码管从左到右
显示整数Hz，例如10 kHz显示`010000`。ADC仍用于跟踪输出正弦的幅度。

此外保留独立的“自由运行DDS”模式。PC向MSPM0发送目标频率、滞后相位和
幅度档位后，MSPM0计算32位频率字并转发给FPGA；此模式不需要T14边沿或ADC
输入，固定满幅采用实测4 Vpp输入对应的1783 ADC峰值码。自由运行和比较器锁相
是两个独立模式，原有实时跟踪功能不受影响。

## 关键参数

| 参数 | 数值 |
|---|---:|
| ADC/DAC采样率 | 25 MHz |
| 自由DDS频率范围 | 0.9～110 kHz |
| 延迟RAM | 8192 × 12 bit |
| 最大附加延迟 | 327.64 µs |
| 110 kHz整数延迟相位步进 | 1.584° |

自由运行模式不依赖输入信号或FFT时间窗，频率精度由25 MHz FPGA时钟和32位
DDS频率字决定。轻量确认帧只确认MSPM0已将命令发往FPGA；实际模拟幅相仍应
用示波器核验。

## FPGA工程

Quartus工程为 `ads805.qpf`，器件为 `EP4CE6F17C8`。

| 文件 | 功能 |
|---|---|
| `ad9226_capture.v` | 25 MSPS ADC采集 |
| `variable_sample_delay.v` | 8192点整数可变延迟RAM |
| `comparator_frequency_meter.v` | T14同步滤波、100 ms测频和DDS频率字计算 |
| `six_digit_frequency_display.v` | 六位七段数码管扫描及Hz显示 |
| `zero_crossing_dds.v` | 比较器上升沿鉴相、自适应PI环、连续相位DDS和包络跟踪 |
| `sine_rom_1024.v` | 仿真模型及Cyclone IV M9K正弦ROM封装 |
| `sine_1024x16.mif` | 1024点Q1.15正弦表 |
| `adc_dac_voltage_mapper.v` | 零点、增益、反相和饱和映射 |
| `dac904_output.v` | DAC904锁存时序 |
| `ads805.v` | 抽取缓存、协议和数据链路顶层 |

当前电压标定参数：

```text
ADC_ZERO_CODE = 2104
DAC_ZERO_CODE = 8279
DAC_GAIN_Q16  = 270600
DAC_INVERT    = 0
```

这组参数来自2026-10-02修复DAC904模拟输出级后的实测结果：静态码
`0/8192/16383`分别输出`-950/-10/+930 mV`，三点线性拟合得到
`114.753 uV/code`和零电压码`8278.81`。ADC采样文件
`data/测量.csv`的4096点平均码为`2103.899`，因此中心码取`2104`。
2026-10-07硬件改为输入0.5倍衰减、输出5倍放大后，4 Vpp输入在2/8档的
五频点平均输出为1.1758 Vpp，因此将原系数按`1/1.1758`修正为`270600`。
修复后DAC输出随DAC码增大而升高，因此关闭数字反相。

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
| `fft_analyzer.c/.h` | 已停用的历史FFT源码，不参与Keil构建 |
| `pc_link.c/.h` | PC目标参数接收和轻量确认返回 |
| `measurement_config.h` | 频率范围、采样率、延迟深度和固定延迟 |
| `main.c` | 目标参数换算和FPGA命令转发主循环 |

`MEASUREMENT_FIXED_LATENCY_SAMPLES` 当前初值为10。它代表流水线路径从ADC
模拟输入到DAC模拟输出的等效25 MSPS采样点数。DDS路径对应顶层参数
`DDS_PIPELINE_ADVANCE_SAMPLES`，当前同样为10。DDS还针对2026-10-06的
0°相位实测数据加入板级补偿：`DDS_PHASE_CALIBRATION_LAG_CDEG=196`，
`DDS_PHASE_CALIBRATION_DELAY_SAMPLES=2`。数据覆盖1～100 kHz的21个测点，
输出超前2.1°～4.8°；补偿相当于额外滞后
`1.96° + 360° × 频率 × 2 / 25 MHz`。该简化模型在测点上的均方根
残差约0.16°，最大绝对残差约0.40°。补偿只用于DDS模式，不改变PC设定的
目标滞后角，也不改变流水线延迟模式。更换比较器或模拟链路后应重新测量。
2倍频模式会同时加倍与比较器参考边沿相关的固定相角和采样延迟补偿，但仍建议
单独进行2倍频扫频标定。

DDS只在首次捕获时对齐相位，并用幅度渐入隐藏捕获瞬态。锁定后，比较器边沿误差
只修改后续的相位步进，绝不跳变相位累加器；PI增益按实测周期点数自动缩放，
以覆盖1～100 kHz。运行中修改目标相位也会限速过渡，避免DAC码突然跳变。
T14门控测量提供锁相DDS基准频率，FPGA比较器边沿环负责连续细跟踪，因此稳定
输出不受MSPM0任务周期影响。自由运行DDS使用MSPM0发送的`phase_step`，且忽略
T14和ADC包络。

## PC界面

```powershell
python -m pip install -r requirements.txt
python scope.py
```

选择MSPM0串口，填写自由运行频率、目标滞后角和幅度，连接后点击“应用输出”。
界面显示MSPM0回传的已转发参数。也可直接运行：

```powershell
python wave_control.py --port COM18 --frequency 10000 --phase 90 --amplitude 8
```

自由运行频率范围为900～110000 Hz；`phase`沿用系统定义，正值表示输出滞后。
界面的“2倍频”会在下发前将填写频率乘2，乘2后的目标仍须不超过110 kHz。
命令采用PC协议类型`0x13`，7字节小端载荷依次为`frequency_mHz:u32`、
`phase_cdeg:u16`、`amplitude_code:u8`。幅度编码`0/1/2/3`分别表示
`8/2/4/6`档，设计成0表示满幅以保持原有模式兼容。FPGA正确接收、校验并应用
参数后，先向MSPM0返回`0x84 OUTPUT_ACK`；MSPM0只有收到该确认才向PC返回
`0x93`确认帧，8字节载荷为`frequency_mHz:u32`、`phase_cdeg:u16`、
`amplitude_code:u8`、`mode:u8`，不再返回大体积FFT结果帧。

FPGA的`0x84`确认帧格式为`A5 5A 84 seq 00 00 crc_lo crc_hi`，其中`seq`
必须与本次`0x04 SET_OUTPUT`命令一致。MSPM0等待FPGA确认失败时，向PC返回
错误帧：错误码1表示超时，2表示帧格式或序号错误，3表示CRC错误，4表示参数
超出允许频率范围。重复下发相同参数也会重新访问FPGA，不会仅凭MSPM0缓存
返回成功。

## MSPM0矩阵键盘与OLED

MSPM0每10 ms扫描并消抖矩阵键盘。OLED同步显示当前输出模式、倍频、幅度和
相位；进入相位输入模式后，最后一行显示尚未确认的角度。键盘和PC均可控制，
最后一次操作生效。

| 按键 | 功能 |
|---|---|
| `D` | 在1倍频和2倍频之间切换 |
| `2/4/6/8` | 将DDS幅度设为输入包络的2/8、4/8、6/8、8/8 |
| `*` | 进入相位输入模式并清空尚未确认的数字 |
| 数字键 | 相位输入模式下输入0～359的整数角度 |
| `A` | 相位输入模式下快捷输入0° |
| `B` | 相位输入模式下快捷输入90° |
| `#` | 确认相位；确认前不会改变输出 |
| `C` | 取消本次相位输入 |

若PC此前选择了流水线模式，按`D`或幅度键会自动切回DDS模式。示例：依次按
`*`、`1`、`8`、`0`、`#`可设置180°；依次按`*`、`B`、`#`可设置90°。
当前键盘板实测物理`*`与`1`的原始扫描码相反，固件在状态机入口统一交换这两个
扫描码；因此物理键帽仍按正常标注使用，并且三位相位中的百位`1`不会被误判为
重新进入相位输入。

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

把与ADC所采正弦对应的比较器方波接入FPGA的`PIN_T14`。比较器输出须为
3.3 V逻辑电平，且与FPGA共地；T14不能直接接模拟正弦或5 V方波。
板上数码管段线和位选已按MINI_FPGA手册表1.4分配，无需外接显示器。

| 连接 | 发送端 | 接收端 | 波特率 |
|---|---|---|---:|
| FPGA→MSPM0 | FPGA R13 | MSPM0 PB11/UART4_RX | 2 Mbaud |
| MSPM0→FPGA | MSPM0 PB10/UART4_TX | FPGA T13 | 2 Mbaud |
| MSPM0→PC | MSPM0 PB4/UART1_TX | USB-TTL RX | 1 Mbaud |
| PC→MSPM0 | USB-TTL TX | MSPM0 PB5/UART1_RX | 1 Mbaud |

MSPM0G3519的UART1位于PD0，BUSCLK为40 MHz，采用`2 + 32/64`分频和16倍
过采样得到1 Mbaud；UART4位于PD1，BUSCLK为80 MHz，采用相同分频得到
2 Mbaud。修改系统时钟或UART实例后必须重新计算这两个分频值。

协议确认由MSPM0和FPGA共同实现，因此更新时必须同时烧录本工程生成的
`KeyBoardpro/Output/evm_test.hex`和`output_files/ads805.sof`。若只更新MSPM0，
旧FPGA不会返回`0x84`，PC会收到超时错误；若只更新FPGA，旧MSPM0不会等待
端到端确认。

## 验证

```powershell
python -m unittest -v test_protocol.py

iverilog -g2012 -o tb_delay.out variable_sample_delay.v tb_variable_sample_delay.v
vvp tb_delay.out

iverilog -g2012 -o tb_dds.out sine_rom_1024.v zero_crossing_dds.v tb_zero_crossing_dds.v
vvp tb_dds.out

iverilog -g2012 -o tb_meter.out comparator_frequency_meter.v six_digit_frequency_display.v tb_comparator_frequency_meter.v
vvp tb_meter.out

iverilog -g2012 -o tb.out ad9226_capture.v variable_sample_delay.v sine_rom_1024.v zero_crossing_dds.v comparator_frequency_meter.v six_digit_frequency_display.v adc_dac_voltage_mapper.v dac904_output.v uart_tx.v uart_rx.v ads805.v tb_ads805.v
vvp tb.out
```

## 调相范围限制

“流水线延迟”模式仍受以下RAM深度限制。“比较器锁相DDS”模式以32位相位字
直接设置输出相位，不受8192点延迟深度限制，可在1～100 kHz范围设置任意
目标角。DDS必须检测到T14正向边沿且ADC幅度超过门限后才输出；未锁定或输入消失时
DAC回到标定零点，避免保持未知波形。

算法按照下式计算总延迟：

```text
目标总延迟 = (k + 目标角度/360°) / 测得频率
附加延迟   = 目标总延迟 - 固有延迟
```

选择最小的 `k` 使附加延迟非负。如果精确结果超过8191点，MSPM0设置flags bit 2，并将附加延迟安全地回到0，避免继续使用上一个频率的大延迟值。

滞后90°在0.9 kHz时约需6944个总采样点，处于支持范围；但更大的低频滞后角可能超过8192点。严格0°同相在0.9 kHz时需要接近一个完整周期，即约27778点，超出片内RAM；当前约在3.05 kHz以上才能覆盖严格0°整周期补偿。3.05 kHz以下设置0°时会退回零附加延迟，此时只剩固有链路延迟：按初值10点计算，0.9 kHz约滞后0.130°、3 kHz约滞后0.432°。若低频也必须覆盖任意相位或严格补偿到0°，需要外部RAM、降低延迟通道采样率，或改为锁相后重新合成正弦。
