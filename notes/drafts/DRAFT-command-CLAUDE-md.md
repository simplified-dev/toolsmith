# CLAUDE.md addition - Shell command hygiene (Simplified Java projects)

Paste the block below verbatim into `C:/Users/BrianGraham/.claude/CLAUDE.md`
(new top-level section, e.g. after `## Control Flow`). It states the facts that
stop the reinvention, and points at the committed helpers.

Install the helpers first (one time):

```bash
mkdir -p ~/.claude/bin
cp DRAFT-modules.sh   ~/.claude/bin/modules.sh
cp DRAFT-jtally.py    ~/.claude/bin/jtally.py
cp DRAFT-gw.sh        ~/.claude/bin/gw && chmod +x ~/.claude/bin/gw
cp DRAFT-locate-java.sh ~/.claude/bin/locate-java && chmod +x ~/.claude/bin/locate-java
# add to ~/.bashrc (the profile the Bash tool sources each call):
#   export PATH="$HOME/.claude/bin:$PATH"
#   alias jtally='python3 $HOME/.claude/bin/jtally.py'
```

---

## Shell command hygiene

### Bash cwd persists - stop re-cd-ing
The Bash tool's working directory persists between calls in a normal session. Do
NOT prefix every command with `cd "W:/Workspace/Java/Simplified/.../module" &&`.
`cd` into the working module ONCE as a standalone command (a bare `cd`, never
`cd X && cmd` - a chained cd can trip the permission prompt), then issue bare
`git`/`gradlew` commands. Exception: agent/subagent threads reset cwd between
calls, so inside an Agent prompt use the alias-based `gw`/`jtally` helpers (which
resolve the module dir internally) rather than relying on cwd.

### Gradle builds - use `gw`, never hand-roll the noise filter
Each module is its own gradle build. Instead of
`cd … && ./gradlew compileJava -q 2>&1 | grep -vE 'incubating|warning' | tail -N ; echo ${PIPESTATUS[0]}`,
run:

```
gw <alias> <task...>        # gw ar compileJava   |   gw d4j test   |   gw ar test --tail 25
```

`gw` cd's into the module by alias, runs gradle with `--console=plain`, strips
gradle noise with ONE canonical filter, prints one status line on success or only
the error lines on failure, appends `EXIT <code>` (the real gradle exit, no
`PIPESTATUS` guessing), and auto-runs the test tally for test tasks. Aliases:
`ar mt nbt vrh github hypixel mojang skyblockdata annotations client coll dataflow d4j expression gson image manager pers refl scheduler spring utils yaml toolsmith sbsapi bot data srv`
(source of truth: `~/.claude/bin/modules.sh`; mirrored in toolsmith `modules.py`). Do not add `-q`, `2>&1`, a
`grep -vE incubating`, a `tail -N`, or an `echo EXIT` around gradle - `gw` owns
all of that. `gw . <task>` uses the current directory.

### Test results - use `jtally`, never inline python/awk
To count JUnit results, do NOT author an inline
`python -c "import glob, xml.etree..."` or `awk '/<testsuite/{...}'` over
`build/test-results/test/*.xml`. Run `jtally <alias>` (or just `jtally` in the
module's cwd). It prints `classes=/tests=/skipped=/failures=/errors=` plus the
failing `Class::test` names, and exits non-zero on any failure (so it is also a
gate). `gw <alias> test` already appends it.

### Search source with the Grep/Read tools, not shell `grep`/`head`/`tail`
- Searching SOURCE for symbols/usages/throw-sites: use the **Grep tool**
  (ripgrep-backed, integrates with file links) or, when IntelliJ is attached,
  `search_symbol`/`search_regex`. Reserve shell `grep` for filtering the output
  of another command in a pipe.
- Viewing part of a file: use the **Read tool** with `offset`/`limit` - it
  returns numbered lines AND satisfies the Edit-tool read requirement. A shell
  `cat`/`head`/`tail`/`sed -n` on a `.java` file does NOT register the read, so a
  following Edit fails with "has not been read yet".
- **Locating a class FILE by name:** `locate-java <ClassName>` (or the Glob tool / IntelliJ
  `find_files_by_name_keyword`) BEFORE a guessed Read - one locate beats a find-fail chain.
- Do not bracket gradle/git output with hand-printed `echo "=== marker ==="` or
  `echo "EXIT: ..."` banners - `gw`/`jtally` label their own output; a bare
  `git status --short` needs no banner.
