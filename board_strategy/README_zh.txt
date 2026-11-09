李萨如视觉闭环板卡接口 v1 — 部署与交接说明
=========================================

一、交付物与边界（请先读）
------------------------
将 lissajous_board_strategy_v1.zip 整个发给接手同学即可。解压后目录为
lissajous_board_strategy/，包含 Python 包、已经导出的模型、接口示例、自检。
不需要原训练目录、训练缓存、旧V1/V2模型、电脑上的虚拟环境或本聊天记录。

部署位置：ROCK 4D 上的 Linux/Python 用户态；FPGA/MSPM0 负责执行 DAC 指令。
不在 FPGA RTL 或 MSPM0 裸机中直接运行 PyTorch；也不假设存在现成串口协议。
导入包、启动 JSON 接口、加载模型都不会自动拍照、训练或操作 DAC/GPIO。

已训练部分：V3 单次/连续专家、图像路由器、窗口选择策略、已保存的校准参数。
当前模型来自原 hybrid_runs/mixed_v3/best.pt，第13轮，12883次更新。
导出文件只含推理所需模型/配置/校准，不包含优化器、训练数据或绝对电脑路径。
assets/manifest.json 记录来源哈希、模型哈希和原始推理代码快照哈希。

新增工程控制：像素测量调幅、+90度两帧探测调相、小频差估计、5秒采样保持。
它们复用了已有几何/相位方法并串联成控制器，不是一个已经训练并上板验证过的
端到端策略。frequency_only 是模型训练范围；hardware_validated=false 是事实。

此前仅测频模型的512组独立新仿真：单次98.44%，连续96.48%，合计97.46%；
是100Hz网格精确命中率，不是整题合格率，也不是本SDK实物闭环成功率。
本SDK另有三种目标的完整有限像素仿真衔接自检；详见 VALIDATION_zh.txt。
不要把少数自检场景理解成全频段、所有相机或实物10秒/5秒保证。

二、目标定义与坐标
------------------
Target(mode='diagonal', height_div=8)  正斜率对角线：输出频率约等于输入。
Target(mode='circle', height_div=8)    圆/椭圆：输出频率约等于输入，相位正交。
Target(mode='infinity', height_div=8)  左右对称“∞”：输出频率约为输入两倍。

height_div 支持2、4、6、8。按0.5V/div，初始期望输出为1、2、3、4Vpp；
后续根据图像实测高度校正DAC增益。输入原信号始终不作为接口参数输入。
圆/对角线要求本身对应8div；将height设成2/4/6用于额外幅度设置，会得到相应
压缩的目标形状。该设置不会把X轴原信号一起缩小。

原题相机自动闭环条款是要求5；本项目讨论中有时称为“第四题/第四步”。
目标控制时间<=10s、稳定>=5s、幅度误差<=0.2div。
几何轮廓阈值0.2div是可解释的工程判据，原题没有给出这个静态轮廓距离公式。
SDK同时检查目标轮廓、覆盖、幅度、分支模型，故判定通常比“漂移<1div”更严格。

控制器只观察图片。signal 参数是“拍下这张图时我们实际在DAC上输出什么”，
不是输入信号的真实频率/幅度/相位，不能由测试夹具偷偷填写未知信号参数。

三、硬件适配必须满足的约定
--------------------------
本队能力确认（2026-10-20）：用户已确认当前DAC支持所需锯齿波，以及改频率时
保持相位累加器连续。因此本队集成时可以显式设置ramp_supported=True、
continuous_phase=True，如下方Python范例所示。该确认不等于本SDK已经完成实机
时序/协议/精度验收；hardware_validated字段仍保持false。

