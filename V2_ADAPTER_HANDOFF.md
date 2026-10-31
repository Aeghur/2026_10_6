# 给另一智能体：v2实际适配代码与实机证据

本交接包是当前工作区的实际文件快照，不是伪代码，也不是仅有Git已提交文件。
只用于离线检查时无需操作硬件。本次整理没有重启相机或修改DAC输出。

## 从哪里读起

| 路径 | 实际职责 |
|---|---|
| lock_hardware_test.py | 实测入口：调用v2、retime、下发命令、ACK、采图、step及保持 |
| lock_hardware_test.py::apply_lock_signal | 下发含微调的绝对频率，超前转滞后，ACK解码和生效时间估计 |
| phase_hardware_test.py::TestCamera | v2实际使用的frame_timed覆盖实现，默认丢两帧再读一帧 |
| vision_calibrator.py::Camera | 相机打开、V4L2手动曝光、分辨率、帧率、缓冲和启动预热 |
| vision_calibrator.py::LensCorrector | 根据实际镜头标定文件remap |
| strategy_control.py::StrategyImageAdapter | 透视、HSV、形态学、连通域、10div画布映射 |
| msp_wave_link.py | 1 Mbaud串口、持久连接、序号关联、等待MSPM0/FPGA端到端ACK |
| protocol.py | 二进制帧、CRC、ACK字段解码，频率mHz、相位0.01度 |
| frequency_calibration.py | Decimal二次标定、0.001Hz量化和标定频率ACK校验 |
| full_workflow.py | 重新测频→标定后输出→调用v2的实际编排 |
| experiments/gated_model/two_flush_two_stage.py | 快降脉冲粗测、补偿正弦候选比较、候选频率选择 |
| experiments/gated_model/two_stage.py::finish_output | 测频结束后的标定正弦输出 |
| lock_board/lock_control/ | 合作者v2完整控制器、拟合、几何、策略及assets模型 |

config.json不是全部生效参数。lock_hardware_test.py读取后，用
experiments/zero_slope/current_capture_profile.json覆盖四角、格数、曝光和增益，
并把该文件的HSV阈值映射到white_value_min/white_saturation_max。
镜头标定读取artifacts/calibration/lens/lens_calibration.json。
包内提供effective_config.json，记录这一合并结果，便于复核。

## 实际调用及补偿边界

1. 名义输入必须已确定为100Hz整数倍；circle同频，infinity先乘二。
2. 首次启动对目标输出做一次多项式补偿，再set_waveform并记录实际ACK。
3. LockController使用名义输入，Signal.frequency_hz填写ACK的真实下发寄存器频率。
4. 后续v2返回的signal.frequency_hz已包含微调，apply_lock_signal直接下发，
   不再进行多项式补偿。频率按Decimal/ROUND_HALF_UP量化到0.001Hz。
5. retime_pending的预计生效时间取now＋上一命令往返时长的一半。
6. set_waveform返回ACK后，使用ACK频率、幅度和相位构造实际Signal，再applied。
7. 等capture_after_s后采新的图，Frame关联最新已确认的command_id和Signal。
8. 图像与完整决策保存在result.json对应目录，图像压缩写盘在控制循环结束后进行。

--initial-command-hz用于从上一轮已ACK的微调频率继续；这一路不再进行标定。
例如名义60000Hz、续测下发60000.442Hz，不能把60000.442当新名义档位。
这时initial_frequency_setting记录的是原始寄存器设置，需同时看
source_frequency_hz、continuation_command_hz和commands中的output_nominal_hz。

v2实测参数：frequency_step_hz=0.001、exposure_ms=8、settle_s=0.08、
command_latency_s=0.011、默认timeout_s=10，shape_tolerance_div=0.2，
hold_seconds=5、max_hold_gap_s=0.25。没有放宽上游成功判据。

## 必须区分的时序事实和假设

- UART返回的是端到端ACK，但没有FPGA命令实际生效时间戳。
  当前applied_at_s=(发送前时间+ACK返回后时间)/2，只是估计，不是硬件实测时刻。
- ACK频率只是寄存器设置反馈，不是模拟输出频率计读数。
- 初始校准的sent时间在调用校准helper之前，因此还包含少量主机计算时间。
- 相机时间估计为read完成后的monotonic−曝光时间的一半；没有V4L2内核时间戳，
  没有测量USB传输、显示刷新和示波器余辉产生的有效图像延迟。
