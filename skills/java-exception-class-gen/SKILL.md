---
name: java-exception-class-gen
description: Generate a Java exception class that conforms to user CLAUDE.md `## Exceptions` rules - five constructors in fixed order, `@NotNull` / `@PrintFormat` / `@Nullable` annotations, root vs child `super(...)` argument order, and the canonical class / constructor javadoc shape. Auto-invoked when Claude is about to write or paste a new `extends RuntimeException` or `extends SomeRootException` class, or when the user asks to "create a new XException". Pure template, no MCP required.
auto_invoke: true
tags: [java, exception, boilerplate, template]
---

# java-exception-class-gen

Emit a CLAUDE.md `## Exceptions`-conformant exception class. Two shapes:
**root** (extends `RuntimeException`, reverses `super()` argument order) and
**child** (extends an existing root, passes arguments through unchanged - the
root does the reversal).

## When to invoke

- About to write or paste a new exception class.
- User asks "create a new `XException`" / "add an exception for [condition]".
- Authoring a class whose `extends` clause is `RuntimeException` or a project
  root exception type.
- Migrating a checked exception to an unchecked / project root form.

## Constructor order (verbatim from CLAUDE.md `## Exceptions`)

Both root and child exceptions declare exactly these five constructors, in
this order:

1. `(Throwable cause)`
2. `(String message)`
3. `(Throwable cause, String message)`
4. `(@PrintFormat String message, Object... args)`
5. `(Throwable cause, @PrintFormat String message, Object... args)`

## Annotations

- `@NotNull` on `Throwable cause` and `String message` parameters.
- `@PrintFormat` on the format-string parameter (constructors 4 and 5). Import
  from `org.intellij.lang.annotations.PrintFormat`.
- `@Nullable` on the `Object... args` vararg.

## Message conventions

- No trailing punctuation.
- Start with an uppercase letter.
- Wrap interpolated values in single quotes: `'%s'`.

## Javadoc shape (CLAUDE.md `## Exceptions` only)

- Class: `Thrown when [condition].` Never opens with "unchecked" or
  "exception".
- Constructor: `Constructs a new {@code ClassName} with [description].`
- `@param` lines: lowercase, no trailing period, single space after the param
  name.

Text-mechanics rules (block form, hyphen style, tag policing) live in
`javadoc-normalize` - do not restate them here.

## Template - Root exception (extends RuntimeException)

The root **reverses** the `super(...)` argument order: it always calls the
JDK constructor with `(message, cause)`, even when its own parameter list
puts `cause` first.

```java
package com.example.foo;

import org.intellij.lang.annotations.PrintFormat;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

/**
 * Thrown when [condition].
 */
public class FooException extends RuntimeException {

    /**
     * Constructs a new {@code FooException} with the given cause.
     *
     * @param cause the underlying cause
     */
    public FooException(@NotNull Throwable cause) {
        super(cause);
    }

    /**
     * Constructs a new {@code FooException} with the given message.
     *
     * @param message the detail message
     */
    public FooException(@NotNull String message) {
        super(message);
    }

    /**
     * Constructs a new {@code FooException} with the given cause and message.
     *
     * @param cause the underlying cause
     * @param message the detail message
     */
    public FooException(@NotNull Throwable cause, @NotNull String message) {
        super(message, cause);
    }

    /**
     * Constructs a new {@code FooException} with the given formatted message.
     *
     * @param message the format string
     * @param args the format arguments
     */
    public FooException(@PrintFormat String message, @Nullable Object... args) {
        super(String.format(message, args));
    }

    /**
     * Constructs a new {@code FooException} with the given cause and formatted message.
     *
     * @param cause the underlying cause
     * @param message the format string
     * @param args the format arguments
     */
    public FooException(@NotNull Throwable cause, @PrintFormat String message, @Nullable Object... args) {
        super(String.format(message, args), cause);
    }
}
```

## Template - Child exception (extends a root)

The child **passes through** unchanged: it calls `super(cause, message)` /
`super(cause, message, args)` because the root constructor already does the
reversal.

```java
package com.example.foo;

import org.intellij.lang.annotations.PrintFormat;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

/**
 * Thrown when [condition].
 */
public class FooBarException extends FooException {

    /**
     * Constructs a new {@code FooBarException} with the given cause.
     *
     * @param cause the underlying cause
     */
    public FooBarException(@NotNull Throwable cause) {
        super(cause);
    }

    /**
     * Constructs a new {@code FooBarException} with the given message.
     *
     * @param message the detail message
     */
    public FooBarException(@NotNull String message) {
        super(message);
    }

    /**
     * Constructs a new {@code FooBarException} with the given cause and message.
     *
     * @param cause the underlying cause
     * @param message the detail message
     */
    public FooBarException(@NotNull Throwable cause, @NotNull String message) {
        super(cause, message);
    }

    /**
     * Constructs a new {@code FooBarException} with the given formatted message.
     *
     * @param message the format string
     * @param args the format arguments
     */
    public FooBarException(@PrintFormat String message, @Nullable Object... args) {
        super(message, args);
    }

    /**
     * Constructs a new {@code FooBarException} with the given cause and formatted message.
     *
     * @param cause the underlying cause
     * @param message the format string
     * @param args the format arguments
     */
    public FooBarException(@NotNull Throwable cause, @PrintFormat String message, @Nullable Object... args) {
        super(cause, message, args);
    }
}
```

## Fill these slots

When applying the template, replace:

- Class name (every occurrence of `FooException` / `FooBarException`).
- `package` declaration to match the target directory.
- Parent class on `extends` (root: keep `RuntimeException`; child: substitute
  the project root).
- Class javadoc `[condition]` phrase.
- Per-constructor `[description]` phrase if the default ("with the given
  cause" etc.) is not specific enough.

## After running

- Reformat the generated file with `mcp__IntelliJ_IDE__reformat_file`. The
  template uses 4-space indentation and a fixed brace style; the project's
  IntelliJ code-style settings may differ (column width, blank-line rules,
  annotation placement). The IDE's `reformat_file` reads the live settings -
  no other formatter does. Skip `google-java-format`, Spotless, and IntelliJ
  headless; all impose their own style.
- Do NOT re-run `javadoc-normalize` on the generated file. Templates already
  conform to the CLAUDE.md `## Javadoc` rules; running normalize is a no-op at
  best and risks spurious flags.
- Invoke `gradle-verify-gate` after dropping the new class into the project
  to confirm compilation and any usages still link.
