已知频率的摄像头李萨如调相模块 v1
================================

交付内容
这是独立的Python板端模块，只依赖NumPy和Pillow，CPU即可运行，不依赖原工程、PyTorch或训练环境。
输入：已完成透视/镜头校正的XY图、图像对应的实际正弦输出状态、帧号与采集时间。
输出：下一步相位命令，或等待、重新取图、检查频率、调整幅度、测量完成等状态。
模块不操作相机、串口、SSH或DAC。硬件收发由接入者自己的适配层负责。

原训练策略在2127个样本上都选择紧凑六张相移图，因此运行时将这个选择固化。
迁移的是训练验证过的流程与几何拟合算法，无需运行一个始终选同一动作的网络。
TRAINING_PROVENANCE.json记录原训练权重摘要及导出依据，不把仿真成功率冒充实测成功率。

目标模式
diagonal：未知输入在X、DAC输出在Y，同频，目标相位0°，形成正斜线。
circle：同频，目标相位+90°，形成正交圆/椭圆；实际成为圆还要求等幅和相同比例。
infinity：DAC频率为输入的二倍，控制广义相位 Psi=输出相位−2×输入相位 为0°。
当前不支持反过来输入二倍频、DAC基频的配置；交换示波器轴后需重新定义模式。

频率和幅度前提
source_frequency_hz必须是已经确定的输入频率，严格为100 Hz整数倍，范围1k..100k。
输入未知相位不需要提供；signal只描述当前DAC真实输出，绝不能填写未知输入状态。
开始前将DAC切成正弦，频率设为目标1:1或2:1比例，幅度设置合适且图形完整。
本模块仅修改相位，所有命令保留原频率和Vpp；不做重新测频或幅度闭环。
频率档位正确但实际设备时钟仍可能有漂移。默认测得输出残余频差大于0.05Hz会要求检查频率，
不允许通过相位模块悄悄把输入频率改成非100 Hz整数倍。
实际锁频/时钟校准应由频率子系统负责，几何图像也需要现场验证。

一、ROCK 4D部署
需要板上已有Python3.10或以上，建议使用系统Python3.11/3.12。
把整个文件夹复制到板上，例如Windows CMD：
scp -r phase_board radxa@192.168.137.2:/home/radxa/
IP请替换为当前板卡地址；先解压ZIP，再复制整个包目录。

在板上SSH终端：
cd /home/radxa/phase_board
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
python -c "from phase_control import PhaseController; print('phase module ready')"

如果提示缺少venv/pip或setuptools，请先安装该系统对应的python3-venv/python3-pip等包。
若editable安装因构建工具缺失失败，也可以不安装本包：在包根目录运行Python，或把此目录加入PYTHONPATH。
离线环境需预先准备与板卡Python和aarch64匹配的NumPy/Pillow依赖；不要复制Windows虚拟环境到板卡。
requirements.txt记录兼容范围，本包没有附带离线依赖镜像。

Windows电脑验证：
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
测试只使用独立虚拟位图和模拟DAC，不会打开硬件。

二、Python接入
from phase_control import PhaseController, Signal, Frame

ctrl = PhaseController(
    source_frequency_hz=3000,
    mode='circle',
    continuous_phase=True,   # 只有确认硬件改相位时不重置累加器，才传True
    target_height_div=None, # 已设置合适幅度时可省略；也可指定2/4/6/8div作额外检查
)

actual = Signal(frequency_hz=3000, vpp=4, phase_deg=0)
decision = ctrl.start(actual, command_id='initial', applied_at_s=board_start_time)

# 每次得到一张新图后调用一次：
decision = ctrl.step(
    Frame(image=rectified_pil_image, signal=actual,
          frame_id=camera_sequence, command_id=actual_command_id,
          captured_at_s=effective_capture_time, calibration_id='geometry-session-1'),
    now_s=board_now,
)
print(decision.to_dict())

一次新测量新建一个ctrl；或重新调用start重置该实例。模块不使用跨测量的历史校准。
不能只调用一次就期望任意相位都被唯一确定：静态椭圆存在相位正负歧义。
连续调用会主动采六张0°/90°交替探测图，利用实际时刻联合拟合相位和漂移，再校正。
详细循环示例见 examples/board_loop.py，替换camera和dac适配接口即可。

三、如何执行返回的相位命令
decision.status == 'set_phase'时，decision.command含：
command_id：本次唯一指令编号。
signal：频率/Vpp保持原值，相位为建议的相移寄存器偏移。
apply_at_s：建议在什么板端单调时钟时刻生效。
reset_accumulator：固定False，绝对不能清零DDS相位累加器。

在真正发送前，如果预定生效时刻改变，必须调用：
cmd = ctrl.retime_pending(actual_planned_application_time)
然后发送cmd.signal.phase_deg，并在硬件确认后调用：
decision = ctrl.applied(cmd.command_id, actual_signal, actual_applied_at_s)

applied必须填写硬件实际使用的设置和生效时间；不能刚发送就编造已经生效。
默认允许相位量化与请求相差0.6°，支持1°步进的四舍五入。实际设置必须仍反馈到后续Frame.signal。
如果应用延迟误差按拟合漂移换算超过0.5°，模块会自动重新探测，而不是宣告校正成功。
如果硬件不能预约生效时间，适配层需测量传输/处理延迟并预测实际生效时刻，ACK记录实测值。
不具备可靠时序时应先验证静态近零频差条件下的精度，不能把电脑收图时间当采集时间。

