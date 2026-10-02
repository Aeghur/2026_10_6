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

