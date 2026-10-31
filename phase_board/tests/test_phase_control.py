import unittest
import math
import numpy as np
from PIL import Image,ImageDraw
from phase_control import PhaseController,Options,Signal,Frame


def bitmap(source,ratio,alpha,beta,register,t,width=2):
    ts=np.linspace(t-.003,t+.003,max(2500,int(source*ratio*.006*30)))
    x=4*np.sin(2*np.pi*source*ts+math.radians(alpha))
    y=4*np.sin(2*np.pi*source*ratio*ts+math.radians(beta+register))
    im=Image.new('L',(384,384));ImageDraw.Draw(im).line(np.column_stack(((x+5)*38.3,(5-y)*38.3)).ravel().tolist(),fill=235,width=width)
    return im


class ControllerTests(unittest.TestCase):
    def test_closed_loop_all_modes_and_hold(self):
        for mode in ('circle','diagonal','infinity'):
            ratio=2 if mode=='infinity' else 1
            ctrl=PhaseController(3000,mode,continuous_phase=True)
            signal=Signal(3000*ratio,4,27);cid='initial';ctrl.start(signal,cid,0)
            now=.1;commands=0;done=False
            for i in range(100):
                im=bitmap(3000,ratio,37,83,signal.phase_deg,now)
                d=ctrl.step(Frame(im,signal,i,cid,now,'geometry-1'),now+.001)
                if d.command:
                    cmd=ctrl.retime_pending(d.command.apply_at_s+.005)
                    self.assertFalse(cmd.reset_accumulator)
                    self.assertEqual(cmd.signal.frequency_hz,signal.frequency_hz)
                    self.assertEqual(cmd.signal.vpp,signal.vpp)
                    signal=cmd.signal;cid=cmd.command_id;commands+=1
                    ctrl.applied(cid,signal,cmd.apply_at_s)
                    now=cmd.apply_at_s+.1
                elif d.success:
                    self.assertGreaterEqual(d.telemetry['verified_hold_s'],5)
                    done=True;break
                else:
                    self.assertIn(d.status,('hold','recapture','wait'),d)
                    now+=.1
            self.assertTrue(done,(mode,d));self.assertGreaterEqual(commands,6)

    def test_exact_grid_and_capability_guard(self):
        with self.assertRaises(ValueError):PhaseController(3001,continuous_phase=True)
        with self.assertRaises(ValueError):PhaseController(3000)
        c=PhaseController(3000,'infinity',continuous_phase=True)
        with self.assertRaises(ValueError):c.start(Signal(3000,4),'init',0)

    def test_stale_duplicate_wrong_command_and_pending_ack(self):
        c=PhaseController(3000,continuous_phase=True);s=Signal(3000,4);c.start(s,'init',0)
        im=bitmap(3000,1,20,80,0,.1)
        self.assertEqual(c.step(Frame(im,s,0,'old',.1,'g'),.1).status,'recapture')
        self.assertEqual(c.step(Frame(im,s,0,'init',.1,'g'),1).status,'recapture')
        d=c.step(Frame(im,s,0,'init',.1,'g'),.1);self.assertEqual(d.status,'set_phase')
        self.assertEqual(c.step(Frame(im,s,1,'init',.2,'g'),.2).status,'await_ack')
        with self.assertRaises(ValueError):c.applied('wrong',d.command.signal,.2)
        c.applied(d.command.command_id,d.command.signal,d.command.apply_at_s)
        self.assertEqual(c.step(Frame(im,d.command.signal,0,d.command.command_id,.3,'g'),.3).status,'recapture')

    def test_bad_image_and_calibration_guard(self):
        c=PhaseController(3000,continuous_phase=True);s=Signal(3000,4);c.start(s,'init',0)
        for i in range(3):d=c.step(Frame(Image.new('L',(384,384)),s,i,'init',.1+i*.1,'g'),.1+i*.1)
        self.assertEqual(d.status,'needs_better_image');self.assertFalse(d.success)
        d=c.step(Frame(Image.new('L',(384,384)),s,4,'init',.6,'changed'),.6)
        self.assertEqual(d.status,'restart_required')

    def test_hold_gap_does_not_count_unobserved_seconds(self):
        c=PhaseController(3000,continuous_phase=True);s=Signal(3000,4,90);c.start(s,'init',0)
        # Enter verification directly to isolate freshness/hold accounting.
        c.phase='hold'
        im=bitmap(3000,1,0,0,90,.1)
        a=c.step(Frame(im,s,1,'init',.1,'g'),.1)
        b=c.step(Frame(im,s,2,'init',5.2,'g'),5.2)
        self.assertEqual(a.status,'hold');self.assertFalse(b.success)
        self.assertEqual(b.telemetry['verified_hold_s'],0)


if __name__=='__main__':unittest.main()
