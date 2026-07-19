#!/usr/bin/env node
// toolsmith PreToolUse advisory (NON-BLOCKING): nudge away from reflexive shell
// `grep`/`rg` for Java DECLARATIONS toward the Grep tool / java-symbol-search skill
// (IntelliJ AST). Fires only on clear declaration/inheritance/throw-site greps over
// .java, never on plain import-greps (those are a legitimate file-mover fallback),
// and NEVER blocks - worst case it is a silent no-op.
'use strict';

let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  let data;
  try { data = JSON.parse(input); } catch (_) { process.exit(0); }

  if ((data.tool_name || '') !== 'Bash') process.exit(0);
  const cmd = (data.tool_input && data.tool_input.command) || '';

  const isGrep = /\b(grep|rg)\b/.test(cmd);
  // .java file/glob, a `/java` source-dir segment (src/main/java), or a src/main|test tree.
  const touchesJava = /\.java\b|[\/\\]java\b|src[\/\\](main|test)/.test(cmd);
  // Declaration / inheritance / throw-site patterns - NOT bare `import` (grepping
  // importers is the sanctioned java-file-mover fallback and must not be nagged).
  const symbolPat = /throw new|\bextends |\bimplements |\bclass\s+[A-Z]|\binterface\s+[A-Z]|@Override/.test(cmd);

  if (isGrep && touchesJava && symbolPat) {
    const msg = 'toolsmith advisory: this looks like a Java symbol/declaration search via shell grep. '
      + 'Prefer the Grep tool (ripgrep-backed, clickable file links) or the java-symbol-search / '
      + 'java-find-usages skills (IntelliJ AST) - one structured call instead of grep plus follow-up '
      + 'file re-reads. (Plain import-greps are fine and are not flagged.)';
    process.stdout.write(JSON.stringify({
      hookSpecificOutput: { hookEventName: 'PreToolUse', additionalContext: msg }
    }));
  }
  process.exit(0);
});
