# lock_board 独立接入与实机结果

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
