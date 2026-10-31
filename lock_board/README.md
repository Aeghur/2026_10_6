# 摄像头反馈修频与李萨如调相模块 v2

输入已经确定的**100 Hz名义频率档位**、摄像头校正图片和对应的实际DAC状态；逐步输出下一次频率、相位命令，直到目标轮廓通过5秒取样保持检查。

运行仅依赖 **Python 3.10+、NumPy、Pillow**。训练模型已转换成NumPy权重，保存在`lock_control/assets/`，不需要PyTorch或原训练工程。该目录必须完整保留。

本项目为新的`lock_board`，与此前只改相位的`phase_board`分别保存。

## 功能和前提

| 模式 | 频率关系（未知输入X，DAC输出Y） | 相位目标 |
|---|---|---|
| `diagonal` | 输出与输入同频 | 正斜线，0° |
| `circle` | 同频 | +90°，圆/正交椭圆 |
| `infinity` | 输出为输入二倍频 | 广义相位β−2α=0°，∞形 |

`source_frequency_hz`是先前测频步骤已确定的名义档位，必须为1k～100k范围的100 Hz整数倍。两路设备实际频率可以有误差，初始相对输出频差假设在±2 Hz内；不需要分辨误差来自哪台设备。

输出为`目标倍频的名义频率＋小数Hz补偿`，补偿默认限于±2.25 Hz。修改频率/相移时必须保持DDS相位累加器连续。本模块不调整幅度；开始前由幅度子系统设置合适的Vpp。

默认校验图片对应X轴8div、Y轴8div的目标。Y目标高度也可配置2/4/6/8div。实际成为圆还需两轴幅度和坐标比例一致；模型的原训练主要使用8div，不代表其他幅度已完成实物验证。

## ROCK 4D安装

将整个`lock_board`复制到板卡，例如在Windows CMD中：

```cmd
scp -r lock_board radxa@192.168.137.2:/home/radxa/
```

然后在板卡SSH终端运行：

```sh
cd /home/radxa/lock_board
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
python -c "from lock_control import LockController; print('ready')"
python -m unittest discover -s tests -v
```

如果系统缺少venv、pip或setuptools，先安装相应系统软件包。也可以不安装本项目，在项目根目录运行Python，或将本目录加入`PYTHONPATH`。离线环境需要准备与板卡Python/aarch64匹配的依赖；不要复制Windows虚拟环境到板卡。

## 最小Python接口

```python
from lock_control import LockController, Signal, Frame, Options

controller = LockController(
    source_frequency_hz=61200,   # 已知名义输入，仍是100 Hz整数倍
    mode="circle",
    options=Options(frequency_step_hz=0.0001),  # 必须填实际DDS指令步长
    use_policy=True,            # 使用本包已导出的第40轮最佳模型
    continuous_phase=True,      # 硬件确实不清零累加器时才确认
)

current = Signal(frequency_hz=61200, vpp=4, phase_deg=0)
decision = controller.start(
    current, command_id="initial",
    applied_at_s=initial_ack_time, now_s=board_now,
)

# 之后每张新图片调用一次，必须使用当前实际已确认的设置：
decision = controller.step(
    Frame(image=rectified_image, signal=current,
          frame_id=camera_sequence, command_id=actual_command_id,
          captured_at_s=effective_capture_midpoint,
          calibration_id="measurement-geometry-1"),
    now_s=board_now,
)
print(decision.to_dict())
```

`Signal.phase_deg`表示DDS附加相移寄存器偏移，增加表示超前；它不是载波瞬时相位，也不是已知的“相对于输入的相位”。若硬件协议使用滞后语义，在适配层取反并统一反馈。

单张静态椭圆不能唯一确定相位方向。控制器会通过多次调用，主动请求6张或8张不同相移视角；∞形默认从8张多角度探测开始。坏帧按几何质量剔除，至少6张合格才拟合。

## 执行命令和硬件确认

`decision.command`非空时，取出命令，由自己的串口/FPGA驱动执行。命令包含：

- `signal.frequency_hz`：新的DDS频率指令，包含补偿。
- `signal.phase_deg`：新的相移寄存器值。
- `signal.vpp`：保持本次测量原幅度。
- `nominal_source_hz`、`output_nominal_hz`、`frequency_trim_hz`：分别记录名义频率与微调量。
- `apply_at_s`：建议的实际生效时刻。
- `command_id`：用于确认和图片关联。
- `reset_accumulator=False`：绝不能清零DDS累加器。

在真正发送前，如生效时刻发生改变，先重新外推相位：

```python
command = controller.retime_pending(actual_planned_application_time)
# 通过你们的DAC协议，设置频率、相位和Vpp；频率变化保持载波相位连续。
# 等硬件确认实际生效，再调用：
decision = controller.applied(
    command.command_id, actual_signal, actual_applied_at_s
)
```

`actual_signal`必须是实际量化后设置，不是尚未执行的目标。默认允许ACK相位与请求相差0.6°；频率差容限为所配置步长的一半。硬件步长、相位分辨率不同，应明确配置适配层。

未收到ACK时不会使用后续图片估计。ACK时间和图片采集时刻必须属于同一板端单调时钟域。修频改相后的画面需要等待旧显示/相机缓存消失；`Options.settle_s`默认0.02秒只是起点，应按实测设置。等待更久可能导致总10秒窗口不够，程序会如实返回超时。