- 相机启动丢8帧；探测阶段丢2帧、读取第3帧；保持阶段丢1帧、读取第2帧。
  CAP_PROP_BUFFERSIZE设为1，但没有回读核实驱动是否接受。
- frame_id为每次实际采集的主机递增序号，不是相机固件提供的序号。
- continuous_phase=True基于仓库RTL的设计推断，尚未读取烧录版本或独立实测累加器连续性。
- 超前角转板端滞后角：lag=(-lead)%360。实际ACK的lag再取反反馈给控制器。
- save()仍会在命令ACK后、调用controller.applied前同步写result.json；
  图片保存移出了循环，但JSON写盘和终端打印仍可能增加时序开销。
- 首次target_distance计算可能造成首次保持间隔较长，模块会按实际帧时刻重置保持计时。

这些是优先检查的接入因素，不能把拟合频差当独立物理真值，或仅凭timeout归因于模型。

## 图像处理的实际顺序

1280×720 MJPG图 → 镜头校正 → 四角透视到960×640（12×8div）
→ HSV选白色（V>=240，S<=120）→ 3×3开运算 → 按边缘、跨中心和面积过滤连通域
→ 横向裁掉左右各1div得到10×8div → 缩放到384×307 → 上下黑边补成384×384。
坐标要求是10×10div，缺失的上下各1div补黑，没有把8div拉伸为10div。

需要检查开运算是否切断细线、连通域过滤是否残留UI、下采样是否形成断点、
极端相位时曲线是否落到原8div可见区域之外，以及X实际幅度是否符合v2固定8div模板。
result.json的frames对应raw.jpg、warped.jpg和实际提交的384.png，可逐帧对照。

## 附带的成功和失败记录

| 目录末尾 | 场景 | 结果 |
|---|---|---|
| optical_lock_v2_1793449885706093957 | 30k，初始30000.216→30000.219Hz | complete，5.041734秒 |
| optical_lock_v2_1793450289623770972 | 60k，从60000.442Hz继续，最终60000.439Hz | complete，5.005732秒 |
| optical_lock_v2_1793450444010639401 | 70k，初始70000.505，后微调到70000.516Hz | timeout，最长保持0.573638秒 |
| optical_lock_v2_1793450472673982773 | 70k从70000.516Hz继续、45度起点 | needs_frequency_check，拟合歧义 |

同时保留60k/70k完整流程摘要、两阶段测频摘要和对应原始图。
full_workflow摘要中的lock_continuation是首轮失败后的人工续测记录，不代表首轮一次成功。
sampling_summary.max_hold_gap_s可能包含已重置的早期区间；有verified_window时
应以最终连续通过窗口的duration_s/max_gap_s评估5秒验收，不能把两个区间混为一段。

所有数据基于执行主机时钟；主机日期与客户端日期不一致，目录名不应用来判断用户当地日期。

## 上游及依赖

上游：Aeghur/2026_10_6，feat/optical-lock-v2，446bbbd。
lock_board中20个上游清单文件已与该提交逐字节比对一致，模型文件保留。
上游MANIFEST.json有8项与该提交自身内容不符，不是本地修改造成；未改动清单。
交接包另附SHA256清单，以本次实际文件为准。

v2本身只依赖NumPy/Pillow；但实际适配脚本经旧项目导入链还会导入torch，
并需要opencv-python和pyserial，实机相机还使用系统v4l2-ctl。
包中保留这些旧模块及测频权重以完整展示真实依赖；没有把实际代码改写成更理想的示例。

## 可复现命令

离线适配测试（不操作硬件）：

```bash
python -m unittest test_lock_hardware_adapter test_frequency_calibration -v
PYTHONPATH=lock_board python -m unittest discover -s lock_board/tests -v
```

仅在连接板卡、准备改变输出时运行：

```bash
python -u full_workflow.py --hardware
python -u lock_hardware_test.py --source-hz 70000 --initial-lead 45
python -u lock_hardware_test.py --source-hz 70000 --initial-command-hz 70000.516 --initial-lead 45
```

请先用已保存图像和日志复核接入的频率单位、相位符号、时序与几何契约，
再决定是否需要修改控制器，避免为了通过单次实测直接放宽0.2div或5秒判据。
