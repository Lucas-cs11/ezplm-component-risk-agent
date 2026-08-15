import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.semantic_cache import (
    SemanticCache,
    canonical_constraint_fingerprint,
)


def test_cache_fingerprint_changes_when_electrical_requirements_change():
    base = {
        "category": "dc_dc_converter",
        "topology": "buck",
        "input_voltage_nominal_v": 12,
        "output_voltage_v": 5,
        "output_current_a": 2,
        "grade": "industrial",
    }
    changed_output = {**base, "output_voltage_v": 3.3}
    changed_topology = {**base, "topology": "boost"}

    assert canonical_constraint_fingerprint(base) != canonical_constraint_fingerprint(changed_output)
    assert canonical_constraint_fingerprint(base) != canonical_constraint_fingerprint(changed_topology)


def test_cache_fingerprint_normalizes_numeric_and_key_order():
    first = {
        "output_current_a": 2.0,
        "output_voltage_v": 5,
        "input_voltage_nominal_v": 12.00,
        "topology": "buck",
    }
    second = {
        "topology": " BUCK ",
        "input_voltage_nominal_v": 12,
        "output_voltage_v": 5.0,
        "output_current_a": 2,
    }

    assert canonical_constraint_fingerprint(first) == canonical_constraint_fingerprint(second)


def test_cache_fingerprint_rejects_incomplete_constraints():
    incomplete = {"topology": "buck", "output_voltage_v": 5}

    assert canonical_constraint_fingerprint(incomplete) is None


def test_exact_cache_round_trip_does_not_cross_constraint_fingerprints():
    constraints = {
        "category": "dc_dc_converter",
        "topology": "buck",
        "input_voltage_nominal_v": 12,
        "output_voltage_v": 5,
        "output_current_a": 2,
    }
    different_constraints = {**constraints, "output_voltage_v": 3.3}
    report = {
        "constraints": constraints,
        "candidates": [{"part": {"part_number": "TEST-5V-2A"}}],
        "recommended_parts": [],
        "risks": {"overall_risk_level": "low", "risk_items": []},
        "evidence": [{"part_number": "TEST-5V-2A", "claim": "output verified"}],
    }

    with tempfile.TemporaryDirectory() as directory:
        cache = SemanticCache(persist_dir=directory)
        fingerprint = canonical_constraint_fingerprint(constraints)

        assert cache.set_exact(fingerprint, report)
        assert cache.get_exact(fingerprint)["cached_result"] == report
        assert cache.get_exact(canonical_constraint_fingerprint(different_constraints)) is None
