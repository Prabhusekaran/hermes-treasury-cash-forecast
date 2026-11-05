"""Model-independent quantile scores; all monetary scores use target units."""
import math
from numbers import Real


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def pinball_loss(actual, predicted, quantile):
    """Mean quantile loss: max(q*(y-f), (q-1)*(y-f))."""
    if not _finite(quantile) or not 0 < quantile < 1:
        raise ValueError("quantile must be between zero and one")
    if not actual or len(actual) != len(predicted):
        raise ValueError("nonempty equal-length inputs required")
    if not all(_finite(v) for v in [*actual, *predicted]):
        raise ValueError("inputs must be finite numeric values")
    return sum(max(quantile*(a-p), (quantile-1)*(a-p))
               for a, p in zip(actual, predicted))/len(actual)


def interval_metrics(actual, lower, upper):
    """Inclusive coverage, width and proper central (1-alpha) interval score."""
    alpha = 0.2
    pinball_loss(actual, lower, alpha/2)
    pinball_loss(actual, upper, 1-alpha/2)
    if any(l > u for l, u in zip(lower, upper)):
        raise ValueError("interval bounds cross")
    n = len(actual)
    return {
        "coverage_80": sum(l <= a <= u for a, l, u in zip(actual, lower, upper))/n,
        "interval_width_80": sum(u-l for l, u in zip(lower, upper))/n,
        "interval_score_80": sum(u-l + 2/alpha*max(l-a, 0)
                                 + 2/alpha*max(a-u, 0)
                                 for a, l, u in zip(actual, lower, upper))/n,
    }


def probabilistic_summary(rows):
    """Pool observations, never fold averages or fabricated missing quantiles.

    Recurring quantiles do not describe unknown one-off uncertainty. Total-flow
    scores therefore apply only to realized zero-one-off months (a retrospective
    conditional diagnostic, not unconditional total-flow calibration).
    """
    eligible = [r for r in rows if r.get("target_name") != "total_net_cash_flow"
                or r.get("actual_one_off") == 0]
    for r in rows:
        q = r.get("quantiles", {})
        if any(not _finite(k) or not 0 < k < 1 or not _finite(v)
               for k, v in q.items()):
            raise ValueError("quantiles must be finite with valid levels")
        values = [q[k] for k in sorted(q)]
        if any(a > b for a, b in zip(values, values[1:])):
            raise ValueError("quantiles cross")
    result = {"probabilistic_eligible_n": len(eligible)}
    for level, label in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90")):
        available = [r for r in eligible if level in r.get("quantiles", {})]
        result["pinball_"+label] = (pinball_loss(
            [r["actual"] for r in available],
            [r["quantiles"][level] for r in available], level) if available else None)
        result["pinball_"+label+"_n"] = len(available)
    intervals = [r for r in eligible if all(k in r.get("quantiles", {}) for k in (0.1, 0.9))]
    result.update(dict.fromkeys(("coverage_80", "interval_width_80", "interval_score_80")))
    result["interval_80_n"] = len(intervals)
    if intervals:
        result.update(interval_metrics([r["actual"] for r in intervals],
            [r["quantiles"][0.1] for r in intervals],
            [r["quantiles"][0.9] for r in intervals]))
    return result
