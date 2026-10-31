import sys
from pathlib import Path
import unittest
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from image_frequency import measure_processed


def trace(frequency=50000,full_height=800,internal=10000,dwell=True):
    image=np.zeros((640,960),np.uint8)
    y=np.linspace(80,610,7000)
    x=480+320*np.sin(2*np.pi*frequency/internal*y/full_height+.28)
    points=np.rint(np.column_stack((x,y))).astype(np.int32)
    cv2.polylines(image,[points],False,255,3,cv2.LINE_AA)
    if dwell:cv2.line(image,(160,72),(800,72),255,7)
    return image


class ImageFrequencyTests(unittest.TestCase):
    def test_known_scale_and_frequency_grid(self):
        r,_=measure_processed(trace(),10000,800)
        self.assertEqual(r['status'],'frequency_candidate')
        self.assertLess(abs(r['frequency_hz']/50000-1),.04)
        self.assertIn(50000,r['grid_candidates_hz'])
        self.assertEqual(r['grid_candidate_hz']%100,0)
        self.assertGreaterEqual(r['independent_crossing_count'],3)
        self.assertFalse(r['confirmed'])
    def test_missing_height_does_not_use_visible_extent(self):
        r,_=measure_processed(trace(),10000)
        self.assertEqual(r['status'],'needs_full_ramp_height')
        self.assertIsNone(r['frequency_hz'])
        self.assertFalse(r['height_is_inferred_from_visible_extent'])
    def test_partial_picture_uses_independent_scale(self):
        image=trace();image[420:]=0
        r,_=measure_processed(image,10000,800)
        self.assertEqual(r['status'],'frequency_candidate')
        self.assertLess(abs(r['frequency_hz']/50000-1),.05)
    def test_different_unknown_frequency(self):
        r,_=measure_processed(trace(67100),10000,800)
        self.assertEqual(r['status'],'frequency_candidate')
        self.assertLess(abs(r['frequency_hz']/67100-1),.05)
    def test_blank_rejected(self):
        with self.assertRaises(ValueError):measure_processed(np.zeros((640,960),np.uint8),10000,800)
    def test_solid_plot_rejected(self):
        image=np.zeros((640,960),np.uint8);image[80:610,160:800]=255
        with self.assertRaises(ValueError):measure_processed(image,10000,800)
    def test_invalid_scale_rejected(self):
        for internal,height in [(0,800),(10000,0),(float('nan'),800),(10000,float('inf'))]:
            with self.assertRaises(ValueError):measure_processed(trace(),internal,height)
    def test_repetition_rate_not_hard_coded(self):
        a,_=measure_processed(trace(),10000,800)
        b,_=measure_processed(trace(),20000,800)
        self.assertAlmostEqual(b['frequency_hz']/a['frequency_hz'],2.,places=5)

class FragmentedCrossingTests(unittest.TestCase):
    def test_nearby_fragments_share_one_crossing_neighbourhood(self):
        image=trace()
        baseline,_=measure_processed(image,10000,800)
        for y in baseline['zero_crossings_y']:
            for delta in (-7,7):
                cv2.circle(image,(480,round(y)+delta),1,255,-1)
        result,_=measure_processed(image,10000,800)
        self.assertEqual(result['status'],'frequency_candidate')
        self.assertGreaterEqual(result['independent_crossing_count'],3)
        self.assertLess(abs(result['frequency_hz']/50000-1),.05)

if __name__=='__main__':unittest.main()
