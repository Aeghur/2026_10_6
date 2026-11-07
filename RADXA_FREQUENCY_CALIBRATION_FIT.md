# Radxa 输出频率标定修正实施说明

## 目标

在 Radxa 上调用现有 `MSPM0WaveLink.set_waveform()` 前，根据目标输出频率计算经过
标定的下发频率，以补偿输入信号源与 FPGA 自由运行 DDS 之间的微小频率误差，使后续
人工或自动调相能够稳定定相。

本次改动只涉及 Radxa 上位机软件。不要修改 PC→MSPM0 二进制协议，不需要重新烧录
MSPM0 或 FPGA。

## 标定数据和拟合结论

标定包含 16 个频点，范围为 1～205 kHz。每个频点记录：

- `source_frequency_hz`：需要匹配的输入信号源频率；
- `calibrated_frequency_hz`：人工观察定相后得到的 DDS 下发频率；
- `frequency_error_hz = calibrated_frequency_hz - source_frequency_hz`。

固定 ppm、普通一次线性和二次模型比较后，推荐使用过原点二次模型。该模型的训练
RMSE 为 0.830 mHz，最大残差为 2.093 mHz，留一交叉验证 RMSE 为 1.032 mHz。
三次模型的训练误差略小，但交叉验证误差更大，不采用。

## 修正公式

输入和结果均以 Hz 为单位：

```text
frequency_error = 7.18530722e-6 × target_frequency
                + 4.57648439e-13 × target_frequency²

command_frequency = target_frequency + frequency_error
```

等效 ppm 随频率变化：

```text
correction_ppm = 7.18530722 + 4.57648439e-7 × target_frequency
```

这里的 `target_frequency` 是用户或业务逻辑希望得到的最终模拟输出频率；
`command_frequency` 才是传给 `set_waveform()` 的频率。必须保留这两个量，不能把已经
修正的频率再次作为目标频率重复修正。

## 推荐 Python 实现

频率协议精度为 0.001 Hz。使用 `Decimal` 计算并按 `ROUND_HALF_UP` 量化，避免连续
浮点计算造成 mHz 边界不一致。

```python
from decimal import Decimal, ROUND_HALF_UP

FREQUENCY_QUANTUM_HZ = Decimal("0.001")
FREQUENCY_CAL_A = Decimal("0.00000718530722")
FREQUENCY_CAL_B = Decimal("0.000000000000457648439")
MIN_TARGET_FREQUENCY_HZ = Decimal("250.000")
MAX_COMMAND_FREQUENCY_HZ = Decimal("210000.000")
# 该值修正并量化后恰好为 210000.000 Hz。
MAX_CALIBRATED_TARGET_HZ = Decimal("209998.471")


def calibrate_output_frequency(frequency_hz: float | str | Decimal) -> Decimal:
    target = Decimal(str(frequency_hz)).quantize(
        FREQUENCY_QUANTUM_HZ, rounding=ROUND_HALF_UP
    )
    if not MIN_TARGET_FREQUENCY_HZ <= target <= MAX_CALIBRATED_TARGET_HZ:
        raise ValueError(
            "启用频率标定后，目标频率必须位于 "
            "250.000～209998.471 Hz"
        )

    command = (
        target
        + FREQUENCY_CAL_A * target
        + FREQUENCY_CAL_B * target * target
    ).quantize(FREQUENCY_QUANTUM_HZ, rounding=ROUND_HALF_UP)

    if command > MAX_COMMAND_FREQUENCY_HZ:
        raise ValueError("标定后的下发频率超过 210000.000 Hz")
    return command
```

现有接口最终需要 `float` 时，只在调用边界转换：

```python
target_frequency = detected_or_requested_frequency_hz
command_frequency = calibrate_output_frequency(target_frequency)

ack = waveform.set_waveform(
    waveform="sine",
    frequency_hz=float(command_frequency),
    phase_degrees=phase_degrees,
    amplitude=amplitude,
)
```

## 接入顺序

1. 业务逻辑先确定最终目标频率。例如需要二倍频时，先计算
   `target_frequency = detected_frequency × 2`。
2. 对最终目标频率执行一次 `calibrate_output_frequency()`。
3. 将得到的 `command_frequency` 传给 `MSPM0WaveLink.set_waveform()`。
4. 日志和状态同时记录目标频率与实际下发频率，禁止只记录其中一个。
5. ACK 校验应与 `command_frequency` 比较，而不是与未修正的目标频率比较。

建议保留显式配置项，例如：

```python
ENABLE_FREQUENCY_CALIBRATION = True
```

关闭配置时直接令 `command_frequency = target_frequency`，便于现场对照和故障排查。

## 不适用情况

- 不要修正 `decay_pulse`。该模式的协议频率固定为 100 Hz，改变频率会被现有参数校验
  拒绝。其余自由运行正弦和锯齿可使用同一修正。
- 不要把修正公式用于 FPGA 比较器实时跟随模式；该模式的频率来源和控制环不同。
- 当前数据覆盖到 205 kHz。205～209.998471 kHz 属于小范围外推，应保留日志并谨慎
  使用。
- 不要在 MSPM0、FPGA 和 Radxa 多处同时加入相同补偿，否则会发生重复修正。

## 参考结果

所有结果均已量化到 0.001 Hz：

| 目标频率 / Hz | 应下发频率 / Hz |
|---:|---:|
| 250.000 | 250.002 |
| 1000.000 | 1000.007 |
| 5000.000 | 5000.036 |
| 10000.000 | 10000.072 |
| 50000.000 | 50000.360 |
| 100000.000 | 100000.723 |
| 120000.000 | 120000.869 |
| 150000.000 | 150001.088 |
| 180000.000 | 180001.308 |
| 205000.000 | 205001.492 |
| 209998.471 | 210000.000 |

`209998.472 Hz` 会修正为 `210000.001 Hz`，超过协议上限，必须拒绝，不能静默钳位到
210 kHz。静默钳位会使调用方误以为目标频率已经准确生效。

## 必须增加的测试

至少覆盖以下测试：

1. 上表中的全部参考点应逐项精确相等。
2. `250.000 Hz` 和 `209998.471 Hz` 应接受。
3. `249.999 Hz` 和 `209998.472 Hz` 应拒绝。
4. 二倍频必须先乘二、再修正，不能分别修正后再乘二。
5. ACK 频率检查使用修正后的 `command_frequency`。
6. `decay_pulse` 保持固定 100 Hz，不进入修正函数。
7. 配置关闭时，下发频率与目标频率完全相同。
8. 日志能够同时看到 `target_frequency_hz`、`command_frequency_hz` 和两者差值。

完成后运行 Radxa 项目原有协议和波形控制测试，确认未改变帧格式、CRC、相位、幅度、
波形方向及串口长连接行为。
