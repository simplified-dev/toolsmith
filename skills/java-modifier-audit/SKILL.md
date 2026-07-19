---
name: java-modifier-audit
description: Audit Java class / field / method modifiers (`final`, `static`, `private`/`protected`/`public`) for common-convention drift. Auto-invoked when the user asks to "audit modifiers", "find missing final", "check visibility", or before a release that wants leaf classes locked down. Routes to IntelliJ MCP `get_file_problems` for the IDE's own modifier inspections (CanBeFinal, MutableStaticField, etc.) plus targeted `search_regex` for patterns the IDE inspection doesn't surface. Conservative - flags candidates, never auto-applies modifiers.
auto_invoke: true
tags: [java, modifiers, audit, final, visibility, mcp, intellij]
---

# java-modifier-audit

Surface Java modifier drift: classes that should be `final`, fields that
should be `final` or `private`, mutable `public static` fields, and
unnecessarily broad visibility. Routes to IntelliJ's own inspections so
the audit aligns with the project's configured profile.

## When to invoke

- User asks to "audit modifiers", "find missing final", "check visibility",
  "lock down leaf classes".
- Before a library release where API surface should be intentional - public
  classes that should be `final` and `public` fields that should be
  `private` matter at version-boundary commitments.
- After `java-exception-class-gen` writes new exception classes - per
  several historical plans (`scan-the-codebase-for-composed-tarjan.md`
  line 19), project exception classes are expected to be `final`. The skill
  is the right post-write check.

Skip when: the file is part of a public-API surface that intentionally
allows subclassing or external mutation. Convention overrides the audit.

## What gets flagged

| Pattern | Why | Tool |
|---|---|---|
| Class with no subclasses, not `final`, not `abstract`, no `sealed` permits | Likely should be `final` (CanBeFinal inspection) | `mcp__IntelliJ_IDE__get_file_problems` |
| Field assigned only in the constructor, not `final` | Effectively-immutable field that should be `final` | `get_file_problems` (FieldCanBeFinal) |
| `public static` mutable field (no `final`) | Hidden mutable singleton state | `search_regex` then verify via `get_file_problems` |
| Method only called from within its own class, not `private` | Unnecessarily broad visibility | `get_file_problems` (CanBePrivate) |
| Exception subclass (`extends *Exception` or `extends Throwable`), not `final` | CLAUDE.md exception convention is `final` for project exception classes | `search_regex` for `^(?:public\s+)?class\s+\w+Exception\s+extends`, cross-check `final` modifier |

## How to use

The IntelliJ inspection profile already encodes most of these. The skill's
job is to surface them in batch rather than file-by-file.

**Single-file audit:**

```
mcp__IntelliJ_IDE__get_file_problems filePath="src/main/java/.../X.java"
```

Filter the output to entries whose `inspection` field matches the modifier
inspections (`CanBeFinal`, `FieldMayBeFinal`, `MutableStaticField`,
`CanBePrivate`, `WeakerAccess`). The IDE reports them inline.

**Project-wide audit (heaviest):**

`mcp__IntelliJ_IDE__build_project` followed by inspection-result reading -
or run IntelliJ's "Inspect Code" via UI. There is no clean MCP path for
batch inspection results across all modules yet; surface the gap in your
reply rather than approximate with regex.

**Targeted regex passes (cheaper, narrower):**

| Goal | Regex |
|---|---|
| Find `public static` non-`final` fields | `^\s*public\s+static\s+(?!final\b)(?!class\b)(?!record\b)(?!interface\b)\w+\s+\w+\s*[=;]` |
| Find exception classes not declared `final` | `^(?!.*\bfinal\b)(?:public\s+)?class\s+\w+Exception\s+extends` |
| Find non-`final` classes whose name starts uppercase with no subclasses | Combine with `java-find-usages` on the class - if zero `extends` references, candidate for `final` |

## Conservatism rules

- **Never auto-apply modifiers.** The skill is audit-only; modifier
  changes are reversible-but-noisy for API consumers.
- **Skip generated code and test fixtures** - many test-only classes are
  intentionally not `final` to allow Mockito sub-classing without the
  inline-mock-maker.
- **Honor `@SuppressWarnings`** - if a class or field has the relevant
  inspection suppressed, do not flag it.

## Cross-reference

- Exception class creation -> `java-exception-class-gen` (already emits
  the class declaration but does NOT add `final`; this skill catches the
  miss).
- Unused-import audit (related modifier surface) -> `java-import-audit`.
- Post-edit verification -> `gradle-verify-gate`.

## After running

Audit is read-only. If the user accepts a flag and adds `final` to a
class, route the follow-up edit through normal Read + Edit, then invoke
`gradle-verify-gate` - adding `final` to a class is a binary-compatibility
break for any external subclasser, so the verify gate is non-optional.
