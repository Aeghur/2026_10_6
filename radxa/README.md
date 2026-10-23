# Radxa ROCK 4D与MSPM0串口控制

供外部AI或独立控制程序使用的完整帧协议、字段编码和字节示例见
[`WAVEFORM_CONTROL_PROTOCOL.md`](WAVEFORM_CONTROL_PROTOCOL.md)。

## 物理连接

双方使用3.3 V UART、8数据位、无校验、1停止位、1 Mbaud。只连接信号线和
地线，不要连接两块板的5 V或3.3 V电源脚。

| Radxa ROCK 4D | MSPM0G3519 | 方向 |
|---|---|---|
| 40针排针PIN 11 / UART4_TX_M1 | PB22 / UART1_RX | Radxa → MSPM0 |
| 40针排针PIN 15 / UART4_RX_M1 | PB21 / UART1_TX | MSPM0 → Radxa |
| PIN 14或其他GND | GND | 共地 |

当前Keil固件在`KeyBoardpro/User/msp_uart_link.h`中将
`HOST_UART_USE_RADXA_PINS`设为1，因此使用PB21/PB22。若要恢复原USB-TTL
测试线PB4/PB5，将它改为0并重新编译烧录；帧协议和Python程序不变。

## Linux启用串口

运行：

```bash
sudo rsetup
```

进入`Overlays` → `Manage Overlays`，启用`UART4-M1`，然后重启。设备节点应为：

```bash
ls -l /dev/ttyS4
```

推荐将当前用户加入串口组，随后注销并重新登录：

```bash
sudo usermod -aG dialout "$USER"
```

不要同时运行`cat /dev/ttyS4`、串口终端和控制程序；一个应用独占打开该设备。
PySerial打开设备时会通过Linux termios自动配置1 Mbaud和8N1，不需要先运行
`stty`。

## 第一次端到端测试

把本仓库复制到Radxa，安装依赖并运行：

```bash
cd ad9226
python3 -m pip install -r requirements.txt
python3 wave_control.py --port /dev/ttyS4 --frequency 10000 --phase 90 --amplitude 8
python3 wave_control.py --port /dev/ttyS4 --waveform sawtooth --frequency 10000 --phase 0 --amplitude 8
```

只有MSPM0收到命令、FPGA应用参数并返回`0x84 OUTPUT_ACK`后，命令才会显示
成功。因此该命令同时验证Radxa→MSPM0和MSPM0→Radxa两个方向，以及
MSPM0→FPGA链路。

## 图像程序直接调用

后续图像识别程序不要反复启动`wave_control.py`，而是在进程中保持串口打开：

```python
from msp_wave_link import MSPM0WaveLink

with MSPM0WaveLink("/dev/ttyS4") as msp:
    # 图像算法得到目标参数后调用；返回值是MSPM0/FPGA确认的参数。
    ack = msp.set_waveform(
        waveform="sawtooth",  # "sine"或"sawtooth"
        frequency_hz=10_000.0,
        phase_degrees=90.0,
        amplitude=8,
    )
    print(ack)
```

Linux把UART表现为普通字符设备文件。应用写入的是字节流，不存在天然的“消息”
边界；本项目使用`A5 5A`同步头、类型、序号、长度和CRC16组成帧。序号用于把
响应与请求对应，CRC用于发现传输错误。`MSPM0WaveLink`已经封装了组帧、CRC、
超时和错误码，图像算法只需传入波形类型、频率、相位和幅度。
