import unittest
from unittest.mock import Mock,patch
import numpy as np
from one_flush_two_stage import Camera


class OneFlushTests(unittest.TestCase):
    def camera(self,reads):
        camera=Camera.__new__(Camera)
        camera.cap=Mock();camera.cap.read.side_effect=reads
        camera.current_exposure=80
        return camera

    def test_uses_second_frame_and_only_two_reads(self):
        old=np.zeros((2,2),np.uint8);fresh=np.ones((2,2),np.uint8)
        camera=self.camera([(True,old),(True,fresh)])
        with patch('one_flush_two_stage.time.monotonic',return_value=10.):
            image,timestamp=camera.frame_timed()
        self.assertIs(image,fresh)
        self.assertEqual(camera.cap.read.call_count,2)
        self.assertAlmostEqual(timestamp,9.996)

    def test_discard_failure_aborts(self):
        camera=self.camera([(False,None)])
        with self.assertRaises(RuntimeError):camera.frame_timed()
        self.assertEqual(camera.cap.read.call_count,1)

    def test_second_frame_failure_aborts(self):
        camera=self.camera([(True,None),(False,None)])
        with self.assertRaises(RuntimeError):camera.frame_timed()
        self.assertEqual(camera.cap.read.call_count,2)

if __name__=='__main__':unittest.main()
