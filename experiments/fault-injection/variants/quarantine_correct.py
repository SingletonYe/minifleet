"""Classify repeated gate outcomes and list the gates worth quarantining.

The attempt-2 implementation: the insufficient-data rule is applied before any
verdict is formed, and the boundary is inclusive.
"""

from __future__ import annotations


def classify(outcomes, min_samples: int = 3, flake_threshold: float = 0.5):
    """Label a series of gate outcomes.

    A sample smaller than ``min_samples`` never receives a verdict: an
    under-sampled gate is insufficient data, which is a different statement from
    "this gate failed".
    """

    if min_samples <= 0:
        raise ValueError("min_samples must be positive")
    samples = len(outcomes)
    if samples < min_samples:
        return {
            "verdict": "insufficient-data",
            "pass_rate": None,
            "samples": samples,
            "dominant": None,
        }
    passes = 0
    for outcome in outcomes:
        if not isinstance(outcome, bool):
            raise TypeError("outcomes must be booleans")
        passes += 1 if outcome else 0
    rate = round(passes / samples, 3)
    if rate == 1.0:
        return {"verdict": "stable-pass", "pass_rate": rate, "samples": samples, "dominant": "pass"}
    if rate == 0.0:
        return {"verdict": "stable-fail", "pass_rate": rate, "samples": samples, "dominant": "fail"}
    return {
        "verdict": "flaky",
        "pass_rate": rate,
        "samples": samples,
        "dominant": "pass" if rate >= flake_threshold else "fail",
    }


def quarantine(gates, min_samples: int = 3, flake_threshold: float = 0.5):
    """Ids of the flaky gates, sorted. Insufficient data is not flaky."""

    flaky = [
        gate_id
        for gate_id, outcomes in gates.items()
        if classify(outcomes, min_samples, flake_threshold)["verdict"] == "flaky"
    ]
    return sorted(flaky)
