"""Shared fixtures for the analysis test suite.

NO TEST IN HERE MAY REQUIRE A REAL PICO OR A REAL LOGIC ANALYSER. Everything
is built from ``tools/analysis/synthetic.py``. That is not a limitation of the
tests, it is the point of having a synthetic data generator: the analysis half
has to be developable on a train.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tools/analysis/tests/conftest.py -> tools/ must be importable as the parent
# of the `analysis` package. Doing it here keeps the suite runnable both from
# the repo root and from this directory.
TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from analysis import synthetic  # noqa: E402  (sys.path is set up just above)


@pytest.fixture(scope="session")
def campaign_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A four-motor staircase campaign plus a before/after PID step pair.

    Session-scoped because generating it is the slow part; every test that
    mutates anything gets its own copy instead.
    """
    root = tmp_path_factory.mktemp("campaign")
    synthetic.write_campaign(root, motors=(1, 2, 3, 4), hold_s=0.6, with_analyser=True)
    return root


@pytest.fixture(scope="session")
def motor_char_root(campaign_root: Path) -> Path:
    return campaign_root / "motor-char"


@pytest.fixture(scope="session")
def pid_step_root(campaign_root: Path) -> Path:
    return campaign_root / "pid-step"


@pytest.fixture
def one_run_dir(tmp_path: Path) -> Path:
    """A single, small, valid run directory that a test may modify freely."""
    return synthetic.write_staircase_run(
        tmp_path / "run", motor_id=1, hold_s=0.4, with_analyser=True,
        analyser_duration_s=0.3,
    )
