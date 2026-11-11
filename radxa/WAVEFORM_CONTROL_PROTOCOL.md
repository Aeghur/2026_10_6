# Radxa → MSPM0 → FPGA 波形控制协议

本文档是给运行在Radxa ROCK 4D上的控制程序或AI使用的独立接口说明。控制程序
只与MSPM0通信，不直接访问FPGA。MSPM0只有在FPGA已经接收并应用参数后才返回
成功确认，因此收到成功帧代表整条`Radxa → MSPM0 → FPGA`数字控制链已完成。

## 推荐接入方式

将以下三个文件放在Radxa应用目录中：

- `protocol.py`：二进制帧、CRC和响应解析。
- `msp_wave_link.py`：线程安全的串口控制类。
- `wave_control.py`：命令行测试工具。

业务程序应保持一个长连接，不要为每一帧图像反复打开串口：

```python
from msp_wave_link import (
    MSPM0CommandError,
    MSPM0LinkError,
    MSPM0TimeoutError,
    MSPM0WaveLink,
)

with MSPM0WaveLink("/dev/ttyS4", timeout=1.0) as waveform:
    ack = waveform.set_waveform(
        waveform="sawtooth",   # "sine"或"sawtooth"
        frequency_hz=10_000.0, # 250.0～210000.0
        phase_degrees=0.0,     # 0.0～359.99，正值表示滞后
        amplitude=4,           # 2、4、6、8四档
        direction="falling",  # rising=缓升快降；falling=缓降快升
    )
    print(ack)
```

`set_waveform()`是新代码应使用的统一接口。历史接口`set_target()`仍可输出正弦，
但不能选择锯齿。`set_sawtooth()`是锯齿输出的快捷封装。

命令行端到端测试：

```bash
cd ~/vision_sorter
source .venv/bin/activate
python wave_control.py \
  --port /dev/ttyS4 \
  --waveform sawtooth \
  --direction falling \
  --frequency 10000 \
  --phase 0 \
  --amplitude 8
```

## 串口与接线

串口参数为`1,000,000 baud, 8 data bits, no parity, 1 stop bit`，无硬件流控。
电平为3.3 V TTL，发送线交叉连接并共地：

| Radxa ROCK 4D | MSPM0G3519 | 方向 |
|---|---|---|
| PIN 11 / UART4_TX_M1 | PB22 / UART1_RX | Radxa → MSPM0 |
| PIN 15 / UART4_RX_M1 | PB21 / UART1_TX | MSPM0 → Radxa |
| GND | GND | 共地 |

Linux设备节点为`/dev/ttyS4`。运行用户必须属于`dialout`组。不要让串口终端、
`cat`、另一个Python进程或systemd服务同时打开这个设备；协议要求一个进程独占
串口。PySerial会设置termios，不需要提前运行`stty`。

## 工程单位与取值

| 字段 | 单位/范围 | 编码 |
|---|---|---|
| `waveform` | 波形类型 | `0=正弦`，`1=缓升快降锯齿`，`2=缓降快升锯齿`，`3=100 Hz快降脉冲` |
| `frequency_mHz` | 250000～210000000 mHz | 目标Hz × 1000，四舍五入 |
| `phase_cdeg` | 0～35999 cdeg | 目标度数 × 100，四舍五入 |
| `amplitude_code` | 2/4/6/8档 | `0=8/8`，`1=2/8`，`2=4/8`，`3=6/8` |

幅度档位控制峰值比例，正弦和锯齿使用同一套峰峰值标度。锯齿的RMS值与同峰值
正弦不同，因此不要用RMS读数比较两种波形的“同幅度”。高频锯齿在真实DAC和
运放上可能因模拟带宽变成圆角，但数字频率和峰值控制仍按上述参数执行。

## 二进制帧

所有多字节整数均为小端序。每帧格式为：

