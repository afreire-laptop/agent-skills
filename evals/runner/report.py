"""report.md and report.json: statistics per condition, condition pair and trigger set, the
sections that show them, and the release gates' verdict."""

from __future__ import annotations

import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TypeVar

from .checks import SENSITIVE_CHECKS
from .gates import gates, missing_results
from .models import (
    CONDITIONS, DIMENSIONS, CheckRow, ConditionStats, GenerationMeta, GroupCounts, HarnessStats, Judged, PairStats,
    ReportParams, ResponseGrade, StoredJudgement, TriggerRun, TriggerStats,
)

PAIRS = (("C", "B"), ("C", "A"), ("B", "A"))

K = TypeVar("K")
V = TypeVar("V")


def grouped(pairs: Iterable[tuple[K, V]]) -> dict[K, list[V]]:
    """Values grouped by key, keys in order of first appearance."""
    groups: dict[K, list[V]] = {}
    for key, value in pairs:
        groups.setdefault(key, []).append(value)
    return groups


def mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def stdev(values: Sequence[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else None


def fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.0f}%" if pct else f"{value:,.0f}"


def fmt_mean_sd(values: Sequence[float]) -> str:
    m, s = mean(values), stdev(values)
    return fmt(m) if s is None else f"{fmt(m)} ± {s * 100:.0f}"


def pass_rate(rows: Sequence[CheckRow]) -> float:
    return sum(r.passed for r in rows) / len(rows)


def ranks_of(judgement: StoredJudgement, first: str, second: str) -> list[tuple[int, int]] | None:
    """(rank of first, rank of second) per dimension; None if the judgement lacks either."""
    label_of = {cond: label for label, cond in judgement.mapping.items()}
    if first not in label_of or second not in label_of:
        return None
    return [(ranks[label_of[first]], ranks[label_of[second]])
            for ranks in ({r.label: r.rank for r in dim.ranks} for dim in judgement.result.dimensions)]


def group_verdict(judgement: StoredJudgement, first: str, second: str) -> int | None:
    """One verdict per judged group: +1 if `first` beats `second` on more dimensions than
    it loses, -1 for the reverse, 0 otherwise. Dimensions from one call are correlated,
    so they are not counted as separate votes."""
    ranks = ranks_of(judgement, first, second)
    if ranks is None:
        return None
    wins, losses = sum(a < b for a, b in ranks), sum(a > b for a, b in ranks)
    return (wins > losses) - (wins < losses)


def dimension_counts(judgements: Sequence[Judged], first: str, second: str) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {d: [0, 0, 0] for d in DIMENSIONS}
    for j in judgements:
        dims = j.judgement.result.dimensions
        for dim, (a, b) in zip(dims, ranks_of(j.judgement, first, second) or []):
            counts[dim.dimension][0 if a < b else 1 if a == b else 2] += 1
    return counts


def bootstrap_ci(per_case: Mapping[str, Sequence[int]], seed: str, rounds: int = 2000) -> tuple[float, float, float]:
    """Mean per-case preference (-1..1) with a 95% bootstrap interval, resampling cases."""
    case_means = [statistics.fmean(v) for v in per_case.values()]
    rng = random.Random(seed)
    stats = sorted(statistics.fmean(rng.choice(case_means) for _ in case_means) for _ in range(rounds))
    return statistics.fmean(case_means), stats[int(0.025 * rounds)], stats[int(0.975 * rounds) - 1]


def writer_line(metas: Sequence[GenerationMeta], judgements: Sequence[Judged]) -> str:
    models = sorted({m.model or "default" for m in metas})
    judges = sorted({f"{j.judgement.judge} ({j.judgement.judge_model or 'default'})" for j in judgements})
    return (f"Writer model: {', '.join(models) or '–'}. Judges: {', '.join(judges) or 'none'}. "
            f"Failed generations: {sum(not m.ok for m in metas)}.")


def response_for(judged: Judged, condition: str) -> ResponseGrade | None:
    label = next((l for l, c in judged.judgement.mapping.items() if c == condition), None)
    return next((r for r in judged.judgement.result.responses if r.label == label), None)


def condition_stats(cond: str, checks: Sequence[CheckRow], judgements: Sequence[Judged],
                    metas: Sequence[GenerationMeta]) -> tuple[ConditionStats, list[float]]:
    """A condition's row, and its per-trial check pass rates."""
    c_checks = [c for c in checks if c.condition == cond]
    per_trial = [pass_rate([c for c in c_checks if c.trial == trial]) for trial in sorted({c.trial for c in c_checks})]
    responses = [r for r in (response_for(j, cond) for j in judgements) if r is not None]
    verdicts = [e.verdict for r in responses for e in r.expectations]
    known = [v for v in verdicts if v != "unknown"]
    c_metas = [m for m in metas if m.condition == cond and m.ok]
    return ConditionStats(
        check_pass_rate=mean(per_trial), check_pass_sd=stdev(per_trial),
        expectation_pass_rate=(sum(v == "pass" for v in known) / len(known)) if known else None,
        expectation_unknown=verdicts.count("unknown"),
        fidelity_pass_rate=mean([float(r.fidelity == "pass") for r in responses]),
        sensitive_leaks=sum(1 for c in c_checks if c.check in SENSITIVE_CHECKS and not c.passed),
        mean_input_tokens=mean([m.usage.get("input_tokens", 0) or 0 for m in c_metas]),
        mean_output_words=mean([m.output_words for m in c_metas]),
    ), per_trial


def condition_table(conditions: Sequence[str], checks: Sequence[CheckRow], judgements: Sequence[Judged],
                    metas: Sequence[GenerationMeta]) -> tuple[list[str], dict[str, ConditionStats]]:
    lines = ["| Condition | Checks passed (mean ± sd over trials) | Expectations passed (judges) "
             "| Fidelity passed (judges) | Sensitive leaks | Input tokens | Output words |",
             "|---|---|---|---|---|---|---|"]
    stats: dict[str, ConditionStats] = {}
    for cond in conditions:
        row, per_trial = condition_stats(cond, checks, judgements, metas)
        stats[cond] = row
        lines.append(f"| {cond} ({CONDITIONS[cond]}) | {fmt_mean_sd(per_trial)} "
                     f"| {fmt(row.expectation_pass_rate)} | {fmt(row.fidelity_pass_rate)} "
                     f"| {row.sensitive_leaks} | {fmt(row.mean_input_tokens, False)} "
                     f"| {fmt(row.mean_output_words, False)} |")
    return lines + [""], stats


def guidance_overhead(stats: Mapping[str, ConditionStats]) -> dict[str, float]:
    """Measured input tokens of B and C over A."""
    base = stats["A"].mean_input_tokens if "A" in stats else None
    tokens = {cond: stats[cond].mean_input_tokens for cond in ("B", "C") if cond in stats}
    return {cond: t - base for cond, t in tokens.items() if base and t}


def case_check_table(cases: Sequence[str], conditions: Sequence[str], checks: Sequence[CheckRow]) -> list[str]:
    def cell(case: str, cond: str) -> str:
        rows = [c for c in checks if c.case == case and c.condition == cond]
        return fmt(pass_rate(rows)) if rows else "–"

    def failing(case: str) -> str:
        return ", ".join(sorted({c.check for c in checks if c.case == case and c.condition == "C" and not c.passed}))

    return (["| Case | " + " | ".join(f"{c} checks" for c in conditions) + " | Failing checks under C |",
             "|---|" + "---|" * len(conditions) + "---|"]
            + [f"| {case} | " + " | ".join(cell(case, cond) for cond in conditions) + f" | {failing(case) or '–'} |"
               for case in cases]
            + [""])


def pair_section(run_id: str, harness: str, first: str, second: str,
                 judgements: Sequence[Judged]) -> tuple[list[str], PairStats | None]:
    judges = sorted({j.judgement.judge for j in judgements})
    verdicts = {judge: [(j.case, v) for j in judgements if j.judgement.judge == judge
                        if (v := group_verdict(j.judgement, first, second)) is not None]
                for judge in judges}
    tallies = {judge: [sum(v == want for _, v in found) for want in (1, 0, -1)] for judge, found in verdicts.items()}
    pooled = [sum(t[i] for t in tallies.values()) for i in range(3)]
    per_case = grouped(pair for judge in judges for pair in verdicts[judge])
    if not per_case:
        return [], None
    preferred = tuple(sorted(k for k, v in per_case.items() if sum(v) > 0))
    point, low, high = bootstrap_ci(per_case, f"{run_id}:{harness}:{first}{second}")
    stats = PairStats(groups=GroupCounts(first=pooled[0], tie=pooled[1], second=pooled[2]),
                      cases_preferred=preferred, net_by_case={k: sum(v) for k, v in per_case.items()},
                      mean_case_preference=point, ci95=(low, high))
    lines = [f"Judge verdicts, {first} vs {second}, one per judged group:", "",
             f"| Judge | {first} preferred | Tie | {second} preferred |", "|---|---|---|---|",
             *[f"| {judge} | {t[0]} | {t[1]} | {t[2]} |" for judge, t in tallies.items()],
             f"| pooled | {pooled[0]} | {pooled[1]} | {pooled[2]} |", "",
             f"{first} net-preferred on {len(preferred)} of {len(per_case)} cases"
             + (f" ({', '.join(preferred)})" if preferred else "")
             + f". Mean per-case preference {point:+.2f} on a −1 to +1 scale, "
             f"95% bootstrap interval {low:+.2f} to {high:+.2f}.", ""]
    if (first, second) == ("C", "B"):
        dims = dimension_counts(judgements, "C", "B")
        lines += ["C vs B by dimension (descriptive; dimensions from one call are correlated):", "",
                  "| Dimension | C better | Tie | B better |", "|---|---|---|---|",
                  *[f"| {d} | {w} | {t} | {l} |" for d, (w, t, l) in dims.items()], ""]
    return lines, stats


def position_audit(judgements: Sequence[Judged], n_conditions: int) -> list[str]:
    def firsts(judge: str) -> list[float]:
        return [float(min(r.rank for r in d.ranks) == next(r.rank for r in d.ranks if r.label == "R1"))
                for j in judgements if j.judgement.judge == judge for d in j.judgement.result.dimensions]

    return [f"Position audit, {judge}: first-shown response ranked best (ties included) in "
            f"{fmt(mean(firsts(judge)))} of dimension judgements; at least {fmt(1 / n_conditions)} "
            f"is expected without bias. With few groups this is confounded with which condition "
            f"happened to be shown first."
            for judge in sorted({j.judgement.judge for j in judgements})] + [""]


def trigger_section(runs: Sequence[TriggerRun]) -> tuple[list[str], TriggerStats]:
    pos = [float(t.triggered) for t in runs if t.should_trigger]
    neg = [float(t.triggered) for t in runs if not t.should_trigger]
    undiscovered = sum(1 for t in runs if t.discovered is False)
    stats = TriggerStats(recall=mean(pos), false_positive_rate=mean(neg), runs=len(runs),
                         undiscovered_runs=undiscovered)

    def query_row(index: int) -> str:
        found = [t for t in runs if t.index == index]
        rate = statistics.fmean(float(t.triggered) for t in found)
        flag = "" if (rate >= 0.5) == found[0].should_trigger else " ⚠"
        query = found[0].query[:90] + ("…" if len(found[0].query) > 90 else "")
        return f"| {index} | {'yes' if found[0].should_trigger else 'no'} | {fmt(rate)}{flag} | {query} |"

    lines = [f"Triggering: recall {fmt(mean(pos))} on should-trigger queries, false-positive rate "
             f"{fmt(mean(neg))} on near misses ({len(runs)} runs"
             + (f"; skill not discovered in {undiscovered} runs" if undiscovered else "") + ").", "",
             "| # | Expected | Trigger rate | Query |", "|---|---|---|---|",
             *[query_row(index) for index in sorted({t.index for t in runs})], ""]
    return lines, stats


def harness_section(params: ReportParams, harness: str, cases: Sequence[str], conditions: Sequence[str],
                    metas: Sequence[GenerationMeta], checks: Sequence[CheckRow], judgements: Sequence[Judged],
                    triggers: Sequence[TriggerRun]) -> tuple[list[str], HarnessStats]:
    h_metas = [m for m in metas if m.harness == harness]
    h_checks = [c for c in checks if c.harness == harness]
    h_judge = [j for j in judgements if j.harness == harness]
    h_trig = [t for t in triggers if t.harness == harness]
    table, stats = condition_table(conditions, h_checks, h_judge, h_metas)
    overhead = guidance_overhead(stats)
    pairs = {f"{first}_vs_{second}": pair_section(params.run_id, harness, first, second, h_judge)
             for first, second in PAIRS}
    trig_lines, trig_stats = trigger_section(h_trig) if h_trig else ([], None)
    lines = [f"## {harness}", "", writer_line(h_metas, h_judge), "", *table,
             *[f"Measured guidance overhead, {cond} vs A: {tokens:,.0f} input tokens per call."
               for cond, tokens in overhead.items()], "",
             *(case_check_table(cases, conditions, h_checks) if h_checks else []),
             *[line for pair_lines, _ in pairs.values() for line in pair_lines],
             *position_audit(h_judge, len(conditions)),
             *trig_lines]
    return lines, HarnessStats(conditions=stats, overhead=overhead, triggers=trig_stats,
                               pairs={name: s for name, (_, s) in pairs.items() if s is not None})


def harness_json(stats: HarnessStats) -> dict[str, object]:
    """report.json's entry for one writer, in the order earlier reports used."""
    conditions = {cond: {**row.model_dump(), **({"guidance_overhead_tokens": stats.overhead[cond]}
                                                if cond in stats.overhead else {})}
                  for cond, row in stats.conditions.items()}
    return {"conditions": conditions, **{name: pair.model_dump() for name, pair in stats.pairs.items()},
            **({"triggers": stats.triggers.model_dump()} if stats.triggers else {})}


def report(params: ReportParams, cases: Sequence[str], metas: Sequence[GenerationMeta], checks: Sequence[CheckRow],
           judgements: Sequence[Judged], triggers: Sequence[TriggerRun], n_queries: int,
           manifest: Path) -> tuple[list[str], dict[str, object]]:
    """report.md's lines and report.json's content. `manifest` is the run's manifest path, as shown."""
    conditions = [c for c in CONDITIONS if c in params.conditions]
    lines = [f"# Eval report: {params.run_id}", "",
             f"Manifest: `{manifest}`. Expected: {len(cases)} cases × "
             f"{len(conditions)} conditions × {params.trials} trials per harness ({', '.join(params.harnesses)}), "
             f"judges: {params.judge_harness}, {params.trigger_runs} trigger runs per query.", ""]
    stats: dict[str, HarnessStats] = {}
    for harness in params.harnesses:
        section, stats[harness] = harness_section(params, harness, cases, conditions, metas, checks, judgements, triggers)
        lines += section
    missing = missing_results(
        params, cases,
        generated={(m.harness, m.case, m.condition, m.trial) for m in metas if m.ok},
        checked={(c.harness, c.case, c.condition, c.trial) for c in checks},
        judged={(j.harness, j.case, j.trial, j.judge): set(j.judgement.mapping.values()) for j in judgements},
        trigger_runs={(t.harness, t.index, t.attempt) for t in triggers},
        n_queries=n_queries)
    gate_lines, results, verdict = gates(stats, len(cases), params.harnesses, missing)
    summary: dict[str, object] = {"run_id": params.run_id, "harnesses": {h: harness_json(s) for h, s in stats.items()},
                                  "gates": results, "missing": missing, "verdict": verdict}
    return lines + gate_lines, summary
