# 定频后调相接入准备

新策略已从远端 main 的 `0a09a2b` 导入 `phase_board/`。
这是独立的纯图像调相模块，支持 diagonal / circle / infinity。
已提供实机调相脚本 phase_hardware_test.py，结果以每次运行的 result.json 为准。

原项目定频、调幅完成并切换到正弦后，调用：

```python
from phase_handoff import start_after_frequency
from phase_board.phase_control import Signal, Frame

controller, decision = start_after_frequency(
    confirmed_source_hz,
    actual_signal=Signal(actual_output_hz, actual_vpp, actual_lead_deg),
    command_id=actual_command_id,
    applied_at_s=actual_application_monotonic_s,
    continuous_phase=verified_continuous_phase,
    mode="circle",
)
```

输入频率必须已经确认，范围 1–100 kHz，且严格为 100 Hz 整数倍；
不要将估计值四舍五入以绕过校验。circle/diagonal 输出同频，infinity
输出二倍频。频率与幅度在整个调相过程中固定。

后续循环参考 `phase_board/examples/board_loop.py`：执行 set_phase 前调用
retime_pending，取得真实硬件生效反馈后调用 applied；capture/wait/hold/
recapture 时采集新图并提交 Frame。complete 才表示通过至少 5 秒采样保持。
needs_frequency_check 交回定频子系统，其余失败状态按模块文档处理。

已有 StrategyImageAdapter.convert 可提供 384×384 灰度、10×10 div 坐标图。
仍需用真实图确认清理后曲线完整、分支拟合可用；不得用拉伸修复裁切。

接通硬件前还需完成：

- 验证固件的相位更新保持 DDS 累加器连续。当前 MSPM0WaveLink.set_waveform
  没有显式的连续相位参数，不能仅凭 ACK 声明具备该能力。
- 提供实际生效时间或校准后的延迟预测；串口 ACK 到达时间不等于生效时间。
- 验证相位超前/滞后约定、ACK 量化反馈、曝光有效时刻和显示余辉等待时间。
  现有 BoardAdapter.apply 返回请求值，不能直接充当新模块的真实状态反馈。
- 确认取图和处理间隔满足默认 0.25 秒最大保持采样间隔，帧龄不超过 0.3 秒。

离线验证（不访问相机或串口）：

```bash
PYTHONPATH=phase_board .venv/bin/python -m unittest discover -s phase_board/tests -v
```

## 输出频率校准

frequency_calibration.py 按用户提供的 RADXA_FREQUENCY_CALIBRATION_FIT.md
实现过原点二次补偿，用 Decimal/ROUND_HALF_UP 量化到 0.001 Hz。
config.json 的 enable_frequency_calibration 控制启用状态。
wave_control.py、strategy_control.py 和 phase_hardware_test.py 已接入。
底层 MSPM0WaveLink 继续接收原始下发频率，不在底层重复补偿；其他独立实验脚本
直接调用该底层接口时仍保持原行为。decay_pulse 的固定 100 Hz 不补偿。

例如目标 61200 Hz，实际下发并由 ACK 核对 61200.441 Hz。日志同时保存
target_frequency_hz、command_frequency_hz 和 frequency_correction_hz。
二倍频先在目标域乘二，再执行一次补偿。上限目标为 209998.471 Hz，超过时拒绝。

PhaseController 的本地扩展 output_command_frequency_hz 单独接收已校准的
下发频率；Signal 和后续相位命令保留 ACK 的真实寄存器值，图像相位拟合
则使用 source_frequency_hz×ratio 作为物理目标。不能将 ACK 频率再次拿去补偿。

实机复测：

```bash
.venv/bin/python phase_hardware_test.py --source-hz 61200 --initial-lead 90 --probe-jitter
```

--no-frequency-calibration 可临时关闭补偿做对照。测试脚本把图像压缩和保存
推迟到控制循环结束，以降低保持采样间隔；几何误差、频差和5秒保持判据不变。
这不会消除 ACK 生效时刻和曝光时刻仍为估计值的现有限制。