若硬件不能预约生效时间，适配层需要测量串口及FPGA处理延迟，用预测时刻重新生成命令，再记录实际ACK时间。不能用网络收到图片的时间代替有效采集时刻。应用时刻误差按拟合漂移换算超过0.5°，模块会重做探测。

完整接入循环见`examples/board_loop.py`。其中相机/DAC方法为适配层约定，需替换成你们已有实现；本包不会自行打开摄像头、串口或SSH。

## 状态处理

| status | 上层处理 |
|---|---|
| `set_signal` | 执行并ACK频率/相位命令 |
| `await_ack` | 确认当前命令实际生效 |
| `capture` / `wait` | 等`capture_after_s`，取新图片 |
| `recapture` | 按原因重新取图/开始新的多角度批次 |
| `hold` | 保持当前设置，继续取图 |
| `complete` | `success=True`，通过至少5秒取样保持 |
| `needs_frequency_check` | 图像频差拟合不可靠或超出预期范围，交回频率子系统 |
| `trim_limit` | 所需补偿超出边界，不继续扩大修正 |
| `needs_amplitude_adjustment` | 先由幅度子系统修正，重开测量 |
| `needs_calibration` / `restart_required` | 修复几何前提，重新开始测量 |
| `not_converged` / `timeout` | 本次未完成，不能当作成功 |

`use_policy=False`可选择固定增益1和固定探测调度基线，接口和保护规则相同。可靠的大频差优先采用增益1，避免反复只消除少量误差而耗尽时间。

## 摄像头图像契约

- 输入为PIL灰度/RGB图，或`uint8`、384×384灰度/RGB数组；OpenCV BGR应先转RGB。
- 已做镜头、透视校正和尺度标定：画面X/Y各10div，覆盖[-5,+5]，X向右、Y向上，左上角为(-5,+5)。
- 未知输入接示波器X，DDS输出接Y。输入X的标准幅度为8div；输出高度按`target_height_div`设置。
- 去掉白框、菜单、读数和纸板，仅保留波形与较暗网格。完整图形不得裁切；不能将裁切区域硬拉伸成同一坐标系。
- `calibration_id`在一次测量中保持一致；相机移动或重新标定后新建测量。
- `frame_id`和采集时间必须递增，不能将旧截图换个编号再送入。
- 有效积分/曝光决定了同一张图内部的相位变化。`Options.exposure_ms`应填写有效值，模型训练默认2ms；示波器余辉可能使实际积分远长于相机曝光。

## 稳定性和时间

默认目标轮廓误差<=0.2div，至少保持5秒，两个合格采样帧间隔不超过0.25秒。丢帧过久会重新计时。保持阶段用完整目标轮廓双向匹配，避免∞形交叉处无法分支导致误拒绝；不接受一个点或一小段曲线冒充完整目标。

圆/∞形在刚进入保持时，若有小局部偏差，可基于上一批已消除歧义的相位分支做一次局部修正，再重新计5秒。这不等于任意单张图都能唯一确定全局相位。

默认整个测量窗口10秒。处理耗时、硬件等待和摄像头延迟都会消耗实际窗口，超过即返回超时。`complete`只证明当前接口条件下的频差/相位轮廓取样检查，不代表整道题的幅度、全部连续时间或整机流程已验收。完成后长期监测由上层另开监测周期。

## C/C++等通过JSON调用

保持一个长期Python子进程，每行输入输出JSON，不能每帧重启进程：

```sh
python -m lock_control --source-hz 61200 --mode circle --continuous-phase --frequency-step 0.0001
```

首次输入：

```json
{"op":"start","signal":{"frequency_hz":61200,"vpp":4,"phase_deg":0},"command_id":"initial","applied_at_s":100.0,"now_s":100.0}
```

图片输入（自行替换路径和实际元数据）：

```json
{"op":"frame","image_path":"/home/radxa/frame.png","signal":{"frequency_hz":61200,"vpp":4,"phase_deg":0},"frame_id":1,"command_id":"initial","captured_at_s":100.1,"calibration_id":"g1","now_s":100.11}
```

收到命令后，可发`{"op":"retime","apply_at_s":100.15}`重新计算。实际执行后输入`op=applied`，带真实的`command_id`、`signal`、`applied_at_s`。后续图片使用最新实际设置。每行返回一个`Decision`或错误原因。

## 文件和来源

- `lock_control/controller.py`：逐帧闭环、ACK、保持和边界检查。
- `fit.py`、`branches.py`、`geometry.py`：与训练工程同源的图像拟合与目标轮廓检查。
- `policy.py`、`features.py`：NumPy模型推断及22维公开特征。
- `assets/policy.npz`、`assets/policy.json`：第40轮最佳模型、来源与SHA256；无训练回放或优化器。
- `VALIDATION.json`：此次独立包检查。
- `MANIFEST.json`：文件校验清单；`.gitattributes`保留LF和二进制权重，避免Git换行处理改变校验值。

训练工程中的108例均衡仿真及12例边界仿真均通过，这是算法来源证据。新逐帧适配器还单独检查了三种图形、正负频差、新鲜度、ACK和保持；它没有在你们的真实板卡/示波器上完成验收。模型原仿真条件为2ms积分、±1ms时间误差、1–2像素线宽和0.0001Hz步长。改动这些条件后需要实拍验证。
