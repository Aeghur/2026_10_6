"""Experimental real-board phase-only test with explicit timing limitations."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import cv2

from msp_wave_link import MSPM0WaveLink
from frequency_calibration import set_calibrated_waveform, waveform_frequency
from phase_board.phase_control import Frame, Options, Signal
from phase_handoff import start_after_frequency
from strategy_control import StrategyImageAdapter
from vision_calibrator import Camera, ROOT


class TestCamera(Camera):
    def frame_timed(self):
        # Drain two queued frames and select a third, as in the existing
        # two_flush_two_stage experiment. Exposure timing remains estimated.
        for _ in range(getattr(self, 'discarded_frames', 2)):
            ok, _ = self.cap.read()
            if not ok:
                raise RuntimeError("camera flush failed")
        ok, raw = self.cap.read()
        if not ok:
            raise RuntimeError("camera capture failed")
        return raw, time.monotonic() - self.current_exposure * .00005


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-hz', type=float, required=True)
    parser.add_argument('--mode', choices=['circle', 'diagonal', 'infinity'], default='circle')
    parser.add_argument('--vpp', type=int, choices=[1, 2, 3, 4], default=4)
    parser.add_argument('--initial-lead', type=float, default=0.)
    parser.add_argument('--probe-jitter', action='store_true', help='Use uneven probe spacing to reduce drift aliases')
    parser.add_argument('--no-frequency-calibration', action='store_true')
    args = parser.parse_args()
    # Validate before opening devices or changing output.
    from phase_board.phase_control import PhaseController
    PhaseController(args.source_hz, args.mode, continuous_phase=True)
    out = ROOT / 'artifacts/diagnostics' / f'phase_hardware_{time.time_ns()}'
    out.mkdir(parents=True)
    cfg = json.loads((ROOT / 'config.json').read_text())
    calibration_enabled = cfg.get('enable_frequency_calibration', True) and not args.no_frequency_calibration
    output_hz = args.source_hz * (2 if args.mode == 'infinity' else 1)
    setting = waveform_frequency('sine', output_hz, enabled=calibration_enabled)
    profile = json.loads((ROOT / 'experiments/zero_slope/current_capture_profile.json').read_text())
    for key in ('full_grid_corners', 'grid_dimensions_div', 'manual_exposure', 'manual_gain'):
        cfg[key] = profile[key]
    cfg['white_saturation_max'] = profile['hsv_saturation_max']
    cfg['white_value_min'] = profile['hsv_value_min']
    report = dict(source_frequency_hz=args.source_hz, mode=args.mode, frequency_setting=setting.to_dict(),
                  success=False, hardware_validated=False, commands=[], frames=[], decisions=[],
                  limitations=[
                      'Accumulator continuity inferred from repository free-run RTL, flashed build not identified.',
                      'Application time estimated as send/ACK midpoint; no hardware application timestamp.',
                      'Camera exposure midpoint estimated after queue flushing, no kernel timestamp.',
                  ])
    print('ARTIFACTS', out, flush=True)

    def save():
        (out / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))

    camera = None
    pending_images = []
    try:
        images = StrategyImageAdapter(cfg)
        camera = TestCamera(cfg)
        with MSPM0WaveLink(cfg['serial_port']) as link:
            def apply(signal, cid):
                lag = round((-signal.phase_deg) % 360, 2) % 360
                sent = time.monotonic()
                # Always calibrate the nominal output target, never the previous
                # ACK or the controller's command-frequency Signal.
                ack, frequency_setting = set_calibrated_waveform(link, 'sine', output_hz,
                    lag, args.vpp * 2, enabled=calibration_enabled)
                received = time.monotonic()
                actual = Signal(ack.frequency_millihz / 1000,
                                {0: 4., 1: 1., 2: 2., 3: 3.}[ack.amplitude_code],
                                (-ack.phase_centidegrees / 100) % 360)
                report['commands'].append(dict(command_id=cid, requested=asdict(signal),
                    **frequency_setting.to_dict(),
                    ack=asdict(ack), send_s=sent, ack_received_s=received,
                    estimated_application_s=(sent + received) / 2))
                save()
                return actual, (sent + received) / 2

            actual, applied = apply(Signal(float(setting.command_frequency_hz), args.vpp, args.initial_lead), 'initial')
            controller, decision = start_after_frequency(args.source_hz,
                actual_signal=actual, command_id='initial', applied_at_s=applied,
                mode=args.mode, continuous_phase=True, options=Options(control_timeout_s=15.),
                output_command_frequency_hz=float(setting.command_frequency_hz))
            sequence = 0
            while True:
                record = decision.to_dict()
                report['decisions'].append(record)
                print(json.dumps(dict(status=decision.status, reason=decision.reason,
                    shape_error_div=(decision.telemetry or {}).get('shape_error_div'),
                    verified_hold_s=(decision.telemetry or {}).get('verified_hold_s')), ensure_ascii=False), flush=True)
                if decision.success or decision.status not in {
                    'set_phase', 'capture', 'wait', 'hold', 'recapture', 'await_ack'
                }:
                    report['success'] = decision.success
                    report['final_status'] = decision.status
                    break
                if decision.command is not None:
                    # Prediction is only experimental: preserve uncertainty in
                    # the report instead of presenting ACK arrival as ground truth.
                    last = report['commands'][-1]
                    estimate = time.monotonic() + (last['ack_received_s'] - last['send_s']) / 2
                    cmd = controller.retime_pending(estimate)
                    actual, applied = apply(cmd.signal, cmd.command_id)
                    decision = controller.applied(cmd.command_id, actual, applied)
                    continue
                after = decision.capture_after_s or time.monotonic()
                time.sleep(max(0., after - time.monotonic()))
                if args.probe_jitter and controller.phase == 'probe':
                    time.sleep((0., .07, .16, .02, .13, .04)[len(controller.samples) % 6])
                # Output stays unchanged during hold; one discarded frame is
                # sufficient for this measured sampling-speed experiment.
                camera.discarded_frames = 1 if controller.phase == 'hold' else 2
                raw, captured = camera.frame_timed()
                clean, warped = images.convert(raw)
                sequence += 1
                stem = f'frame_{sequence:04d}'
                # Evaluate before disk writes so saving does not inflate age.
                decision = controller.step(Frame(clean, actual, sequence,
                    controller.command_id, captured, 'current-capture-profile'), time.monotonic())
                # Save after the control loop so PNG/JPEG compression and disk
                # writes cannot interrupt the 0.25 s hold sampling requirement.
                pending_images.append((stem, raw, clean, warped))
                report['frames'].append(dict(stem=stem, captured_at_s=captured,
                    decision_status=decision.status,
                    shape_error_div=(decision.telemetry or {}).get('shape_error_div')))
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if camera is not None:
            camera.close()
        for stem, raw, clean, warped in pending_images:
            cv2.imwrite(str(out / f'{stem}_raw.jpg'), raw)
            cv2.imwrite(str(out / f'{stem}_384.png'), clean)
            cv2.imwrite(str(out / f'{stem}_warped.jpg'), warped)
        times = [f['captured_at_s'] for f in report['frames']]
        gaps = [b-a for a, b in zip(times, times[1:])]
        report['sampling_summary'] = dict(max_gap_s=max(gaps, default=0),
            max_verified_hold_s=max(((d.get('telemetry') or {}).get('verified_hold_s', 0)
                for d in report['decisions']), default=0))
        hold_times = [f['captured_at_s'] for f in report['frames'] if f['decision_status'] == 'hold']
        report['sampling_summary']['max_hold_gap_s'] = max(
            (b-a for a,b in zip(hold_times, hold_times[1:])), default=0)
        save()
    print('RESULT', report.get('final_status'), 'success=', report['success'], flush=True)


if __name__ == '__main__':
    main()
