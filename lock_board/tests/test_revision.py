import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from lock_control import LockController,Options,Signal,Frame
from lock_control.branches import measure_branches
from lock_control.fit import phase_track
from lock_control.tracking import drift_fit,local_phase
from test_controller import Plant

FIXTURES=Path(__file__).parent/'fixtures'


class RevisionTests(unittest.TestCase):
    def test_real_failed_batch_retains_and_rebases_valid_views(self):
        folder=FIXTURES/'continuation'
        records=json.loads((folder/'frames.json').read_text(encoding='utf-8'))['frames']
        c=LockController(70000,continuous_phase=True)
        c.start(Signal(70000.516,4,45),'initial',0)
        for r in records[:6]:
            im=Image.open(folder/r['image']).convert('L')
            if measure_branches(im,'circle').valid:c.samples.append((im,r['time_s'],r['signal']['phase_deg']-45))
        self.assertEqual(len(c.samples),4)
        c.signal=Signal(70000.516,4,135)
        c._begin_probe(2,preserve=True)
        self.assertEqual(c.carried,4)
        # Metadata rebase preserves the actual old commanded phase.
        for _,_,offset in c.samples:
            self.assertIn(round((offset+135)%360),(45,135))
        for r in records[6:]:
            im=Image.open(folder/r['image']).convert('L')
            if measure_branches(im,'circle').valid:c.samples.append((im,r['time_s'],r['signal']['phase_deg']-135))
        fit=phase_track(c.samples,70000.516,'circle',135,2.2,.01,source_grid_hz=0)
        self.assertTrue(fit['accepted'])
        self.assertAlmostEqual(fit['residual_output_frequency_hz'],.0033,places=4)
        self.assertGreater(fit['other_drift_peak_gap'],.1)
        # A changed frequency must not retain old observations.
        c.signal=Signal(70000.515,4,135);c._begin_probe(0,preserve=True)
        self.assertEqual(len(c.samples),0)

    def test_real_hold_images_propose_small_correction_before_reprobe(self):
        folder=FIXTURES/'hold_drift'
        records=json.loads((folder/'frames.json').read_text(encoding='utf-8'))['frames']
        c=LockController(70000,continuous_phase=True,options=Options(frequency_step_hz=.001,exposure_ms=8))
        s=Signal(70000.516,4,313.46);c.start(s,'hold',0)
        # Component replay of the previously acquired, near-90-degree branch.
        c.stage='hold';c.phase_anchor=(0.,90.,0.);c.last_fit={'accepted':True,'residual_output_frequency_hz':0.}
        for i,r in enumerate(records):
            t=.1+r['time_s'];d=c.step(Frame(Image.open(folder/r['image']),s,i,'hold',t,'saved'),t+.001)
            if d.command:break
        self.assertEqual(d.status,'set_signal')
        self.assertIn('Local multi-frame',d.command.reason)
        self.assertFalse(d.success)
        self.assertLess(d.command.signal.frequency_hz,s.frequency_hz)
        self.assertLess(s.frequency_hz-d.command.signal.frequency_hz,.01)
        self.assertEqual(d.telemetry['verified_hold_s'],0)
        # Old images after this point cannot validate a counterfactual command.

    def test_history_is_age_bounded_and_retry_has_no_fake_success(self):
        c=LockController(70000,continuous_phase=True)
        c.start(Signal(70000,4),'a',0)
        im=Image.new('L',(384,384))
        c.samples=[(im,float(i),0.) for i in range(20)]
        c._begin_probe(1,preserve=True)
        self.assertLessEqual(len(c.samples),5)
        c.samples=[]
        result=c.step(Frame(im,c.signal,1,'a',6.,'g'),6.)
        self.assertEqual(result.status,'insufficient_time');self.assertFalse(result.success)

    def test_local_regression_uses_multiple_frames_and_sign(self):
        self.assertIsNone(drift_fit([(0.,90.),(.2,90.2)]))
        for rate in (-.006,.006):
            samples=[(i*.19,90+360*rate*i*.19) for i in range(8)]
            result=drift_fit(samples)
            self.assertAlmostEqual(result['drift_hz'],rate,places=8)
            self.assertGreater(abs(rate),3*result['drift_sigma_hz'])
        c=LockController(70000,continuous_phase=True)
        c.start(Signal(70000,4),'a',0)
        self.assertIsNone(c._track(Image.new('L',(384,384)),.1))

    def test_ambiguous_probe_retries_with_bounded_retained_evidence(self):
        p=Plant(1,0.)
        c=LockController(61200,continuous_phase=True);cid='initial'
        c.start(p.signal,cid,0);t=.04
        ambiguous=dict(accepted=False,residual_output_frequency_hz=.004,
                       fit_error=.0001,other_drift_peak_gap=.00001)
        with patch('lock_control.controller.phase_track',return_value=ambiguous):
            for i in range(8):
                d=c.step(Frame(p.image(t),p.signal,i,cid,t,'a'),t+.001)
                if d.command:
                    p.apply(d.command);cid=d.command.command_id
                    ack=c.applied(cid,p.signal,d.command.apply_at_s)
                    t=max(t+.12,ack.capture_after_s or 0)
        self.assertEqual(d.status,'recapture')
        self.assertFalse(d.success)
        self.assertGreater(c.carried,0)
        self.assertLessEqual(len(c.samples),24)
        self.assertEqual(c.batch_count,1)

    def test_slow_capture_closed_loop_after_external_compensation(self):
        # Measured-like probe spacing .268s, hold spacing .19s and mHz UART.
        cases=[(mode,error) for mode in ('circle','diagonal','infinity') for error in (-.012,0.,.004,.012)]
        for mode,error in cases:
            with self.subTest(mode=mode,error=error):
                ratio=2 if mode=='infinity' else 1
                p=Plant(ratio,error)
                c=LockController(61200,mode,continuous_phase=True,
                    options=Options(frequency_step_hz=.001,settle_s=.08,command_latency_s=.006,exposure_ms=8))
                cid='initial';d=c.start(p.signal,cid,0);t=.15
                for i in range(150):
                    d=c.step(Frame(p.image(t,exposure_ms=8,width=2),p.signal,i,cid,t,'slow'),t+.04)
                    if d.command:
                        cmd=c.retime_pending(t+.046);p.apply(cmd);cid=cmd.command_id
                        d=c.applied(cid,p.signal,cmd.apply_at_s)
                    elif d.success:break
                    else:self.assertIn(d.status,('capture','hold','recapture','wait'),d)
                    interval=.19 if c.stage=='hold' else .268
                    t=max(t+interval,d.capture_after_s or 0)
                self.assertTrue(d.success,d)
                self.assertGreaterEqual(d.telemetry['verified_hold_s'],5.)
                self.assertLessEqual(t,10.)

if __name__=='__main__':unittest.main()
