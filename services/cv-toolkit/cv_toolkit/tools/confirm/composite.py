"""Composite tool - multi-tool aggregator."""

from __future__ import annotations

import numpy as np

from cv_toolkit.models import CompositeInput, ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("composite")
def composite(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Run multiple tools on the same frame and aggregate results.

    Aggregate modes:
        "all"      — all must match (AND). Score = min of all scores.
        "any"      — at least one must match (OR). Score = max of all scores.
        "majority" — >50% must match. Score = mean of all scores.
        "weighted" — weighted average of scores. Match = score >= threshold.

    Params (required):
        composite (dict): A CompositeInput dict with "steps" and "aggregate".
    """
    from cv_toolkit.registry import run_tool  # Local import to avoid circular

    raw = tool_input.params.get("composite")
    if raw is None:
        raise ValueError("composite tool requires params['composite'] with steps and aggregate")

    comp = CompositeInput.model_validate(raw)

    sub_results: list[ToolResult] = []
    for step in comp.steps:
        step_input = ToolInput(
            tool=step.tool,
            threshold=step.threshold,
            region=step.region,
            params=step.params,
        )
        result = run_tool(image, step_input, reference)
        sub_results.append(result)

    scores = [r.score for r in sub_results]
    matches = [r.match for r in sub_results]

    if comp.aggregate == "all":
        agg_score = min(scores) if scores else 0.0
        agg_match = all(matches)
    elif comp.aggregate == "any":
        agg_score = max(scores) if scores else 0.0
        agg_match = any(matches)
    elif comp.aggregate == "majority":
        agg_score = float(np.mean(scores)) if scores else 0.0
        agg_match = sum(matches) > len(matches) / 2
    elif comp.aggregate == "weighted":
        weights = [step.weight for step in comp.steps]
        total_weight = sum(weights)
        if total_weight > 0:
            agg_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        else:
            agg_score = 0.0
        agg_match = agg_score >= tool_input.threshold
    else:
        agg_score = 0.0
        agg_match = False

    return ToolResult(
        tool="composite",
        score=agg_score,
        match=agg_match,
        threshold=tool_input.threshold,
        details={
            "aggregate": comp.aggregate,
            "individual_scores": scores,
            "individual_matches": matches,
            "sub_results": [r.model_dump() for r in sub_results],
        },
    )
