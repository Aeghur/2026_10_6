# 图片斜率测频

当前目标：处理后的图片 → 多个正弦零线穿越点的局部斜率 → 待测频率及 100 Hz 网格候选。
默认入口已切换为图片测量，不再自行扫描六个频率。原合作策略不变，旧实验扫描保留为显式选项。

## 使用

已有处理图：

```bash
cd /home/radxa/vision_sorter
.venv/bin/python experiments/zero_slope/slope_experiment.py \
  --image /absolute/path/to/processed.png \
  --internal-saw-hz 10000 \
  --full-ramp-height-px FULL_HEIGHT
```

`FULL_HEIGHT` 是完整线性下降段在这张图中的像素高度，必须来自完整轨迹或独立标定，
不能用当前可见的局部轨迹高度替代。也可使用 `--full-ramp-divisions` 提供总格数：
960×640 校正图每格 80 像素，384×384 标准画布每格 38.4 像素。
其他输入尺寸需要显式 `--pixels-per-div`；二者单位必须对应输入图片。

省略高度参数时，仍输出测点、斜率、Hz/像素系数，状态为 `needs_full_ramp_height`，
`frequency_hz=null`。不会自动把裁切的图形当作完整扫描。

实机入口：

```bash
.venv/bin/python experiments/zero_slope/slope_experiment.py --hardware
```

仅读相机，不访问串口，不改变当前硬件输出。曝光 8 ms、增益 0。
使用 `current_capture_profile.json` 中当前标定四角和 HSV 范围：H 0–179、S 0–120、V 240–255。
提取全分辨率 960×640 图，不做会删除点状曲线的开运算。
配置中的 10 kHz 内锯齿由用户提供。用户随后确认更新后的硬件总幅度为 4 Vpp，
结合新拍画面 Y 通道 500 mV/格，当前完整高度为 8 格、640 像素。
改变 Y 档位或下降幅度后必须更新这个标定；不得沿用旧的像素高度。
完整高度确认后可以通过参数提供，或写入配置的 `full_ramp_height_px`（实机 960×640 单位）。
原 config.json 不改动。

## 换算

正弦接 X，线性扫描接 Y。设横向正弦半幅 A、完整扫描高度 H，
内锯齿频率 fs，零线处局部斜率 b=dy/dx，则：

    f = H fs / (2π A |b|)

固定 100 Hz 的脉冲重复频率不用于该换算。当前按用户所述内锯齿 10 kHz，
假设完整线性扫描历时 100 μs；这条时基假设明确写入结果。
若内锯齿定义还包含其他非线性/停留部分，应修正为实际线性段历时。

## 局部测量

- 稳健估计横向振幅与中心；不因顶部横线碰到画布边缘而整图退出。
- 标记并排除长时间停留形成的粗水平白带，避免将其当作零线穿越。
- 在中央竖线附近收集多个穿越点，从逐列白段中心提取局部方向。
- 使用 asin((x-c)/A) 坐标对局部弯曲做修正；正、负方向分别提取。
- 按支持列数、跨度、残差和斜率分辨率筛选，跨测点汇总并记录分歧。
- 有完整高度且至少两个独立穿越点一致时，输出连续频率估计、最近的 100 Hz 档位和不确定区间内全部网格候选。

没有生成一批完整正弦图片进行匹配，也没有把某个已知输入频率写入识别器。
最终 `confirmed` 仍为 false：像素误差和扫描标定不足以仅凭这一帧证明 100 Hz 精度。

## 输出

`artifacts/diagnostics/slope_frequency_<时间>/` 包含：

- `result.json`：状态、局部测点、Hz 候选、时间和高度依据、不确定范围。
- `slopes.png`：黄线为中央零线，绿色为符合共识的局部方向，红色为不一致方向。
- `processed.png`：实际测量输入。
- 实机额外保存 `raw.jpg`、`corrected.png` 和 `capture.json`。

所有图像测量默认输入已经过 HSV 处理；已有彩色相机图应先用相同标定和 HSV 处理。

## 保留旧实验

```bash
.venv/bin/python experiments/zero_slope/slope_experiment.py --legacy-scan --hardware
```

这会实际控制 UART：按 500、1000、2000、5000、10000、20000 Hz 顺序探索传统锯齿窗口，
再用候选频率及其一半进行验证。请仅在需要旧方案时使用。
旧验证器保留连续主曲线跟踪、类似回扫直线残迹检查和 5% 粗周期容差。
该模式仍使用观察高度作为条件假设，不代表独立标定。

## 验证

```bash
.venv/bin/python -m unittest discover -s experiments/zero_slope -p 'test_*.py'
```

21 项测试包括：局部测点、顶部杂线、边界接触、回扫残迹、额外分支拒绝，以及新测频路径的
已知标定频率、67.1 kHz、部分轨迹、缺失高度、空图、填满图、时基参数换算和点状穿越的碎片合并。


## 当前有效跨度标定更新

在有效下降时长准确的假设下，用66.6kHz参考图建立一个统一软件尺度：
完整跨度从640改为630.685911像素，等效Vpp约3.941787V（未独立测量电压）。
硬件幅度档位仍为标称4Vpp，时间参数不变。配置将nominal与effective两项分别保存。
2kHz、5.6kHz保存图作为未参与拟合的频率检查，均回到正确档位；66.6kHz参考组仍有100–200Hz残差。
新的实拍尝试只见水平亮线，尚不能用于验证本次标定。详细记录见
artifacts/diagnostics/vertical_scale_20261031_093716/标定报告.md。