| 偏移 | 长度 | 字段 |
|---:|---:|---|
| 0 | 2 | 同步头，固定`A5 5A` |
| 2 | 1 | `type` |
| 3 | 1 | `sequence` |
| 4 | 2 | `payload_length`，小端 |
| 6 | N | payload |
| 6+N | 2 | CRC16-CCITT，小端 |

CRC参数：多项式`0x1021`、初值`0xFFFF`、不反射、无最终异或。CRC覆盖范围从
`type`开始，到payload最后一字节为止；同步头和CRC本身不参与计算。

`sequence`用于匹配请求和响应。推荐按`1...255`循环递增；收到其他序号的帧时
忽略并继续等待本次序号。串口是字节流，接收器必须支持分片、粘包、噪声后的
`A5 5A`重新同步和CRC校验，不能假定一次`read()`就是一整帧。

## 设置波形：`0x14 SET_WAVEFORM`

请求类型为`0x14`。正弦和锯齿payload为8字节：

```text
waveform:u8 + frequency_mHz:u32LE + phase_cdeg:u16LE + amplitude_code:u8
```

`waveform=3`的快降脉冲在末尾追加`decay_us:u16LE`，payload共10字节。
频率必须为100000 mHz，`decay_us`范围1～9999，默认界面值为1000。

示例：序号1、锯齿、10000 Hz、0°、8/8幅度：

```text
A5 5A 14 01 08 00 01 80 96 98 00 00 00 00 B7 A7
```

字段拆解：

```text
14                type = SET_WAVEFORM
01                sequence = 1
08 00             payload length = 8
01                waveform = sawtooth
80 96 98 00       frequency = 10,000,000 mHz
00 00             phase = 0 cdeg
00                amplitude = 8/8
B7 A7             CRC = 0xA7B7，小端发送
```

历史正弦命令`0x13 SET_TARGET`的payload为7字节
`frequency_mHz:u32LE + phase_cdeg:u16LE + amplitude_code:u8`。保留它仅为兼容
旧程序；新程序统一发送`0x14`。

## 成功确认：`0x93 TARGET_ACK`

成功响应类型为`0x93`，sequence必须与请求相同。普通波形payload为8字节：

```text
frequency_mHz:u32LE + phase_cdeg:u16LE + amplitude_code:u8 + mode:u8
```

快降脉冲确认在末尾追加已应用的`decay_us:u16LE`，共10字节。客户端必须核对
该字段与请求一致，才能将下降时间标记为生效。

上述锯齿命令的响应示例：

```text
A5 5A 93 01 08 00 80 96 98 00 00 00 00 31 27 AB
```

`mode`位定义：

| 位 | 含义 |
|---:|---|
| 0 | DDS已启用 |
| 1 | 实时跟踪模式的二倍频标志 |
| 3:2 | 幅度编码 |
| 4 | 自由运行模式；Radxa目标命令必须为1 |
| 5 | 波形；`0=正弦`，`1=锯齿` |
| 6 | 锯齿方向；`0=缓升快降`，`1=缓降快升` |
| 7 | 保留，必须为0 |

示例中的`mode=0x31`表示DDS、自由运行、锯齿、8/8幅度。客户端在宣告成功前
至少应检查：类型为`0x93`、序号匹配、CRC正确、位4为1、位5与请求波形一致、位6与请求方向一致。
响应中的相位和幅度应与请求一致；频率应按本文量化容差校验，不要求与原始浮点请求逐位相等。

位5:6组合定义为：`00=正弦`、`01=缓升快降锯齿`、`11=缓降快升锯齿`、
`10=100 Hz快降脉冲`。快降脉冲的请求频率必须为100000 mHz；每个10 ms周期中，
保持负峰值的时间为`10000-decay_us`，随后瞬间跳到正峰值，并在`decay_us`内
线性下降回负峰值。8/8档使用完整双极性标度，因此峰峰值与满幅正弦相同。

注意：该确认不是MSPM0“已收到串口数据”的简单回显。MSPM0会先向FPGA发送
`SET_OUTPUT`，等待FPGA返回有效ACK，然后才向Radxa发送`0x93`。

