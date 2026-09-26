# PC、MSPM0 与 FPGA 通信协议

## 数据流

一次测量按以下顺序执行：

1. PC向MSPM0发送 `0x10` 采集命令。
2. MSPM0向FPGA发送 `0x01` 采集命令，并沿用PC序号。
3. FPGA按16倍抽取采集4096点，返回 `0x81` 数据帧。
4. MSPM0检查同步字、类型、序号、长度、采样率、点数和CRC。
5. MSPM0执行Hann窗和4096点Q15 FFT，搜索三个峰值。
6. MSPM0向PC返回 `0x90` 结果帧；失败时返回 `0xE0` 错误帧。

## 通用帧

```text
A5 5A | type:u8 | sequence:u8 | payload_length:u16LE | payload | crc:u16LE
```

CRC为CRC16-CCITT，初值 `0xFFFF`，多项式 `0x1021`，覆盖从 `type` 到payload最后一个字节，不包括同步字和CRC本身。

## PC与MSPM0

UART1，PB4/PB5，1,000,000 baud，8N1。

### `0x10` PC采集请求

payload为空。MSPM0只接受CRC正确且payload长度为0的请求。

### `0x90` MSPM0测量结果

payload按小端序排列：

```text
sample_rate:u32
sample_count:u16
spectrum_count:u16
flags:u16
peak[3] { frequency_millihz:u32, magnitude:u16 }
samples[sample_count]:u16[]
spectrum[spectrum_count]:u16[]
```

当前固定值：

- `sample_rate = 1,562,500`
- `sample_count = 4096`
- `spectrum_count = 1311`，对应0～约500 kHz
- `flags bit 0`：AD9226 OTR曾触发
- `flags bit 1`：ADC到DAC映射曾削顶

PC的 `scope.py` 与该格式兼容。

### `0xE0` MSPM0错误

payload为一个 `error_code:u16`：

| 错误码 | 含义 |
|---:|---|
| 1 | 等待FPGA字节超时 |
| 2 | FPGA响应类型、序号、长度、采样率或点数错误 |
| 3 | FPGA响应CRC错误 |

## MSPM0与FPGA

UART4，PB10/PB11，2,000,000 baud，8N1。

### `0x01` MSPM0采集请求

payload为空。FPGA收到合法命令后开始抽取采集。

### `0x81` FPGA采样响应

```text
sample_rate:u32       = 1,562,500
sample_count:u16      = 4096
flags:u16
samples[4096]:u16[]   = 低12位有效
```

FPGA不再实现PC直连所用的 `0x02/0x82` 原始25 MSPS采集模式。

## FFT参数

- ADC采样率：25 MHz
- FPGA抽取倍数：16
- FFT采样率：1.5625 MHz
- FFT长度：4096
- 频率分辨率：约381.47 Hz
- 峰值搜索范围：10～500 kHz
- 窗函数：Hann
- 峰值修正：三点抛物线插值

## 排错

- PC超时且无错误帧：检查PC与MSPM0的PB4/PB5交叉接线、共地和1 Mbaud支持。
- 返回错误码1：检查PB10/PB11与FPGA R13/T13交叉接线，以及两端是否都为2 Mbaud。
- 返回错误码2：确认FPGA下载的是本工程最新SOF，且抽取倍数和4096点配置未改动。
- 返回错误码3：检查线长、电平、共地和2 Mbaud信号质量。
- OTR触发：检查AD9226输入共模和幅度。
- DAC削顶：降低输入幅度或重新标定映射参数。
