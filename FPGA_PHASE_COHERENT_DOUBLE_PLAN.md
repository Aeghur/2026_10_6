# FPGA相位确定的1倍频到2倍频切换方案

后续实现说明：正式协议现见项目WAVEFORM_CONTROL_PROTOCOL.md，已定义0x15 ARM_DOUBLE、
0x94 DOUBLE_ACK及自由运行Q38 FTW。对应上位机已接入msp_wave_link.py及circle_double_control.py。
下文“当前仓库32位”分析针对当时检查的旧远端RTL，不应当作新版Q38固件的精度结论。
新版固件尚未由本次工作烧录或核实，联调前必须配套更新FPGA和MSPM0。

## 结论和适用前提

在确定的DDS有效相位边界切换，可消除UART到达时刻随机造成的倍频初始相位差。
只在任意过零点改FTW，并不能无条件保证相对输入的目标相位。
零点切换不是数学上的必要条件，但便于同时保持输出电压连续和相位关系可预测。

本方案针对：X为输入正弦，Y为FPGA自由运行DDS；圆阶段多帧消歧后已经满足
Y超前X约90度，两路物理频率基本相同，且DDS相位累加器不被重置。
最终目标为Y频率约2X，广义相位psi=theta_y-2*theta_x=0。
频差、模拟链路延迟、原有图像标定误差仍需独立处理。

## 当前仓库实现的依据

检查的是collaborator/codex/radxa-sawtooth-control中的RTL；未确认板上烧录版本。

- zero_crossing_dds.v：config_valid时直接更新phase_step/phase_lag，
  carrier_phase继续累加，applied_phase_lag逐样本追赶phase_lag。
- 自由运行的有效查表相位为q=carrier_phase-applied_phase_lag。
- ads805.v：UART命令CRC确认后，更新配置并同时触发output_ack_request；
  ACK没有等到相位平滑结束，也没有DAC流水线生效标记。
- KeyBoardpro/User/fpga_capture.c：目前接收0x84空载荷ACK。

所以当前ACK证明命令经过FPGA收包，不足以证明在已知相位发生倍频、
新的相移已全部应用或对应样本已从DAC输出。

## 为什么优先选下降过零

设旧输出相位theta_1=theta_x+delta，最终希望theta_2=2*theta_x+psi。
在同一切换时刻，正确的初始相位应为：

```text
theta_2 = 2*theta_1 - 2*delta + psi  (mod 2*pi)
```

圆阶段delta=pi/2、目标psi=0时，theta_2=2*theta_1-pi。

- 旧Y下降过零：theta_1=pi，所需theta_2=pi，保留有效相位即可；
  电压均为零，导数方向均为负，适合相位连续切换。
- 旧Y上升过零：theta_1=0，所需theta_2=pi；若直接保留0，相位目标变为psi=pi。
- 因此不应仅凭ROM输出码等于中点就触发；应明确有效相位和方向。

正负号依赖已核实的X/Y方向、板端滞后角语义和模拟输出极性。
若圆实际是delta=-90度，下降过零同样按通式计算；不能只凭静态圆图猜相位方向。

## 最小固件改动：专用ARM_DOUBLE_AT_FALLING_ZERO命令

不要把所有SET_OUTPUT都改为等零点，否则原v2.1微调的时序假设会随之变化。
保留现有立即更新命令，新增专用的相位确定倍频操作。

### 接收与排队

CRC通过后，将命令序号、倍频操作和必要参数放入shadow寄存器，置pending。
接收不等于执行；有pending时拒绝第二个相关命令并回BUSY，避免覆盖事务。
限定自由运行正弦、频率非零、当前频率/幅度有效；记录相位与频率快照。
如需先改相位，应先完成原圆阶段的相位平滑，再允许倍频。

### 在DDS样本域提交

1. 等applied_phase_lag已经稳定，不在相位slew过程中判定过零。
2. 仅在sample_valid有效时，按有效查表相位q=carrier_phase-applied_phase_lag
   检测跨越半周边界，而非carrier_phase=0或ROM幅值=零。
3. 对目前正向、每样本相位步进远小于半周的DDS，下降过零可用
   q_old[31]==0且q_next_old[31]==1检测。定义好q_old/q_next_old对应的样本编号。
4. 在确定的样本边沿原子提交K_new=2*K_old，保留当前carrier_phase和
   applied_phase_lag，幅度和波形类型保持不变；同时将phase_lag目标保持为当前值，
   不得再叠加旧软件的固定90/180度相移或启动额外相位slew。
5. 明确“第一个使用新步进的查表样本”是n还是n+1，避免非阻塞赋值导致一拍偏差；
   累加器只允许一个更新分支，避免同拍普通累加覆盖特殊提交。

事件示意（不是可直接替换现有多always块的完整RTL）：

