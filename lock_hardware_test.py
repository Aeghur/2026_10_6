"""Real-board adapter for the collaborator optical frequency/phase lock v2."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import cv2

from frequency_calibration import set_calibrated_waveform, quantized_frequency
from circle_double_control import CircleThenDoubleController
from lock_board.lock_control import LockController, Signal, Frame, Options, __version__ as LOCK_VERSION
from msp_wave_link import MSPM0WaveLink, MSPM0LinkError
from phase_hardware_test import TestCamera
from strategy_control import StrategyImageAdapter
from vision_calibrator import ROOT


def apply_lock_signal(link, requested):
    """Controller signals already contain trim: quantize, never recalibrate."""
    frequency = quantized_frequency(requested.frequency_hz)
    amplitude = {1.: 2, 2.: 4, 3.: 6, 4.: 8}.get(requested.vpp)
    if amplitude is None:
        raise ValueError('UART supports only 1/2/3/4 Vpp')
    lag = round((-requested.phase_deg) % 360, 2) % 360
    sent = time.monotonic()
    ack = link.set_waveform('sine', float(frequency), lag, amplitude)
    received = time.monotonic()
    if ack.frequency_millihz != int(frequency * 1000):
        raise MSPM0LinkError('ACK frequency differs from lock command')
    actual = Signal(ack.frequency_millihz / 1000,
        {0: 4., 1: 1., 2: 2., 3: 3.}[ack.amplitude_code],
        (-ack.phase_centidegrees / 100) % 360)
    return actual, (sent + received) / 2, dict(ack=asdict(ack), send_s=sent,
        ack_received_s=received, estimated_application_s=(sent + received) / 2,
        command_frequency_hz=float(frequency), actual_signal=asdict(actual))


def arm_hardware_double(link):
    """Single transaction, no retry; sample counter stays in FPGA time domain."""
    sent = time.monotonic()
    ack = link.arm_double_at_falling_zero()
    received = time.monotonic()
    return ack, received, dict(ack_type=0x94, ack=asdict(ack),
        operation='arm_double_at_falling_zero', send_s=sent, ack_received_s=received,
        host_application_time_basis='ack_received_upper_bound_not_actual_event_time',
        sample_counter_clock_domain='fpga', command_frequency_hz=ack.new_frequency_millihz/1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-hz', type=float, required=True)
    parser.add_argument('--mode', choices=['circle', 'diagonal', 'infinity'], default='circle')
    parser.add_argument('--initial-lead', type=float, default=278.64)
    parser.add_argument('--vpp', type=int, choices=[1, 2, 3, 4], default=4)
    parser.add_argument('--no-policy', action='store_true')
    parser.add_argument('--no-initial-calibration', action='store_true')
    parser.add_argument('--initial-command-hz', type=float,
        help='Continue from a previous acknowledged trimmed UART frequency; do not recalibrate it')
    parser.add_argument('--timeout', type=float, default=10.)
    args = parser.parse_args()
    cfg = json.loads((ROOT / 'config.json').read_text())
    profile = json.loads((ROOT / 'experiments/zero_slope/current_capture_profile.json').read_text())
    for key in ('full_grid_corners', 'grid_dimensions_div', 'manual_exposure', 'manual_gain'):
        cfg[key] = profile[key]
    cfg['white_saturation_max'] = profile['hsv_saturation_max']
    cfg['white_value_min'] = profile['hsv_value_min']
    options = Options(frequency_step_hz=.001, exposure_ms=cfg['manual_exposure'] * .1,
                      settle_s=.08, command_latency_s=.011, timeout_s=args.timeout)
    controller_options = dict(options=options, continuous_phase=True, use_policy=not args.no_policy)
    controller = (CircleThenDoubleController(args.source_hz, **controller_options)
        if args.mode == 'infinity' else LockController(args.source_hz, args.mode, **controller_options))
    # Every mode starts at the source frequency; infinity first acquires a
    # quadrature refinement, then doubles frequency and uses original v2.1
    # control to acquire the 2:1 generalized phase and verify the final hold.
    output_nominal = args.source_hz
    if args.initial_command_hz is not None:
        controller._signal_ok(Signal(args.initial_command_hz, args.vpp, args.initial_lead))
    out = ROOT / 'artifacts/diagnostics' / f'optical_lock_v2_{time.time_ns()}'
    out.mkdir(parents=True)
    report = dict(algorithm_version=LOCK_VERSION, release_source='lissajous_lock_board_v2_1.zip',
        source_frequency_hz=args.source_hz,
        mode=args.mode, options=asdict(options), use_policy=not args.no_policy,
        frequency_route='quadrature_refine_then_hardware_double_then_hold' if args.mode == 'infinity' else 'v2_1_target',
        success=False, hardware_validated=False, commands=[], frames=[], decisions=[],
        limitations=[
            'Accumulator continuity inferred from repository RTL, flashed build not identified.',
            'Application timestamp is send/ACK midpoint estimate, not hardware timestamp.',
            'Exposure midpoint estimated after frame flushing; display persistence unmeasured.',
            'Only startup uses polynomial calibration; v2 commands are absolute UART frequencies.',
        ])
    print('ARTIFACTS', out, flush=True)
    pending_images = []
    camera = None
    double_state_unverified = False

    def save():
        (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def record_command(cid, requested, actual, timing, reason):
        report['commands'].append(dict(command_id=cid, reason=reason,
            requested=asdict(requested), nominal_source_hz=args.source_hz,
            output_nominal_hz=controller.output_nominal,
            frequency_trim_hz=actual.frequency_hz-controller.output_nominal, **timing))
        save()

    try:
        images = StrategyImageAdapter(cfg)
        camera = TestCamera(cfg)
        with MSPM0WaveLink(cfg['serial_port']) as link:
            calibrated = cfg.get('enable_frequency_calibration', True) and not args.no_initial_calibration
            # This initial physical target is calibrated once. The loop below
            # intentionally uses the raw transport for absolute v2 commands.
            sent = time.monotonic()
            initial_frequency = output_nominal if args.initial_command_hz is None else args.initial_command_hz
            ack, setting = set_calibrated_waveform(link, 'sine', initial_frequency,
                round((-args.initial_lead) % 360, 2) % 360, args.vpp * 2,
                enabled=calibrated if args.initial_command_hz is None else False)
            received = time.monotonic()
            actual = Signal(ack.frequency_millihz / 1000,
                {0: 4., 1: 1., 2: 2., 3: 3.}[ack.amplitude_code],
                (-ack.phase_centidegrees / 100) % 360)
            applied = (sent + received) / 2
            report['initial_frequency_setting'] = setting.to_dict()
            report['continuation_command_hz'] = args.initial_command_hz
            record_command('initial', actual, actual, dict(ack=asdict(ack), send_s=sent,
                ack_received_s=received, estimated_application_s=applied,
                command_frequency_hz=actual.frequency_hz, actual_signal=asdict(actual)), 'Initial sine')
            decision = controller.start(actual, 'initial', applied, now_s=time.monotonic())
            while True:
                report['decisions'].append(decision.to_dict())
                telemetry = decision.telemetry or {}
                print(json.dumps(dict(status=decision.status, reason=decision.reason,
                    requested_mode=args.mode, output_stage=(
                        ('double_' if controller.double_acknowledged else 'circle_') + controller.stage
                    ) if args.mode == 'infinity' else controller.stage,
                    residual_hz=telemetry.get('residual_output_frequency_hz'),
                    shape_error_div=telemetry.get('shape_error_div'),
                    verified_hold_s=telemetry.get('verified_hold_s')), ensure_ascii=False), flush=True)
                if decision.success or decision.status not in {
                    'capture', 'set_signal', 'await_ack', 'recapture', 'wait', 'hold'
                }:
                    report['success'] = decision.success
                    report['final_status'] = decision.status
                    break
                if decision.command is not None:
                    last = report['commands'][-1]
                    planned = time.monotonic() + (last['ack_received_s']-last['send_s']) / 2
                    cmd = controller.retime_pending(planned)
                    if args.mode == 'infinity' and controller.stage == 'await_double':
                        # This branch MUST NOT send SET_WAVEFORM or add a phase.
                        double_state_unverified = True
                        ack, acknowledged_at, timing = arm_hardware_double(link)
                        decision = controller.applied_double(cmd.command_id, ack, acknowledged_at)
                        actual = controller.signal
                        timing['actual_signal'] = asdict(actual)
                        record_command(cmd.command_id, cmd.signal, actual, timing, cmd.reason)
                        double_state_unverified = False
                        continue
                    actual, applied, timing = apply_lock_signal(link, cmd.signal)
                    record_command(cmd.command_id, cmd.signal, actual, timing, cmd.reason)
                    decision = controller.applied(cmd.command_id, actual, applied)
                    continue
                after = decision.capture_after_s or time.monotonic()
                time.sleep(max(0., after-time.monotonic()))
                camera.discarded_frames = 1 if controller.stage == 'hold' else 2
                raw, captured = camera.frame_timed()
                clean, warped = images.convert(raw)
                sequence = len(report['frames']) + 1
                began = time.monotonic()
                decision = controller.step(Frame(clean, actual, sequence, controller.command_id,
                    captured, 'current-capture-profile'), time.monotonic())
                stem = f'frame_{sequence:04d}'
                report['frames'].append(dict(stem=stem, captured_at_s=captured,
                    signal=asdict(actual), command_id=controller.command_id,
                    decision_status=decision.status, step_seconds=time.monotonic()-began,
                    shape_error_div=(decision.telemetry or {}).get('shape_error_div')))
                pending_images.append((stem, raw, clean, warped))
            # Leave the latest acknowledged state; report it explicitly even if
            # failure occurred while probing, rather than claiming a lock.
            report['final_output'] = asdict(actual)
            if args.mode == 'infinity':
                report['target_switch_completed'] = controller.double_acknowledged
                report['final_output_stage'] = ('double_' if controller.double_acknowledged else 'circle_') + controller.stage
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        if double_state_unverified:
            report['output_state'] = 'unknown_after_arm_double_failure'
            report['output_state_verification_required'] = True
            report['automatic_retry_performed'] = False
            report['final_output'] = None
            report['last_acknowledged_output_before_double'] = asdict(actual)
        raise
    finally:
        if camera is not None:
            camera.close()
        save()
        for stem, raw, clean, warped in pending_images:
            cv2.imwrite(str(out / f'{stem}_raw.jpg'), raw)
            cv2.imwrite(str(out / f'{stem}_384.png'), clean)
            cv2.imwrite(str(out / f'{stem}_warped.jpg'), warped)
        hold_times = [f['captured_at_s'] for f in report['frames'] if f['decision_status'] == 'hold']
        report['sampling_summary'] = dict(max_hold_gap_s=max(
            (b-a for a,b in zip(hold_times, hold_times[1:])), default=None),
            max_verified_hold_s=max(((d.get('telemetry') or {}).get('verified_hold_s', 0)
                for d in report['decisions']), default=0))
        save()
    print('RESULT', report.get('final_status'), 'success=', report['success'], flush=True)
    if args.mode == 'infinity' and not report.get('target_switch_completed'):
        print('TARGET_NOT_APPLIED: circle acquisition failed or time ran out; output is still at source frequency.', flush=True)


if __name__ == '__main__':
    main()
