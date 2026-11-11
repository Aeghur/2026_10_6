import unittest
import math
import numpy as np
from PIL import Image,ImageDraw
from lock_control import LockController,Signal,Frame,Options

class Plant:
    def __init__(self,ratio,error):
        self.source=61200.1;self.nominal=61200;self.ratio=ratio;self.bias=ratio*.1+error
        self.time=0.;self.carrier=1.1;self.alpha=.63;self.signal=Signal(ratio*61200,4,20)
    def advance(self,t):
        actual=self.signal.frequency_hz+self.bias+.01*(self.signal.frequency_hz-self.ratio*61200)
        self.carrier=(self.carrier+2*np.pi*actual*(t-self.time))%(2*np.pi);self.time=t;return actual
    def apply(self,command):
        self.advance(command.apply_at_s);self.signal=command.signal
    def image(self,t,exposure_ms=2,width=1):
        actual=self.advance(t);tau=np.linspace(-exposure_ms*.0005,exposure_ms*.0005,12000)
        x=4*np.sin(2*np.pi*self.source*(t+tau)+self.alpha)
        y=4*np.sin(self.carrier+2*np.pi*actual*tau+math.radians(self.signal.phase_deg))
        im=Image.new('L',(384,384));ImageDraw.Draw(im).line(np.column_stack(((x+5)*38.3,(5-y)*38.3)).ravel().tolist(),fill=235,width=width)
        return im

class Tests(unittest.TestCase):
    def test_closed_loop_signed_error_three_targets(self):
        for mode,error in [('circle',2.),('circle',-2.),('diagonal',.433),('infinity',-.433),('infinity',2.)]:
            p=Plant(2 if mode=='infinity' else 1,error)
            c=LockController(61200,mode,continuous_phase=True)
            cid='initial';d=c.start(p.signal,cid,0);t=.04;complete=False
            for i in range(250):
                im=p.image(t);d=c.step(Frame(im,p.signal,i,cid,t,'g1'),t+.001)
                if d.command:
                    cmd=c.retime_pending(d.command.apply_at_s+.001)
                    self.assertFalse(cmd.reset_accumulator);self.assertLessEqual(abs(cmd.frequency_trim_hz),2.25)
                    self.assertEqual(cmd.nominal_source_hz%100,0)
                    p.apply(cmd);cid=cmd.command_id;d=c.applied(cid,p.signal,cmd.apply_at_s)
                elif d.success:
                    complete=True;self.assertGreaterEqual(d.telemetry['verified_hold_s'],5);break
                else:self.assertIn(d.status,('capture','hold','recapture','wait'),(mode,error,d))
                t=max(p.time+.025,d.capture_after_s or 0)
            self.assertTrue(complete,(mode,error,d))
            actual=p.signal.frequency_hz+p.bias+.01*(p.signal.frequency_hz-p.ratio*61200)
            self.assertLess(abs(actual-p.ratio*p.source),.005)

    def test_metadata_and_nominal_guards(self):
        with self.assertRaises(ValueError):LockController(61201,continuous_phase=True)
        with self.assertRaises(ValueError):LockController(61200)
        c=LockController(61200,continuous_phase=True);s=Signal(61200,4)
        c.start(s,'a',0)
        image=Image.new('L',(384,384))
        self.assertEqual(c.step(Frame(image,s,0,'wrong',.1,'g'),.1).status,'recapture')
        self.assertEqual(c.step(Frame(image,s,0,'a',.1,'g'),1).status,'recapture')
        d=c.step(Frame(image,s,0,'a',.1,'g'),.1);self.assertEqual(d.status,'set_signal')
        self.assertEqual(c.step(Frame(image,s,1,'a',.2,'g'),.2).status,'await_ack')
        with self.assertRaises(ValueError):c.applied('bad',d.command.signal,.2)

    def test_missing_hold_frames_reset_clock(self):
        p=Plant(1,0);p.carrier=p.alpha;p.signal=Signal(61200,4,90)
        c=LockController(61200,continuous_phase=True);c.start(p.signal,'a',0);c.stage='hold'
        a=c.step(Frame(p.image(.1),p.signal,1,'a',.1,'g'),.1)
        b=c.step(Frame(p.image(5.3),p.signal,2,'a',5.3,'g'),5.3)
        self.assertFalse(a.success);self.assertFalse(b.success);self.assertEqual(b.telemetry['verified_hold_s'],0)

if __name__=='__main__':unittest.main()
