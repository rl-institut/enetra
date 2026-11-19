"""Tests for ports.util.process_file_to_solar_radiation."""

import datetime
import tempfile
from pathlib import Path

import pandas as pd
from django.test import TestCase

from ports.models import SolarRadiation
from ports.util import process_file_to_solar_radiation

# datetime.UTC needs Python 3.11, the project runs on 3.10 (ruff targets py312)
UTC = datetime.timezone.utc  # noqa: UP017


def local_times(start: str, periods: int, freq: str = "15min") -> list[str]:
    """Europe/Berlin wall-clock timestamps as they appear in the csv files (no offset)."""
    index = pd.date_range(start, periods=periods, freq=freq, tz="Europe/Berlin")
    return [t.strftime("%Y-%m-%d %H:%M:%S") for t in index]


class ProcessFileToSolarRadiationTest(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write_csv(self, name: str, times: list[str], values: list[float]) -> Path:
        file = self.dir / f"{name}.csv"
        lines = ["time,power"] + [f"{t},{v}" for t, v in zip(times, values, strict=True)]
        file.write_text("\n".join(lines) + "\n")
        return file

    def test_start_and_timestep_come_from_time_column(self):
        times = local_times("2025-01-01 00:00", 4)
        values = [0.0, 12.5, 30.0, 7.25]
        process_file_to_solar_radiation(self.write_csv("winter", times, values))

        radiation = SolarRadiation.all_objects.get(name="winter")
        self.assertEqual(radiation.timeseries, values)
        self.assertEqual(radiation.timestep, 15.0)
        # 00:00 CET is 23:00 UTC on the previous day
        self.assertEqual(
            radiation.timeseries_start, datetime.datetime(2024, 12, 31, 23, 0, tzinfo=UTC)
        )

    def test_spring_dst_change_is_consistent(self):
        # local clock jumps from 01:45 to 03:00, in UTC the steps stay 15 min
        times = local_times("2025-03-30 01:00", 8)
        self.assertIn("2025-03-30 03:00:00", times)
        with self.assertNoLogs("ports.util", level="WARNING"):
            process_file_to_solar_radiation(self.write_csv("spring", times, [1.0] * 8))

        radiation = SolarRadiation.all_objects.get(name="spring")
        self.assertEqual(radiation.timestep, 15.0)
        self.assertEqual(
            radiation.timeseries_start, datetime.datetime(2025, 3, 30, 0, 0, tzinfo=UTC)
        )

    def test_autumn_dst_change_is_consistent(self):
        # local clock repeats 02:00-02:45, "infer" resolves the ambiguous hour by order
        times = local_times("2025-10-26 01:00", 12)
        self.assertEqual(times.count("2025-10-26 02:00:00"), 2)
        with self.assertNoLogs("ports.util", level="WARNING"):
            process_file_to_solar_radiation(self.write_csv("autumn", times, [1.0] * 12))

        radiation = SolarRadiation.all_objects.get(name="autumn")
        self.assertEqual(radiation.timestep, 15.0)
        self.assertEqual(
            radiation.timeseries_start, datetime.datetime(2025, 10, 25, 23, 0, tzinfo=UTC)
        )
