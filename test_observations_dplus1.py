"""Pure offline tests for the conservative, non-destructive D+1 gate."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from pathlib import Path
import json

import evaluate_observations_dplus1 as trial
from study_config import TZ


class Dplus1Tests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 16)
        self.targets = trial.targets_for(self.day)
        self.recovered = {}
        self.originals = {}
        for station in trial.ACTIVE:
            sid = station["id"]
            for target in self.targets:
                key = (sid, target.isoformat())
                row = {"observed_at_local": target.isoformat(),
                       "observed": {field: (0 if field == "precip_mm" else 8)
                                    for field in trial.FIELDS}}
                self.recovered[key] = row
                self.originals[key] = {"record": row, "path": "original.json"}

    def test_expected_96_and_dates(self):
        self.assertEqual(trial.EXPECTED, 96)
        self.assertEqual(len(self.recovered), 24)
        self.assertEqual([x.hour for x in self.targets], [9, 12, 15, 18])

    def test_recovered_complete_matches_and_passes(self):
        result = trial.compare(self.day, self.recovered, self.originals, [])
        self.assertTrue(result["passed"])
        self.assertEqual(result["dplus1_values_present"], 96)
        self.assertEqual(result["original_values_matched"], 96)

    def test_missing_or_changed_original_field_blocks_cutover(self):
        key = next(iter(self.recovered))
        self.recovered[key] = {
            "observed_at_local": self.recovered[key]["observed_at_local"],
            "observed": {**self.recovered[key]["observed"], "direction_deg": 22}}
        self.assertFalse(trial.compare(self.day, self.recovered, self.originals, [])["passed"])
        self.recovered[key]["observed"]["direction_deg"] = None
        self.assertFalse(trial.compare(self.day, self.recovered, self.originals, [])["passed"])

    def test_missing_precipitation_in_both_is_not_regression(self):
        for key in self.recovered:
            self.recovered[key]["observed"]["precip_mm"] = None
            self.originals[key]["record"]["observed"]["precip_mm"] = None
        result = trial.compare(self.day, self.recovered, self.originals, [])
        self.assertTrue(result["passed"])
        self.assertEqual(result["dplus1_values_present"], 72)

    def test_measurement_outside_window_blocks_cutover(self):
        key = next(iter(self.recovered))
        t = datetime.fromisoformat(key[1]) + timedelta(minutes=31)
        self.recovered[key] = {
            "observed_at_local": t.isoformat(),
            "observed": self.recovered[key]["observed"]}
        self.assertFalse(trial.compare(self.day, self.recovered, self.originals, [])["passed"])

    def test_provider_error_blocks_cutover(self):
        self.assertFalse(trial.compare(self.day, self.recovered, self.originals,
                                      [{"provider": "PortosRio", "error": "timeout"}])["passed"])


if __name__ == "__main__":
    unittest.main()
