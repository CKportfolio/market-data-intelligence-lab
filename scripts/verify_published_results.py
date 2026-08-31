from pathlib import Path
import json, math

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "03-adaptive-sampling-research" / "published-results"
metrics = json.loads((PUB / "holdout_metrics.json").read_text(encoding="utf-8"))

assert metrics["pass"] is True
assert metrics["gates_passed"] == metrics["gate_count"] == 9
assert metrics["tier_a_recall"] >= 0.98
assert metrics["tier_b_recall"] >= 0.90
assert metrics["ab_weighted_recall"] >= 0.95
assert metrics["tier_a_snapshot_recall"] >= 0.98
assert metrics["tier_b_snapshot_recall"] >= 0.90
assert metrics["reaction_p95_ms"] <= 750
assert metrics["oversampling_p95_ms"] <= 6000
assert metrics["false_fast_time"] <= 0.20
assert metrics["snapshot_reduction"] >= 0.40

expected = 1 - metrics["snapshot_count"] / metrics["fixed1s_snapshot_count"]
assert math.isclose(expected, metrics["snapshot_reduction"], rel_tol=0, abs_tol=1e-12)

cal = json.loads((ROOT / "04-adaptive-collector" / "calibration" / "research-v04-btcusdc-spot-btcusdt-perp.json").read_text(encoding="utf-8"))
assert cal["algorithm"] == "adaptive-ob-v0.4-winner"
assert cal["sensorUniverse"]["spotSymbol"] == "BTCUSDC"
assert cal["sensorUniverse"]["perpSymbol"] == "BTCUSDT"
assert cal["sensorUniverse"]["orderbookDepth"] == 50
assert cal["sensorUniverse"]["tickMs"] == 250

print("Published result contracts: PASS")
print(f"snapshot reduction: {metrics['snapshot_reduction']:.2%}")
print(f"A/B event recall: {metrics['tier_a_recall']:.0%} / {metrics['tier_b_recall']:.0%}")
print(f"gates: {metrics['gates_passed']}/{metrics['gate_count']}")
