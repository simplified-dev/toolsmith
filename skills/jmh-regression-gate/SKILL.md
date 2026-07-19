---
name: jmh-regression-gate
description: Compare a JMH benchmark run against a baseline and halt when any benchmark regresses beyond a configurable threshold. Auto-invoked when a plan or session has captured paired JMH outputs (typically `baseline-*.txt` / `final-*.txt` from `./gradlew jmh > file.txt`) and needs a go / no-go signal before advancing. Threshold defaults to 2% regression; configurable via `--threshold`. Reads gradle-stdout JMH summary tables and JMH `--rf json` output; never re-runs the benchmarks. Halt is advisory - the gate exits non-zero and surfaces the worst regressions, but the user decides whether to bisect or accept.
auto_invoke: true
tags: [java, jmh, benchmark, regression, verification]
---

# jmh-regression-gate

Compare two JMH runs and halt on regressions past a threshold. Mirrors the
`gradle-verify-gate` shape: skill describes routing, helper script does the
mechanical bulk.

## When to invoke

- A plan has captured paired JMH outputs (e.g. `baseline-jmh.txt` +
  `phase3-jmh.txt`) and the next phase is gated on "no regression > X%".
- User asks "compare these JMH runs", "did anything regress vs baseline",
  "check the perf delta".
- After `./gradlew jmh > file.txt` completes and the session already has a
  baseline file to compare against.
- A `Phase N -> verify` boundary in a multi-phase plan declares a JMH
  regression bound (e.g. "Halt advancement if regression > 2%" - this exact
  phrasing appears in `concurrent-perf-optimization.md`).

Do NOT invoke for the initial baseline capture - there is nothing to compare
yet. Skip when only one JMH file exists.

## What this gate does

- Parses two JMH text or JSON outputs.
- Matches benchmarks 1-to-1 by `(Benchmark name, Mode, Params)` tuple.
- Computes per-benchmark percentage delta: `(candidate - baseline) / baseline * 100`.
- Categorizes: `regression` (delta < -threshold), `wash` (within +/-threshold),
  `improvement` (delta > +threshold).
- Halts (exits non-zero) when any regression exceeds the threshold.
- Surfaces the worst N regressions cleanly; does not dump every row.

## Standard invocation

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/jmh-regression-gate/compare.py" BASELINE CANDIDATE
```

`BASELINE` and `CANDIDATE` may be:

- Gradle-redirect text files: `./gradlew jmh ... > baseline-jmh.txt 2>&1`
  (the format used across `concurrent-perf-*.md` plans).
- JMH JSON results: `./gradlew jmh -Pjmh.resultFormat=JSON -Pjmh.resultsFile=results.json`.
  Auto-detected from `.json` extension or content sniff.

Common flags:

- `--threshold N` - regression bound in percent. Default `2.0`. Plans that
  declare a stricter bound override here.
- `--top N` - how many worst regressions to print. Default `10`.
- `--ignore PATTERN` - regex on `Benchmark` name to exclude (repeatable).
  Use for known-noisy benchmarks; document the reason inline in the plan.
- `--format text|json|md` - output format. `md` produces the same shape as
  `concurrent-perf-hardened-results.md` (Benchmark | Size | Baseline | Final
  | Delta % table). Default `text`.
- `--require-pairs` - exit non-zero if a benchmark exists in one file but not
  the other. Default off (warn only); turn on when you want a strict 1-to-1
  guarantee.

## Decision rules

| Situation | Behavior |
|---|---|
| All deltas within +/-threshold | Exit 0. Print summary count. |
| Any benchmark regressed past threshold | Exit 1. Print worst N regressions. |
| Benchmarks present in only one file | Warn (exit 0) unless `--require-pairs`. |
| Either file unparseable | Exit 2. Print first parse error + line. |
| Both files empty | Exit 2. Print "no benchmarks parsed". |

## Failure reporting

On regression exit, surface:

- Top N regressions ranked by delta magnitude.
- Per row: `Benchmark | Params | Baseline (score +/- err) | Candidate | Delta%`.
- Footer: total benchmarks compared, regression count, improvement count,
  wash count.

Direct the user to re-run with `--format md` if they want the full diff for a
plan write-up.

## Skip when

- The user explicitly says "skip the regression check" or "I already
  reviewed the deltas".
- Only one JMH file is available (no comparison to make).
- The candidate run completed but the baseline is older than the last
  benchmark-relevant code change - flag that the baseline is stale rather
  than producing a misleading green.

## Why not just diff the text files

JMH output rows reorder run-to-run, include warmup noise lines, and embed
fork timestamps. A naive `diff` produces false positives on every row. The
script normalizes by (Benchmark, Mode, Params) tuple and ignores ordering.

## Output stability

- Text and markdown outputs are deterministic given the same inputs.
- Regression ranking uses delta magnitude; ties break on benchmark name
  alphabetic so output is reproducible.

## Cross-reference

`gradle-verify-gate` covers `compileJava` + `test`. This gate is the
benchmark-equivalent for JMH-heavy refactors (see `concurrent-perf-*.md`
plans). The two are complementary - run `gradle-verify-gate` first to
confirm the code compiles and unit-tests pass, then `jmh-regression-gate` to
confirm the change did not regress benchmark scores.

## Invariants

- The script never runs gradle, never invokes JMH, never modifies the input
  files.
- The script does not require any non-stdlib Python dependency.
- Exit codes are stable: 0 = pass, 1 = regression, 2 = parse failure.
