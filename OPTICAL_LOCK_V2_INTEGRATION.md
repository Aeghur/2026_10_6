# lock_board 独立接入与实机结果

完整流程的最终图形可通过参数选择，默认仍为圆：

```bash
.venv/bin/python -u full_workflow.py --hardware
.venv/bin/python -u full_workflow.py --hardware --mode diagonal
.venv/bin/python -u full_workflow.py --hardware --mode infinity
```

diagonal以同频、0度相位为最终正斜线目标。
infinity现采用quadrature_refine_then_hardware_double_then_hold路线：
先在输入同频下标定，复用diagonal的v2.1流程完成多角度采集、正交相位对齐，
继续在正交圆/椭圆位置采多帧回归精修漂移。
原diagonal流程算出的直线0度修正先转换为正交90度修正，并在同频下用普通
set_waveform实际应用；新图仍须通过圆轮廓检查，不能仅凭命令值认定圆已形成。
随后调用arm_double_at_falling_zero，发送空载荷0x15，不使用set_waveform倍频，
不在切换时固定加90度或180度。FPGA在Y下降过零边界将当前Q38 FTW精确左移一位，
保留相位累加器、相移寄存器和幅度。
只有收到序号匹配、status=0、new_ftw=2*old_ftw的0x94 DOUBLE_ACK，才标记切换生效。
普通0x93不能确认这次操作。ACK代表新样本已更新DAC数字接口；
applied_sample_counter原样保存为FPGA时钟域，不能当Radxa时间。
上位机使用ACK接收时刻作为生效时间的保守上界，从该时刻额外等待settle_s并采新图。
ACK的Q38 FTW换算结果作为新实际频率，允许它与“旧mHz回执×2”有1mHz舍入差异。
圆精修后的新图误差须≤0.3 div才允许ARM_DOUBLE。
倍频ACK后，丢弃同频历史图片和相位锚点，直接采集新二倍频图片进行保持验收。
不再进行二倍频探测、修频或调相，也不再下发set_waveform；保持倍频ACK确认的输出。
最终图形仍须误差≤0.2 div、连续保持至少5秒；图形误差超限或采样间隔超过0.25秒
会重置保持计时，继续采图，不触发重探测或DAC命令。
circle_double_control.py为独立适配层，导出的lock_board控制器保持原样。
正交圆阶段不累计正式5秒，整体10秒窗口不重启，时间不足仍报告insufficient_time。
infinity的--initial-command-hz续测参数现在接收同频圆阶段的下发频率，不能传二倍频值。
日志中的target_switch_completed只有倍频初始命令获得ACK后才为true，不代表已完成保持验收；
output_stage会继续区分double_hold阶段，只有complete且success=true才通过。
若同频圆阶段未通过或时间不足，输出仍为同频，终端明确打印TARGET_NOT_APPLIED。
专用事务超时、错误或确认异常时，停止控制并记录output_state_verification_required；
不自动重试、不用普通倍频命令回退、不自动恢复旧频率。当前协议未定义输出状态查询命令，
应先人工核对实际输出，再决定是否显式重新初始化；不能仅凭上一次缓存判断没发生倍频。
同一链路实例的未确认专用事务会阻止再次ARM，直到显式绝对波形命令得到成功ACK。
必须先配套更新FPGA和MSPM0固件；本次只实现上位机，未发送实机ARM或烧录固件。

专用操作可在圆已确认、固件更新后单独运行：

```bash
.venv/bin/python wave_control.py --port /dev/ttyS4 --arm-double
```

协议依据：项目根目录WAVEFORM_CONTROL_PROTOCOL.md，0x15请求和17字节0x94响应。
探测和精修期间可能暂时出现其他形状，complete只按所选最终图形的5秒保持判定。
下方30k实测数据是此前circle模式记录，不代表另外两种模式已通过实机验收。

来源为 collaborator/feat/optical-lock-v2 的 446bbbd。
完整保留 lock_board/lock_control/ 及 assets/policy.npz、policy.json，
没有修改上游控制器、模型或验收阈值。摄像头、图像校正、串口和DAC驱动复用原项目。

适配层：lock_hardware_test.py。

```bash
.venv/bin/python lock_hardware_test.py --source-hz 30000 --initial-lead 81.36
```

输入必须是已确认的100 Hz名义档位、幅度已基本合适的正弦信号。
当前默认circle，4 Vpp；频率协议步长显式设为0.001 Hz，摄像头曝光8 ms，
等待显示/相机更新80 ms，默认控制窗口仍为10秒。

初始正弦采用现有多项式校准，例如30000→30000.216 Hz。
此后v2返回的signal.frequency_hz已经包含微调，是绝对下发频率，
直接经原MSPM0WaveLink.set_waveform发送，禁止再套多项式。
每次频率和相位命令先按预计生效时间retime_pending，端到端ACK后使用
实际量化频率、幅度和相位回传applied，再提交关联的新图。
硬件用滞后角，策略用超前角，适配层取反；频率/相位变化均要求累加器连续。

时间仍有实机适配限制：发送/ACK中点只代表生效时间估计，曝光中心由取帧时间估计；
没有硬件时间戳，也未识别烧录固件版本，不能将ACK到达当成精确生效时刻。
保持期间继续取新帧，图像压缩保存推迟到控制结束，避免写盘打断采样。
测试完成后保留最后实际ACK的设置，不自动恢复探测前相位。

## 本轮30 kHz实机结果

用户确认输入30000 Hz。结果目录：
artifacts/diagnostics/optical_lock_v2_1793449885706093957/。

初始下发30000.216 Hz，六帧均通过几何检查，拟合残余频差为-0.0026 Hz。
策略采用增益1，提出30000.219 Hz，串口ACK确认30000219 mHz。
同一校正命令的实际相位为超前281.02度，即协议滞后78.98度。
名义输入仍为30000 Hz；拟合中的estimated_source_frequency_hz包含寄存器坐标下
的设备偏差，不应作为新的名义输入频率或独立频率计测量值。

最终complete、success=true，连续有效保持5.041734秒，最终轮廓误差0.175246 div。
首次进入保持时出现过一次0.344秒采样间隔，保持计时按模块规则重置；
最后通过的5秒窗口另行记录于result.json的sampling_summary.verified_window。
这些结果只代表本轮circle图形的采样验收，不代表无限时长锁定或其他模式已实机验收。

## 离线验证及来源核对

3项上游控制器测试通过，包含正负频差和三种目标的模拟闭环；
3项本地适配测试通过，覆盖避免重复补偿、错误ACK和量化相位反馈。

```bash
PYTHONPATH=lock_board .venv/bin/python -m unittest discover -s lock_board/tests -v
.venv/bin/python -m unittest test_lock_hardware_adapter -v
```

导入的20个清单文件与446bbbd中的Git内容逐字节一致，模型文件完整保留。
上游MANIFEST.json有8个条目与该提交自身的实际文件哈希不符，本地未修改清单；
已在实测result.json记录不匹配项、Git比对结果及实际SHA256，便于复核。
