import pytest

from app.engine.constraints import certify, check
from app.schemas.metrics import Feasibility, Metric, Provenance

AN = Provenance.ANALYTICAL


def test_metric_band_must_be_ordered():
    with pytest.raises(ValueError):
        Metric(value=5, unit="W", low=6, high=7, provenance=AN)


def test_metric_rel_band():
    m = Metric.rel(10, "W", AN, 0.2)
    assert (m.low, m.high) == (8.0, 12.0)


def test_le_check_states():
    assert check("p", "<=", 55, Metric.rel(40, "W", AN, 0.2)).status == "pass"  # high 48
    assert check("p", "<=", 55, Metric.rel(50, "W", AN, 0.2)).status == "uncertain"  # 40..60
    assert check("p", "<=", 55, Metric.rel(80, "W", AN, 0.2)).status == "fail"  # low 64


def test_ge_check_states():
    ok = Metric(value=0.99, unit="f", low=0.95, high=1, provenance=AN)
    unc = Metric(value=0.92, unit="f", low=0.85, high=0.97, provenance=AN)
    bad = Metric(value=0.85, unit="f", low=0.8, high=0.89, provenance=AN)
    assert check("a", ">=", 0.9, ok).status == "pass"
    assert check("a", ">=", 0.9, unc).status == "uncertain"
    assert check("a", ">=", 0.9, bad).status == "fail"


def test_limit_is_inclusive():
    assert check("p", "<=", 55, Metric.exact(55, "W", AN)).status == "pass"
    assert check("a", ">=", 0.9, Metric.exact(0.9, "f", AN)).status == "pass"


def test_certify_verified_requires_measured():
    ok = check("p", "<=", 55, Metric.exact(10, "W", Provenance.MEASURED))
    assert certify([(ok, Provenance.MEASURED)]) == Feasibility.VERIFIED_FEASIBLE
    assert certify([(ok, AN)]) == Feasibility.PREDICTED_FEASIBLE
    bad = check("p", "<=", 55, Metric.exact(99, "W", Provenance.MEASURED))
    assert certify([(bad, Provenance.MEASURED)]) == Feasibility.VERIFIED_INFEASIBLE
    assert certify([(bad, AN)]) == Feasibility.INFEASIBLE


def test_certify_fail_beats_uncertain():
    unc = check("p", "<=", 55, Metric.rel(50, "W", AN, 0.2))
    bad = check("q", "<=", 1, Metric.exact(9, "W", AN))
    assert certify([(unc, AN), (bad, AN)]) == Feasibility.INFEASIBLE