## 相位确定的1倍频到2倍频：`0x15 ARM_DOUBLE`

圆阶段已经确认自由运行正弦相对输入约超前90°后，不要再用普通`SET_WAVEFORM`
命令计算一个独立的2倍频FTW。发送空载荷命令`0x15`，FPGA会等待当前有效查表相位
`carrier_phase-applied_phase_lag`跨越下降过零边界，再把当前Q38 FTW原子左移一位。
相位累加器、相移和幅度均不重置。

Python接口：

```python
ack = waveform.arm_double_at_falling_zero()
print(ack.applied_sample_counter)
print(hex(ack.old_phase_step), hex(ack.new_phase_step))
```

也可在Radxa本机执行：

```bash
python wave_control.py --port /dev/ttyS4 --arm-double
```

该操作只接受当前状态为自由运行、1倍频、正弦、已锁定、相位命令已完成平滑，且
FTW左移后不超过210 kHz的情况。锯齿、快降脉冲、实时跟随模式、重复倍频及越界请求
会被拒绝。专用操作等待真实提交，不能使用fire-and-forget模式。

成功响应为`0x94 DOUBLE_ACK`，17字节小端载荷：

```text
status:u8 + applied_sample_counter:u32 + old_ftw:u32 +
new_ftw:u32 + commit_phase:u32
```

成功时`status=0`且`new_ftw=2*old_ftw`。`commit_phase`是提交前的旧频率有效查表
相位，应位于下降过零跨越之前；样本计数属于FPGA采样时钟域，不是Radxa时间戳。
ACK仅在首个带新频率标签的样本穿过DDS、幅度映射并更新DAC数字接口后返回。

错误帧仍为`0xE0`：错误码5表示事务忙，6表示当前输出状态不允许该操作，7表示等待
相位稳定、下降过零或DAC生效超时。普通`0x14 SET_WAVEFORM`行为保持不变，仍用于
后续绝对频率和相位微调。

## 错误响应：`0xE0 ERROR`

错误payload固定2字节，为`error_code:u16LE`：

| 错误码 | 含义 |
|---:|---|
| 1 | MSPM0等待FPGA确认超时 |
| 2 | FPGA确认帧格式或序号错误 |
| 3 | FPGA确认帧CRC错误 |
| 4 | 目标频率超出250～210000 Hz范围 |
| 5 | 下降过零倍频事务忙 |
| 6 | 当前输出状态不允许确定相位倍频 |
| 7 | 等待下降过零或DAC生效超时 |

收到错误帧后不要把命令记为已执行。可记录错误并进行有限次数重试；如果连续出现
错误1～3，应报告硬件链路故障，不要无限快速重发。建议单次端到端等待时间为1秒。

## AI控制程序必须遵守的约束

1. 整个视觉/AI进程共享一个`MSPM0WaveLink`实例。
2. 只有在目标参数发生变化时才下发，避免逐帧重复发送相同值。
3. 控制决策先做范围限制；非法参数不得发送。
4. 必须等待`TargetAck`后才能把新波形状态标记为生效。
5. 超时、错误响应和串口断开必须进入安全错误状态，不能伪造成功。
6. 不要绕过`msp_wave_link.py`自行修改CRC或幅度编码，除非确实需要移植到其他语言。
7. 自动模式退出时应由上层明确决定保持最后波形、切回正弦或停止；当前协议没有
   单独的“停止输出”命令。

## 最小状态更新函数

```python
class WaveformController:
    def __init__(self, port="/dev/ttyS4"):
        self.link = MSPM0WaveLink(port, timeout=1.0)
        self.last_target = None

    def open(self):
        self.link.open()
        self.last_target = None

    def close(self):
        self.link.close()
        self.last_target = None

    def apply(self, waveform, frequency_hz, phase_degrees, amplitude,
              *, direction="rising"):
        target = (waveform, frequency_hz, phase_degrees, amplitude, direction)
        if target == self.last_target:
            return None
        self.last_target = None  # 确认失败时不保留可能已失效的状态
        ack = self.link.set_waveform(
            waveform, frequency_hz, phase_degrees, amplitude,
            direction=direction
        )
        self.last_target = target
        return ack
```

