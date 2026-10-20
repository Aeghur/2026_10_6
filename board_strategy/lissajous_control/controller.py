"""A measurement session, driven only by calibrated pixels and DAC receipts.

The learned part is frequency recognition/window selection. Amplitude, phase,
fine trim and sampled hold are explicit engineering control, not learned claims.
"""
from dataclasses import asdict, replace
import math
import time
import uuid
import numpy as np
from .types import Signal, Target, Options, Capabilities, Frame, Command, Decision, finite
from .geometry import prepare_image
from ._vendor.pixel_vision import observe_image, drift_between, foreground
from ._vendor.phase_probe import measure_branches, estimate_phase, corrected_phase


def wrapped(degrees):
    return (degrees + 180) % 360 - 180


def same_signal(a, b):
    return (a.waveform == b.waveform and abs(a.frequency_hz-b.frequency_hz) <= max(.002, a.frequency_hz*5e-7)
            and abs(a.vpp-b.vpp) <= .02 and abs(wrapped(a.phase_deg-b.phase_deg)) <= .15
            and (a.waveform != 'ramp' or (abs(a.rise_time_us-b.rise_time_us) <= a.rise_time_us*.0005
                                        and abs(a.reset_ratio-b.reset_ratio) <= 1e-5)))


class Controller:
    """One instance per measurement; not thread-safe. Call start/applied/step.

    clock, frame timestamps and actuation receipts MUST share the same board
    monotonic clock. The controller never writes a device or takes a photo.
    """
    def __init__(self, calibration_id, capabilities=None, options=None, *, artifact_dir=None,
                 frequency_backend=None, clock=time.monotonic):
        if not calibration_id:
            raise ValueError('Create calibration at the start of EACH measurement')
        self.calibration_id = calibration_id
        self.caps = capabilities or Capabilities()
        self.options = options or Options()
        if self.options.max_hold_gap_s > .25:
            raise ValueError('Hold sampling gap must be <= .25 seconds')
        self.backend = frequency_backend
        self.artifact_dir = artifact_dir
        self.clock = clock
        self.stage = 'idle'
        self.telemetry = {'learned_component': 'frequency_only', 'hardware_validated': False}
        self.command = None
        self.awaiting = False
        self.current = None

    def _result(self, status, reason, command=None, capture_after=None):
        return Decision(status, self.stage, reason, command, capture_after, status == 'done', dict(self.telemetry))

    def _fail(self, reason):
        self.stage = 'failed'; self.hold_start = None
        return self._result('failed', reason)

    def _issue(self, signal, reason, stage):
        if signal.frequency_hz > self.caps.max_frequency_hz or signal.vpp > self.caps.max_vpp:
            return self._fail('hardware_range_exceeded')
        self.stage = stage; self.serial += 1
        self.command = Command(f'{self.session}:{self.serial}', signal, reason, self.options.settle_s)
        self.awaiting = True; self.hold_start = None
        return self._result('apply', reason, self.command)

    def start(self, target=None, current_signal=None, *, now_s=None):
        if self.stage not in ('idle', 'done', 'failed', 'stopped'):
            raise ValueError('Stop the active session before starting a new measurement')
        self.target = target or Target()
        self.current = current_signal or Signal()
        self.started = float(self.clock() if now_s is None else now_s)
        if not finite(self.started):
            raise ValueError('Invalid monotonic start time')
        self.last_now = self.started; self.last_capture = -math.inf; self.last_frame_id = -1
        self.session = uuid.uuid4().hex; self.serial = 0
        self.hold_start = None; self.last_hold = None; self.hold_reference = None
        self.first_ready_time = None; self.retries = 0; self.bad_fits = 0; self.amplitude_updates = 0
        self.phase_pairs = 0; self.previous_phase = None; self.pair = []
        self.telemetry = {'learned_component': 'frequency_only', 'hardware_validated': False,
                          'target': asdict(self.target), 'session_id': self.session}
        if not self.caps.ramp_supported:
            return self._fail('ramp_required_by_trained_model')
        if not self.caps.continuous_phase:
            return self._fail('continuous_phase_required_for_quadrature_probe')
        if self.caps.max_vpp < 4 or self.caps.max_frequency_hz < 1e6/(73*1.05):
            return self._fail('insufficient_probe_output_range')
        if self.backend is None:
            from .backend import FrequencyBackend
            self.backend = FrequencyBackend(self.artifact_dir)
        self.backend.reset()
        return self._ramp(self.backend.next_window())

    def _ramp(self, window):
        self.pair = []
        return self._issue(Signal('ramp', 1e6/(window*1.05), 4., 0., window, .05),
                           'measure_unknown_frequency', 'frequency')

    def applied(self, command_id, actual_signal, applied_at_s=None):
        """Call AFTER the board acknowledges actual application, not UART send."""
        when = float(self.clock() if applied_at_s is None else applied_at_s)
        if not self.awaiting or command_id != self.command.command_id:
            raise ValueError('Unexpected or already acknowledged command')
        if not finite(when) or when < self.last_now:
            raise ValueError('Actuation time must use the same monotonic clock')
        if not same_signal(self.command.signal, actual_signal):
            raise ValueError('Applied output differs from request: check units/quantization/direction')
        self.current = actual_signal; self.applied_at = when
        self.capture_after = when + self.command.settle_s
        self.awaiting = False; self.last_now = when

    def stop(self):
        self.stage = 'stopped'; self.hold_start = None
        return self._result('stopped', 'caller_stopped; hardware output is managed by caller')

    def _wait(self, reason, after=None, invalidate_hold=False):
        if invalidate_hold:
            self.hold_start = None; self.last_hold = None
            self.telemetry['hold_elapsed_s'] = 0.
        return self._result('capture', reason, capture_after=after)

    def step(self, frame: Frame, *, now_s=None):
        if self.stage in ('idle', 'failed', 'done', 'stopped'):
            return self._result('done' if self.stage == 'done' else self.stage, 'terminal_or_not_started')
        now = float(self.clock() if now_s is None else now_s)
        if not finite(now) or now < self.last_now:
            raise ValueError('Time must be monotonic')
        self.last_now = now
        if self.awaiting:
            return self._result('await_ack', 'call applied() after hardware acknowledgement', self.command)
        if frame.calibration_id != self.calibration_id:
            return self._fail('calibration_changed; start a new controller/session')
        if (not isinstance(frame.frame_id, int) or isinstance(frame.frame_id, bool)
                or not finite(frame.captured_at_s) or frame.captured_at_s > now
                or frame.frame_id <= self.last_frame_id or frame.captured_at_s <= self.last_capture):
            return self._wait('duplicate_or_nonmonotonic_frame', invalidate_hold=True)
        if frame.command_id != self.command.command_id or not same_signal(frame.signal, self.current):
            return self._wait('frame_does_not_belong_to_applied_command', invalidate_hold=True)
        self.last_capture = frame.captured_at_s; self.last_frame_id = frame.frame_id
        if now-frame.captured_at_s > self.options.frame_max_age_s:
            return self._wait('stale_frame', invalidate_hold=True)
        if frame.captured_at_s < self.capture_after:
            return self._wait('discard_old_exposure_or_display_persistence', self.capture_after, True)
        self.telemetry['elapsed_s'] = now-self.started
        # The 10 s limit concerns acquisition of the goal, not the additional
        # 5 s evidence interval. Losing the goal after 10 s cannot restart it.
        if self.stage != 'hold' and now-self.started > self.options.control_limit_s:
            return self._fail('control_time_limit_exceeded')
        try:
            image = prepare_image(frame.image)
        except (TypeError, ValueError) as error:
            return self._wait('invalid_calibrated_image: '+str(error), invalidate_hold=True)
        if np.count_nonzero(foreground(image)) < 30:
            return self._wait('no_usable_waveform', invalidate_hold=True)
        if self.stage == 'frequency':
            self.pair.append(image)
            if len(self.pair) < 2:
                self.capture_after = frame.captured_at_s+self.options.pair_interval_s
                return self._wait('second_fresh_frame_for_same_ramp', self.capture_after)
            result = self.backend.observe(self.current.rise_time_us, self.pair)
            self.telemetry['frequency'] = result
            if result['confirmed']:
                self.nominal_output_hz = float(result['frequency_hz']*self.target.frequency_ratio)
                self.telemetry['source_frequency_estimate_hz'] = result['frequency_hz']
                return self._issue(Signal('sine', self.nominal_output_hz, self.target.height_div*.5, 0.),
                                   'frequency_identified; verify_sine_geometry', 'phase_first')
            if result['next_window_us'] is None:
                return self._fail('frequency_unconfirmed_after_all_windows')
            return self._ramp(result['next_window_us'])
        return self._sine(image, frame.captured_at_s, now)

    def _ready(self, observation):
        o = self.options
        return (abs(observation.y_height-self.target.height_div) <= o.amplitude_tolerance_div
                and abs(observation.x_height-o.x_reference_div) <= o.amplitude_tolerance_div
                and observation.shape_error <= o.shape_tolerance_div and observation.coverage >= .98
                and observation.family_error_div <= o.shape_tolerance_div
                and (self.target.mode != 'infinity' or observation.symmetry_error <= o.shape_tolerance_div))

    def _retry_frequency(self, reason):
        self.retries += 1; self.telemetry['frequency_restarts'] = self.retries
        if self.retries > 1:
            return self._fail(reason)
        self.backend.reset(); self.previous_phase = None; self.bad_fits = 0
        return self._ramp(self.backend.next_window())

    def _sine(self, image, timestamp, now):
        obs = observe_image(image, self.target.mode, self.target.height_div)
        self.telemetry['observation'] = {k:v for k,v in asdict(obs).items() if k != 'profile'}
        mask = foreground(image); yy,xx = np.where(mask)
        if min(xx.min(), yy.min()) <= 1 or max(xx.max(), yy.max()) >= 382:
            return self._wait('waveform_clipped; enlarge_visible_calibrated_region', invalidate_hold=True)
        if self.stage == 'hold':
            gap = timestamp-self.last_hold if self.last_hold is not None else math.inf
            ready = self._ready(obs)
            drift = drift_between(self.hold_reference, obs) if self.hold_reference is not None else math.inf
            self.telemetry['drift_div'] = drift if math.isfinite(drift) else None
            if ready and (self.hold_start is None or gap > self.options.max_hold_gap_s):
                self.hold_start = timestamp; self.hold_reference = obs
            elif not ready or drift >= self.options.drift_limit_div:
                self.hold_start = None; self.stage = 'phase_first'
                if now-self.started > self.options.control_limit_s:
                    return self._fail('goal_lost_after_control_deadline')
                return self._phase_first(image, timestamp)
            self.last_hold = timestamp
            elapsed = timestamp-self.hold_start
            self.telemetry['hold_elapsed_s'] = elapsed
            if elapsed >= self.options.hold_seconds:
                self.stage = 'done'
                return self._result('done', 'goal_verified_by_fresh_sampled_images_for_5_seconds')
            return self._wait('continue_fresh_hold_sampling', timestamp+self.options.pair_interval_s)
        if self.stage != 'phase_quarter' and self._ready(obs):
            if now-self.started > self.options.control_limit_s:
                return self._fail('control_time_limit_exceeded')
            self.stage = 'hold'; self.hold_start = self.last_hold = timestamp; self.hold_reference = obs
            self.first_ready_time = now
            self.telemetry.update(control_time_s=now-self.started, hold_elapsed_s=0.)
            return self._wait('goal_acquired; begin_5_second_sampled_hold', timestamp+self.options.pair_interval_s)
        if self.stage != 'phase_quarter' and abs(obs.y_height-self.target.height_div) > self.options.amplitude_tolerance_div:
            if self.amplitude_updates >= 4 or obs.y_height < .5:
                return self._fail('amplitude_not_converged_or_trace_too_small')
            desired = self.current.vpp*self.target.height_div/obs.y_height
            if not .1 <= desired <= self.caps.max_vpp:
                return self._fail('amplitude_requires_output_outside_hardware_limits')
            self.amplitude_updates += 1; self.previous_phase = None
            return self._issue(replace(self.current, vpp=desired), 'correct_measured_peak_to_peak_height', 'phase_first')
        if self.stage == 'phase_quarter':
            return self._phase_quarter(image, timestamp)
        return self._phase_first(image, timestamp)

    def _phase_first(self, image, timestamp):
        fit = measure_branches(image, self.target.mode)
        if not fit.valid:
            self.bad_fits += 1
            if self.bad_fits >= 3:
                return self._retry_frequency('phase_geometry_invalid; frequency_or_visibility_mismatch')
            return self._wait('phase_geometry_invalid: '+fit.reason, timestamp+self.options.pair_interval_s, True)
        self.bad_fits = 0
        self.phase_first = (fit, self.current, timestamp)
        return self._issue(replace(self.current, phase_deg=(self.current.phase_deg+90)%360),
                           'positive_90_degree_quadrature_probe', 'phase_quarter')

    def _phase_quarter(self, image, timestamp):
        first, initial_signal, initial_time = self.phase_first
        fit = measure_branches(image, self.target.mode)
        if timestamp-initial_time > self.options.max_phase_probe_gap_s:
            self.stage = 'phase_first'
            return self._wait('phase_probe_gap_too_long; retry_from_current_phase', timestamp+self.options.pair_interval_s)
        try:
            phi = estimate_phase(first, fit, self.target.mode)
        except ValueError as error:
            self.stage = 'phase_first'; self.bad_fits += 1
            if self.bad_fits >= 3:
                return self._retry_frequency('quadrature_probe_inconsistent')
            return self._wait('quadrature_probe_rejected: '+str(error), timestamp+self.options.pair_interval_s)
        self.phase_pairs += 1
        if self.phase_pairs > 20:
            return self._fail('phase_not_converged')
        frequency = self.current.frequency_hz
        fine_error = 0.
        # Independent quadrature pairs remove known commanded phase offsets.
        # Small-drift estimate only; this is not a globally unaliased frequency
        # meter. Reject large jumps rather than extrapolating over a 360 turn.
        intrinsic = (phi-initial_signal.phase_deg)%360
        if self.previous_phase is not None:
            old_intrinsic, old_time, old_frequency = self.previous_phase
            dt = initial_time-old_time; delta = wrapped(intrinsic-old_intrinsic)
            if 0 < dt <= 2 and abs(delta) < 90 and abs(frequency-old_frequency) < .001:
                fine_error = delta/360/dt
                proposed = frequency-fine_error
                if abs(proposed-self.nominal_output_hz) <= self.options.max_trim_hz:
                    frequency = proposed
                else:
                    return self._retry_frequency('phase_drift_exceeds_fine_trim_range')
        phase = corrected_phase(initial_signal.phase_deg, phi, self.target.mode)
        phase = (phase-360*fine_error*(timestamp-initial_time))%360
        self.previous_phase = (intrinsic, initial_time, self.current.frequency_hz)
        self.telemetry.update(phase_estimate_deg=phi, phase_probe_pairs=self.phase_pairs,
                              fine_frequency_error_hz=fine_error)
        return self._issue(Signal('sine', frequency, self.current.vpp, phase),
                           'correct_visual_phase_and_small_frequency_drift', 'phase_first')
