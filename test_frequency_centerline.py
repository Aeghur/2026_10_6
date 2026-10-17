"""Checks for the frequency-stage-only fitted-centreline route."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frequency_centerline import FrequencyCenterlineExtractor, FrequencyStageBackend
from lissajous_control.backend import FrequencyBackend


class FakeObserver:
    def __init__(self):
        self.call = None

    def observe(self, window, images, geometry_images=None):
        self.call = (window, images, geometry_images)

    def decide(self):
        return {"confirmed": False, "next_window_us": 1750.0}


class FakeInner:
    def __init__(self):
        self.call = None

    def reset(self):
        pass

    def next_window(self):
        return 175.0

    def observe(self, window, images, geometry_images=None):
        self.call = (window, images, geometry_images)
        return {"confirmed": False, "next_window_us": 1750.0}


class FakeExtractor:
    def extract(self, image, window, seed=0):
        result = np.zeros((384, 384), np.uint8)
        result[100:280, 190] = 255
        return result, {"method": "test", "output_valid": True}


class FrequencyCenterlineTests(unittest.TestCase):
    def test_backend_keeps_model_and_geometry_images_separate(self):
        backend = FrequencyBackend.__new__(FrequencyBackend)
        backend.observer = FakeObserver()
        model = [np.full((384, 384), 17, np.uint8), np.full((384, 384), 23, np.uint8)]
        geometry = [np.full((384, 384), 31, np.uint8), np.full((384, 384), 47, np.uint8)]
        backend.observe(175.0, model, geometry_images=geometry)
        _, seen_model, seen_geometry = backend.observer.call
        self.assertIs(seen_model, model)
        self.assertIs(seen_geometry, geometry)

    def test_stage_wrapper_feeds_centrelines_to_model_and_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FrequencyStageBackend.__new__(FrequencyStageBackend)
            backend.inner = FakeInner()
            backend.extractor = FakeExtractor()
            backend.output = Path(directory)
            backend.sequence = 0
            original = [np.zeros((384, 384), np.uint8) for _ in range(2)]
            result = backend.observe(175.0, original)
            _, model, geometry = backend.inner.call
            self.assertIsNot(model, original)
            self.assertTrue(all(item.mode == "L" and item.size == (384, 384) for item in model))
            self.assertTrue(all(np.count_nonzero(np.asarray(item)) for item in model))
            self.assertTrue(all(np.count_nonzero(item) for item in geometry))
            for model_image, geometry_image in zip(model, geometry):
                np.testing.assert_array_equal(np.asarray(model_image), geometry_image)
            self.assertEqual(result["centerline_scope"], "frequency_stage_only")
            self.assertEqual(result["model_input"], "fitted_centerline")
            self.assertEqual(len(list(Path(directory).glob("*_centerline.png"))), 2)

    def test_sparse_ramp_is_smoothed_and_still_geometrically_valid(self):
        window = 175.0
        cycles = 1.68
        position = np.linspace(0.0, 1.0, 220)
        columns = 192 + 150 * np.sin(2 * np.pi * cycles * position + .4)
        points = np.column_stack((
            np.rint(columns).astype(np.int32), np.arange(82, 302, dtype=np.int32)
        )).reshape(-1, 1, 2)
        image = np.zeros((384, 384), np.uint8)
        cv2.polylines(image, [points], False, 255, 1, cv2.LINE_8)
        extractor = FrequencyCenterlineExtractor(
            [20, 82, 365, 302], coarse_samples=100, fine_samples=100
        )
        centreline, report = extractor.extract(image, window)
        self.assertEqual(report["method"], "thin_trace_refit")
        self.assertTrue(report["output_valid"])
        self.assertGreater(np.count_nonzero(centreline), 200)
        self.assertAlmostEqual(report["output_frequency_hz"], cycles / (window * 1e-6), delta=150)

    def test_dense_finite_width_trace_is_reduced_to_valid_centreline(self):
        window = 1e6 / 1050
        cycles = 11.58
        y0, y1 = 88, 307
        position = np.linspace(0.0, 1.0, y1 - y0)
        columns = 187 + 153 * np.sin(2 * np.pi * cycles * position + 2.3)
        points = np.column_stack((
            np.rint(columns).astype(np.int32), np.arange(y0, y1, dtype=np.int32)
        )).reshape(-1, 1, 2)
        image = np.zeros((384, 384), np.uint8)
        cv2.polylines(image, [points], False, 255, 8, cv2.LINE_AA)
        extractor = FrequencyCenterlineExtractor(
            [20, y0, 355, y1], coarse_samples=2000, fine_samples=3000
        )
        _, report = extractor.extract(image, window, seed=5)
        self.assertEqual(report["method"], "finite_width_sine_tube")
        self.assertTrue(report["output_valid"])
        self.assertGreater(report["inner_tube_support"], .95)
        self.assertAlmostEqual(report["cycles"], cycles, delta=.08)


if __name__ == "__main__":
    unittest.main()
