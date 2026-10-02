# MSPM0G3519 DPLL控制工程

Keil入口：`Project/dpll_dds.uvprojx`。

已使用Keil µVision 5、ARM Compiler 6.24完成全量编译：0 error、0 warning。
本机生成的烧录文件位于`Output/dpll_dds_mspm0.hex`；`Output`属于构建产物，
不提交Git，可随时由工程重新生成。

功能：

- UART4以2 Mbaud连接DPLL FPGA；
- UART1以1 Mbaud连接PC；
- 将PC的0～359.99°目标相位转换为32 bit FPGA相位字；
- 查询FPGA频率、锁定、OTR和DAC削顶状态；
- 保持现有`scope.py`的设置相位、单次采集和结果帧兼容。

新FPGA不再上传4096点ADC/FFT数据，因此兼容结果中仅返回一个中心采样点和
一个状态幅值点。PC界面仍可显示频率、CRC、OTR、DAC削顶和调相状态，时域及
频谱图不再代表实际采集波形。

工程复用仓库根目录`KeyBoardpro/Source`中的TI DriverLib，避免复制整套SDK。
因此不要单独移动本目录；应保持它位于当前仓库结构中。

接线：

| 方向 | MSPM0 | FPGA | 波特率 |
|---|---|---|---:|
| MSPM0→FPGA | PB10/UART4_TX | T13/uart_rx | 2 Mbaud |
| FPGA→MSPM0 | PB11/UART4_RX | R13/uart_tx | 2 Mbaud |
| PC→MSPM0 | PB5/UART1_RX | USB-TTL TX | 1 Mbaud |
| MSPM0→PC | PB4/UART1_TX | USB-TTL RX | 1 Mbaud |

所有数字设备使用3.3 V逻辑并共地。

烧录与启动：

1. 用Keil打开`Project/dpll_dds.uvprojx`，选择`DPLL_DDS_MSPM0G3519`。
2. 执行Build，确认生成`Output/dpll_dds_mspm0.hex`。
3. 用UniFlash烧录该HEX；如果已经在Keil目标选项中配置好板载调试器，也可直接Download。
4. FPGA先烧录`quartus/output_files/dpll_dds.sof`，再给MSPM0复位。
5. PC运行仓库原界面，选择MSPM0对应串口和1,000,000 baud。

上电后MSPM0会自动向FPGA发送0°、幅度2143、相位校准0、输出使能的默认配置。
如果暂时不接MSPM0，FPGA的UART RX必须上拉到3.3 V；DPLL实时锁相本身仍在FPGA
中运行，但不能从PC设置相位或读取状态。
