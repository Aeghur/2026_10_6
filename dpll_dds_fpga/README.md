# 独立 DPLL/DDS FPGA 工程

本目录用于开发纯 FPGA 数字锁相与 DDS 相移输出方案。它与仓库根目录的
`ads805.qpf` 延迟线工程相互独立，不复用原工程顶层，也不在开发阶段替换
当前可工作的固件。

## 目标信号链

```text
12 bit AD9226
  -> 去直流与输入有效性检测
  -> 粗频率捕获（1~100 kHz）
  -> I/Q 鉴相与低通
  -> DPLL 环路滤波器
  -> 32 bit NCO
  -> 用户相位与链路相位补偿
  -> 正弦生成
  -> 零点/幅度标定与限幅
  -> 14 bit DAC904
```

MSPM0只承担PC通信、参数设置和状态读取，不参与实时锁相。现有PC到MSPM0
功能继续兼容，PC不直接连接FPGA。

## 目录

| 目录 | 用途 |
|---|---|
| `rtl/` | 可综合Verilog模块和顶层 |
| `sim/` | Testbench、仿真输入与检查器 |
| `scripts/` | LUT、定点参数和测试数据生成脚本 |
| `constraints/` | 引脚与时序约束源文件 |
| `quartus/` | 独立Quartus工程文件 |
| `docs/` | 架构、定点格式、环路设计和标定记录 |
| `references/` | 外部参考模块的来源及适配说明 |
| `mspm0/` | 独立方案的MSPM0配置与状态驱动 |

## 固定硬件条件

- FPGA：Cyclone IV E `EP4CE6F17C8`
- 板载时钟：50 MHz
- ADC/DAC更新率：25 MSPS
- ADC：12 bit偏移二进制，实测中心码暂取2104
- DAC：14 bit，实测零电压码暂取8279
- 输入：1~100 kHz单正弦
- 相位命令：0~359.99度，沿用“输出滞后输入”的定义

## 开发阶段

1. 32 bit NCO、正弦发生器和相位偏置。
2. 固定频率下的I/Q鉴相、低通与锁相。
3. FPGA粗频率捕获，覆盖1~100 kHz。
4. 动态相位命令、锁定检测和异常回退。
5. DAC幅度、零点及频率相关相位补偿。
6. 全频段仿真、Quartus时序检查和上板扫频标定。

## 当前实现状态

以上六阶段的第一版RTL均已实现，包括粗频率捕获、I/Q鉴相、CORDIC atan2、
PI锁相、DDS相移输出、DAC标定、MSPM0配置/状态协议和独立Quartus工程。
算法参数已通过Icarus Verilog回归；实际相位补偿表仍必须根据板上扫频结果生成。

运行全部仿真：

```powershell
cd E:\eishero2q\ad9226\dpll_dds_fpga
.\scripts\run_tests.ps1
```

Quartus入口为`quartus/dpll_dds.qpf`。详细设计和上板步骤见`docs/`。

## 可参考资源

`E:/eishero2q/学长资料`中的`Cordic.v`和`Cordic_Atan2.v`可分别用于正交
载波生成和全象限鉴相。引入前需要单独检查复位极性、Q23角度定义、25级
流水线延迟、有效信号接口、位宽增长和50/25 MHz时序，不能直接复制后接线。

