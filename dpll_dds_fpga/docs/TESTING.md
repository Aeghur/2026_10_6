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

40～100 kHz真实综合参数专项回归耗时较长，单独执行：

```powershell
.\scripts\run_high_band_test.ps1
```

该测试覆盖40、50、约60、约80和100 kHz，检查锁定、频率误差以及0°输出
是否保持同极性。当前五个频点全部通过；100 kHz仿真的稳态环路相位误差约
+2.63°，没有出现40 kHz反相。

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

Quartus Prime 18.1实测完整编译已通过，生成文件为
`quartus/output_files/dpll_dds.sof`。当前EP4CE6F17C8资源占用较高：逻辑单元
6078/6272（97%），但50 MHz慢速85 °C模型建立时间余量仍为+0.490 ns。

## MSPM0 Keil工程

打开：

```text
mspm0/Keil_DPLL/Project/dpll_dds.uvprojx
```

选择`DPLL_DDS_MSPM0G3519`目标并执行Build。工程使用ARM Compiler 6，
复用仓库`KeyBoardpro/Source`内的TI DriverLib，并在编译前调用MSPM0 SDK的
SysConfig脚本。Keil µVision 5实测编译结果为0 error、0 warning，生成HEX：

```text
mspm0/Keil_DPLL/Output/dpll_dds_mspm0.hex
```

烧录后先只连接PC串口，确认相位设置命令有回应；再连接FPGA串口并检查PC界面
可读取频率和锁定状态。详细引脚定义见`mspm0/Keil_DPLL/README.md`。

## 上板顺序

1. 暂不连接模拟输入，确认DAC静态中心接近0 V。
2. 输入10 kHz、约0.5 Vpp正弦，先设置0°。
3. 查询状态，等待`signal_present=1`和`locked=1`。
4. 依次设置0、45、90、180、270°，测量相位和频率。
5. 再测试1、3、10、20、40、60、80、100 kHz。
6. 记录每点残差，计算`phase_calibration(f)`表后再固化补偿。

当前MSPM0默认向FPGA下发`0x80000000`（180°）固定相位校准，用于抵消板级
模拟通道极性翻转。因此测试表中的目标角度应直接按PC界面填写，不再人工加180°。

高频异常时，应先运行根目录的实时诊断脚本：

```powershell
python dpll_control.py --port COM6 --phase 0
```

重点记录40、50、60、80和100 kHz的`lock`、`signal`、`phase_error`、`clip`
和`OTR`。若`lock=否`或`signal=无`，先排查输入幅度、ADC零点和采样质量；若
`lock=是`且`phase_error`接近0°，但示波器仍显示反相，则应检查DAC模拟链路、
示波器通道反相选项及探头参考地，而不是继续调整DPLL增益。

