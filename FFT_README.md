# 自动频率跟踪与调相协议

## 通用帧

```text
A5 5A | type:u8 | sequence:u8 | payload_length:u16LE | payload | crc:u16LE
```

CRC为CRC16-CCITT，初值 `0xFFFF`，多项式 `0x1021`，覆盖type、sequence、长度和payload。

## PC与MSPM0

UART1，PB4/PB5，1,000,000 baud，8N1。

### `0x11` 设置目标滞后角

```text
phase_centidegrees:u16
```

范围为 `0～35999`，例如90°发送 `9000`。设置在MSPM0完成CRC检查后生效。

### `0x10` 请求最近测量结果

payload为空。MSPM0持续在后台采集和跟踪频率，该命令不启动跟踪，只要求上传下一份完整结果。

### `0x90` 测量结果

```text
sample_rate:u32        = 390625
sample_count:u16       = 4096
spectrum_count:u16     = 1050
flags:u16
peak[3] { frequency_millihz:u32, magnitude:u16 }
samples[4096]:u16[]
spectrum[1050]:u16[]
```

单正弦模式只使用 `peak[0]`，其余两个峰为0，以保持PC结果帧结构兼容。

flags：

| 位 | 含义 |
|---:|---|
| 0 | AD9226 OTR触发 |
| 1 | DAC映射削顶 |
| 2 | 所需精确附加延迟超过8192点范围，FPGA退回零附加延迟 |

### `0xE0` 错误

payload为 `error_code:u16`：1为FPGA超时，2为格式错误，3为CRC错误。

## MSPM0与FPGA

UART4，PB10/PB11，2,000,000 baud，8N1。

### `0x01` 请求FFT采样

payload为空。FPGA按64倍抽取采集4096点。

### `0x81` FFT采样响应

```text
sample_rate:u32        = 390625
sample_count:u16       = 4096
flags:u16
samples[4096]:u16[]
```

### `0x03` 设置FPGA附加延迟

```text
delay_samples:u16
```

有效范围 `0～8191`。FPGA仅在整帧CRC正确时更新延迟配置。0表示旁路延迟RAM。

## FFT设计

- 输入：1～100 kHz单正弦；
- 原始采样率：25 MHz；
- 抽取：64；
- FFT采样率：390625 S/s；
- 点数：4096；
- 记录时间：约10.49 ms；
- 原始频率分辨率：约95.37 Hz；
- 窗函数：Hann；
- 频率估计：搜索范围内最强局部峰值，加三点抛物线插值。

频率更新周期还包含约41 ms的2 Mbaud采样帧传输和FFT处理，因此适合固定频率或缓慢变化的单正弦，不适合快速扫频或跳频。

## 首次相位标定

1. 暂时将目标延迟设置为0。
2. 用双通道示波器同时测量ADC输入和DAC模拟输出。
3. 在10 kHz附近测量时间差 `τ0`。
4. 计算 `round(τ0 × 25 MHz)`。
5. 将结果写入 `MEASUREMENT_FIXED_LATENCY_SAMPLES`，重新构建MSPM0固件。
6. 分别测试1、10、50、100 kHz和90°目标相位；如模拟群延迟随频率明显变化，后续应改为频率分段补偿表。

## 排错

- PC无响应：检查PB4/PB5交叉接线、共地和USB-TTL的1 Mbaud能力。
- FPGA超时：检查PB10/PB11、R13/T13和两端2 Mbaud设置。
- 调相显示“超范围”：当前频率和目标角度需要超过8191点附加延迟。
- 频率跳动：检查输入是否确为单正弦、幅度是否过低、OTR以及高频干扰混叠。
- 相位固定偏差：重新标定 `MEASUREMENT_FIXED_LATENCY_SAMPLES`。
- 0°目标低于约3.05 kHz时整周期延迟超出RAM，固件回到零附加延迟并设置相位范围标志，避免保留旧频率的延迟值。
