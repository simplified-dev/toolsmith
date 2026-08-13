---
name: java-record-audit
description: Audit Java record declarations against CLAUDE.md `## Javadoc` rules for record components. Auto-invoked when the user asks to "audit records", "check record javadocs", "validate record component params", or before reviewing a PR that adds / modifies records. Flags `@param` lines that describe what to *pass* rather than what the component *is*, `Gets `/`Returns ` prefixes on component descriptions, and missing component docs. Surfaces results for manual review; never auto-rewrites prose because the right phrasing is semantic.
auto_invoke: true
tags: [java, records, audit, javadoc, claude-md-conformance]
---

# java-record-audit

Validate Java `record` declarations against the CLAUDE.md `## Javadoc`
field-like rules. Records are the highest-noise area for the field-like
javadoc convention because every component is both a `@param` (record
javadoc) and a field-equivalent (accessor docs the IDE may auto-generate).

## When to invoke

- User asks to "audit records", "check record javadocs", "validate record
  component params".
- About to review a PR that adds or modifies a record declaration.
- After `java-bulk-rename` renamed record components - the rename keeps the
  `@param` name but the description may now be stale.

Skip when: the file has zero `record` declarations. The skill is record-
specific; for general javadoc audits use `java-docs-normalize`.

## CLAUDE.md rules being checked

From the user-global `~/.claude/CLAUDE.md` `## Javadoc` section:

- **Voice**: record component = fragment, no tags on the accessor.
- **Field-like (record components)**: doc on the field/component, never the
  accessor. No `@return`, no "Gets"/"Returns" prefix.
- **Record `@param`** describes what the component *is*, not what to *pass*.

The first two are mechanically caught by `java-docs-normalize`'s
`gets-prefix` rule. The third - "describes what the component *is*, not
what to *pass*" - is semantic and needs a heuristic flagger.

## Heuristic flags

| Pattern in `@param` description | Likely violation | Reason |
|---|---|---|
| starts with "the value to" / "the value used as" | passing-language | describes the argument |
| starts with "value passed " / "value supplied " | passing-language | describes the argument |
| starts with "input" / "the input" + noun | passing-language | argument framing |
| starts with `[noun]` + "to set" / "to use" | passing-language | imperative on the caller |
| starts with "if " | conditional-as-doc | usually means the component is a flag and the doc explains what `true`/`false` mean - acceptable, but flag for review |
| matches `Gets `/`Returns ` | accessor-voice | violates CLAUDE.md field-like rule |

Acceptable patterns (no flag):

- starts with "the " + noun (`the canvas width`)
- starts with a bare noun phrase (`canvas width in pixels`)
- starts with "a " / "an " + noun (`a non-null Comparator`)

The flagger is intentionally conservative - false positives are expected
on records whose components legitimately are "the value to compare against"
(comparator-style records). Surface for review; do not auto-rewrite.

## How to use

Two passes. Both are scripted as part of `java-docs-normalize` already - this
skill is the routing wrapper that documents the rules and the IntelliJ
discovery path.

1. **Discovery** - find all records in scope:
   ```
   mcp__IntelliJ_IDE__search_regex q="record\s+\w+\s*\(" paths=["**/*.java"]
   ```
   Or with the IDE engine: `search_symbol` filtered to record decls.

2. **Audit** - run `java-docs-normalize` against the same paths to catch the
   mechanical violations (`gets-prefix`, oneliner-to-block on component
   docs). For the passing-language heuristic, currently a manual review of
   record component `@param` lines is the path - the heuristic is not yet
   mechanical (TODO: add a `record-param-voice` flag to
   `toolsmith/javadoc.py` if the audit produces enough hits to justify the
   regex).

## Sample output expectation

```
record OptionsPair {
    /**
     * @param x the value to compare against     <- flag: passing-language
     * @param y the comparator used to compare   <- ok
     */
    record OptionsPair(int x, Comparator<?> y) {}
}
```

## Cross-reference

- Mechanical javadoc rules (`gets-prefix`, oneliner-to-block, `@author`
  removal) -> `java-docs-normalize`.
- Record component renames -> `java-bulk-rename`.

## After running

Audit is read-only. No `gradle-verify-gate` needed unless the user then
edits records as a follow-up.
