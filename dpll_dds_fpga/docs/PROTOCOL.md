# MSPM0与DPLL FPGA协议

物理层为2 Mbaud、8N1。帧格式延续现有工程：

```text
A5 5A TYPE SEQ LENGTH_LO LENGTH_HI PAYLOAD CRC_LO CRC_HI
```

CRC为CRC16-CCITT，初值`0xffff`、多项式`0x1021`，计算范围从TYPE到
PAYLOAD最后一个字节。多字节整数均为小端。

## 设置参数：TYPE 0x10

载荷长度11字节：

| 偏移 | 长度 | 内容 |
|---:|---:|---|
| 0 | 4 | 输出滞后相位字 |
| 4 | 4 | 相位校准字，允许自然回绕表示负数 |
| 8 | 2 | DAC峰值幅度码，硬件限制到8104 |
| 10 | 1 | control bit0：输出使能 |

角度到相位字：

```text
phase_word = round(centidegrees × 2^32 / 36000)
```

## 请求状态：TYPE 0x01

载荷为空。FPGA返回TYPE `0x90`、16字节载荷：

| 偏移 | 长度 | 内容 |
|---:|---:|---|
| 0 | 4 | 当前NCO phase_increment |
| 4 | 4 | 符号扩展后的Q23相位误差 |
| 8 | 2 | 状态标志 |
| 10 | 2 | 当前DAC码 |
| 12 | 4 | 采样率，固定25000000 Hz |

状态标志：bit0锁定、bit1检测到输入、bit2 DAC削顶、bit3 ADC OTR，bit5:4为
当前环路配置。

频率换算：

```text
frequency_hz = phase_increment × 25000000 / 2^32
```

## 设置环路配置：TYPE 0x11

载荷长度1字节：0=`legacy`、1=`balanced`、2=`fast`。非法值回退到
`balanced`。该命令不改变目标相位、输出幅度或校准字。

## 请求扩展诊断：TYPE 0x02

载荷为空。FPGA返回TYPE `0x91`、8字节载荷。为节省EP4CE6逻辑资源，扩展帧
只传送环路内部量；频率、相位误差、状态和DAC码继续从基础状态帧读取。
四个量在收到请求时同时锁存，避免UART发送期间数据变化造成多字节撕裂。

| 偏移 | 长度 | 内容 |
|---:|---:|---|
| 0 | 2 | I低通输出÷256，有符号 |
| 2 | 2 | Q低通输出÷256，有符号 |
| 4 | 2 | CORDIC信号模长÷256，有符号 |
| 6 | 2 | PI积分器÷32，有符号 |

原有`0x01/0x90`状态帧保持不变，因此旧PC采集界面仍可使用。

`mspm0/dpll_fpga_link.c`实现了配置、查询、CRC和换算，可在切换到新工程时加入
现有Keil工程；当前旧工程不需要修改。

