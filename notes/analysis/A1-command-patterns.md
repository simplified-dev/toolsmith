# A1 - Command & tool-call token waste (Simplified Java projects)

Analysis of 33,627 recorded shell commands (raw: `bash_commands_raw.txt`) plus the
ranked-dedup and ad-hoc-script distillations. Goal: collapse the highest-volume
reinvented command shapes into one short canonical call each, and quantify the saving.

All counts below are `Grep --output_mode=count` hits over `bash_commands_raw.txt`
unless stated otherwise.

## 0. Headline numbers (evidence)

| Shape (grep over raw 33,627 cmds) | Hits | What it is |
|---|---|---|
| `asset-renderer` appears in a command | 5513 | the dominant module - most work is here |
| `cd `/`&& cd `/`; cd ` prefix | 5913 | per-command re-cd into a module (Bash cwd already persists) |
| `./gradlew (compileJava\|compileTestJava\|test\|build)` | 1537 | gradle verify invocations |
| `PIPESTATUS` | 661 | hand-written `echo "EXIT: ${PIPESTATUS[0]}"` tails |
| `incubating` | 394 | hand-written gradle-noise filter (`grep -vE 'incubating\|warning'`) |
| `console=plain` | 233 | the discord4j-framework variant of the same filter |
| `(cat\|head\|tail) ... .java` | 243 | reading Java source through the shell instead of the Read tool |
| `git status --short` | 563 | status/diff combos (often re-cd'd first) |
| `build/test-results/test/*.xml` | 113 | inline test-XML tally (python/awk), ~44 python variants |
| `md5sum -c .../parity-baseline` | 35 | byte-parity baseline check reinvented inline |
| `jitpack.io/api/builds` | 26 | jitpack build-status poll loops reinvented inline |

Command-verb histogram (from `command_keywords.txt`): `echo` 4821, `grep` 3852
(ripgrep `rg` only 34), `head` 2353, `tail` 1888, `gradlew`+`./gradlew` ~4630,
`sed` 1037, `&&` chains 5036, `|` pipes 5029.

Ad-hoc throwaway-tool authoring events: 869 (`adhoc_scripts.txt`). The single
most-repeated real shape (hundreds of near-identical variants):

```
cd "W:/.../asset-renderer" && ./gradlew compileJava -q 2>&1 \
  | grep -vE "incubating|warning" | tail -N ; echo "EXIT: ${PIPESTATUS[0]}"
```

Every invocation hand-rewrites five volatile knobs: the `cd` prefix, the noise
filter, `tail -N` (observed N in {8,12,15,20,25,30,40}), the `echo EXIT`, and
sometimes a trailing test tally. That is the core waste this analysis targets.

## 1. The gradle compile/test incantation (reinvented ~1500x)

### 1.1 Why it wastes tokens (two channels)

- **Input (command generation).** The full incantation is ~120-220 chars
  (~40-70 output tokens) that Claude *generates* every time. `gw ar test` is
  ~4 tokens. Net ~50 generated tokens saved per gradle call, over ~1537 calls.
- **Output (result readback).** The bigger cost. `tail -40` pulls 40 lines of
  build log into context even on a clean `BUILD SUCCESSFUL` (which is 1 line).
  The dedup file shows the SAME command re-run at different tail depths
  (`tail -8`, `-12`, `-15`, `-20`, `-25`, `-30`, `-40`) because the first depth
  guessed wrong - each re-run re-imports the whole tail. A wrapper that prints a
  fixed compact result (status line + only the error lines) removes the guess
  loop and the over-tail.

### 1.2 Failure modes baked into the current shape

1. **`PIPESTATUS` is fragile and often wrong.** In `... | tail -N; echo ${PIPESTATUS[0]}`
   the `[0]` is the exit of the *first* pipe stage (gradle) - correct - but many
   recorded variants use `${PIPESTATUS[1]}` (the `grep`/`tail` exit, always 0) or
   drop it entirely. 661 hand-written PIPESTATUS echoes, several demonstrably
   reading the wrong index (dedup lines 95, 120 use `[1]`).
2. **Noise filter drift.** At least 8 distinct filter spellings appear:
   `incubating`, `incubating|warning`, `incubating module`, `incubat`,
   `incubating|warning:|^[0-9]+ warning`, `incubator`, `incubating|^> Task :`,
   `incubating|WARNING: Using`. Each is re-derived from memory; some miss lines,
   some over-strip real errors.
3. **`-q` hides `BUILD FAILED`.** With `-q`, gradle prints nothing on success and
   only errors on failure, so several variants then *couldn't* find the status
   and re-ran without `-q`. The wrapper standardizes on `--console=plain` + an
   explicit captured exit code, so status is never ambiguous.

### 1.3 Canonical replacement - `gw` (see `DRAFT-gw.sh`)

One short command, module alias + gradle tasks:

```
gw ar compileJava              # asset-renderer compile, noise-stripped, EXIT line
gw ar compileJava compileTestJava
gw d4j test                    # discord4j-framework test + auto XML tally
gw ar test --tail 15           # override tail depth only when you actually need more
```

Behavior (fixed, so Claude never re-derives it):
- Resolves a short module **alias** to its absolute dir (`ar`, `atj`, `d4j`,
  `pers`, `bot`, `srv`, `vrh`, ...) and `cd`s there internally - kills the 5913
  `cd`-prefixes and works even in agent threads where cwd resets.
- Runs `./gradlew <tasks> --console=plain` capturing the **real** exit code to a
  file (no PIPESTATUS race).
- Streams the log through ONE canonical noise filter, then prints:
  - on success: `BUILD SUCCESSFUL  (ar: compileJava)  EXIT 0` + nothing else.
  - on failure: only `error:` / `.java:NN:` / `FAILED` / `BUILD FAILED` lines
    (default cap 40, `--tail N` to widen), then `EXIT <code>`.
- If any task is test-shaped (`test`, `slowTest`, `check`, `*Test`), it appends
  the canonical XML tally (section 3) automatically - the exact combination the
  discord4j sessions hand-wrote as `...===TALLY=== grep -hoE 'tests="..."' | awk`.

Estimated saving: ~50 generated tokens + ~300-450 readback tokens per call. Over
the ~1500 gradle calls that carry the boilerplate, on the order of 500k-700k
tokens across the corpus, before counting eliminated re-runs.

## 2. The `cd "W:/.../module" &&` prefix (5913x)

Root cause: each module is its own gradle build with its own `./gradlew`
(confirmed: `asset-renderer`, `discord4j-framework`, `persistence`,
`SkyBlock-Simplified` each ship their own `gradlew`), and the absolute path is
long (`W:/Workspace/Java/Simplified/Minecraft-Library/asset-renderer`, 52 chars).
Claude re-types the full `cd "<abs>" && ...` every call, ~55 chars (~18 tokens)
of pure boilerplate x 5913 ~= 100k+ generated tokens.

Two independent fixes, both shipped:

1. **CLAUDE.md fact: the Bash tool cwd persists between calls in a normal
   session.** Claude has been defensively re-cd-ing on every call. State the rule
   once (see `DRAFT-command-CLAUDE-md.md`): `cd` to the working module ONCE as a
   bare command at the start of a module's work, then issue bare gradle/git
   commands. Caveat (already in the tool docs): `cd X && cmd` in a *compound*
   command can trip a permission prompt - so prefer a standalone `cd`, not a
   chained one. NOTE: agent/subagent threads DO reset cwd between calls, so
   inside an Agent prompt the alias-based helper (fix 2) is the safe form.

2. **Module aliases resolved inside `gw`/`jtally`.** `gw ar test` needs no `cd`
   and no absolute path, and is cwd-independent (works in agent threads). The
   alias table lives in ONE place (`DRAFT-modules.sh`, sourced by both helpers
   and by the profile) instead of being retyped as a literal path 5913 times.

The alias table (short -> absolute), derived from the observed module set:

| alias | module dir |
|---|---|
| `ar` | Minecraft-Library/asset-renderer |
| `mt` | Minecraft-Library/minecraft-text |
| `nbt` | Minecraft-Library/nbt-factory |
| `vrh` | Minecraft-Library/vanilla-reference-harness |
| `d4j` | Simplified-Dev/discord4j-framework |
| `spring` | Simplified-Dev/spring-framework |
| `pers` | Simplified-Dev/persistence |
| `gson` | Simplified-Dev/gson-extras |
| `coll` | Simplified-Dev/collections |
| `mojang` | Simplified-Api/mojang |
| `hypixel` | Simplified-Api/hypixel |
| `bot` | SkyBlock-Simplified/simplified-bot |
| `srv` | SkyBlock-Simplified/simplified-server |

## 3. The build/test-results/*.xml tally (reinvented inline)

113 commands touch `build/test-results/test/*.xml`; `adhoc_scripts.txt` shows
~44 near-identical inline python blocks plus several awk variants, each parsing
the JUnit XML for `tests=/failures=/errors=` and listing failed testcases. They
differ only in cosmetics (regex vs ElementTree, `t=s=f=e=0` vs `tot=fail=0`,
`fail[:15]` vs `[:10]`, encoding='utf-8' guards added after a UnicodeDecodeError).
Each block is ~15-25 lines (~200-350 generated tokens) authored from scratch.

Two distinct parse strategies appear and BOTH are needed depending on the file:
- regex `tests="(\d+)"...` over raw text (fast, but brittle to attribute order),
- `xml.etree.ElementTree` walking `testcase`/`failure`/`error` (robust, and the
  only one that can name failing tests).

Canonical replacement - `jtally` (see `DRAFT-jtally.py`, invoked as one command,
optionally via `gw`):

```
jtally ar                 # tally asset-renderer's build/test-results/test
jtally                    # tally cwd's build/test-results/test
jtally ar --fails 20      # cap the failing-testcase list (default 15)
```

Output is a single compact block, stable across runs:

```
classes=37 tests=412 skipped=3 failures=2 errors=0   (ar)
FAIL GeometryParserTest::parsesNestedCubes
FAIL BridgeParityTest::matchesLegacyBytes
```

It uses ElementTree (names failures), guards encoding, and returns non-zero when
`failures+errors>0` so it doubles as a gate. `gw` calls it automatically after a
test task, so the common case needs no separate call at all.

Saving: ~200-350 generated tokens per tally that is no longer authored inline,
over ~44+ occurrences (~10k-15k tokens), plus it removes the repeated
"UnicodeDecodeError -> re-author with encoding guard" correction loop visible in
`adhoc_scripts.txt` (lines 61-65 add `TextIOWrapper(...errors='replace')`).

## 4. echo / grep / head-tail file slicing vs the right tools

### 4.1 `echo` 4821x - mostly section markers

The dominant `echo` use is `echo "=== marker ==="` separators bracketing a pipe,
plus `echo "EXIT: ..."`. Both are absorbed by `gw`/`jtally` (they print their own
labeled, fixed-format sections). Guidance in CLAUDE.md: do not hand-print
`=== marker ===` / `EXIT:` lines around gradle/git output - the helper labels its
own output; a bare `git status --short` needs no echo banner.

### 4.2 `grep` 3852x vs `rg` 34x, and grepping SOURCE vs grepping OUTPUT

Two very different uses hide under "grep":
- **Grepping build OUTPUT** inside a pipe (`... | grep -vE incubating`): fine to
  keep, but it is exactly what `gw` internalizes - so most of these disappear.
- **Grepping SOURCE files** for symbols/usages (`grep -rn 'throw new'`,
  `grep 'extends'`): this should route to the **Grep tool** (ripgrep-backed,
  integrates with the permission UI and file links) or the IntelliJ MCP symbol
  tools, not shell `grep`. The 3852:34 shell-grep-to-`rg` ratio shows Claude
  almost never reaches for ripgrep and rarely reaches for the Grep tool when
  spelunking source. CLAUDE.md guidance: source search = Grep tool; IDE attached
  = `search_symbol`/`search_regex`; shell `grep` only for piping tool output.

### 4.3 `head` 2353 / `tail` 1888 - slicing FILES instead of Read offset/limit

243 commands `cat`/`head`/`tail` a `.java` file. Reading a file slice through the
shell (`head -80 Foo.java`, `sed -n '40,90p'`) dumps raw bytes into context with
no line numbers and no state tracking, and is a frequent source of the
"has not been read yet" Edit failures (220 in the corpus) because a shell `cat`
does NOT satisfy the Edit-tool's read requirement. Guidance: to view part of a
file use the **Read tool** with `offset`/`limit` (returns numbered lines AND
registers the read so a subsequent Edit succeeds). Reserve `head`/`tail` for
truncating command *output* (and even there, `gw`/`jtally` already bound it).

Net: `gw` + `jtally` + the Grep/Read routing rules retire the large majority of
the echo/grep/head/tail volume that is build-output plumbing, and the CLAUDE.md
rules redirect the source-spelunking remainder to the cheaper typed tools.

## 5. The 869 throwaway-tool events - helper vs skill triage

Triage rule: a *deterministic, parameterizable* routine -> **committed helper
script** (zero tokens to invoke). A routine needing *judgement / prose / IDE
routing* -> **skill**. Data already largely written to files -> neither; just a
one-line reader.

| Recurring ad-hoc (evidence) | Count | Verdict |
|---|---|---|
| gradle noise-filter+tail+PIPESTATUS | ~1500 | **helper** `gw` (section 1) |
| JUnit-XML test tally (python/awk) | ~44+ | **helper** `jtally` (section 3) |
| parity `md5sum -c baseline` OK/FAIL count | 35 | **helper (recommended follow-up)** `jparity <alias> <key>` - deterministic; ~15 lines; not in this draft set |
| jitpack build-status poll loop | 26 | **helper (recommended follow-up)** `jitpack-wait $GROUP $REPO $SHA` - deterministic poll; not in this draft set |
| `/tmp/sortimports.py` import reorderer | many | **skill** - already covered by `javadoc-normalize`'s import injector + IntelliJ Optimize-Imports; do NOT commit the naive sorter (it interleaves `import static`, never reorders across blank groups - it is IntelliJ-INaccurate). Route to the existing skill. |
| `cat > /tmp/pr-body.md <<EOF` heredocs | several | **no helper** - genuinely one-off prose; keep as heredoc, but write to the scratchpad dir, not `/tmp`. |
| resume/next-session prompt heredocs | several | already covered by the CLAUDE.md "Next-session prompts" clipboard pattern; no new helper. |
| JSON-shape probes over `renderer/*.json` (python `json.load(...); print(...)`) | 100+ | **skill or helper** `jq`-style reader - see note below; highest-count *un-addressed* category after gradle. |

### 5.1 The renderer-JSON probe firehose (asset-renderer-specific)

By far the largest single ad-hoc category in `adhoc_scripts.txt` is inline
`python -c "import json; d=json.load(open('.../renderer/entity_models.json')); ..."`
probes - counting families, dumping one family, diffing vs `HEAD:`, checking a
geometry key. These are asset-renderer-domain and volatile in what they print, so
a single committed helper `rjson` (render-json query) that takes a file alias +
a dotted path or a small verb (`families`, `count`, `get <key>`, `diff-head`)
would absorb most of them. This is asset-renderer-scoped, so it belongs in that
repo's existing `scripts/` dir (confirmed present), not in the global `~/.claude`
helpers. Flagged here as the top follow-up; a full `rjson` spec is out of scope
for the global command-hygiene deliverables but noted for the asset-renderer
owner.

## 6. Deliverables index & install steps

All under the scratchpad root (`.../scratchpad/`):

| Draft file | Installs to | What it is |
|---|---|---|
| `DRAFT-modules.sh` | `~/.claude/bin/modules.sh` | alias->abs-dir table (single source of truth), sourced by `gw`; embeds `SIMPLIFIED_ROOT` override |
| `DRAFT-jtally.py` | `~/.claude/bin/jtally.py` | canonical JUnit-XML tally; standalone or driven by `gw`; exits non-zero on failure (gate) |
| `DRAFT-gw.sh` | `~/.claude/bin/gw` (+`chmod +x`) | gradle wrapper: alias + tasks -> noise-stripped compact output + real EXIT + auto tally |
| `DRAFT-command-CLAUDE-md.md` | append to `~/.claude/CLAUDE.md` | the `## Shell command hygiene` section + the one-time install block |

Install (one time), copied verbatim into `DRAFT-command-CLAUDE-md.md`:

```bash
mkdir -p ~/.claude/bin
cp DRAFT-modules.sh ~/.claude/bin/modules.sh
cp DRAFT-jtally.py  ~/.claude/bin/jtally.py
cp DRAFT-gw.sh      ~/.claude/bin/gw && chmod +x ~/.claude/bin/gw
# ~/.bashrc (profile the Bash tool sources each call):
#   export PATH="$HOME/.claude/bin:$PATH"
#   alias jtally='python3 $HOME/.claude/bin/jtally.py'
```

Portability notes:
- Runs in git-bash on Windows; `./gradlew` (the POSIX launcher) works from
  git-bash, and `W:/...` forward-slash paths resolve.
- `gw` is a *script* (subshell), so its internal `cd` never disturbs the caller's
  cwd - safe to call from any directory and inside agent threads.
- The alias map is duplicated in `jtally.py` only for standalone `jtally ar`
  ergonomics; when `gw` drives it, `gw` passes an absolute dir so the map is
  bypassed. If the module set changes, edit `modules.sh` (and, for standalone
  use, the mirror dict at the top of `jtally.py`).
- These are NOT skills - they are zero-token-to-invoke helpers. They compose with
  the existing `gradle-verify-gate` skill: that skill should call `gw <alias>
  compileJava test` instead of describing the raw incantation (a one-line patch
  to the skill, flagged as a follow-up, not included here).

## 7. Token-waste model & aggregate saving estimate

Two token channels per repeated command: **generation** (Claude writes the
command string) and **readback** (the command's output enters context). Estimates
use ~4 chars/token and are deliberately conservative; assumptions are stated.

### 7.1 gradle incantation (the big one)
- Generation: full incantation ~120-220 chars (~40-70 tok) vs `gw ar test`
  (~4 tok) -> ~50 tok saved/call.
- Readback: `tail -40` imports ~40 log lines (~600 tok) even on success; `gw`
  success prints 1 line (~20 tok) -> ~500 tok saved on the success path; on
  failure `gw` prints only error lines vs a 40-line tail -> ~300 tok saved.
- Re-run elimination: dedup shows the same command re-issued at 2-3 different
  tail depths; conservatively 1 avoided re-run per ~4 calls -> amortize ~150 tok.
- Blended ~500 tok/call over ~1500 boilerplate-carrying gradle calls
  => ~0.75M tokens on the gradle channel alone (order-of-magnitude: 0.5M-0.9M).

### 7.2 cd prefix
- ~55 chars (~18 tok) generation x 5913 => ~106k tokens of pure `cd`-prefix
  boilerplate, eliminated by cwd-persistence + alias helpers. (Readback: none.)

### 7.3 test tally
- Generation: ~200-350 tok inline block vs `jtally ar` (~3 tok) over ~44+
  authored blocks => ~10k-15k tokens, plus removal of the recurring
  UnicodeDecodeError -> re-author correction loop.

### 7.4 echo/grep/head/tail plumbing
- `echo` markers + `echo EXIT` (661 PIPESTATUS echoes + a large share of 4821
  echoes) and output-`grep`/`tail` are mostly absorbed into `gw`/`jtally`; the
  source-search remainder routes to the cheaper Grep/Read tools. Hard to price
  precisely; conservatively ~50-100k tokens, and it also removes a chunk of the
  220 "has not been read yet" Edit failures (each failure = a wasted round trip).

### 7.5 Aggregate
Summing the grounded channels: **~0.9M-1.1M tokens** of avoidable
generation+readback across the recorded corpus, dominated (~80%) by the gradle
incantation. Forward-looking, the per-session saving scales with gradle/test
volume - in an asset-renderer-heavy session (the dominant module) that is the
difference between `gw ar test` (~3 tok in, ~30 tok out) and the ~120-tok-in,
~600-tok-out hand-rolled equivalent, i.e. ~20x per verify cycle, dozens of cycles
per session.

The single highest-leverage un-addressed follow-up (not in this draft set) is the
renderer-JSON probe firehose (section 5.1): 100+ inline `json.load` probes that an
asset-renderer-scoped `rjson` helper would collapse the same way `jtally`
collapses the XML tally.