1. 必须能输出正弦波和以下锯齿探测波。仅有任意频率正弦DAC还不够。
   锯齿从 -Vpp/2 线性上升到 +Vpp/2，然后线性回扫；直流中心0V。
   rise_time_us 是上升用时；回扫用时=0.05*上升用时，不是总周期的5%。
   总周期=1.05*T，frequency_hz=1e6/(1.05*T_us)。探测幅度固定4Vpp。
   T候选：73、110、175、223、809、1750、3500us；先测175和1750。
   例如175us对应5442.1768707483Hz。不能用5442Hz正弦代替它，也不能把T当周期。
   测频模型不会神奇地将示波器设为单次锁存；实际采集由示波器决定。

2. 相位单位为度，phase_deg增大表示“超前”，不是“滞后”。定义为：
   y(t)=Vpp/2 * sin(theta_DDS(t)+phase_deg*pi/180)。
   范围[0,360)。若板卡协议用滞后角：lag_deg=(-phase_deg)%360。
   Vpp是峰峰值V，不是峰值，不是数码管档位，也不是DAC码值。

3. 改相位偏置及微调频率时，必须保留DDS相位累加器连续运行，不能重置。
   相位校正指令的reset_accumulator始终false。
   必须支持非100Hz整数的频率精调；原信号在100Hz网格不代表DAC时钟无ppm误差。
   相位分辨率建议<=0.1度；回执容差0.15度。频率回执容差max(0.002Hz,0.5ppm)。
   频率字换算应在硬件适配层完成。超差会拒绝回执，不能把请求值冒充实测设置。
   正弦输出至少要覆盖1..200kHz；200kHz附近还应为精调留余量。
   DAC幅度量程要覆盖4Vpp且允许模拟链路增益修正；显式传入实际硬件上限。

4. 这里不启用/读取T14比较器或ADC输入频率，不依赖原信号电气支路。
   接入视觉自动模式时必须由固件选择独立、可编程的DDS模式。
   原来的“由T14测频驱动DDS”模式不会自动变成相机独立控制模式。
   FPGA如尚未实现独立DDS/锯齿/连续相位控制，需要硬件同学补上并标定协议。

5. 确认DAC确实生效后才调用applied()。一个指令有唯一command_id。
   UART发送成功或写入发送缓冲，不等于DAC已经更新完成。
   同一个command_id只能确认一次；未知/旧回执拒绝。
   Controller只返回“建议下一步”，不擅自发送串口包、改寄存器、蜂鸣或点灯。

Capabilities默认ramp_supported=false、continuous_phase=false。
只有适配方验证硬件满足约定后才能显式设true；否则start返回failed说明缺项。
未提供串口包格式是一个清晰的集成边界，不代表已经完成FPGA固件接线。

四、图片与每次测量的标定
------------------------
模型图片必须是384x384，物理范围为10x10div：X、Y均[-5,+5]，中心在(191.5,191.5)。
8x8div参考框约在像素38..345。不能把8x8裁剪区直接缩放为384x384。
图像Y像素向下，绘图物理Y向上；正斜线在图像里从左下到右上。
白色/明亮波形、暗背景。网格、文字、屏幕白边、图标要在预处理阶段压低/屏蔽。
测量需要的整条波形都应可见；不能补画屏幕之外的轨迹后当成真实观测。

每次测量开始重新建立几何标定；这次测量期间固定使用它。移动相机、改变分辨率、
示波器档位/位置、镜头焦距时停止控制，重做标定，新建Controller。
calibration_id就是该次标定的标识，不得复用旧ID冒充新标定。

与原vision_capture/live_scope.py会话标定衔接：
  from lissajous_control import Calibration, prepare_image
  cal = Calibration.from_session_reference('本次session_calibrations/.../reference.json')
  clean = prepare_image(raw_bgr, cal, channel_order='BGR', threshold=180,
                        valid_mask=canonical_plot_mask)
  calibration_id = cal.session_id