浮点识别结果建议先量化再参与`last_target`比较，例如频率量化到0.001 Hz、相位量化到
0.01°，否则视觉噪声会导致串口不断更新。

若视觉题型要求自由输出二倍频，Radxa应在调用前计算
`output_frequency_hz = detected_frequency_hz * 2`，再把最终输出频率传给
`set_waveform()`。最终值不得超过210000 Hz；例如识别到105 kHz时传入210 kHz。
自由模式不需要设置FPGA跟随模式的二倍频位，因为频率字已经是最终输出频率。


### 视觉策略

策略适配器最终输出范围须从原110 kHz上限改为250～210000 Hz。
输入识别范围1～100 kHz；圆形和对角线同频，∞形两倍频，保留±5 Hz微调余量。
正相位仍表示滞后，上位机负责符号转换。

锯齿为4 Vpp、0 V中心，在整个周期T内线性上升，到顶后直接回落，
不另设回扫时间，频率为`1e6/T_us`。T为73、110、175、223、809、1750、3500 μs时，
分别输出13698.630137、9090.909091、5714.285714、4484.304933、1236.093943、
571.428571、285.714286 Hz。调用`set_sawtooth(frequency_hz, 0.0, 4)`，
保持mHz精度，不可取整Hz或用正弦替代。

回执频率为实际DDS频率字换算并四舍五入到mHz的设置值，容差为
`max(0.002 Hz, 目标频率*0.5 ppm)`。自由运行频率字为
`round(frequency_mHz * 2^38 / (25000000 * 1000))`，25 MHz下步进约
0.000090949 Hz。MSPM0与FPGA须配套升级；跟随模式仍使用原Q32频率字，
相位字仍为Q32。改频率和相位时保持累加器（含小数余数）连续。


### 锯齿方向控制

`0x14 SET_WAVEFORM`的普通波形保持8字节payload；首字节waveform为：
0=正弦，1=缓升快降锯齿（兼容旧命令），2=缓降快升锯齿，3=快降脉冲。
waveform=3使用10字节payload，在原字段后追加`decay_us:u16LE`。
MSPM0向FPGA转发的mode位5选择锯齿，位6选择反向；`0x93`回执mode位6
同步返回方向，客户端检查方向是否匹配。正弦应清除位6。
方向表示数字DAC码的变化方向；若实际模拟链路反相，可选择相反方向补偿。
切换方向保留累加器，但波形极性翻转可能造成瞬时电压跳变。

```python
# 与默认锯齿方向相反，频率、幅度和相位定义不变
ack = link.set_sawtooth(544.218, 0.0, 4, direction="falling")
# 恢复原方向
ack = link.set_sawtooth(544.218, 0.0, 4, direction="rising")
```

命令行：
```sh
python3 wave_control.py --port /dev/ttyS4 --waveform sawtooth --direction falling --frequency 544.218 --phase 0 --amplitude 4
```
MSPM0、FPGA和客户端需要配套更新；旧固件不支持waveform=2。


#### 反向锯齿完整帧示例

序号1、544.218 Hz、0°、4/8幅度、`direction="falling"`，请求为：

```text
A5 5A 14 01 08 00 02 DA 4D 08 00 00 00 02 C5 17
```

对应成功回执如下（此频率的实际设置四舍五入到mHz仍为544218）：

```text
A5 5A 93 01 08 00 DA 4D 08 00 00 00 02 79 74 9A
```

回执`mode=0x79`：DDS启用、自由运行、锯齿、4/8幅度、反向。
仅修改请求首字节waveform后必须重新计算CRC，不能复用另一方向的CRC。
缓存目标时应包含direction；只有确认成功后才记录新目标，重连后清空缓存。
