"""Fit AD9226/DAC904 measurements and print FPGA calibration parameters."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SineFit:
    input_vpp: float
    path: Path
    frequency_hz: float
    center_code: float
    code_vpp: float
    residual_rms: float


def parse_pair(value: str, label: str) -> tuple[float, str]:
    try:
        left, right = value.split("=", 1)
        return float(left), right
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            f"{label}格式应为 数值=数值或路径，收到：{value!r}"
        ) from exc


def load_capture(path: Path) -> tuple[np.ndarray, np.ndarray]:
    table = np.genfromtxt(path, delimiter=",", names=True, encoding="utf-8-sig")
    if table.size < 16 or not table.dtype.names:
        raise ValueError(f"{path}没有足够的CSV数据")
    names = {name.lower(): name for name in table.dtype.names}
    if "adc_code" not in names:
        raise ValueError(f"{path}缺少adc_code列")
    codes = np.atleast_1d(table[names["adc_code"]]).astype(np.float64)
    if "time_s" not in names:
        raise ValueError(f"{path}缺少time_s列，无法确定采样率")
    times = np.atleast_1d(table[names["time_s"]]).astype(np.float64)
    valid = np.isfinite(times) & np.isfinite(codes)
    times, codes = times[valid], codes[valid]
    if times.size < 16 or np.any(np.diff(times) <= 0):
        raise ValueError(f"{path}的时间列无效")
    return times, codes


def linear_sine_fit(times: np.ndarray, codes: np.ndarray, frequency: float):
    angle = 2.0 * np.pi * frequency * times
    design = np.column_stack((np.ones(times.size), np.sin(angle), np.cos(angle)))
    coefficients, _, _, _ = np.linalg.lstsq(design, codes, rcond=None)
    residual = codes - design @ coefficients
    return coefficients, float(np.mean(residual * residual))


def fit_sine(path: Path, input_vpp: float, expected_frequency: float) -> SineFit:
    times, codes = load_capture(path)
    span = max(expected_frequency * 0.20, 100.0)
    coarse = np.linspace(expected_frequency - span, expected_frequency + span, 401)
    coarse_mse = np.array([linear_sine_fit(times, codes, f)[1] for f in coarse])
    best = int(np.argmin(coarse_mse))
    step = coarse[1] - coarse[0]
    fine = np.linspace(coarse[best] - step, coarse[best] + step, 401)
    fine_mse = np.array([linear_sine_fit(times, codes, f)[1] for f in fine])
    frequency = float(fine[int(np.argmin(fine_mse))])
    coefficients, mse = linear_sine_fit(times, codes, frequency)
    amplitude = float(np.hypot(coefficients[1], coefficients[2]))
    return SineFit(
        input_vpp=input_vpp, path=path, frequency_hz=frequency,
        center_code=float(coefficients[0]), code_vpp=2.0 * amplitude,
        residual_rms=float(np.sqrt(mse)),
    )


def fit_through_origin(x: np.ndarray, y: np.ndarray) -> float:
    denominator = float(x @ x)
    if denominator <= 0:
        raise ValueError("拟合输入必须包含正数")
    return float((x @ y) / denominator)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="由ADC CSV和DAC/I-V实测值计算电压映射参数"
    )
    parser.add_argument(
        "--adc", action="append", required=True, metavar="VIN_VPP=CSV",
        help="输入端示波器Vpp及对应原始采样CSV；可重复指定",
    )
    parser.add_argument(
        "--dac-vpp", action="append", default=[], metavar="VIN_VPP=VOUT_VPP",
        help="与--adc输入幅度对应的DAC/I-V输出Vpp；可重复指定",
    )
    parser.add_argument("--frequency", type=float, default=10_000.0)
    parser.add_argument(
        "--current-dac-gain", type=float, default=4.0,
        help="测量DAC Vpp时使用的DAC码/ADC码增益，默认4",
    )
    parser.add_argument(
        "--zero-measure", metavar="DAC_CODE=VOUT_DC",
        help="固定DAC码及测得的输出直流电压，用于计算DAC_ZERO_CODE",
    )
    parser.add_argument(
        "--slope-sign", type=int, choices=(-1, 1), default=1,
        help="DAC码增大时输出电压的方向，+1或-1",
    )
    args = parser.parse_args()

    adc_inputs = [parse_pair(item, "--adc") for item in args.adc]
    fits = [fit_sine(Path(path), vpp, args.frequency) for vpp, path in adc_inputs]
    fits.sort(key=lambda item: item.input_vpp)

    print("ADC正弦拟合：")
    for item in fits:
        print(
            f"  Vin={item.input_vpp:.6g} Vpp  f={item.frequency_hz:.3f} Hz  "
            f"C0={item.center_code:.4f}  Cpp={item.code_vpp:.4f}  "
            f"残差RMS={item.residual_rms:.4f}码  ({item.path})"
        )

    input_vpp = np.array([item.input_vpp for item in fits])
    code_vpp = np.array([item.code_vpp for item in fits])
    k_adc = fit_through_origin(input_vpp, code_vpp)
    adc_zero = int(np.rint(np.mean([item.center_code for item in fits])))
    print(f"\nK_ADC = {k_adc:.8f} ADC码/V")
    print(f"系统实测ADC LSB = {1000.0 / k_adc:.8f} mV/码")
    print(f"ADC_ZERO_CODE = {adc_zero}")

    measured_outputs = {
        input_vpp: float(output_vpp)
        for input_vpp, output_vpp in (
            parse_pair(item, "--dac-vpp") for item in args.dac_vpp
        )
    }
    if not measured_outputs:
        print("\n尚未提供--dac-vpp：DAC_GAIN_Q16暂不能完成实测标定。")
        print("临时保持：DAC_ZERO_CODE=8192, DAC_GAIN_Q16=262144 (G=4.0)")
        return

    selected = [(fit, measured_outputs[fit.input_vpp]) for fit in fits
                if fit.input_vpp in measured_outputs]
    if len(selected) != len(measured_outputs):
        raise ValueError("--dac-vpp中的输入Vpp必须与某个--adc左侧数值完全一致")
    dac_code_vpp = np.array(
        [args.current_dac_gain * fit.code_vpp for fit, _ in selected]
    )
    output_vpp = np.array([value for _, value in selected])
    k_dac = fit_through_origin(dac_code_vpp, output_vpp)
    gain = 1.0 / (k_adc * k_dac)
    gain_q16 = int(np.rint(gain * 65536.0))

    dac_zero = 8192
    if args.zero_measure:
        test_code, measured_text = parse_pair(args.zero_measure, "--zero-measure")
        measured_dc = float(measured_text)
        dac_zero = int(np.rint(test_code - measured_dc / (args.slope_sign * k_dac)))
        dac_zero = min(16383, max(0, dac_zero))

    print(f"\nK_DAC = {k_dac:.12g} V/DAC码")
    print(f"G = {gain:.9f} DAC码/ADC码")
    print(f"GAIN_Q16 = {gain_q16}")
    print("\n写入ads805.v的参数：")
    print(f"  ADC_ZERO_CODE = {adc_zero}")
    print(f"  DAC_ZERO_CODE = {dac_zero}")
    print(f"  DAC_GAIN_Q16  = {gain_q16}")
    print(f"  DAC_INVERT    = {1 if args.slope_sign < 0 else 0}")
    if not args.zero_measure:
        print("注意：未提供--zero-measure，DAC_ZERO_CODE仍暂用8192。")


if __name__ == "__main__":
    main()
