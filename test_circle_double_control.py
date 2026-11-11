import math
import unittest

import numpy as np
from PIL import Image, ImageDraw

from circle_double_control import CircleThenDoubleController
from lock_board.lock_control import Frame, LockController, Options, Signal
from protocol import DoubleAck


def contour(mode):
    t = np.linspace(0, 2*np.pi, 4000)
    x = 4*np.sin(t)
    y = 4*(np.cos(t) if mode == 'circle' else np.sin(2*t))
    image = Image.new('L', (384, 384))
    ImageDraw.Draw(image).line(np.column_stack(((x+5)*38.3, (5-y)*38.3)).ravel().tolist(), fill=235, width=1)
    return image


def double_ack(signal, status=0):
    old = round(signal.frequency_hz*(1 << 38)/25_000_000)
    return DoubleAck(status, 0xf1000000, old, 2*old, 0x7ffffff0)


class CircleDoubleTests(unittest.TestCase):
    def ready(self, cls=CircleThenDoubleController):
        kwargs = {} if cls is CircleThenDoubleController else {'mode': 'diagonal'}
        controller = cls(40000, continuous_phase=True, use_policy=False, **kwargs)
        actual = Signal(40000.292, 4, 37)
        controller.start(actual, 'circle-ack', 0.)
        controller.stage = 'refine_diagonal'
        controller.phase_anchor = (0., 90., 0.)
        controller.last_fit = {'accepted': True, 'residual_output_frequency_hz': .001}
        return controller, actual

    def refinement(self):
        controller, actual = self.ready()
        for i in range(8):
            t = .1+i*.17
            decision = controller.step(Frame(contour('circle'), actual, i+1, 'circle-ack', t, 'g'), t+.001)
            if decision.command:
                return controller, decision.command, t
        self.fail('Quadrature refinement did not finish')

    def arm(self):
        controller, refinement, t = self.refinement()
        controller.applied(refinement.command_id, refinement.signal, refinement.apply_at_s)
        self.assertTrue(controller.circle_refined)
        t += .17
        decision = controller.step(Frame(contour('circle'), refinement.signal, 20, refinement.command_id, t, 'g'), t+.001)
        self.assertEqual(controller.stage, 'await_double')
        self.assertEqual(decision.status, 'set_signal')
        return controller, refinement.signal, decision.command, t

    def test_refinement_stays_at_quadrature_and_arm_preserves_register(self):
        controller, actual = self.ready()
        straight, _ = self.ready(LockController)
        for i in range(8):
            t = .1+i*.17
            frame = Frame(contour('circle'), actual, i+1, 'circle-ack', t, 'g')
            decision = controller.step(frame, t+.001)
            diagonal = straight.step(frame, t+.001)
            if decision.command:
                self.assertEqual(decision.command.signal.frequency_hz, diagonal.command.signal.frequency_hz)
                self.assertEqual(decision.command.signal.phase_deg, (diagonal.command.signal.phase_deg+90)%360)
                self.assertFalse(controller.doubled)
                break
        controller, actual, cmd, _ = self.arm()
        self.assertEqual(cmd.signal.frequency_hz, actual.frequency_hz*2)
        self.assertEqual(cmd.signal.phase_deg, actual.phase_deg)
        self.assertFalse(cmd.reset_accumulator)
        self.assertFalse(controller.double_acknowledged)

    def test_only_successful_double_ack_can_advance_and_samples_stay_fpga_domain(self):
        controller, actual, cmd, t = self.arm()
        with self.assertRaises(ValueError): controller.applied(cmd.command_id, cmd.signal, t+.02)
        with self.assertRaises(ValueError): controller.applied_double(cmd.command_id, double_ack(actual, status=1), t+.02)
        with self.assertRaises(ValueError): controller.applied_double('wrong', double_ack(actual), t+.02)
        old_clock = controller.started_at
        controller.applied_double(cmd.command_id, double_ack(actual), t+.02)
        self.assertTrue(controller.double_acknowledged)
        self.assertEqual(controller.stage, 'hold')
        self.assertEqual(controller.samples, [])
        self.assertIsNone(controller.phase_anchor)
        self.assertEqual(controller.started_at, old_clock)
        self.assertLess(controller.applied_at, 10)
        self.assertEqual(controller.last_fit['double_ack']['applied_sample_counter'], 0xf1000000)
        decision = controller.step(Frame(contour('circle'), actual, 21, 'old', t+.2, 'g'), t+.201)
        self.assertEqual(decision.status, 'recapture')

    def test_q38_actual_double_can_differ_from_twice_rounded_millihz(self):
        controller, actual, cmd, t = self.arm()
        # An old frequency rounding downward can double to an mHz rounding
        # upward. The exact hardware FTW is authoritative for this operation.
        old = round((actual.frequency_hz+.0003)*(1 << 38)/25_000_000)
        ack = DoubleAck(0, 123, old, old*2, 0x7ffffff0)
        controller.applied_double(cmd.command_id, ack, t+.02)
        self.assertEqual(controller.signal.frequency_hz, ack.new_frequency_millihz/1000)
        self.assertEqual(controller.signal.phase_deg, actual.phase_deg)
        self.assertEqual(controller.signal.vpp, actual.vpp)

    def test_original_deadline_and_target_trim_bound_are_preserved(self):
        controller, actual = self.ready()
        decision = controller.step(Frame(contour('circle'), actual, 1, 'circle-ack', 6., 'g'), 6.001)
        self.assertEqual(decision.status, 'insufficient_time')
        self.assertFalse(controller.doubled)
        controller, actual, cmd, t = self.arm()
        controller.applied_double(cmd.command_id, double_ack(actual), t+.02)
        controller._signal_ok(Signal(controller.signal.frequency_hz+.002, 4, 12))
        with self.assertRaises(ValueError): controller._signal_ok(Signal(80004., 4, 12))

    def test_continuous_plant_arms_at_falling_zero_then_verifies_without_commands(self):
        controller = CircleThenDoubleController(40000, continuous_phase=True, use_policy=False,
            options=Options(frequency_step_hz=.001, settle_s=.02, command_latency_s=.006))
        signal = Signal(40000.292, 4, 37)
        cid = 'initial'
        controller.start(signal, cid, 0.)
        carrier, previous, t = 1.1, 0., .1
        refined = armed = False
        for i in range(130):
            actual_frequency = signal.frequency_hz/(1+7.2e-6)
            carrier = (carrier+2*math.pi*actual_frequency*(t-previous))%(2*math.pi)
            previous = t
            tau = np.linspace(-.001, .001, 4000)
            x = 4*np.sin(2*math.pi*40000*(t+tau)+.63)
            y = 4*np.sin(carrier+2*math.pi*actual_frequency*tau+math.radians(signal.phase_deg))
            image = Image.new('L', (384, 384))
            ImageDraw.Draw(image).line(np.column_stack(((x+5)*38.3, (5-y)*38.3)).ravel().tolist(), fill=235, width=1)
            refined |= controller.stage == 'refine_diagonal'
            decision = controller.step(Frame(image, signal, i+1, cid, t, 'g'), t+.001)
            if decision.command:
                cmd = controller.retime_pending(t+.008)
                carrier = (carrier+2*math.pi*actual_frequency*(cmd.apply_at_s-previous))%(2*math.pi)
                previous = cmd.apply_at_s
                cid = cmd.command_id
                if controller.stage == 'await_double':
                    effective = (carrier+math.radians(signal.phase_deg))%(2*math.pi)
                    delay = ((math.pi-effective)%(2*math.pi))/(2*math.pi*actual_frequency)
                    carrier = (carrier+2*math.pi*actual_frequency*delay)%(2*math.pi)
                    previous += delay
                    self.assertAlmostEqual((carrier+math.radians(signal.phase_deg))%(2*math.pi), math.pi, places=6)
                    decision = controller.applied_double(cid, double_ack(signal), previous)
                    signal = controller.signal
                    armed = True
                    break
                else:
                    signal = cmd.signal
                    decision = controller.applied(cid, signal, cmd.apply_at_s)
            elif decision.success:
                break
            else:
                self.assertIn(decision.status, ('capture', 'hold', 'wait', 'recapture'), decision)
            t = max(t+.13, decision.capture_after_s or 0)
        self.assertTrue(refined and armed)
        self.assertEqual(controller.stage, 'hold')
        frozen = controller.signal
        # Failed geometry resets validation but must never trigger reacquisition
        # or any ordinary frequency/phase command after the DOUBLE_ACK.
        t = max(t+.13, decision.capture_after_s or 0)
        decision = controller.step(Frame(contour('circle'), frozen, 200, cid, t, 'g'), t+.001)
        self.assertIsNone(decision.command)
        self.assertEqual(controller.stage, 'hold')
        self.assertEqual(decision.telemetry['verified_hold_s'], 0.)
        for i in range(45):
            t += .13
            decision = controller.step(Frame(contour('infinity'), frozen, 201+i, cid, t, 'g'), t+.001)
            self.assertIsNone(decision.command)
            self.assertEqual(controller.signal, frozen)
            if decision.success:
                break
            self.assertEqual(decision.status, 'hold', decision)
        self.assertTrue(decision.success, decision)
        self.assertGreaterEqual(decision.telemetry['verified_hold_s'], 5.)
        self.assertLessEqual(t, 10.)


if __name__ == '__main__': unittest.main()
