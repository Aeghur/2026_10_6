import unittest
from unittest.mock import Mock
import cv2
import numpy as np
from sine_refinement import candidates,score_image,choose


def render(phases):
    image=np.zeros((640,960),np.uint8);t=np.linspace(0,2*np.pi,3000)
    for phase in phases:
        points=np.rint(np.column_stack((480+320*np.sin(t),320+290*np.sin(t+phase)))).astype(np.int32)
        cv2.polylines(image,[points],False,255,3)
    return image


class SineTests(unittest.TestCase):
    def test_candidate_grid(self):
        self.assertEqual(candidates(41200),[41000,41100,41200,41300,41400])
    def test_single_curve_beats_phase_overlap(self):
        busy=score_image(render(np.linspace(0,2*np.pi,35)))['score']
        for phase in [0,.4,1.57,2.8,3.14]:
            clear=score_image(render([phase]))['score']
            self.assertLess(clear,busy/2)
    def test_fast_interframe_phase_changes_allowed(self):
        clear=[dict(metrics=score_image(render([p])))for p in [0,2.1,.6]]
        busy=[dict(metrics=score_image(render(np.linspace(0,6,30))))for _ in range(3)]
        r=choose([dict(frequency_hz=41300,frames=clear),dict(frequency_hz=41200,frames=busy)])
        self.assertEqual(r['frequency_hz'],41300)
    def test_no_clear_winner_is_ambiguous(self):
        frames=[dict(metrics=score_image(render([1.])))for _ in range(3)]
        self.assertIsNone(choose([dict(frequency_hz=41000,frames=frames),dict(frequency_hz=41100,frames=frames)])['frequency_hz'])
    def test_blank_rejected(self):
        with self.assertRaises(ValueError):score_image(np.zeros((640,960),np.uint8))
class FinishOutputTests(unittest.TestCase):
    def test_success_holds_selected_sine(self):
        from two_stage import finish_output
        link=Mock()
        waveform,_=finish_output(link,dict(status='frequency_candidate',frequency_hz=41300))
        self.assertEqual(waveform,'sine')
        link.set_waveform.assert_called_once_with('sine',41300,0,8)
        link.set_decay_pulse.assert_not_called()
    def test_ambiguous_restores_probe(self):
        from two_stage import finish_output
        link=Mock()
        waveform,_=finish_output(link,dict(status='ambiguous',frequency_hz=None))
        self.assertEqual(waveform,'decay_pulse')
        link.set_waveform.assert_not_called()
        link.set_decay_pulse.assert_called_once_with(100,0,8)

if __name__=='__main__':unittest.main()
