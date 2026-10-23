"""The committed fixtures must match what the Python model produces today.

If a model change is intended, regenerate with `uv run python -m model.fixtures`
(and `--full` for the 12-month cases) and commit the new files.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from model.fixtures import FIXTURE_DIR, expected
from model.household import Battery, Inputs, load_inputs
from model.tariffs import TariffParams


def load(name):
    path = FIXTURE_DIR / name
    return json.loads(path.read_text()) if path.exists() else []


def build(fx):
    i = fx["inputs"]
    if "window" in i:
        inputs = load_inputs(i["window"], i["annual_load_kwh"], i["annual_solar_kwh"], i["curtail"])
    else:
        inputs = Inputs(np.array(i["prices"]), np.array(i["load"]), np.array(i["solar"]),
                        np.array(i["day_starts"]), i["curtail"])
    return inputs, Battery(**fx["battery"]), TariffParams(**fx["tariff"])


SMALL = load("small_cases.json")


@pytest.mark.parametrize("fx", SMALL, ids=[f["name"] for f in SMALL])
def test_small_fixture_reproduces(fx):
    inputs, battery, tariff = build(fx)
    got = expected(tariff, inputs, battery, fx["lookahead_days"])
    for strategy, exp in fx["expected"].items():
        assert got[strategy]["total"] == pytest.approx(exp["total"], abs=1e-6), strategy
        assert got[strategy]["regime"] == exp["regime"]


def test_small_fixtures_exist():
    assert len(SMALL) >= 12


YEAR = load("year_cases.json")


@pytest.mark.parametrize("fx", YEAR, ids=[f["name"] for f in YEAR])
def test_year_fixture_ordering(fx):
    """On real prices the optimiser beats both baselines (within a cent)."""
    e = fx["expected"]
    assert e["optimal"]["total"] <= e["none"]["total"] + 0.01
    assert e["optimal"]["total"] <= e["rule"]["total"] + 0.01
