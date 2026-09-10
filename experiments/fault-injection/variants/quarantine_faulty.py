"""Classify repeated gate outcomes (attempt 1 - defective on purpose).

Two injected defects, both of the kind a worker really produces:

1. an empty sample is reported as ``stable-fail`` - "no data" is silently
   promoted to "this gate always fails";
2. the insufficient-data guard is ``<=`` instead of ``<``, so exactly
   ``min_samples`` outcomes are refused a verdict.

Neither is caught by the tests below, which only exercise 4-5 sample series.
"""

from __future__ import annotations


def classify(outcomes, min_samples: int = 3, flake_threshold: float = 0.5):
    samples = len(outcomes)
    if samples == 0:
        return {"verdict": "stable-fail", "pass_rate": 0.0, "samples": 0, "dominant": "fail"}
    if samples <= min_samples:
        return {
            "verdict": "insufficient-data",
            "pass_rate": None,
            "samples": samples,
            "dominant": None,
        }
    passes = sum(1 for outcome in outcomes if outcome)
    rate = round(passes / samples, 3)
    if rate == 1.0:
        verdict, dominant = "stable-pass", "pass"
    elif rate == 0.0:
        verdict, dominant = "stable-fail", "fail"
    else:
        verdict = "flaky"
        dominant = "pass" if rate >= flake_threshold else "fail"
    return {"verdict": verdict, "pass_rate": rate, "samples": samples, "dominant": dominant}


def quarantine(gates, min_samples: int = 3, flake_threshold: float = 0.5):
    flaky = [
        gate_id
        for gate_id, outcomes in gates.items()
        if classify(outcomes, min_samples, flake_threshold)["verdict"] == "flaky"
    ]
    return sorted(flaky)
