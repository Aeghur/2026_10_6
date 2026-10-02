# 编译与测试

## RTL回归

在PowerShell中执行：

```powershell
cd E:\eishero2q\ad9226\dpll_dds_fpga
.\scripts\run_tests.ps1
```

回归内容：

1. 1、10、100 kHz粗测频和串行除法器；
2. DDS的0、45、90、180、270°输出；
3. DPLL在10 kHz锁定并跳变到20 kHz重新捕获；
4. UART参数帧、CRC和状态返回；
5. 独立顶层的完整语法展开、板级时钟与失锁零电压输出。

闭环Testbench使用缩短的IIR时间常数加速RTL仿真；综合顶层仍使用
`LPF_SHIFT=16`和`LOOP_DECIMATION_LOG2=10`。

## Quartus

打开：

```text
quartus/dpll_dds.qpf
```

执行完整编译并确认：

- 无RAM未推断导致的寄存器超限；
- 50 MHz主时钟时序通过；
- 正弦ROM已由`sine_1024x16.hex`初始化；
- ADC和DAC引脚与旧板卡一致。

## 上板顺序

1. 暂不连接模拟输入，确认DAC静态中心接近0 V。
2. 输入10 kHz、约0.5 Vpp正弦，先设置0°。
3. 查询状态，等待`signal_present=1`和`locked=1`。
4. 依次设置0、45、90、180、270°，测量相位和频率。
5. 再测试1、3、10、20、40、60、80、100 kHz。
6. 记录每点残差，计算`phase_calibration(f)`表后再固化补偿。

