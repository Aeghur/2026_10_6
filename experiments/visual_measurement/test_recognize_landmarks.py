"""Independent rational phase families and rejection checks, no hardware."""
import unittest
import numpy as np
import cv2
from recognize_landmarks import recognize


def fixture(p,q):
    # Directly sample continuous time over q ramps, then plot each ramp.
    image=np.zeros((384,384),np.uint8)
    for k in range(q):
        u=np.linspace(0,1,5000)
        t=k+u
        points=np.column_stack((186.5+157.5*np.sin(2*np.pi*(p/q)*t+.3),44+300*u))
        layer=np.zeros_like(image)
        cv2.polylines(layer,[np.rint(points).astype(np.int32)],False,255,2,cv2.LINE_AA)
        image=np.maximum(image,layer)
    return image


class RecognitionChecks(unittest.TestCase):
    def test_rational_families_and_conditional_frequency(self):
        for p,q in ((13,2),(7,3),(13,4),(16,5),(19,6)):
            with self.subTest(p=p,q=q):
                report,*_=recognize(fixture(p,q),500.,300.)
                self.assertTrue(report['gap_topology_usable'])
                best=report['candidates'][0]
                self.assertEqual(best['phase_family_size_candidate'],q)
                self.assertLess(abs(best['frequency_hz']/(500*p/q)-1),.07)
                self.assertFalse(report['confirmed'])

    def test_merged_edge_cannot_confirm_phase_family(self):
        image=fixture(13,4)
        image[44:345,338:346]=255
        report,*_=recognize(image,500.)
        self.assertFalse(report['extrema_counts_balanced'])
        self.assertFalse(report['gap_topology_usable'])
        self.assertIsNone(report['phase_family_count_from_gaps'])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):recognize(np.zeros((384,384),np.uint8),500.)

    def test_uncalibrated_height_does_not_emit_measured_hz(self):
        report,*_=recognize(fixture(13,4),500.)
        self.assertNotIn('frequency_hz',report['candidates'][0])
        self.assertFalse(report['confirmed'])

    def test_solid_block_is_not_a_periodic_trace(self):
        image=np.zeros((384,384),np.uint8);image[44:345,28:346]=255
        with self.assertRaises(ValueError):recognize(image,500.)


if __name__=='__main__':unittest.main()