threshold=180只是初始示例，必须看实际波形/网格亮度调节；不要让白边进入波形掩模。
valid_mask是384x384、已经处于同一标准坐标的绘图区掩模，非零为有效，不是原图掩模。
如果上游已输出干净、正确尺度的384x384图片，直接prepare_image(image)验证即可。
from_session_reference只接受带measurement_session的本次reference；不加载早期的
provisional circle calibration.json，不用圆的外接框强行替代真实网格尺寸。
单应矩阵中的div-Y沿用原标定向下为正；prepare_image已处理这个坐标约定。
本包不自动去除镜头畸变；如有棋盘格内参校正，应先去畸变，再用匹配坐标的单应矩阵。
不能把原始畸变图的H用在另一个已去畸变坐标系上。

相机曝光和示波器余辉会影响连续扫描叠加。训练配置为曝光8ms、有效余辉2ms，
使用max-union显示近似；实际设备未标定到这个模型。需记录和检查实际采集条件，
不应假设换相机/曝光后分类置信度仍准确。单独拍若干新实物样本做验收。

五、ROCK 4D部署（操作由接手者执行）
---------------------------------
推荐64位Linux、Python>=3.10，CPU推理即可。当前没有RKNN/NPU版本，也没有CUDA要求。
不要把电脑Windows的.venv复制到ARM板上。

电脑CMD上传（替换成当前板卡地址）：
  scp "lissajous_board_strategy_v1.zip" radxa@192.168.137.2:~/

开发板SSH终端：
  uname -m
  python3 --version
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip unzip
  mkdir -p ~/lissajous_deploy
  unzip ~/lissajous_board_strategy_v1.zip -d ~/lissajous_deploy
  cd ~/lissajous_deploy/lissajous_board_strategy
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install "torch>=2.6,<3" --index-url https://download.pytorch.org/whl/cpu
  python -m pip install .
  python -c "import torch; import lissajous_control; print(torch.__version__, lissajous_control.__version__)"

如PyTorch提示No matching distribution，核对aarch64、Python版本与官方CPU安装选择器，
不要安装x86_64的whl。网络故障和没有匹配二进制包是不同问题。可在联网的同架构环境
下载对应whl后离线安装。本包未包含PyTorch本体，ZIP约2MB不等于完整运行环境大小。

官方资料（部署环境参考，不表示已经在本机板卡上执行）：
  https://docs.radxa.com/en/rock4/rock4d
  https://pytorch.org/get-started/locally/

运行自检：
  python -m unittest discover -s tests -v
该命令仅运行有限仿真和接口测试，不训练、不启动相机、不输出DAC。
板卡上运行时间取决于CPU；桌面测得的耗时不能替代板卡推理延迟验收。

六、Python核心调用方式
---------------------
这是有状态的逐帧函数接口。不能为每张图重新构造Controller，否则会丢掉历史窗口、
探测相位对和5秒计时。每次测量/按键启动新建一个Controller；单实例串行调用。
任意一张正弦李萨如照片通常不能唯一给出输入的绝对频率；start会先要求输出已知
锯齿探测波，再接收对应图片。初始输出可以是1kHz、1Vpp、0度，不能跳过探测后
把普通正弦照片误标成175us锯齿窗口传给测频模型。

  import time
  from lissajous_control import Controller, Target, Capabilities, Signal, Frame

  c = Controller(calibration_id='本次标定ID',
      capabilities=Capabilities(ramp_supported=True, continuous_phase=True,
                                max_frequency_hz=200100, max_vpp=5))
  decision = c.start(Target('infinity', 8),
                     current_signal=Signal('sine', 1000, 1, 0))

处理返回decision：
  if decision.status == 'apply':
      cmd = decision.command
      actual = my_dac_apply(cmd.signal)  # 接手者实现；返回真实生效的Signal
      c.applied(cmd.command_id, actual, applied_at_s=time.monotonic())
      # 之后清空旧相机缓冲，取得曝光发生在c.capture_after之后的新帧。
  elif decision.status == 'capture':
      # 保持当前输出，等待decision.capture_after_s后取得新帧。
      pass

