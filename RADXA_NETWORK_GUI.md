# Radxa 网络波形控制窗口

PC 在本项目目录运行：

```powershell
python -m pip install -r requirements.txt
python radxa_scope.py
```

默认连接参数已按现有设备核对：

- 地址：192.168.137.2；SSH 用户：radxa。
- 程序目录：/home/radxa/vision_sorter。
- Python：/home/radxa/vision_sorter/.venv/bin/python。
- 串口：/dev/ttyS4，1 Mbaud / 8N1。

选择“连接”，设置波形参数后选择“应用输出”。支持正弦、双向锯齿、固定100 Hz快降脉冲、
0～359.99°滞后相位、2/4/6/8幅度档位，以及普通波形下发前计算二倍频。
快降脉冲周期固定10 ms（100 Hz）；下降时间可设置为1～9999 us，负峰保持时间自动为
`10000 − 下降时间` us，该阶段保持负峰值。到达下降起点时瞬间升至正峰值，
随后线性下降回负峰值；8/8档约为4 Vpp。
收到FPGA确认并校验回执参数后才显示成功。断网、超时后的实际输出状态未知，
需要重连并重新应用。断开窗口不会停止最后输出。

控制链为 PC → SSH → Radxa现有 msp_wave_link.py → MSPM0 → FPGA。
整个会话保持一个SSH连接和一个串口连接，不需要在Radxa安装网络服务、上传脚本或改动现有模块。
原始二进制串口协议、波特率、Radxa接线不变；PC无需USB-TTL。
连接只检查环境并打开串口，不主动设置波形。

先退出使用该串口的视觉自动控制、systemd服务或串口工具。窗口发现串口占用时会拒绝连接，
不会自动停止现有任务。连接期间不要另启其他串口控制程序。

窗口使用Windows自带OpenSSH及本机已有SSH密钥。当前电脑的密钥连接已验证可用。
其他电脑需先在终端完成主机密钥确认和免密登录：

```powershell
ssh radxa@192.168.137.2
```

窗口严格检查已知主机密钥，使用非交互登录；如果提示 Permission denied (publickey,password)，
需要先配置SSH公钥登录。若提示 Host key verification failed，先在终端核对并确认主机身份。
密码不会被窗口读取或保存。连接失败时日志会显示SSH错误。

源码默认 HOST_UART_USE_RADXA_PINS=1，维持原Radxa PB21 TX/PB22 RX接线。
无需为网络窗口重新编译、烧录MSPM0或FPGA。