关键概念：Signal.phase_deg是附加相位寄存器偏移，不是载波瞬时角度，也不是“相对输入的相位”。
增加phase_deg定义为相位超前；若硬件参数代表滞后，适配层负责取反并反馈统一语义。
模块已处理六张探测图最后一张的+90°偏移，不要在建议相位上再自行减90°。
不要在相位命令期间改频率或幅度；如有修改，重新start。

四、图像要求
输入PIL灰度/RGB图，或uint8的384x384灰度/RGB NumPy数组。
OpenCV的BGR数组需先转换为RGB。模块不依赖OpenCV，也不会自行打开/dev/video*。
需要先完成镜头/透视校正和轴标定，标准图像覆盖X/Y各10div，即[-5,+5]。
像素(0,0)对应(-5,+5)，X向右、Y向上；完整8div图形通常在约38..345像素以内。
仅保留波形和较暗网格，去除示波器菜单、读数、白色边框、纸板等UI/背景。
不允许图形被裁切。若真实轨迹略微超出原8x8参考区，请扩大物理校正范围并保持10div坐标标定。
不能把裁切图硬拉伸后声称它仍是同一10div标定。
calibration_id在一次测量中固定。移动摄像头或重标定后，新建测量实例。
frame_id必须来自真实新采集帧，递增；不要将同一张旧截图包装成不同编号骗过新鲜度检查。
captured_at_s是这一图像代表的有效采集/曝光中心时刻，now_s、ACK时间使用同一板端monotonic时钟。
图像显示/采集的旧设置残留需要先消失；settle_s需按真实示波器余辉、曝光和缓存调整。
默认settle_s=0.08秒只是接口起点，不代表已测量你们的示波器延迟。

五、状态处理
set_phase：执行并ACK命令。
await_ack：还有命令未ACK，不能用新图替代ACK。
capture / wait：等到capture_after_s，再获取新图。
recapture：重新采集；可能是旧帧、设置未对应、暂时几何失败或目标偏离，reason会说明。
hold：当前形状在容差内，继续取图检查。
complete：success=True，已通过至少5秒、最大相邻帧间隔0.25秒的相位轮廓采样检查。
needs_better_image：连续多帧拟合失败，改善曝光/对焦/线宽/遮挡后重开测量。
needs_frequency_check：频率比例、残余物理漂移或相位拟合存在问题，交回频率子系统。
needs_amplitude_adjustment：可选的高度检查未通过，由幅度子系统处理后重开测量。
needs_calibration / restart_required：图像标定缺失或变化，先修复几何前提。
timeout / not_converged：没有完成，不可当作锁定成功。

默认总测量窗口10秒，相位轮廓误差<=0.2div、至少保持5秒；帧太旧或中间丢帧过久会重新计时。
这是基于新鲜采样帧的验证，不是对两个采样时刻之间全部连续时间的证明。
complete只表示本模块相位轮廓检查通过，不代表整道题的频率/幅度/总流程已验收。
complete后如需继续监测长期漂移，应由上层开启新监测/测量周期。

六、其他语言通过JSON调用
可在C/C++/其他程序里启动长期Python子进程：
python -m phase_control --source-hz 3000 --mode circle --continuous-phase
每行输入一条JSON，每行输出一个JSON；不要每帧重启进程，否则会丢失探测状态。

首次输入：
{"op":"start","signal":{"frequency_hz":3000,"vpp":4,"phase_deg":0},"command_id":"initial","applied_at_s":100.0}
图片输入（路径自行替换）：
{"op":"frame","image_path":"/home/radxa/frame.png","signal":{"frequency_hz":3000,"vpp":4,"phase_deg":0},"frame_id":1,"command_id":"initial","captured_at_s":100.1,"calibration_id":"g1","now_s":100.11}
返回command后可先调整应用时刻：
{"op":"retime","apply_at_s":100.15}
收到硬件ACK后（下面相位只是示意，必须使用实际返回/实际执行的值）：
{"op":"applied","command_id":"phase-1","signal":{"frequency_hz":3000,"vpp":4,"phase_deg":90},"applied_at_s":100.15}
继续提交新图，直到complete或需上层处理的状态。

七、批量纯函数
如果上层已经自行采集六张探测图片，可直接调用：
from phase_control import estimate_phase_batch
result=estimate_phase_batch([(image, effective_time_s, relative_probe_offset_deg), ...],
                            output_hz=3000, mode='circle', base_phase_deg=0,
                            command_delay_s=0.02)
这个函数与电脑端多帧相位拟合同源，返回相位、漂移、建议相位和约定生效时间。
调用者要自行保证频率/幅度固定、图像校准、新鲜度、时序、100Hz前提，并自行做校正后保持验收。
建议初次接入优先使用PhaseController，它包含ACK、状态和保持检查。

八、测试边界
本包独立虚拟闭环测试覆盖正斜线、圆和∞形，检查旧图、重复帧、错误ACK、标定变化和丢帧保持。
导出的分支算法仍可能拒绝粗线或严重混叠图，之前仿真中已有这类拒绝案例，不能放宽判据伪造成功。
还没有在你们的ROCK 4D、相机和DSO-X 2022A组合上完成实物联调。
原训练权重只作为来源记录；本包不加载权重，不开始训练，不修改原训练目录。
运行验证：python -m unittest discover -s tests -v