下一次图片的调用：
  decision = c.step(Frame(
      image=clean_384_image,
      signal=actual_applied_signal,
      command_id=c.command.command_id,
      frame_id=camera_sequence,
      captured_at_s=exposure_midpoint_monotonic,
      calibration_id='本次标定ID',
  ))
  print(decision.to_dict())  # 全部可序列化为JSON

完整循环适配范例见examples/integration_loop.py的run(adapter)。
该文件不包含虚构的串口包，也不会在导入时运行。adapter需要实现：
  apply(Signal)->实际Signal；capture_after(t)->(干净图,曝光时间,相机帧序号)；
  signal、calibration_id、max_frequency_hz、max_vpp属性。
成功后由上层程序发声光提示。失败/停止时保持、关闭或输出零点由上层硬件策略决定，
SDK不会偷偷改变硬件状态。通过c.stop()取消；不会把用户取消当成成功。

七、返回值与状态机
------------------
Decision关键字段：
  status            apply/capture/await_ack/done/failed/stopped/idle
  stage             frequency/phase_first/phase_quarter/hold/done/failed等
  reason            稳定的英文原因及必要诊断
  command           apply时的Command；含command_id、signal、settle_s、reset_accumulator
  capture_after_s   capture时最早允许的曝光时间，板卡monotonic秒
  success           只有done才为true
  telemetry         源频率估计、模型候选、几何测量、调相、控制耗时、保持耗时等

常见原因：
  ramp_required_by_trained_model          固件尚未声明支持所需探测波
  continuous_phase_required_for_quadrature_probe  尚未声明相位连续
  frequency_unconfirmed_after_all_windows 采满窗口仍无可靠结论；不强行猜一个正弦输出
  stale_frame / duplicate_or_nonmonotonic_frame   旧图/重复/非单调帧
  frame_does_not_belong_to_applied_command 图片与DAC回执不匹配
  invalid_calibrated_image / no_usable_waveform   图像尺寸/类型不符或无有效波形
  waveform_clipped                       部分波形被裁切，无法实测幅度/相位
  calibration_changed                   本次测量标定变化，必须重新开始
  phase_geometry_invalid                当前曲线不支持两分支模型；不能硬套调相公式
  control_time_limit_exceeded            10秒内没有建立目标

frequency阶段每个窗口两张新帧，图像决定继续窗口还是转正弦。
phase_first检查幅度后拍当前相位；phase_quarter要求实际输出+90度后再拍。
静态图对相位存在歧义，两次有已知动作的观测用于消除它。
若两帧超时或分支模型不符会拒绝，而不是把假设当观测。
连续相位估计最多20对，错误可重新测频一次；超时/无法确认就返回failed。

小频差修正：比较两次相位探测对，去掉已知DAC偏置，
delta_f≈wrap(delta_phi)/(360*delta_t)，输出频率减去delta_f。
仅在间隔<=2秒、相位变化<90度、名义频率附近±5Hz范围内采用。
这是有小漂移假设的工程估计，有相位绕回混叠风险；不宣称是全局无歧义频率估计。
大的频差应重做锯齿测频，不能靠这个公式修复任意错误类别。

八、时间戳、保持验证与旧帧问题
------------------------------
1. 所有时间来自同一块板的time.monotonic()。不要混用电脑墙钟、板卡墙钟和模拟秒。
   captured_at_s是相机曝光时刻/中点，不能用“读到JPEG的时刻”冒充旧缓存的拍摄时刻。
   解码、USB、示波器余辉、队列延迟都需要适配层处理；命令后丢弃旧缓冲。
2. 默认DAC生效后等待80ms；同一窗口两图至少间隔40ms；旧于300ms的图拒绝。
   曝光、显示余辉更长时要调整settle_s，同时检查是否仍满足相位探测最大250ms间隔。
