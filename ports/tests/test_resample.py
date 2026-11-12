from datetime import datetime

from django.test import SimpleTestCase

from ports.util import resample

START = datetime(2025, 1, 1)
END = datetime(2025, 1, 2)  # one day, inclusive at both ends


class TestResample(SimpleTestCase):
    def test_same_timestep(self):
        series = resample(list(range(97)), 15, START, END, 15)
        self.assertEqual(len(series), 97)
        self.assertEqual(series.iloc[-1], 96)

    def test_upsample_has_no_nan(self):
        # hourly profile (e.g. solar radiation) used in a 15 min simulation
        series = resample([float(h) for h in range(25)], 60, START, END, 15)
        self.assertEqual(len(series), 97)
        self.assertFalse(series.isna().any())
        self.assertEqual(series.index[0], START)
        self.assertEqual(series.index[-1], END)
        # each hourly value is repeated for its four quarter hours
        self.assertEqual(list(series.iloc[:5]), [0.0, 0.0, 0.0, 0.0, 1.0])

    def test_downsample_averages(self):
        series = resample([float(i) for i in range(97)], 15, START, END, 60)
        self.assertEqual(len(series), 25)
        self.assertFalse(series.isna().any())
        self.assertEqual(series.iloc[0], 1.5)  # mean of 0, 1, 2, 3

    def test_short_profile_is_forward_filled(self):
        series = resample([1.0, 2.0], 60, START, END, 15)
        self.assertEqual(len(series), 97)
        self.assertFalse(series.isna().any())
        self.assertEqual(series.iloc[-1], 2.0)

    def test_long_profile_is_cut(self):
        series = resample([1.0] * 1000, 60, START, END, 15)
        self.assertEqual(len(series), 97)
