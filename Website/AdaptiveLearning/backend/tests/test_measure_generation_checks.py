"""scripts/measure_generation_checks.py: a check that never compared is reported inert, not agreeing."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

import measure_generation_checks as tool  # noqa: E402


def test_a_scenario_the_negation_check_never_reads_is_reported_inert():
    """The default --difficulty medium resolves probability to 'dice'; 100% agreed was the report."""
    tool._SEEN.clear()
    tool._spy_negation("A die is rolled. What is the chance it shows more than 4?", "dice")
    assert tool._classify("probability")["negation"] == "inert:not_probability"


def test_a_compared_scenario_still_reports_what_it_found():
    tool._SEEN.clear()
    tool._spy_negation("What is the probability of not drawing red?", "probability_of")
    assert tool._classify("probability")["negation"] == "engaged/rejected"
