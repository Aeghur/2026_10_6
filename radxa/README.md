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

当前Keil固件默认`HOST_UART_USE_RADXA_PINS=1`，保留上表Radxa接线。
PC可运行`python radxa_scope.py`，经SSH调用Radxa现有波形接口，无需改线。
仅单独使用PC USB-TTL直连PB4/PB5时，才需将宏改为0并编译烧录。
两种引脚配置的帧协议和1 Mbaud波特率相同。

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
python3 wave_control.py --port /dev/ttyS4 --waveform decay_pulse --frequency 100 --decay-us 1000 --phase 0 --amplitude 8
python3 wave_control.py --port /dev/ttyS4 --frequency 210000 --phase 0 --amplitude 8
# 反转锯齿方向（数字输出缓降快升）
python3 wave_control.py --port /dev/ttyS4 --waveform sawtooth --direction falling --frequency 544.218 --phase 0 --amplitude 4
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
        waveform="sawtooth",  # "sine"、"sawtooth"或"decay_pulse"
        frequency_hz=10_000.0,
        phase_degrees=90.0,
        amplitude=4,
        direction="falling",  # rising=缓升快降，falling=缓降快升
    )
    print(ack)
```

Linux把UART表现为普通字符设备文件。应用写入的是字节流，不存在天然的“消息”
边界；本项目使用`A5 5A`同步头、类型、序号、长度和CRC16组成帧。序号用于把
响应与请求对应，CRC用于发现传输错误。`MSPM0WaveLink`已经封装了组帧、CRC、
超时和错误码，图像算法只需传入波形类型、频率、相位、幅度和锯齿方向。

视觉题型需要二倍频时，由Radxa先将识别频率乘2，再调用`set_waveform()`；最终
自由输出频率上限为210 kHz。


## 频率、方向与升级要求

自由运行范围为**250～210000 Hz**，支持小数Hz，协议保持mHz单位。
`decay_pulse`是固定100 Hz的例外。`decay_microseconds`/`--decay-us`控制下降时间，
范围1～9999 us；每个10 ms周期的其余时间保持负峰值，在下降起点瞬间上升到
正峰值，随后线性下降回负峰值。8/8档使用双极性满量程，约为4 Vpp。
默认下降时间为1000 us。
回执`ack.frequency_millihz / 1000`表示实际量化频率，按
`max(0.002 Hz, 目标频率×0.5 ppm)`判断误差。

| `direction` | 数字锯齿方向 | 协议waveform值 | 回执mode位6 |
|---|---|---:|---:|
| `rising`（默认） | 缓慢上升，到顶后立即下降 | 1 | 0 |
| `falling` | 缓慢下降，到底后立即上升 | 2 | 1 |

`direction`是`set_waveform()`和`set_sawtooth()`的关键字参数；
正弦仅接受默认`rising`。方向表示DAC数字码的变化方向，若模拟输出反相，
选择相反方向补偿。方向切换不重置DDS累加器，但极性翻转可能造成电压跳变。

探测设置为4 Vpp、0 V中心，无独立回扫时间；上升周期T对应
`f=1e6/T_us`，不再使用旧的`1e6/(1.05*T_us)`。相位正值仍表示滞后。
完整探测频率见[协议文档](WAVEFORM_CONTROL_PROTOCOL.md#视觉策略)。

部署时同步更新`protocol.py`、`msp_wave_link.py`、`wave_control.py`以及MSPM0、
FPGA固件。自由运行频率字已采用Q38精度，旧FPGA与新MSPM0不能混用；
旧MSPM0不支持waveform=2。更新上位机策略适配器的范围为250～210000 Hz，
并将方向加入目标状态缓存，确保只改变方向时也会发送命令。
