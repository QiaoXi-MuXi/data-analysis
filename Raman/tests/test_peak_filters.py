# -*- coding: utf-8 -*-
"""峰检测的假峰判据回归：单点尖峰、噪声、激光线/滤光片截止边、真带保留。

用合成谱，不依赖 data 里的实测文件，判据本身变了这里就会报出来。
"""
import unittest

import numpy as np

import plot_raman as pr


def synth(bands=(), sigma=1.0, step=2.3, lo=0.0, hi=1000.0, seed=0,
          baseline=500.0, slope=0.0):
    """平基线 + 白噪声 + 若干高斯带 (center, amplitude, sigma_cm)。"""
    x = np.arange(lo, hi + step / 2.0, step, dtype=float)
    rng = np.random.default_rng(seed)
    y = baseline + slope * (x - x.mean()) + rng.normal(0.0, sigma, x.size)
    for center, amp, width in bands:
        y += amp * np.exp(-0.5 * ((x - center) / width) ** 2)
    return x, y


def peaks(x, y, thr):
    ys = pr.smooth(y)
    return pr.find_peaks(x, y, ys, thr)


class SinglePointSpikeTests(unittest.TestCase):
    def sprayed(self, sigma=1.0, amp=40.0):
        """一条谱 + 900 cm-1 处一个单点尖峰，其余是噪声。"""
        x, y = synth([(600.0, 80.0, 4.0)], sigma=sigma)
        i = int(np.argmin(np.abs(x - 900.0)))
        y[i] += amp
        return x, y, i

    def test_spike_has_zero_half_width(self):
        x, y, i = self.sprayed()
        ys = pr.smooth(y)
        base = pr.baseline_under(x, ys, i)
        self.assertEqual(pr.half_width_pts(ys, i, base, y[i] - base), 0)

    def test_real_band_has_resolvable_half_width(self):
        x, y, _ = self.sprayed(amp=0.0)
        ys = pr.smooth(y)
        k = int(np.argmin(np.abs(x - 600.0)))
        base = pr.baseline_under(x, ys, k)
        self.assertGreaterEqual(pr.half_width_pts(ys, k, base, y[k] - base), 3)

    def test_spike_never_gets_a_label(self):
        for seed in range(6):
            x, y = synth([(600.0, 80.0, 4.0)], sigma=1.0, seed=seed)
            i = int(np.argmin(np.abs(x - 900.0)))
            y[i] += 40.0
            found = peaks(x, y, 10.0)
            self.assertTrue(all(abs(x[k] - 900.0) > 20.0 for _, k, _ in found),
                            "seed=%d: 单点尖峰被当成峰了" % seed)

    def test_real_band_next_to_a_spike_is_kept(self):
        x, y, _ = self.sprayed()
        found = peaks(x, y, 10.0)
        self.assertTrue(any(abs(x[k] - 600.0) <= 5.0 for _, k, _ in found))


class NoiseFloorTests(unittest.TestCase):
    def test_noise_only_spectra_get_no_labels(self):
        for seed in range(20):
            x, y = synth(seed=seed)
            thr, _sig, _hmax = pr.auto_threshold(x, y)
            self.assertEqual(peaks(x, y, thr), [],
                             "seed=%d: 纯噪声谱标出了峰" % seed)

    def test_weak_real_band_above_noise_is_kept(self):
        # 弱信号谱：σ=1.2，真带只有 9 counts（约 7.5σ）
        x, y = synth([(566.0, 9.0, 4.0)], sigma=1.2, seed=3)
        thr, _sig, _hmax = pr.auto_threshold(x, y)
        found = peaks(x, y, thr)
        self.assertEqual(len(found), 1)
        self.assertLess(abs(x[found[0][1]] - 566.0), 5.0)


class LaserEdgeTests(unittest.TestCase):
    def test_guard_radius_is_50_by_default(self):
        self.assertEqual(pr.ZERO_GUARD, 50.0)
        x, y = synth([(40.0, 80.0, 4.0), (144.0, 40.0, 4.0)])
        found = peaks(x, y, 10.0)
        self.assertFalse(any(abs(x[k] - 40.0) <= 10.0 for _, k, _ in found))
        self.assertTrue(any(abs(x[k] - 144.0) <= 5.0 for _, k, _ in found))

    def test_low_wavenumber_band_at_76_is_labelled(self):
        # 76 cm-1 的低频带（这批示谱里 0 J 没有、烧蚀后出现，宽约 15 cm-1）
        # 保留标注；这条锁住"不抹掉低波数端标注"的现行口径。
        x, y = synth([(566.0, 80.0, 4.0), (76.0, 100.0, 6.0)])
        found = peaks(x, y, 10.0)
        self.assertTrue(any(abs(x[k] - 76.0) <= 10.0 for _, k, _ in found),
                        "76 cm-1 的低频带应保留标注")


class AdaptiveThresholdTests(unittest.TestCase):
    def test_threshold_is_driven_by_noise_and_tallest_band(self):
        x, y = synth([(566.0, 600.0, 4.0), (144.0, 12.0, 4.0)], sigma=0.9)
        thr, sig, hmax = pr.auto_threshold(x, y)
        self.assertAlmostEqual(sig, 0.9, delta=0.2)
        self.assertGreater(hmax, 500.0)
        # 高动态谱由相对主峰的比例项主导 (1.1% × 600 ≈ 6.6)，比 5.5σ ≈ 5 高；
        # 144 处 12 counts 的弱带仍保留，噪声鼓包则压掉。
        self.assertGreater(thr, 5.5 * sig)
        found = peaks(x, y, thr)
        centers = [x[k] for _, k, _ in found]
        self.assertEqual(len(centers), 2)
        self.assertLess(abs(centers[0] - 144.0), 4.0)
        self.assertLess(abs(centers[1] - 566.0), 4.0)


if __name__ == "__main__":
    unittest.main()