3. frame_id必须是真实采集序号且严格增加。同图重复读HTTP/文件不算新采样。
   两张内容相同但确实重新曝光的锁存图允许；代码不能仅凭像素哈希区别它们。
   图片command_id标记必须来自真实命令/相机清缓冲流程，不能仅在客户端随便盖章。
4. 建立目标后，维持不变的正弦参数，连续取得满足形状/幅度的图像至少5秒。
   默认样本间隔超过200ms就重置保持计时；Options最多允许250ms间隔。
   旧图、重复图、无效图会重置计时。仅首尾两图相隔5秒绝不会被认作成功。
   图形变化>=1div或失去目标时重新校正；控制期限已过则返回失败。
5. done表示完成了一次新帧采样验证，不表示未来永远稳定，也不证明两帧之间
   没有短暂抖动。提高采样率、检查振荡器频漂、上板长时间测试仍有必要。
6. control_time_s是首次开始当前成功保持段时距start的时长；5秒证据可在10秒之后
   才积满，不能把“调到目标+继续观察5秒”的总时长错误当成10秒控制期限。

九、C/C++或其他语言调用（持续子进程）
-----------------------------------
激活环境后启动一个长期进程：
  python -m lissajous_control.cli
或安装后的命令：lissajous-control
stdin每行一个JSON，stdout每行一个JSON，状态保存在同一子进程内。
不要每发一张图就重新启动子进程，也不要把该进程未经限制暴露到网络。

启动示例（本行只返回第一条探测指令，不控制硬件）：
  {"op":"start","calibration_id":"measurement-20261020-A","target":{"mode":"circle","height_div":8},"capabilities":{"ramp_supported":true,"continuous_phase":true,"max_frequency_hz":200100,"max_vpp":5}}

收到command后，硬件执行并回执：
  {"op":"applied","command_id":"返回的ID","signal":{"waveform":"ramp","frequency_hz":5442.1768707483,"vpp":4,"phase_deg":0,"rise_time_us":175,"reset_ratio":0.05},"applied_at_s":实际板卡monotonic秒}

提交一张已经标定和清理后的图片：
  {"op":"step","image_path":"/tmp/clean_001.png","command_id":"返回的ID","signal":{"waveform":"ramp","frequency_hz":5442.1768707483,"vpp":4,"phase_deg":0,"rise_time_us":175,"reset_ratio":0.05},"frame_id":1,"captured_at_s":实际曝光monotonic秒,"calibration_id":"measurement-20261020-A"}

停止：{"op":"stop"}
上面占位的时间值必须替换成JSON数字；不要直接复制带中文占位的整行当可执行JSON。
所有对硬件的实际操作仍由父进程/适配层负责，Python服务只算下一步。
raw图片请先通过Python prepare_image或上游程序处理，JSON step不自动猜图像标定。

十、接手者最少需要实现与验收的内容
---------------------------------
A. DAC适配：独立DDS模式、锯齿波、Vpp/频率字/超前相位转换、相位连续、回执。
B. 相机适配：新帧队列、命令后清旧帧、真实曝光时间、当前会话标定、波形/绘图区掩模。
C. 应用：三个启动键分别传Target模式，幅度按键传height_div，done才声光提示。
D. 实物验收：已知源频率仅供测试记录，绝不传入Controller；覆盖1k、100k边界、
   连续高频易错区、两倍频200k输出、全部幅度、多个初始相位、轻微时钟偏差；
   保存原图、标定、所有请求/回执/时间戳/decision；检查真实10秒与5秒指标。
E. 先做只读/人工执行指令的联调，再接自动执行。包内没有自动连接现有板卡的脚本，
   本次交付不改原FPGA/MSPM0工程，也没有自动运行正式训练或连接SSH。

如果硬件约定或图像条件不满足，不应去掉failed/未确认保护来制造“成功”。
应在适配层补齐功能、重新做图像实物标定，或重新采集数据训练相应条件的模型。
