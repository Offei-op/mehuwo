"""Exact Bayesian inference under a fixed-parameter DINA response model.

P(correct | profile) = 1-slip if every Q-matrix skill is mastered,
otherwise guess. Independent Bernoulli skill priors; no hard prerequisite
constraints. Parameters are assumptions or supplied values, not fitted here.
See docs/diagnostics.md for interpretation and research limitations.
"""
from __future__ import annotations

import numpy as np


def binary_entropy(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1 - 1e-15)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


class DinaModel:
    def __init__(self, bank: dict, *, slip: float = 0.1,
                 guess: float = 0.25, prior: float = 0.5):
        self.skills = bank["meta"]["node_order"]
        self.items = bank["single_skill_items"] + bank["multi_skill_items"]
        k = len(self.skills)
        if not 1 <= k <= 16 or len(set(self.skills)) != k:
            raise ValueError("DINA requires 1-16 unique skills; split larger units.")
        if not self.items or len(self.items) * 2 ** k > 8_000_000:
            raise ValueError("Empty bank or exact inference budget exceeded; split the unit.")
        if not all(np.isfinite(v) and 0 < v < 1 for v in (slip, guess, prior)):
            raise ValueError("Slip, guess, and prior must be finite and strictly between 0 and 1.")
        self.q = np.asarray([it.get("q_vector", []) for it in self.items])
        if (self.q.shape != (len(self.items), k)
                or not np.isin(self.q, [0, 1]).all()
                or not self.q.any(axis=1).all()):
            raise ValueError("Every item needs a nonempty binary q_vector aligned with node_order.")
        self.q = self.q.astype(int)
        ids = [it.get("item_id") for it in self.items]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Every item needs a unique nonempty item_id.")
        self.parameters = []
        for it in self.items:
            params = it.get("diagnostic_parameters", {})
            s, g = float(params.get("slip", slip)), float(params.get("guess", guess))
            if not (np.isfinite(s) and np.isfinite(g) and 0 < s < 1 and 0 < g < 1 and s + g < 1):
                raise ValueError(f"{it['item_id']}: require 0 < slip, guess < 1 and slip + guess < 1.")
            self.parameters.append({"item_id": it["item_id"], "slip": s, "guess": g,
                                    "q_vector": ",".join(map(str, it["q_vector"])),
                                    "source": "supplied" if params else "assumed"})
        self.profiles = ((np.arange(2 ** k)[:, None] >> np.arange(k)) & 1).astype(float)
        eta = self.profiles @ self.q.T == self.q.sum(axis=1)
        slips = np.array([p["slip"] for p in self.parameters])
        guesses = np.array([p["guess"] for p in self.parameters])
        self.correct = np.where(eta, 1 - slips, guesses)
        self.log_prior = (self.profiles * np.log(prior)
                          + (1 - self.profiles) * np.log1p(-prior)).sum(axis=1)

    def infer(self, responses) -> dict:
        responses = np.asarray(responses, dtype=float)
        if responses.shape != (len(self.items),):
            raise ValueError("Response count does not match the bank.")
        observed = ~np.isnan(responses)
        if not np.isin(responses[observed], [0, 1]).all():
            raise ValueError("Responses must be 0, 1, or missing (NaN).")
        p = self.correct[:, observed]
        r = responses[observed]
        log_posterior = self.log_prior + (np.log(p) * r + np.log1p(-p) * (1 - r)).sum(axis=1)
        weights = np.exp(log_posterior - log_posterior.max())
        weights /= weights.sum()
        probability = weights @ self.profiles
        evidence = self.q[observed].sum(axis=0)
        # An unmeasured skill must not masquerade as a measured prior of 0.5.
        probability[evidence == 0] = np.nan
        return {"probability": probability, "entropy": binary_entropy(probability),
                "evidence": evidence, "posterior": weights, "observed": observed}

    def next_items(self, inference: dict, limit: int = 3) -> list[dict]:
        """Rank unobserved bank items by expected information gain in bits.

        Blank responses may also mean omitted/not-reached questions: these
        recommendations require teacher review before administration.
        """
        weights = inference["posterior"]
        missing = np.flatnonzero(~inference["observed"])
        if not len(missing):
            return []
        p = self.correct[:, missing]
        gains = binary_entropy(weights @ p) - weights @ binary_entropy(p)
        ranked = sorted(zip(missing, gains), key=lambda x: (-x[1], x[0]))[:limit]
        return [{"item_id": self.items[i]["item_id"], "item_column": f"item_{i+1}",
                 "expected_information_gain_bits": max(0.0, float(g)),
                 "skills": ", ".join(s for s, q in zip(self.skills, self.q[i]) if q)}
                for i, g in ranked]


def mastery_status(probability: float, evidence: int, low: float = 0.2,
                   high: float = 0.8) -> str:
    if not evidence or not np.isfinite(probability):
        return "insufficient_evidence"
    if probability >= high:
        return "likely_mastered"
    if probability <= low:
        return "likely_not_mastered"
    return "uncertain"
