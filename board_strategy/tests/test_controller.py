"""Bounded image-only integration checks. No hardware, training or sleeps."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lissajous_control import Controller, Signal, Target, Capabilities, Options, Frame, Calibration, prepare_image
from lissajous_control.backend import FrequencyBackend
from lissajous_control._vendor.multisweep_model import MultiPlant
from lissajous_control._vendor.sweep_optics import SweepSetting
from lissajous_control._vendor.active_optics import Setting


def trace(mode='circle', phase=90, height=8):
    t = np.linspace(0, 2*np.pi, 3000)
    x = 4*np.sin(t)
    y = height/2*np.sin((2 if mode == 'infinity' else 1)*t+np.radians(phase))
    im = Image.new('L', (384,384))
    points = np.column_stack(((x+5)*383/10, (5-y)*383/10))
    ImageDraw.Draw(im).line(points.ravel().tolist(), fill=240, width=1)
    return im


class StubFrequency:
    def __init__(self, confirmed=True):
        self.confirmed=confirmed
    def reset(self):
        pass
    def next_window(self):
        return 175.
    def observe(self, window, images):
        return {'confirmed': self.confirmed, 'frequency_hz':10000, 'next_window_us':None}


class GuardChecks(unittest.TestCase):
    def controller(self):
        c=Controller('session', Capabilities(True,True), frequency_backend=StubFrequency())
        c.start(now_s=0)
        c.applied(c.command.command_id,c.command.signal,0)
        return c

    def sine_controller(self):
        c=self.controller()
        c.step(Frame(trace(),c.current,c.command.command_id,1,.08,'session'),now_s=.08)
        result=c.step(Frame(trace(),c.current,c.command.command_id,2,.13,'session'),now_s=.13)
        self.assertEqual(result.status,'apply')
        c.applied(c.command.command_id,c.command.signal,.13)
        return c

    def test_default_capabilities_reject_unimplemented_ramp(self):
        c=Controller('x')
        self.assertEqual(c.start(now_s=0).reason,'ramp_required_by_trained_model')

    def test_ack_old_exposure_wrong_command_and_calibration(self):
        c=self.controller()
        early=c.step(Frame(trace(),c.current,c.command.command_id,1,.01,'session'),now_s=.01)
        self.assertEqual(early.reason,'discard_old_exposure_or_display_persistence')
        wrong=c.step(Frame(trace(),c.current,'old-command',2,.1,'session'),now_s=.1)
        self.assertEqual(wrong.reason,'frame_does_not_belong_to_applied_command')
        failure=c.step(Frame(trace(),c.current,c.command.command_id,2,.1,'other'),now_s=.1)
        self.assertEqual(failure.status,'failed')

    def test_five_seconds_requires_fresh_frames_and_small_gaps(self):
        c=self.sine_controller(); sequence=3; t=.22
        r=c.step(Frame(trace(),c.current,c.command.command_id,sequence,t,'session'),now_s=t)
        self.assertEqual(r.stage,'hold')
        duplicate=c.step(Frame(trace(),c.current,c.command.command_id,sequence,t,'session'),now_s=t+.01)
        self.assertFalse(duplicate.success); self.assertIsNone(c.hold_start)
        # A lone image after five seconds must begin a new hold, not pass it.
        t=5.3; sequence+=1
        r=c.step(Frame(trace(),c.current,c.command.command_id,sequence,t,'session'),now_s=t)
        self.assertFalse(r.success); self.assertEqual(c.hold_start,t)
        for i in range(1,52):
            when=t+i*.1; sequence+=1
            r=c.step(Frame(trace(),c.current,c.command.command_id,sequence,when,'session'),now_s=when)
            if r.success: break
        self.assertTrue(r.success,r.reason)
        self.assertGreaterEqual(r.telemetry['hold_elapsed_s'],5)
        json.dumps(r.to_dict(),allow_nan=False)

    def test_stale_frame_resets_hold_and_no_frame_timeout(self):
        c=self.sine_controller()
        c.step(Frame(trace(),c.current,c.command.command_id,3,.22,'session'),now_s=.22)
        r=c.step(Frame(trace(),c.current,c.command.command_id,4,.3,'session'),now_s=1.)
        self.assertEqual(r.reason,'stale_frame'); self.assertIsNone(c.hold_start)

    def test_timeout_blank_and_visible_departure_cannot_succeed(self):
        c=self.controller()
        r=c.step(Frame(trace(),c.current,c.command.command_id,1,10.1,'session'),now_s=10.1)
        self.assertEqual(r.reason,'control_time_limit_exceeded')
        c=self.sine_controller()
        c.step(Frame(trace(),c.current,c.command.command_id,3,.22,'session'),now_s=.22)
        r=c.step(Frame(Image.new('L',(384,384)),c.current,c.command.command_id,4,.3,'session'),now_s=.3)
        self.assertFalse(r.success); self.assertIsNone(c.hold_start)
        r=c.step(Frame(Image.new('L',(200,200)),c.current,c.command.command_id,5,.4,'session'),now_s=.4)
        self.assertIn('invalid_calibrated_image',r.reason)
        c=self.sine_controller()
        c.step(Frame(trace(),c.current,c.command.command_id,3,.22,'session'),now_s=.22)
        r=c.step(Frame(trace(phase=0),c.current,c.command.command_id,4,.3,'session'),now_s=.3)
        self.assertFalse(r.success); self.assertIsNone(c.hold_start)

    def test_phase_reset_receipt_and_limits_rejected(self):
        c=self.controller()
        with self.assertRaises(ValueError):
            c.applied(c.command.command_id,c.current,.1)
        with self.assertRaises(ValueError):
            Signal('ramp',1000,4,0,175)
        c=self.sine_controller()
        r=c.step(Frame(trace(phase=35),c.current,c.command.command_id,3,.22,'session'),now_s=.22)
        self.assertFalse(r.command.reset_accumulator)
        with self.assertRaises(ValueError):
            c.applied(c.command.command_id,replace(c.command.signal,phase_deg=0),.22)

    def test_small_frequency_drift_direction_from_two_quadrature_pairs(self):
        c=self.sine_controller()
        c.step(Frame(trace(phase=33),c.current,c.command.command_id,3,.22,'session'),now_s=.22)
        c.applied(c.command.command_id,c.command.signal,.22)
        r=c.step(Frame(trace(phase=123),c.current,c.command.command_id,4,.31,'session'),now_s=.31)
        c.applied(c.command.command_id,c.command.signal,.31)
        # Intrinsic phase has advanced by +10 degrees after .5 seconds.
        # Both images remain valid two-branch contours; hardware phase offsets
        # are removed by the estimator. Faster output needs a negative trim.
        new_phi=(33+c.current.phase_deg+10)%360
        c.step(Frame(trace(phase=new_phi),c.current,c.command.command_id,5,.72,'session'),now_s=.72)
        c.applied(c.command.command_id,c.command.signal,.72)
        r=c.step(Frame(trace(phase=(new_phi+90)%360),c.current,c.command.command_id,6,.81,'session'),now_s=.81)
        self.assertEqual(r.status,'apply')
        self.assertLess(r.command.signal.frequency_hz,10000)
        self.assertAlmostEqual(r.telemetry['fine_frequency_error_hz'],10/360/.5,delta=.015)

    def test_unconfirmed_frequency_does_not_guess_sine(self):
        c=self.controller(); c.backend.confirmed=False
        c.step(Frame(trace(),c.current,c.command.command_id,1,.08,'session'),now_s=.08)
        r=c.step(Frame(trace(),c.current,c.command.command_id,2,.13,'session'),now_s=.13)
        self.assertEqual(r.status,'failed'); self.assertEqual(c.current.waveform,'ramp')

    def test_amplitude_and_phase_from_pixels(self):
        for mode in ('diagonal','circle','infinity'):
            c=self.sine_controller(); c.target=Target(mode,4)
            c.current=replace(c.current,vpp=2.)
            c.command=replace(c.command,signal=c.current)
            r=c.step(Frame(trace(mode,33,3.5),c.current,c.command.command_id,3,.22,'session'),now_s=.22)
            self.assertEqual(r.status,'apply'); self.assertEqual(r.reason,'correct_measured_peak_to_peak_height')
            c.applied(c.command.command_id,c.command.signal,.22)
            r=c.step(Frame(trace(mode,33,4),c.current,c.command.command_id,4,.31,'session'),now_s=.31)
            self.assertEqual(r.reason,'positive_90_degree_quadrature_probe')
            c.applied(c.command.command_id,c.command.signal,.31)
            r=c.step(Frame(trace(mode,123,4),c.current,c.command.command_id,5,.40,'session'),now_s=.40)
            self.assertEqual(r.reason,'correct_visual_phase_and_small_frequency_drift')
            self.assertLess(abs(r.telemetry['phase_estimate_deg']-33),2)

    def test_calibration_identity_and_bgr(self):
        h=((10/383,0,-5),(0,10/383,-5),(0,0,1))
        cal=Calibration('new-session',(384,384),h)
        source=trace()
        result=prepare_image(source,cal)
        self.assertLess(np.abs(np.asarray(result,float)-np.asarray(source,float)).mean(),.5)
        with self.assertRaises(ValueError):
            prepare_image(Image.new('L',(640,480)))
        with self.assertRaises(ValueError):
            Options(hold_seconds=1)


class LearnedPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend=FrequencyBackend(threads=2)

    def test_real_weights_and_continuous_carrier_close_all_three_shapes(self):
        for mode,height in (('diagonal',8),('circle',6),('infinity',4)):
            with self.subTest(mode=mode):
                plant=MultiPlant(9600,37,seed=129,domain='clean',acquisition='free',exposure_ms=8,persistence_ms=2)
                c=Controller('new-session-'+mode,Capabilities(True,True),frequency_backend=self.backend,clock=lambda:plant.time)
                r=c.start(Target(mode,height)); sequence=0
                for _ in range(230):
                    if r.status in ('done','failed'): break
                    if r.status=='apply':
                        s=r.command.signal
                        setting=SweepSetting(s.rise_time_us,s.vpp,s.phase_deg) if s.waveform=='ramp' else Setting(s.frequency_hz,s.vpp,s.phase_deg)
                        plant.command(setting,wait=0)
                        c.applied(r.command.command_id,s,plant.time)
                        after=c.capture_after
                    else:
                        after=r.capture_after_s or plant.time
                    plant.advance(max(.05,after-plant.time+.001))
                    sequence+=1
                    r=c.step(Frame(plant.capture().image,c.current,c.command.command_id,sequence,plant.time,c.calibration_id))
                self.assertEqual(r.status,'done',json.dumps(r.to_dict(),ensure_ascii=True))
                self.assertEqual(r.telemetry['source_frequency_estimate_hz'],9600)
                self.assertGreaterEqual(r.telemetry['hold_elapsed_s'],5)
                self.assertLessEqual(r.telemetry['control_time_s'],10)


if __name__=='__main__':
    unittest.main()
