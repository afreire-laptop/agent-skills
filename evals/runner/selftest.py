"""Self-checks of the graders and the release gates. Each prints a line per check and returns its
number of failures."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from .checks import run_check
from .gates import gates, missing_results
from .models import (
    DIMENSIONS, HARNESSES, Case, ConditionStats, GroupCounts, HarnessStats, JudgeResult, PairStats, ReportParams,
    TriggerStats, first_error,
)
from .prompts import judge_schema


def reference_line(case: Case, reference: str, raw: str, input_words: int) -> tuple[bool, str]:
    """Whether the reference passes every check and the raw input fails at least one."""
    ref_failures = [c.id for c in case.checks if not run_check(c, reference, input_words)[0]]
    raw_failures = [c.id for c in case.checks if not run_check(c, raw, input_words)[0]]
    ok = not ref_failures and bool(raw_failures)
    return ok, (f"{'ok  ' if ok else 'FAIL'} {case.name}: reference fails {ref_failures or 'none'}; "
                f"raw input fails {len(raw_failures)}/{len(case.checks)} checks")


def selftest_gates() -> int:
    """The release gates must fail closed on partial runs and decide correctly on full ones."""
    def harness(c_preferred: int, b_preferred: int, cases_preferred: int) -> HarnessStats:
        cond = ConditionStats(check_pass_rate=0.97, check_pass_sd=None, expectation_pass_rate=None,
                              expectation_unknown=0, fidelity_pass_rate=0.9, sensitive_leaks=0,
                              mean_input_tokens=None, mean_output_words=None)
        pair = PairStats(groups=GroupCounts(first=c_preferred, tie=0, second=b_preferred),
                         cases_preferred=tuple(f"case{i}" for i in range(cases_preferred)),
                         net_by_case={}, mean_case_preference=0.0, ci95=(0.0, 0.0))
        return HarnessStats(conditions={"B": cond, "C": cond}, pairs={"C_vs_B": pair},
                            triggers=TriggerStats(recall=0.9, false_positive_rate=0.1, runs=0, undiscovered_runs=0))

    full = {"claude": harness(30, 10, 5), "codex": harness(25, 15, 4)}
    scenarios: list[tuple[str, dict[str, HarnessStats], list[str], str]] = [
        ("empty run", {}, [], "INCOMPLETE"),
        ("one harness missing", {"claude": full["claude"]}, [], "INCOMPLETE"),
        ("no judge results", {**full, "codex": full["codex"].model_copy(update={"pairs": {}})}, [], "INCOMPLETE"),
        ("missing generations", full, ["generation codex/x/C/t3"], "INCOMPLETE"),
        ("complete and passing", full, [], "SHIP"),
        ("short rule as good", {**full, "codex": harness(10, 12, 2)}, [], "SIMPLIFY"),
    ]
    failures = 0
    for name, stats, missing, expected in scenarios:
        _, _, verdict = gates(stats, 7, HARNESSES, missing)
        ok = verdict.startswith(expected)
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} gates, {name}: {verdict}")
    return failures


def selftest_partial_judgement(case: str, n_queries: int) -> int:
    """A group judged while one output was missing must count as missing until it is redone."""
    params = ReportParams(run_id="selftest", harnesses=("claude",), conditions=("A", "B", "C"), trials=1,
                          judge_harness="claude", trigger_runs=0)
    complete = {("claude", case, c, 1) for c in params.conditions}
    failures = 0
    for mapping, expect_missing in (({"R1": "B", "R2": "C"}, True),
                                    ({"R1": "B", "R2": "C", "R3": "A"}, False)):
        judged = {("claude", case, "t1", "claude"): set(mapping.values())}
        missing = missing_results(params, [case], complete, complete, judged, set(), n_queries)
        ok = bool(missing) == expect_missing
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} judgement over {','.join(sorted(mapping.values()))}: "
              f"{'counted missing' if missing else 'complete'}")
    return failures


def schema_sample(schema: Mapping[str, object], k: int) -> object:
    """A value that `schema` allows, taking the k-th value of every enum (cycling)."""
    enum, kind = schema.get("enum"), schema.get("type")
    if isinstance(enum, list):
        return enum[k % len(enum)]
    properties, items = schema.get("properties"), schema.get("items")
    if kind == "object" and isinstance(properties, dict):
        return {name: schema_sample(sub, k) for name, sub in properties.items()}
    if kind == "array" and isinstance(items, dict):
        return [schema_sample(items, k)]
    return {"string": "x", "integer": 1}[str(kind)]


def selftest_judge_schema() -> int:
    """Every output the judge schema allows must validate as a JudgeResult, or valid
    judgements would be rejected. The schema is sent to the judges and must not change."""
    errors = []
    for k in range(len(DIMENSIONS)):
        try:
            JudgeResult.model_validate(schema_sample(judge_schema(), k))
        except ValidationError as exc:
            errors.append(first_error(exc))
    print(f"{'ok  ' if not errors else 'FAIL'} judge schema and JudgeResult model agree"
          + (f": {errors[0]}" if errors else ""))
    return len(errors)