```text
IDLE -> PENDING -> WAIT_PHASE_SETTLED -> WAIT_FALLING_ZERO
     -> ATOMIC_COMMIT -> WAIT_OUTPUT_PIPELINE -> APPLIED_ACK -> IDLE
```

数字采样通常不会正好命中pi，不要等待32位相位字严格等于0x80000000。
最小方案保留跨越时的微小相位余量，会留下一个样本量级的确定性误差；
需要更高精度时，使用下面的相位映射方案，而不是盲目清零累加器。

### 超时与异常

从pending设置有界等待计数器，等待多个旧输出周期仍没有有效过零则回错误，
不静默退回立即更新。K为0、溢出、输出超范围、非正弦、相位持续变化均应明确拒绝。
复位取消pending，禁止复位后发送旧序号的成功ACK。

## 更高精度方案：同一采样时刻做谐波相位映射

当delta和目标psi可用时，原子映射有效相位：

```text
q2 = 2*q1 - 2*delta_word + psi_word  (mod 2^N)
C2 = q2 + L2                      (板端滞后角定义)
K2 = 2*K1
```

圆delta=90度、psi=0时，q2=2*q1-半周。
这一步是在同一硬件样本时刻做相位坐标变换，不是把累加器随意清零。
可以结合下降过零执行，电压不连续量由量化余量决定，需仿真评估。
若在任意非零相位执行映射，可能产生电压跳变，不能同时宣称电压无缝连续。

另一种架构是保留基频参考累加器，以q_out=ratio*q_ref+offset生成1/2倍频，
并在合适相位切换ratio。需要同步设计两模式offset，不能只左移相位字就假定
相对输入为0度。此架构还需评估倍频模式微调分辨率和现有绝对频率命令的映射。

## ACK、流水线与上位机配合

- zero_crossing_dds新增commit/applied事件及命令序号，ads805使用该事件驱动ACK，
  不再在收包时对该专用操作发送“已生效”。
- 将提交tag随实际ROM、幅度乘法、DAC寄存器路径传递；等首次新频率样本抵达
  数字DAC接口再确认。具体延迟按现有数据通路测定，不能直接把某个补偿参数当延迟。
- 建议专用ACK带applied_sample_counter、旧/新FTW、提交相位、状态。
  FPGA样本计数不能直接冒充Radxa的monotonic时间；需建立时钟映射，
  或明确记录其为独立时钟域并继续采用保守等待。
- MSPM0需要转发专用命令/ACK；Radxa等待真正提交完成后才采集新输出图片。
  如只延迟现有空ACK，也只能改善“已执行”语义，仍不能得到精确应用时间戳。
- 保留原SET_OUTPUT用于v2.1后续小数Hz修频和相位微调。只有1到2倍频切换使用新操作。
- 软件删除切换时固定加90/180度，后续v2.1仍用二倍频新图修正残余广义相位。

最小方案从硬件当前实际K直接左移得到K2，而不是由上位机重新计算两次FTW，
可避免两个独立舍入值不满足精确2倍的情况；之后的微调仍使用明确的绝对命令。

## 独立的精度问题：频率字分辨率

按当前仓库SAMPLE_HZ=25MHz、32位相位累加器估算：

```text
DDS频率LSB = 25,000,000 / 2^32 = 0.005820766 Hz
```

UART可写0.001Hz不表示实际DDS可分辨1mHz。这个约5.82mHz量化会影响微小频差闭环，
与是否在零点切换是两件事。只为修正倍频初相时可先保持原位宽；若要兑现mHz微调，
应另行评估增加相位累加器及FTW位宽，例如40/48位，并同步修改MSPM0计算和传输格式。
在25MHz下48位理论频率LSB约8.88e-8Hz，模拟参考时钟误差仍需标定。

## 验证与验收

1. RTL随机扫描UART命令抵达相位，验证总是在选定下降过零边界提交，
   K2=2*K1、无累加器清零、无幅度变更、无同拍更新覆盖。
2. ACK必须晚于真实commit及相应数字输出样本，序号匹配，busy/timeout路径可复现。
3. 在已知theta_x的仿真中设圆delta=+90度，检查切换后的theta_y-2*theta_x，
   明确量化余量，而不是只看sin值恰好接近0。
4. 扫描delta误差、相位平滑、频差、不同频率和流水线长度，区分初相误差和后续漂移。
5. 实机用逻辑分析仪观察sample_valid、commit、pipeline_tag及ACK，示波器记录切换；
   模拟滤波器的频率相关群延迟另行校准，数字提交正确不等于模拟引脚相位已正确。
6. 最后由Radxa对新图片进行二倍频目标验收，保持原0.2div/5秒/0.25秒标准。

参考：Analog Devices《Are frequency changes of a DDS phase coherent?》明确区分
phase-continuous与phase-coherent：
https://www.analog.com/en/resources/faqs/faq_are_frequency_changes_of_a_dds_phase_coherent.html

本文件仅为修改方案；没有修改、烧录FPGA/MSPM0，也没有改变当前DAC输出。
