"""Refine a +90 degree circle, ARM hardware doubling, then verify the target.

Only the special DOUBLE_ACK can advance the 1x->2x transition. Normal frequency
and phase refinements use v2.1 only before doubling.
"""
from dataclasses import asdict, replace

from lock_board.lock_control import LockController, Decision, Signal
from lock_board.lock_control.geometry import shape_error, target_distance
from protocol import DoubleAck


class CircleThenDoubleController(LockController):
    # Only the 1x circle confirmation before ARM_DOUBLE uses this tolerance.
    circle_confirmation_tolerance_div = 0.3

    def __init__(self, source_frequency_hz, **kwargs):
        super().__init__(source_frequency_hz, mode='diagonal', **kwargs)
        target_distance('circle', self.options.target_height_div, 384, 384, (-5., -5., 5., 5.))
        target_distance('infinity', self.options.target_height_div, 384, 384, (-5., -5., 5., 5.))
        self.doubled = False
        self.double_acknowledged = False
        self.circle_refined = False
        self.double_frequency_hz = None

    def start(self, signal, command_id, applied_at_s, now_s=None):
        self.mode = 'diagonal'
        self.ratio = 1
        self.output_nominal = self.nominal
        self.doubled = False
        self.double_acknowledged = False
        self.circle_refined = False
        self.double_frequency_hz = None
        return super().start(signal, command_id, applied_at_s, now_s)

    def _diagonal_refine(self, image, t, now):
        if self.circle_refined:
            error = shape_error(image, 'circle', self.options.target_height_div)
            self.last_fit = dict(self.last_fit or {}, circle_confirmation_error_div=error,
                                 circle_confirmation_tolerance_div=self.circle_confirmation_tolerance_div)
            if error is not None and error <= self.circle_confirmation_tolerance_div:
                return self._arm_double(now, error)
            # Reuse measured quadrature refinement if the new acknowledged
            # circle still fails geometry; never arm on the commanded phase alone.
            self.circle_refined = False
        decision = super()._diagonal_refine(image, t, now)
        if decision.command is None or self.stage != 'await_diagonal':
            return decision
        original = decision.command
        # The original diagonal result targets relative phase 0. For the ARM
        # precondition, apply the *computed* quadrature correction at 1x first.
        # This +90 belongs to circle refinement, never to the double operation.
        command = replace(original,
            signal=Signal(original.signal.frequency_hz, original.signal.vpp,
                          (original.signal.phase_deg+90.) % 360.),
            reason='Finish multi-frame refinement at +90 degrees before hardware ARM_DOUBLE')
        self.pending = command
        self.stage = 'await_circle_refinement'
        return replace(decision, command=command)

    def _arm_double(self, now, circle_error):
        budget = self._budget(now+self.options.command_latency_s+self.options.settle_s)
        if budget:
            return budget
        frequency = self.signal.frequency_hz*2
        nominal = self.nominal*2
        if not 250 <= frequency <= 210000:
            return Decision('frequency_limit', 'Doubled frequency exceeds UART range')
        if abs(frequency-nominal) > self.options.trim_limit_hz+self.options.frequency_step_hz:
            return Decision('trim_limit', 'Doubled frequency exceeds v2.1 target trim bound')
        reference = self.signal
        reference_fit = self.last_fit
        self.double_frequency_hz = frequency  # Estimate only; DOUBLE_ACK FTW is authoritative.
        self.doubled = True
        self.mode = 'infinity'
        self.ratio = 2
        self.output_nominal = nominal
        self.last_fit = dict(measurement_stage='await_hardware_double',
            circle_refinement=reference_fit, reference_circle_error_div=circle_error,
            reference_ack_frequency_hz=reference.frequency_hz,
            estimated_double_frequency_hz=frequency,
            generalized_target_phase_deg=0., verified_hold_s=0.)
        self.stage = 'await_double'
        return self._issue(frequency, reference.phase_deg, now,
            'Arm hardware doubling at falling zero; preserve phase register and amplitude')

    def applied(self, command_id, signal, applied_at_s):
        if self.stage == 'await_double':
            raise ValueError('1x->2x requires applied_double() with a successful 0x94 DOUBLE_ACK')
        refining = self.stage == 'await_circle_refinement'
        if refining:
            self.stage = 'await_correction'
        try:
            decision = super().applied(command_id, signal, applied_at_s)
        except Exception:
            if refining:
                self.stage = 'await_circle_refinement'
            raise
        if refining:
            self.circle_refined = self.stage == 'refine_diagonal' and self.phase_anchor is not None
        return decision

    def applied_double(self, command_id, ack, acknowledged_at_s):
        if self.stage != 'await_double' or self.pending is None:
            raise ValueError('No hardware double transaction is pending')
        if not isinstance(ack, DoubleAck) or ack.status != 0 or ack.old_phase_step <= 0 or ack.new_phase_step != 2*ack.old_phase_step:
            raise ValueError('Successful exact-doubling DOUBLE_ACK required')
        if command_id != self.pending.command_id:
            raise ValueError('Wrong double command id')
        old_frequency = ack.old_frequency_millihz/1000
        tolerance = max(.002, self.signal.frequency_hz*.5e-6)
        if abs(old_frequency-self.signal.frequency_hz) > tolerance:
            raise ValueError('DOUBLE_ACK old FTW differs from the last acknowledged 1x state; verify output')
        actual = Signal(ack.new_frequency_millihz/1000, self.signal.vpp, self.signal.phase_deg)
        self._signal_ok(actual)
        # ARM doubles the actual Q38 word, not the previous rounded mHz echo.
        # Update this operation's expected frequency from its verified FTW ACK.
        self.pending = replace(self.pending, signal=actual,
                               frequency_trim_hz=actual.frequency_hz-self.output_nominal)
        decision = super().applied(command_id, actual, acknowledged_at_s)
        self.double_acknowledged = True
        self.double_frequency_hz = actual.frequency_hz
        self.last_fit = dict(self.last_fit or {}, double_ack=asdict(ack),
            sample_counter_clock_domain='fpga', measurement_stage='double_target_hold')
        self.phase_anchor = None
        self.correction_rate = 0.
        self.samples = []
        self.local_samples = []
        self.stage = 'hold'
        self.hold_start = None
        self.last_hold = None
        return replace(decision, reason='0x94 DOUBLE_ACK recorded; capture fresh 2x image after DAC update')

    def _hold(self, image, t, now):
        if not self.double_acknowledged:
            return super()._hold(image, t, now)
        error = shape_error(image, 'infinity', self.options.target_height_div)
        telemetry = dict(self.last_fit or {}, shape_error_div=error,
                         frequency_trim_hz=self.signal.frequency_hz-self.output_nominal,
                         verified_hold_s=0.)
        self.last_fit = telemetry
        if error is None or error > self.options.shape_tolerance_div:
            self.hold_start = None
            self.last_hold = None
            return self._budget(now) or Decision('hold',
                'Target contour outside tolerance; reset hold without changing output',
                capture_after_s=t+.1, telemetry=telemetry)
        if self.last_hold is None or t-self.last_hold > self.options.max_hold_gap_s:
            self.hold_start = t
        self.last_hold = t
        held = t-self.hold_start
        telemetry['verified_hold_s'] = held
        if held >= self.options.hold_seconds:
            self.done = True
            return Decision('complete', 'Doubled target verified for >=5s without further DAC commands',
                            success=True, telemetry=telemetry)
        return self._budget(now, self.options.hold_seconds-held) or Decision('hold',
            'Keep doubled output and continue fresh image validation',
            capture_after_s=t+.1, telemetry=telemetry)
