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
        frequency_hz=10_000.0, # 900.0～110000.0
        phase_degrees=0.0,     # 0.0～359.99，正值表示滞后
        amplitude=8,           # 2、4、6、8四档
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
| `waveform` | 正弦或锯齿 | `0=sine`，`1=sawtooth` |
| `frequency_mHz` | 900000～110000000 mHz | 目标Hz × 1000，四舍五入 |
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

请求类型为`0x14`，payload固定8字节：

```text
waveform:u8 + frequency_mHz:u32LE + phase_cdeg:u16LE + amplitude_code:u8
```

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

成功响应类型为`0x93`，sequence必须与请求相同，payload固定8字节：

```text
frequency_mHz:u32LE + phase_cdeg:u16LE + amplitude_code:u8 + mode:u8
```

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
| 7:6 | 保留，必须为0 |

示例中的`mode=0x31`表示DDS、自由运行、锯齿、8/8幅度。客户端在宣告成功前
至少应检查：类型为`0x93`、序号匹配、CRC正确、位4为1、位5与请求波形一致。
响应中的频率、相位和幅度也应与请求一致。

注意：该确认不是MSPM0“已收到串口数据”的简单回显。MSPM0会先向FPGA发送
`SET_OUTPUT`，等待FPGA返回有效ACK，然后才向Radxa发送`0x93`。

## 错误响应：`0xE0 ERROR`

错误payload固定2字节，为`error_code:u16LE`：

| 错误码 | 含义 |
|---:|---|
| 1 | MSPM0等待FPGA确认超时 |
| 2 | FPGA确认帧格式或序号错误 |
| 3 | FPGA确认帧CRC错误 |
| 4 | 目标频率超出900～110000 Hz范围 |

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

    def close(self):
        self.link.close()

    def apply(self, waveform, frequency_hz, phase_degrees, amplitude):
        target = (waveform, frequency_hz, phase_degrees, amplitude)
        if target == self.last_target:
            return None
        ack = self.link.set_waveform(
            waveform, frequency_hz, phase_degrees, amplitude
        )
        self.last_target = target
        return ack
```

浮点识别结果建议先量化再参与`last_target`比较，例如频率取整到1 Hz、相位取整到
0.01°，否则视觉噪声会导致串口不断更新。
