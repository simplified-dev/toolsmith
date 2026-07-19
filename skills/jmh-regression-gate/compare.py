#!/usr/bin/env python3
"""
Compare two JMH outputs and flag benchmarks that regressed beyond a threshold.

Reads:
  - Gradle stdout text (e.g. ``./gradlew jmh > out.txt 2>&1``) - JMH summary table.
  - JMH JSON results (``-Pjmh.resultFormat=JSON`` / ``--rf json``).

Pairs benchmarks by ``(benchmark, mode, params)`` so row order drift doesn't matter.
Exit codes:
  0  no regression past threshold
  1  at least one regression past threshold
  2  parse failure / no benchmarks parsed
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# JMH summary table header. Mode column is always present; param columns vary.
# Canonical column order: Benchmark [(params...)] Mode Cnt Score Error Units
HEADER_RE = re.compile(
    r"^Benchmark(?P<params>(?:\s+\([^)]+\))*)\s+Mode\s+Cnt\s+Score\s+Error\s+Units\s*$"
)
PARAM_NAME_RE = re.compile(r"\(([^)]+)\)")


@dataclass(frozen=True)
class Key:
    benchmark: str
    mode: str
    params: tuple[tuple[str, str], ...]  # sorted (name, value) pairs

    def label(self) -> str:
        if not self.params:
            return f"{self.benchmark} [{self.mode}]"
        param_str = ", ".join(f"{k}={v}" for k, v in self.params)
        return f"{self.benchmark} [{self.mode}] ({param_str})"


@dataclass
class Result:
    key: Key
    score: float
    error: float
    unit: str


@dataclass
class Comparison:
    key: Key
    baseline: Result
    candidate: Result

    @property
    def delta_pct(self) -> float:
        if self.baseline.score == 0:
            return float("inf") if self.candidate.score > 0 else 0.0
        return (self.candidate.score - self.baseline.score) / self.baseline.score * 100


@dataclass
class ParseReport:
    results: dict[Key, Result] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def parse_file(path: Path) -> ParseReport:
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.lstrip().startswith("["):
        return _parse_json(text, path)
    return _parse_text(text, path)


def _parse_json(text: str, path: Path) -> ParseReport:
    report = ParseReport()
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as ex:
        report.errors.append(f"{path}: JSON parse failed at line {ex.lineno}: {ex.msg}")
        return report
    if not isinstance(rows, list):
        report.errors.append(f"{path}: expected JSON array, got {type(rows).__name__}")
        return report
    for row in rows:
        try:
            metric = row["primaryMetric"]
            params = row.get("params", {})
            key = Key(
                benchmark=row["benchmark"],
                mode=row["mode"],
                params=tuple(sorted((str(k), str(v)) for k, v in params.items())),
            )
            report.results[key] = Result(
                key=key,
                score=float(metric["score"]),
                error=float(metric.get("scoreError", 0.0) or 0.0),
                unit=metric.get("scoreUnit", ""),
            )
        except (KeyError, TypeError, ValueError) as ex:
            report.errors.append(f"{path}: bad JSON row {row!r}: {ex}")
    return report


def _parse_text(text: str, path: Path) -> ParseReport:
    report = ParseReport()
    lines = text.splitlines()
    header_idx, param_names = _find_header(lines)
    if header_idx is None:
        report.errors.append(f"{path}: no JMH summary header found")
        return report
    for raw in lines[header_idx + 1:]:
        line = raw.rstrip()
        if not line.strip():
            continue
        if HEADER_RE.match(line):
            continue
        result = _parse_text_row(line, param_names)
        if result is None:
            continue
        report.results[result.key] = result
    return report


def _find_header(lines: list[str]) -> tuple[int | None, list[str]]:
    for idx, line in enumerate(lines):
        match = HEADER_RE.match(line)
        if match:
            param_blob = match.group("params") or ""
            param_names = PARAM_NAME_RE.findall(param_blob)
            return idx, param_names
    return None, []


def _parse_text_row(line: str, param_names: list[str]) -> Result | None:
    tokens = line.split()
    expected_units = ("ops/s", "ops/ms", "ops/us", "ops/ns", "ns/op", "us/op",
                      "ms/op", "s/op", "ops/min", "ops/h")
    if not tokens or tokens[-1] not in expected_units:
        return None
    unit = tokens[-1]
    # Layout: benchmark [param-values...] mode cnt score "+/-" error unit
    n_params = len(param_names)
    fixed_tail = 6  # mode cnt score +/- error unit
    if len(tokens) < 1 + n_params + fixed_tail:
        return None
    benchmark = tokens[0]
    params_section = tokens[1: 1 + n_params]
    rest = tokens[1 + n_params:]
    if len(rest) != fixed_tail:
        return None
    mode, cnt_str, score_str, pm, error_str, _unit = rest
    if pm not in ("+/-", "±"):
        return None
    try:
        score = float(score_str)
        error = float(error_str)
        int(cnt_str)
    except ValueError:
        return None
    params = tuple(sorted(zip(param_names, params_section)))
    key = Key(benchmark=benchmark, mode=mode, params=params)
    return Result(key=key, score=score, error=error, unit=unit)


def compare(baseline: ParseReport, candidate: ParseReport) -> tuple[list[Comparison], set[Key], set[Key]]:
    paired: list[Comparison] = []
    for key, b in baseline.results.items():
        c = candidate.results.get(key)
        if c is None:
            continue
        paired.append(Comparison(key=key, baseline=b, candidate=c))
    only_baseline = set(baseline.results) - set(candidate.results)
    only_candidate = set(candidate.results) - set(baseline.results)
    return paired, only_baseline, only_candidate


def render_text(
    paired: list[Comparison],
    threshold: float,
    top: int,
    only_baseline: set[Key],
    only_candidate: set[Key],
) -> str:
    higher_is_better = _infer_direction(paired)
    regressions = [c for c in paired if _is_regression(c, threshold, higher_is_better)]
    improvements = [c for c in paired if _is_improvement(c, threshold, higher_is_better)]
    washes = len(paired) - len(regressions) - len(improvements)

    lines = []
    lines.append(f"Compared {len(paired)} benchmarks (threshold +/-{threshold:.2f}%).")
    direction = "higher is better" if higher_is_better else "lower is better"
    lines.append(f"Direction: {direction} (inferred from units).")
    lines.append(
        f"Regressions: {len(regressions)} | Improvements: {len(improvements)} | "
        f"Within threshold: {washes}"
    )
    if only_baseline:
        lines.append(f"Only in baseline: {len(only_baseline)} (use --require-pairs to fail)")
    if only_candidate:
        lines.append(f"Only in candidate: {len(only_candidate)} (use --require-pairs to fail)")
    if regressions:
        lines.append("")
        lines.append(f"Top {min(top, len(regressions))} regressions (worst first):")
        worst = sorted(regressions, key=lambda c: _regression_magnitude(c, higher_is_better), reverse=True)
        for c in worst[:top]:
            delta = c.delta_pct
            lines.append(
                f"  {delta:+7.2f}%  {c.key.label()}  "
                f"baseline={c.baseline.score:.3f}+/-{c.baseline.error:.3f} {c.baseline.unit}  "
                f"candidate={c.candidate.score:.3f}+/-{c.candidate.error:.3f}"
            )
    return "\n".join(lines)


def render_markdown(paired: list[Comparison], threshold: float, top: int, higher_is_better: bool) -> str:
    regressions = [c for c in paired if _is_regression(c, threshold, higher_is_better)]
    rows = sorted(regressions, key=lambda c: _regression_magnitude(c, higher_is_better), reverse=True)[:top]
    out = ["| Benchmark | Params | Baseline | Candidate | Delta % |", "|---|---|---|---|---|"]
    for c in rows:
        params = ", ".join(f"{k}={v}" for k, v in c.key.params) or "-"
        out.append(
            f"| {c.key.benchmark} [{c.key.mode}] | {params} | "
            f"{c.baseline.score:.3f} +/- {c.baseline.error:.3f} | "
            f"{c.candidate.score:.3f} +/- {c.candidate.error:.3f} | "
            f"{c.delta_pct:+.2f}% |"
        )
    return "\n".join(out)


def render_json(paired: list[Comparison]) -> str:
    return json.dumps(
        [
            {
                "benchmark": c.key.benchmark,
                "mode": c.key.mode,
                "params": dict(c.key.params),
                "baseline": {"score": c.baseline.score, "error": c.baseline.error, "unit": c.baseline.unit},
                "candidate": {"score": c.candidate.score, "error": c.candidate.error, "unit": c.candidate.unit},
                "delta_pct": c.delta_pct,
            }
            for c in paired
        ],
        indent=2,
    )


def _infer_direction(paired: list[Comparison]) -> bool:
    """True if higher scores are better (throughput units)."""
    for c in paired:
        unit = c.baseline.unit.lower()
        if unit.startswith("ops/"):
            return True
        if "/op" in unit:
            return False
    return True


def _is_regression(c: Comparison, threshold: float, higher_is_better: bool) -> bool:
    if higher_is_better:
        return c.delta_pct < -threshold
    return c.delta_pct > threshold


def _is_improvement(c: Comparison, threshold: float, higher_is_better: bool) -> bool:
    if higher_is_better:
        return c.delta_pct > threshold
    return c.delta_pct < -threshold


def _regression_magnitude(c: Comparison, higher_is_better: bool) -> float:
    return -c.delta_pct if higher_is_better else c.delta_pct


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--threshold", type=float, default=2.0,
                        help="Regression bound in percent (default 2.0)")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--ignore", action="append", default=[],
                        help="Regex on benchmark name to exclude (repeatable)")
    parser.add_argument("--format", choices=("text", "md", "json"), default="text")
    parser.add_argument("--require-pairs", action="store_true",
                        help="Exit non-zero if benchmarks aren't 1-to-1 across files")
    args = parser.parse_args(argv)

    baseline = parse_file(args.baseline)
    candidate = parse_file(args.candidate)
    for err in baseline.errors + candidate.errors:
        print(err, file=sys.stderr)
    if not baseline.results or not candidate.results:
        print("no benchmarks parsed", file=sys.stderr)
        return 2

    ignored = [re.compile(p) for p in args.ignore]
    if ignored:
        for store in (baseline.results, candidate.results):
            for key in list(store):
                if any(p.search(key.benchmark) for p in ignored):
                    del store[key]

    paired, only_baseline, only_candidate = compare(baseline, candidate)
    if args.format == "md":
        print(render_markdown(paired, args.threshold, args.top, _infer_direction(paired)))
    elif args.format == "json":
        print(render_json(paired))
    else:
        print(render_text(paired, args.threshold, args.top, only_baseline, only_candidate))

    higher_is_better = _infer_direction(paired)
    has_regression = any(_is_regression(c, args.threshold, higher_is_better) for c in paired)
    pair_mismatch = args.require_pairs and (only_baseline or only_candidate)
    if has_regression or pair_mismatch:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
