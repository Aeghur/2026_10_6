import sys
from pathlib import Path
import unittest
import numpy as np
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parent))
from slope_experiment import slopes, verify_single_trace
from generate_overlap import render

class SlopeTests(unittest.TestCase):
    def image(self,r=3,k=1):
        return render((384,384),(40,50,344,334),[r,.37,2*np.pi*r,k,2],samples=2500)
    def test_grid_candidates_include_true_frequency(self):
        r,_=slopes(self.image(),5000)
        self.assertIn(15000,r['candidates_hz'][:10])
        self.assertFalse(r['confirmed'])
    def test_multi_phase_candidates(self):
        r,_=slopes(self.image(2.2,5),5000)
        self.assertIn(11000,r['candidates_hz'][:10])
    def test_filled_image_rejected(self):
        a=np.zeros((384,384),np.uint8);a[50:335,40:345]=255
        with self.assertRaises(ValueError):slopes(a,5000)
    def test_single_trace_verification(self):
        self.assertTrue(verify_single_trace(self.image(1),5000,1)['accepted'])
        self.assertFalse(verify_single_trace(self.image(3),5000,1)['accepted'])
    def test_overlapped_branches_not_verified(self):
        self.assertFalse(verify_single_trace(self.image(2.2,5),5000,1)['accepted'])
    def test_straight_return_trace_allowed(self):
        image=self.image(1)
        cv2.line(image,(192,52),(192,332),255,2)
        result=verify_single_trace(image,5000,1)
        self.assertTrue(result['accepted'],result)
        self.assertTrue(result['residual_is_line_like'])
    def test_short_fragments_allowed(self):
        image=self.image(1)
        for y in (90,140,220,270):
            cv2.line(image,(65,y),(72,y+2),255,2)
        self.assertTrue(verify_single_trace(image,5000,1)['accepted'])
    def test_long_missing_curve_rejected(self):
        image=self.image(1);image[160:200]=0
        self.assertFalse(verify_single_trace(image,5000,1)['accepted'])
    def test_second_sine_rejected(self):
        image=self.image(1,2)
        other=render((384,384),(40,50,344,334),[1,1.8,0,1,2],samples=2500)
        image=np.maximum(image,other)
        self.assertFalse(verify_single_trace(image,5000,1)['accepted'])
    def test_top_line_does_not_veto_interior_crossings(self):
        clean=self.image(3)
        baseline,_=slopes(clean,5000)
        noisy=clean.copy();cv2.line(noisy,(0,43),(383,43),255,2)
        result,_=slopes(noisy,5000)
        self.assertEqual(result['bounds'][0],baseline['bounds'][0])
        self.assertEqual(result['bounds'][2],baseline['bounds'][2])
        self.assertGreaterEqual(result['independent_crossing_count'],2)
        before=np.median([abs(v['dy_dx']) for v in baseline['slopes']])
        after=np.median([abs(v['dy_dx']) for v in result['slopes']])
        self.assertLess(abs(after/before-1),.08)
    def test_boundary_contact_is_recorded_not_vetoed(self):
        image=render((384,384),(0,50,383,334),[3,.37,0,1,2],samples=2500)
        result,_=slopes(image,5000)
        self.assertTrue(result['possible_horizontal_clipping'])
        self.assertFalse(result['confirmed'])
    def test_local_obstruction_preserves_other_crossings(self):
        image=self.image(3);image[150:185,150:235]=255
        result,_=slopes(image,5000)
        self.assertGreaterEqual(result['independent_crossing_count'],2)
if __name__=='__main__':unittest.main()
