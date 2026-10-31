import unittest
from unittest.mock import Mock,patch
import numpy as np
from two_flush_two_stage import Camera


class TwoFlushTests(unittest.TestCase):
    def camera(self,reads):
        camera=Camera.__new__(Camera)
        camera.cap=Mock();camera.cap.read.side_effect=reads
        camera.current_exposure=80
        return camera

    def test_uses_third_frame_and_only_three_reads(self):
        old=np.zeros((2,2),np.uint8);fresh=np.ones((2,2),np.uint8)
        camera=self.camera([(True,old),(True,old),(True,fresh)])
        with patch('two_flush_two_stage.time.monotonic',return_value=10.):
            image,timestamp=camera.frame_timed()
        self.assertIs(image,fresh)
        self.assertEqual(camera.cap.read.call_count,3)
        self.assertAlmostEqual(timestamp,9.996)

    def test_discard_failure_aborts(self):
        camera=self.camera([(False,None)])
        with self.assertRaises(RuntimeError):camera.frame_timed()
        self.assertEqual(camera.cap.read.call_count,1)

    def test_selected_frame_failure_aborts(self):
        camera=self.camera([(True,None),(True,None),(False,None)])
        with self.assertRaises(RuntimeError):camera.frame_timed()
        self.assertEqual(camera.cap.read.call_count,3)

    def test_second_discard_failure_aborts(self):
        camera=self.camera([(True,None),(False,None)])
        with self.assertRaises(RuntimeError):camera.frame_timed()
        self.assertEqual(camera.cap.read.call_count,2)

if __name__=='__main__':unittest.main()
