# 图像频差微调与锁相 v2.1

本版针对已提供的70 kHz实机失败记录修正控制逻辑，接口仍为
`LockController / Signal / Frame / Options`。原版另外保留。
名义频率仍必须是1k～100k范围的100 Hz整数倍，小数Hz补偿单独作用于输出命令。
先由原系统完成测频、幅度设置和一次性频率标定，再交给本模块。

## 这次修正

- 不足或歧义批次中，保留同一频率、同一图像标定下的有效图片，并统一相移参考。
  历史限4秒/32张；输出频率变化后清空，不能直接混用不同频率的旧图。
- 探测相移改为乱序，打破等间隔采图和等角度递增的镜像混叠。
  有6张可用图片即可尝试拟合，不强制等满整批。
- 有歧义先换探测补采；只有可靠估计超范围或最佳候选位于捕获边界等情况才要求重新检查频率。
- 按修正后的预计频差进入保持，不因修正前频差较大而机械地再测一批。
- 构造控制器时预热轮廓模板，位于`start()`计时之前。
- 圆和∞形：有多角度测量确认的相位分支后，保持期间用至少5张新图、至少0.65秒跨度
  估计局部漂移及不确定度，按置信条件发出有界小频差/相位调整，每次实际修正后重新计满5秒。
  对角线先在接近圆的正交相位短暂细测频差，再转到0°开始正式保持，
  不从退化直线单帧推测旋转方向。正交细测不是目标保持，不能计入5秒。
- 检查时间预算；来不及完成完整5秒时返回`insufficient_time`。

模型权重未重训。原频差增益模型仅用于6～8张、无跨批历史的特征；跨批拟合使用固定增益。
新探测顺序和局部保持反馈是确定性控制逻辑，不宣称为新训练成果。
模型不决定验收是否通过。`use_policy=False`可完全采用确定性控制。

## 验收标准和限制

默认仍为轮廓误差0.2 div、保持5秒、合格采样帧间隔不超过0.25秒、总窗口10秒。
没有放宽这些条件。时间不足也是失败，不能用退出码0代替`decision.success`。
延长诊断窗口后的成功不能计为原10秒内成功。
图像轮廓本身超限时仍拒绝计时，不会把每帧椭圆强行缩放成圆来隐藏幅度或标定问题。

模块不直接操作摄像头、串口或FPGA。保持完成后不继续跟踪，需要上层另开监测周期。
本版通过了原图局部重放与虚拟闭环，尚未在真实硬件上执行修正后的动作。
已保存图只能检验对应的原命令，修正命令后的效果必须重新采图，不能用旧图证明。

## 升级合作者的现有项目

压缩包根目录为`lock_board/`，与现有`lock_hardware_test.py`的
`from lock_board.lock_control import ...`一致。
先停止旧测试，再备份原`lock_board/`，解压新版覆盖该目录。
不覆盖频率标定、图像转换、相机、串口驱动或配置文件。
原实测入口按非继续状态退出，新状态`insufficient_time`可以直接正确退出。

```bash
python -c "from lock_board.lock_control import __version__; print(__version__)"
PYTHONPATH=lock_board python -m unittest discover -s lock_board/tests -v
```

继续状态为`capture / set_signal / await_ack / recapture / wait / hold`；
只有`complete`且`success=true`才是成功，其余终止状态记录原因并结束本次测量。
完整升级与手动实测指令见`UPGRADE_zh.txt`。

## 接口

```python
from lock_board.lock_control import LockController, Options, Signal, Frame

# 在测量计时前构造，自动预热模板。
ctrl = LockController(70000, 'circle', continuous_phase=True,
    options=Options(frequency_step_hz=.001, exposure_ms=8, settle_s=.08,
                    command_latency_s=.011, timeout_s=10))
```

1. 对名义目标只标定一次，实际发送并收到ACK，调用
   `ctrl.start(actual_signal, command_id, applied_at_s, now_s=...)`。
2. 返回命令时调用`ctrl.retime_pending(expected_application_time_s)`，实际执行其`signal`。
   频率已是最终绝对命令，不能再次套补偿公式；不清零相位累加器。
3. 用实际量化后的ACK调用`ctrl.applied(command_id, actual_signal, applied_at_s)`。
4. 等待`capture_after_s`后拍新图，调用
   `ctrl.step(Frame(image, actual_signal, frame_id, command_id, captured_at_s, calibration_id), now_s)`。

`Signal.phase_deg`为增加表示超前的附加相移寄存器值；硬件滞后角在适配层取反。
ACK时刻和图片有效采集时刻统一到板端单调时钟。V4L2 read结束不是精确曝光时刻，
示波器显示刷新/余辉也会造成滞后，需要实机测量。示例循环见`examples/board_loop.py`。

## 图像契约

- `uint8`、384×384灰度或RGB图片；OpenCV彩色BGR须转RGB，二值灰度无需转换。
- 完成镜头/透视校正，画布为中心对齐的10×10 div，X向右、Y向上。
- X为未知输入，Y为DDS。坐标中心和格数来自固定网格标定，不按每帧波形重新归一化。
- 白波形、暗背景，去掉菜单、白框和读数；完整波形不能裁切。
- X默认8div峰峰值，Y为`target_height_div`（2/4/6/8）；本模块不调幅。
- 一次测量中相机固定，`calibration_id`不变；移动后新建测量。
- 帧编号、采集时刻递增，图片对应已ACK命令且旧缓存已刷新。

目标：`diagonal`同频正对角线、`circle`同频90°、`infinity`输出为输入二倍频。
轮廓有对称歧义，因此先多角度探测，再允许局部跟踪。

## 局部跟踪边界

仅圆/∞形，且先有可靠多角度结果。默认捕获±0.03Hz，至少5张图且跨度0.65秒，
丢帧超0.25秒清空局部历史。频差修正需超过3倍标准差、1.5个频率步长、0.0015Hz的最大值。
相位测量噪声下限0.15°，防止把像素量化当成无限精度。
对角线的正交细测允许采图间隔最多0.6秒，适配实际约0.268秒的探测采集；
转成对角线后的正式保持仍严格要求0.25秒以内。
干净拟合用频率增益1，较噪时0.75，最多修正4次，始终受总时间及±2.25Hz边界约束。
如果有效保持已开始而剩余时间不够重开5秒，会跳过非必需修正并继续严格检查；
实际轮廓一旦越界则清零保持，报告时间不足或重探测。

## 安装、IPC与校验

Python>=3.10，依赖NumPy/Pillow，板端无需PyTorch：

```bash
python -m pip install -r lock_board/requirements.txt
# 独立安装可用：python -m pip install ./lock_board
python -m lock_board.lock_control --source-hz 70000 --mode circle --continuous-phase --frequency-step .001
```

JSON逐行接口沿用v2：`op=start / frame / retime / applied`，一个测量保持一个进程。
字段与Python接口相同，`image_path`为板端已有的校正图路径。

`VALIDATION.json`记录本版检查，`MANIFEST.json`记录SHA256；文本规范化LF后再生成清单。
`tests/fixtures/`是本次20张原始校正图及寄存器/相对时间元数据，仅用于回归测试。
