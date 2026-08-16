"""Diff a JSON capture against the Java source tree that binds it.

Replaces the regex Java parser plus JSON walk that gets re-derived, per module,
by everyone who captures a fixture and needs to know what it does not bind.
Walks a JSON document and a Java class graph in parallel and reports the JSON
keys no field binds.

Traps this encodes:

  * The Java is read with regexes, so what it understands is a VOCABULARY
    rather than a language. It knows ``@SerializedName``, ``@SerializedPath``,
    ``@Extract``, ``@Capture``, ``@Collapse``, ``@Key`` and ``@Flatten``, and
    it reads none of ``@Lenient``, ``@Split`` or ``@Fallback`` because no key's
    answer depends on them. A field declaration split across two lines matches
    nothing at all, which is why a class that parsed zero fields is reported as
    a diagnostic.
  * A simple type name is looked up globally when no nested or same-file class
    answers, and a workspace-wide source root makes collisions ordinary. A
    global hit with more than one candidate resolves to whichever file sorted
    first AND is recorded in ``ambiguous_types``; a wrong resolution walks the
    right JSON against the wrong class and reports its keys unmapped.
  * Exit 1 means unmapped keys were found, which is a red verdict and not a
    precondition failure. A root class that does not exist under ``src`` is the
    precondition, and it is a 2.
  * Key lists carry ``cap`` rows and the counts beside them do not, so
    ``truncated`` is what says a list is short.
  * The strictness switches all default OFF. Each names one annotation whose
    correct handling differs from the hand-authored script's, and turning one
    on can change the verdict on a capture the script called clean.
  * There are two directions and only the first one gates. The walk answers
    which JSON keys nothing binds; the projection under ``phantom`` also
    answers which fields no JSON key reaches, and that second answer is full
    of shapes the wire simply did not send this time - an absent subtree is a
    player's privacy setting rather than a defect. It changes an exit code
    only under ``fail_on_phantom``.

Path expressions - ``node`` and ``union`` - are dotted, and a segment is one of:

  ``name``   a JSON object key, or a list index when every character is a digit
  ``[]``     every element of the array at this position (``union`` only)
  ``{}``     every value of the object at this position (``union`` only)

``node`` narrows the document to one subtree and must name exactly one.
``union`` selects many and merges them into a single template object, so a key
that is optional in every individual sample is still audited once. Auditing a
Hypixel profiles capture unions every member of every profile, which is
``union="profiles.[].members.{}"``; reading one profile whole is
``node="profiles.0"``. ``section`` narrows a third time, to one top-level key
of whatever the first two produced.

Failure is data: the public function returns a dict and nothing raises across
the boundary. The renderers turn one of those dicts into text and read nothing
else - no source file, no capture, no environment - so every front end renders
the same run from the same data and a report can be produced from a result that
was carried across a process boundary.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import discovery

# --------------------------------------------------------------------------
# Configuration defaults.
# --------------------------------------------------------------------------

# Types the walk never descends into. These are the JDK and gson names alone -
# a domain type that is opaque because it lives in another repo (a date model,
# an NBT tag) belongs to the caller and is added through the `opaque` argument.
# A type absent from here and absent from `src` is not silently skipped: it is
# reported under `unresolved_types` when its wire value is an object, which is
# how a source root that is too narrow announces itself.
_OPAQUE = frozenset({
    "String", "UUID", "int", "long", "double", "float", "boolean", "short", "byte",
    "Integer", "Long", "Double", "Float", "Boolean", "Short", "Byte", "Number",
    "Object", "JsonElement", "JsonObject", "JsonArray",
})

# Collection shapes, not domain types: a map's LAST type argument describes the
# JSON object's values, a sequence's FIRST describes its elements, and a
# wrapper is unwrapped to the type inside it. They are defaults rather than
# constants for the same reason `opaque` is - another workspace spells its
# concurrent collections differently.
_MAP_TYPES = frozenset({"Map", "ConcurrentMap", "ConcurrentLinkedMap", "HashMap",
                        "LinkedHashMap"})
_SEQ_TYPES = frozenset({"List", "Set", "ConcurrentList", "ConcurrentSet",
                        "ConcurrentLinkedList", "ArrayList", "HashSet", "Collection"})
_WRAPPER_TYPES = frozenset({"Optional", "AtomicReference"})

# The strictness switches, each named for the annotation whose handling it
# corrects. They default off so that a capture the hand-authored script called
# clean stays clean: every one of them is a fix, and a fix that changes a
# verdict has to be asked for.
#
#   names     read @SerializedName's `value =` and `alternate =` forms, and let
#             @SerializedPath win over @SerializedName on one field. Off, only
#             the lone-literal form is read and the first annotation written
#             wins, so a named-parameter site falls back to the Java name.
#   extract   an @Extract field binds no wire key of its own - it is fed from
#             another field's overflow. Off, its value is read as a wire path,
#             which invents a binding wherever the source field's wire name
#             differs from its Java name.
#   capture   read @Capture's `descend` flag rather than inferring descent from
#             the field carrying a name, sort filtered captures ahead of the
#             catch-all, report an inner key the filter rejects, and spend the
#             matched key as the collection's own level so the node under it is
#             one entry. Off, a named @Capture is treated as an ordinary named
#             field, captures are tried in declaration order, and the whole
#             declared collection is walked against a single entry - which is
#             what makes a class-valued capture's own keys invisible, and what
#             the projection half of the same result disagrees with, since it
#             spends that level either way.
#   collapse  an object-shaped wire value reaches a sequence field only through
#             @Collapse; without it the shape is a mismatch and is reported.
#             Off, any sequence field walks an object's values as elements,
#             which hides the decode that would throw.
#   flatten   consume the wrapper level @Flatten names inside each entry. Off,
#             the annotation is ignored and the declared element type is walked
#             against the wrapper rather than against what is inside it.
_STRICT_FEATURES = frozenset({"names", "extract", "capture", "collapse", "flatten"})

# @Split, @Lenient and @Fallback are read by nothing here, and each is inert
# for its own reason:
#   @Split consumes exactly the key it would consume without it and splits that
#     key's string VALUE into a pair. A string has no child keys, so the walk
#     never reaches anything the annotation moved.
#   @Lenient routes an entry's VALUE to the declared collection or to overflow,
#     and overflow merges back on write. The field claims every key in its
#     subtree either way, so reporting one unmapped would be a false positive.
#   @Fallback marks an enum CONSTANT so an unrecognised name resolves to it.
#     The walk returns on an enum without reading its body, and no key's answer
#     depends on which constant a name landed on.

_FIELD_RE = re.compile(
    r"^(?P<mods>(?:(?:private|protected|public|static|final|transient)\s+)+)"
    r"(?P<rest>[^=;()]+?)\s+(?P<name>\w+)\s*(?:=[^=]|;)"
)
_CLASS_RE = re.compile(
    r"\b(?P<kind>class|enum|interface|record)\s+(?P<name>\w+)"
    r"(?:\s*<[^>]*>)?(?:\s+extends\s+(?P<super>[\w.]+))?"
)


@dataclass(frozen=True)
class _Settings:
    """The vocabulary and strictness one run walks with."""

    opaque: frozenset[str]
    map_types: frozenset[str]
    seq_types: frozenset[str]
    wrapper_types: frozenset[str]
    strict: frozenset[str]

    def on(self, feature: str) -> bool:
        """True when a strictness switch is set.

        Args:
            feature: one of _STRICT_FEATURES

        Returns:
            whether the run corrects that annotation's handling
        """
        return feature in self.strict


def _type_set(default: frozenset[str], extra: Sequence[str]) -> frozenset[str]:
    """Adds to a default type set, or subtracts an entry written with a leading '-'.

    One mechanism covers both directions because a Java type name cannot
    contain a hyphen, so a leading one is never part of a name.

    Args:
        default: the built-in set
        extra: caller entries, each added or, when prefixed '-', removed

    Returns:
        the effective set
    """
    out = set(default)
    for entry in extra:
        entry = entry.strip()
        if not entry:
            continue
        if entry.startswith("-"):
            out.discard(entry[1:].strip())
        else:
            out.add(entry)
    return frozenset(out)


# --------------------------------------------------------------------------
# Reading annotations off a field's preceding lines.
#
# Every helper here takes `ann`, the annotation lines preceding one field
# joined by spaces. Java allows whitespace anywhere around the parentheses, the
# parameter name, the '=' and the string literal, so every position is
# \s*-tolerant.
# --------------------------------------------------------------------------


def _has_ann(ann: str, name: str) -> bool:
    """True when annotation @name is present, tolerating whitespace before '('.

    The trailing word boundary is load-bearing: it is what keeps @Key from
    matching a sibling @KeyField.

    Args:
        ann: the joined annotation text
        name: the simple annotation name

    Returns:
        whether the annotation is on the field
    """
    return re.search(r"@\s*%s\b" % name, ann) is not None


def _ann_value(ann: str, *names: str, param: str | None = None) -> str | None:
    """Reads a string annotation argument.

    Args:
        ann: the joined annotation text
        names: annotation names to try, as one alternation
        param: a named parameter to read, or None for the lone-literal form

    Returns:
        the string argument, or None when the annotation or the parameter is
        absent
    """
    alt = "|".join(names)
    if param:
        pattern = r'@\s*(?:%s)\s*\(\s*(?:[^)]*?,\s*)?%s\s*=\s*"([^"]*)"' % (alt, param)
    else:
        pattern = r'@\s*(?:%s)\s*\(\s*"([^"]*)"\s*\)' % alt
    m = re.search(pattern, ann)
    return m.group(1) if m else None


def _ann_flag(ann: str, name: str, param: str) -> bool:
    """True when a boolean annotation parameter is explicitly set to true.

    Args:
        ann: the joined annotation text
        name: the simple annotation name
        param: the boolean parameter

    Returns:
        whether the parameter reads `true`
    """
    return re.search(r'@\s*%s\s*\([^)]*\b%s\s*=\s*true\b' % (name, param), ann) is not None


def _ann_args(ann: str, name: str) -> str | None:
    """The raw text between an annotation's parentheses.

    Args:
        ann: the joined annotation text
        name: the simple annotation name

    Returns:
        the argument text, or None when the annotation is absent or carries no
        parentheses at all
    """
    m = re.search(r"@\s*%s\s*\(([^)]*)\)" % name, ann)
    return m.group(1) if m else None


def _serialized_names(ann: str) -> tuple[str, ...]:
    """Every wire key @SerializedName claims, its value first then its alternates.

    Gson binds a field from `value` and from each `alternate` alike, so all of
    them are keys the one field consumes. Both may be written in either order
    and `alternate` may be a braced array.

    Args:
        ann: the joined annotation text

    Returns:
        the keys, empty when the field carries no @SerializedName
    """
    args = _ann_args(ann, "SerializedName")
    if args is None:
        return ()
    keys: list[str] = []
    named = re.search(r'\bvalue\s*=\s*"([^"]*)"', args)
    lone = re.match(r'\s*"([^"]*)"', args)
    if named:
        keys.append(named.group(1))
    elif lone:
        keys.append(lone.group(1))
    alternate = re.search(r'\balternate\s*=\s*(\{[^}]*\}|"[^"]*")', args)
    if alternate:
        keys.extend(re.findall(r'"([^"]*)"', alternate.group(1)))
    return tuple(keys)


# --------------------------------------------------------------------------
# Java type strings.
# --------------------------------------------------------------------------


def _split_generics(text: str) -> list[str]:
    """Splits the top-level comma-separated arguments of a generic type.

    Args:
        text: the text between the outermost angle brackets

    Returns:
        one entry per top-level argument, nested generics kept whole
    """
    out: list[str] = []
    depth, cur = 0, ""
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _parse_type(text: str) -> tuple[str, list[str]]:
    """Splits a Java type string into its raw name and its generic arguments.

    Args:
        text: the declared type, with any type-use annotations still on it

    Returns:
        (raw name, generic argument strings)
    """
    text = re.sub(r"@\w+(?:\([^)]*\))?", "", text).strip()
    text = text.replace("final ", "").strip()
    m = re.match(r"^([\w.]+)\s*(?:<(.+)>)?\s*$", text)
    if not m:
        return text, []
    raw = m.group(1)
    args = _split_generics(m.group(2)) if m.group(2) else []
    return raw, args


def _content_type(settings: _Settings, type_pair: tuple[str, list[str]]) -> tuple[str, list[str]]:
    """Unwraps Optional and friends down to the type whose fields describe an object.

    Args:
        settings: the run's vocabulary
        type_pair: a (raw name, generic arguments) pair

    Returns:
        the innermost (raw name, generic arguments) pair
    """
    raw, args = type_pair
    simple = raw.split(".")[-1]
    if simple in settings.wrapper_types and args:
        return _content_type(settings, _parse_type(args[0]))
    return raw, args


def _describe(value: object) -> str:
    """Renders a JSON value as the one-line shape a report row carries.

    Args:
        value: the decoded JSON value

    Returns:
        a short description - the size and first keys of an object, the length
        of an array, the type and value of a scalar
    """
    if isinstance(value, dict):
        return "obj{%d} %s" % (len(value), list(value.keys())[:5])
    if isinstance(value, list):
        return "arr[%d]" % len(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str %r" % (value[:40],)
    return "%s %s" % (type(value).__name__, value)


# --------------------------------------------------------------------------
# Parsing a source tree.
#
# Regex over lines, so a field declaration wrapped across two lines matches
# nothing. The regex is not widened to chase that: a multi-line declaration is
# rare and a looser field pattern starts matching statements inside method
# bodies, which is a worse answer than a missing field. What guards it instead
# is the zero-field diagnostic - a class that parsed no fields at all is the
# signature of a scan that missed a whole file.
# --------------------------------------------------------------------------


class _Field:
    """One declared instance field and everything its annotations claim."""

    __slots__ = ("owner", "name", "type", "key", "serialized_names", "serialized_path",
                 "extract", "is_extract", "capture", "explicit_name", "descend",
                 "collapse", "is_key", "flatten", "is_transient")

    def __init__(self, name: str, type_: tuple[str, list[str]]):
        self.owner: _JClass | None = None
        self.name = name
        self.type = type_
        self.key = name
        self.serialized_names: tuple[str, ...] = ()
        self.serialized_path: str | None = None
        self.extract: str | None = None
        self.is_extract = False
        self.capture: str | None = None
        self.explicit_name = False
        self.descend = False
        self.collapse = False
        self.is_key = False
        self.flatten: str | None = None
        # gson excludes a transient field by contract, so it binds no wire key
        # whatever its annotations say. Only the projection reads this: the
        # walk's binding table is pinned byte for byte against captures taken
        # from the script it replaces, and a transient field that DOES claim a
        # key there is a disagreement worth seeing as a diff line rather than
        # one worth silently resolving.
        self.is_transient = False

    def keys(self, settings: _Settings) -> tuple[str, ...]:
        """The wire keys this field consumes.

        Args:
            settings: the run's strictness

        Returns:
            one key normally; several when @SerializedName declares alternates
            and the `names` switch is on
        """
        if not settings.on("names"):
            return (self.key,)
        # @SerializedPath wins over @SerializedName on one field: the path is
        # the chain of wire keys, and the name only spells the flattened key
        # the delegate adapter sees.
        if self.serialized_path is not None:
            return (self.serialized_path,)
        if self.serialized_names:
            return self.serialized_names
        if self.extract is not None:
            return (self.extract,)
        return (self.name,)


class _JClass:
    """One parsed Java type declaration and the fields declared directly in it."""

    __slots__ = ("name", "simple", "file", "fields", "is_enum", "kind", "super_name")

    def __init__(self, name: str, simple: str, file: str, is_enum: bool,
                 kind: str, super_name: str | None = None):
        self.name = name
        self.simple = simple
        self.file = file
        self.fields: list[_Field] = []
        self.is_enum = is_enum
        self.kind = kind
        self.super_name = super_name


def _read_field(line: str, ann: str) -> _Field | None:
    """Builds a field from one declaration line and the annotations above it.

    Args:
        line: the stripped source line
        ann: the annotation lines above it, joined by spaces

    Returns:
        the field, or None when the line declares no instance field
    """
    fm = _FIELD_RE.match(line)
    if not fm or "static" in fm.group("mods"):
        return None
    field = _Field(fm.group("name"), _parse_type(fm.group("rest")))
    field.is_transient = "transient" in fm.group("mods")
    name = _ann_value(ann, "SerializedName", "SerializedPath")
    # @Extract takes a filter as well as a value, so the lone-literal form does
    # not match every site. Without the named-parameter attempt first, an
    # @Extract(value = ..., filter = ...) field falls back to its Java name.
    field.extract = _ann_value(ann, "Extract", param="value") or _ann_value(ann, "Extract")
    # Presence is tracked separately from the value because it is what decides
    # whether the field binds at all, and a form neither value regex reads
    # would otherwise fall through to binding the Java name.
    field.is_extract = _has_ann(ann, "Extract")
    field.serialized_names = _serialized_names(ann)
    field.serialized_path = (_ann_value(ann, "SerializedPath")
                             or _ann_value(ann, "SerializedPath", param="value"))
    field.explicit_name = name is not None or field.extract is not None
    if name is not None:
        field.key = name
    elif field.extract is not None:
        field.key = field.extract
    # A bare @Capture means filter = "", which matches every sibling key.
    if _has_ann(ann, "Capture"):
        declared = _ann_value(ann, "Capture", param="filter")
        field.capture = declared if declared is not None else ""
    field.descend = _ann_flag(ann, "Capture", "descend")
    field.collapse = _has_ann(ann, "Collapse")
    field.is_key = _has_ann(ann, "Key")
    field.flatten = _ann_value(ann, "Flatten") or _ann_value(ann, "Flatten", param="value")
    return field


def _parse_sources(src_root: str) -> _Sources:
    """Parses every .java file under a source root into a class graph.

    Args:
        src_root: the directory to walk

    Returns:
        a _Sources holding the classes, the name registry behind the ambiguity
        ledger, and the parse diagnostics
    """
    classes: dict[str, _JClass] = {}
    registry: dict[str, list[_JClass]] = defaultdict(list)
    empty: list[dict] = []
    unreadable: list[dict] = []
    count = 0

    for path in sorted(glob.glob(os.path.join(src_root, "**/*.java"), recursive=True)):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append({"file": path, "error": str(exc)})
            continue
        # strip block comments so commented-out fields are not parsed
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        declared: list[_JClass] = []
        stack: list[list] = []
        depth, pend = 0, []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if line.startswith("//"):
                continue
            cm = _CLASS_RE.search(line)
            if cm and not line.startswith("*"):
                simple = cm.group("name")
                qualified = ".".join([e[0].simple for e in stack] + [simple])
                cls = _JClass(qualified, simple, path, " enum " in " " + line,
                              cm.group("kind"), cm.group("super"))
                classes.setdefault(qualified, cls)
                classes.setdefault(simple, cls)
                registry[qualified].append(cls)
                if simple != qualified:
                    registry[simple].append(cls)
                declared.append(cls)
                # remember the depth the class was opened at so method bodies
                # closing inside it do not pop it off the stack
                stack.append([cls, depth, False])
                depth += line.count("{") - line.count("}")
                pend = []
                continue

            if line.startswith("@"):
                pend.append(line)
            else:
                field = _read_field(line, " ".join(pend)) if stack else None
                if field is not None:
                    field.owner = stack[-1][0]
                    stack[-1][0].fields.append(field)
                if line and not line.startswith("*"):
                    pend = []

            depth += line.count("{") - line.count("}")
            while stack:
                if depth > stack[-1][1]:
                    stack[-1][2] = True
                    break
                if stack[-1][2]:
                    stack.pop()
                else:
                    break

        # An enum body is never walked and an interface holds no state, so
        # neither says anything by having no fields. A class and a record do:
        # the class because a field declaration the line regex could not see is
        # exactly what this looks like, the record because its components live
        # in the header where the regex does not look at all, so a record used
        # as a bound type reports every key it holds unmapped. The kind rides
        # along because it is what tells those two rows apart.
        empty.extend({"class": cls.name, "kind": cls.kind, "file": cls.file}
                     for cls in declared
                     if cls.kind in ("class", "record") and not cls.fields)
        count += len(declared)

    return _Sources(classes, registry, count, empty, unreadable)


# --------------------------------------------------------------------------
# Resolving a type name, and the ambiguity ledger.
# --------------------------------------------------------------------------


class _Sources:
    """A parsed class graph, the names it registered, and its parse diagnostics."""

    __slots__ = ("classes", "registry", "count", "empty_classes", "unreadable", "ambiguous")

    def __init__(self, classes: dict[str, _JClass], registry: dict[str, list[_JClass]],
                 count: int, empty_classes: list[dict], unreadable: list[dict]):
        self.classes = classes
        self.registry = registry
        self.count = count
        self.empty_classes = empty_classes
        self.unreadable = unreadable
        self.ambiguous: dict[str, dict] = {}

    def resolve(self, raw: str, ctx: _JClass | None, opaque: frozenset[str]) -> _JClass | None:
        """Resolves a type name, preferring classes nested in or declared beside ctx.

        Args:
            raw: the declared type name, qualified or simple
            ctx: the class the name was written in, or None
            opaque: type names the walk never descends into

        Returns:
            the class, or None when the name is opaque or nothing declares it
        """
        simple = raw.split(".")[-1]
        if raw in opaque or simple in opaque:
            return None
        if ctx is not None:
            # innermost-first: Outer.Inner.Target, then Outer.Target, then same file
            parts = ctx.name.split(".")
            for i in range(len(parts), 0, -1):
                hit = self.classes.get(".".join(parts[:i]) + "." + raw)
                if hit is not None:
                    return hit
            for cls in self.classes.values():
                if cls.file == ctx.file and cls.simple == simple:
                    return cls
        # The global fallback, and the one lookup that can be wrong without
        # saying so. Simple names collide across files - two nested `Profile`
        # classes, say - and every class registers under its simple name with
        # setdefault, so the winner is whichever file sorted first. The order
        # here is unchanged from the context-scoped lookups above; what a
        # collision adds is a ledger entry, because walking the right JSON
        # against the wrong class reports its keys unmapped and reads exactly
        # like a DTO gap.
        for key in (raw, simple):
            hit = self.classes.get(key)
            if hit is not None:
                self.note_ambiguity(key, hit)
                return hit
        return None

    def note_ambiguity(self, key: str, chosen: _JClass) -> None:
        """Records a global hit that had more than one candidate.

        Args:
            key: the name that was looked up
            chosen: the class the lookup returned
        """
        # Two nested classes of one name in one file collide the same way two
        # files do, so the count of candidates decides rather than the count of
        # files.
        candidates = self.registry.get(key, ())
        if len(candidates) < 2:
            return
        self.ambiguous.setdefault(key, {
            "name": key,
            "chosen": chosen.name,
            "chosen_file": chosen.file,
            "candidates": [{"class": cls.name, "file": cls.file} for cls in candidates],
        })


# --------------------------------------------------------------------------
# The parallel walk.
#
# One JSON node and one class descend together. A key the class binds is
# mapped, a key nothing binds is reported, and the value is walked against
# whatever the field's declared type says is inside it.
# --------------------------------------------------------------------------


class _Auditor:
    """Walks a JSON node and a class graph together, collecting the verdicts."""

    def __init__(self, sources: _Sources, settings: _Settings,
                 show_mapped: bool = False, max_depth: int = 12):
        self.sources = sources
        self.settings = settings
        self.show_mapped = show_mapped
        self.max_depth = max_depth
        self.missing: list[tuple[str, str]] = []
        self.mapped: list[str] = []
        self.mapped_count = 0
        self.unresolved: set[str] = set()
        self.mismatches: list[dict] = []

    def _resolve(self, raw: str, ctx: _JClass | None) -> _JClass | None:
        """Resolves a type name against the run's opaque set.

        Args:
            raw: the declared type name
            ctx: the class the name was written in

        Returns:
            the class, or None
        """
        return self.sources.resolve(raw, ctx, self.settings.opaque)

    def _note_mapped(self, path: str, label: str, field: _Field, suffix: str = "") -> None:
        """Counts a bound key, and records it when the caller asked for the list.

        Args:
            path: the JSON path of the key
            label: the binding class's name
            field: the field that binds it
            suffix: a marker naming how it bound, when it was not by name
        """
        self.mapped_count += 1
        if self.show_mapped:
            self.mapped.append("%s -> %s.%s%s" % (path, label, field.name, suffix))

    def inherited_fields(self, cls: _JClass) -> list[_Field]:
        """Fields declared on cls plus every superclass in the chain.

        Args:
            cls: the class to collect from

        Returns:
            the fields, subclass first
        """
        fields: list[_Field] = []
        seen: set[str] = set()
        cur: _JClass | None = cls
        while cur is not None and cur.name not in seen:
            seen.add(cur.name)
            fields.extend(cur.fields)
            cur = self._resolve(cur.super_name, cur) if cur.super_name else None
        return fields

    def _is_capture(self, field: _Field) -> bool:
        """Whether a field gathers sibling keys rather than binding its own.

        Args:
            field: the field to classify

        Returns:
            True when the field's filter applies to the declaring class's own
            sibling keys
        """
        if field.capture is None:
            return False
        if self.settings.on("capture"):
            # A descend capture consumes its own named key and filters the keys
            # INSIDE it; every other @Capture gathers siblings, and its
            # @SerializedName binds nothing because the factory's known-key
            # discovery skips capture fields outright.
            return not field.descend
        # Without the switch, descent is inferred from the field carrying an
        # explicit name, which also makes that name bind.
        return not field.explicit_name

    def _order_captures(self, captures: list[tuple[re.Pattern, _Field]]
                        ) -> list[tuple[re.Pattern, _Field]]:
        """Puts the catch-all capture last.

        Args:
            captures: the captures in declaration order

        Returns:
            the order the factory tries them in, when the switch is on
        """
        if not self.settings.on("capture"):
            return captures
        return sorted(captures, key=lambda pair: pair[0].pattern == "")

    def bindings(self, cls: _JClass) -> tuple[dict[str, list], list[tuple[re.Pattern, _Field]]]:
        """The wire keys a class binds, split into named bindings and captures.

        Args:
            cls: the class to read

        Returns:
            (key -> [(remaining path, field)], [(compiled filter, field)])
        """
        direct: dict[str, list] = defaultdict(list)
        captures: list[tuple[re.Pattern, _Field]] = []
        for f in self.inherited_fields(cls):
            # A @Key field is injected from the enclosing @Collapse entry's key
            # and is never a key inside the value object.
            if f.is_key:
                continue
            # An @Extract field is fed from another field's overflow, so it
            # binds no wire key of its own.
            if self.settings.on("extract") and f.is_extract:
                continue
            if self._is_capture(f):
                captures.append((re.compile(f.capture), f))
                continue
            for key in f.keys(self.settings):
                parts = key.split(".") if key else [""]
                direct[parts[0]].append((tuple(parts[1:]), f))
        return direct, self._order_captures(captures)

    def walk_class(self, node: object, cls: _JClass | None, path: str, depth: int) -> None:
        """Walks a JSON object against a class.

        Args:
            node: the JSON value at this path
            cls: the class that binds it
            path: the report path of this node
            depth: how far the walk has descended
        """
        if cls is None or cls.is_enum:
            return
        direct, captures = self.bindings(cls)
        self.walk_bindings(node, direct, captures, path, depth, cls.name)

    def walk_bindings(self, node: object, direct: dict[str, list],
                      captures: list[tuple[re.Pattern, _Field]], path: str,
                      depth: int, label: str) -> None:
        """Classifies every key of one JSON object against a binding table.

        Args:
            node: the JSON value at this path
            direct: key -> [(remaining path, field)]
            captures: the compiled sibling captures, in the order they are tried
            path: the report path of this node
            depth: how far the walk has descended
            label: the binding class's name, for the mapped rows
        """
        if depth > self.max_depth or not isinstance(node, dict):
            return
        for key, value in node.items():
            child = "%s.%s" % (path, key)
            if key in direct:
                bound = direct[key]
                leaf = [f for rem, f in bound if not rem]
                nested = [(rem, f) for rem, f in bound if rem]
                # A leaf binding at this level wins over a nested one: the field
                # whose whole key is this segment consumes it.
                if leaf:
                    self._note_mapped(child, label, leaf[0])
                    self.descend(value, leaf[0], child, depth)
                elif nested:
                    # A @SerializedPath prefix is a find-or-create, so every
                    # field sharing it merges into one sub-table and the object
                    # is entered once.
                    merged: dict[str, list] = defaultdict(list)
                    for rem, f in nested:
                        merged[rem[0]].append((rem[1:], f))
                    self.walk_bindings(value, merged, [], child, depth + 1, label)
            else:
                hit = next((f for rx, f in captures if rx.search(key)), None)
                if hit:
                    # Affix grouping is not modelled. In grouping mode the
                    # factory strips the matched portion of the key and folds
                    # the remainder onto the value class by prefix, suffix or
                    # bare field, and a stripped key answering to none of those
                    # it drops outright. Every key the filter matches is counted
                    # mapped here, so such a key reads bound where the decode
                    # throws it away.
                    self._note_mapped(child, label, hit, " (@Capture)")
                    # The key just matched IS the level the wire spends on the
                    # capture's collection, so what sits under it is one entry.
                    # Off, the whole declared collection is walked against that
                    # one entry, which reaches an entry's own keys nowhere: on
                    # the hypixel capture that leaves 414 keys under a
                    # class-valued capture classified in neither direction.
                    self.descend(value, hit, child, depth,
                                 consumed=self.settings.on("capture"))
                else:
                    self.missing.append((child, _describe(value)))

    def descend(self, value: object, field: _Field, path: str, depth: int,
                consumed: bool = False) -> None:
        """Walks a bound value against what the field's declared type holds.

        Args:
            value: the JSON value the field bound
            field: the field that bound it
            path: the report path of the value
            depth: how far the walk has descended
            consumed: whether the outermost level of the declared collection is
                already spent, which is what a matched @Capture key is
        """
        if depth > self.max_depth:
            return
        if isinstance(value, dict) and self._descends_inside(field):
            value = self._filter_inner(value, field, path)
        raw, args = _content_type(self.settings, field.type)
        simple = raw.split(".")[-1]
        if consumed:
            # `_java_shape` drops the same level, so the two halves of one
            # result describe the entry at the same depth.
            inside = self._inside(simple, args)
            if inside is not None:
                raw, args = inside
                simple = raw.split(".")[-1]
        # @Collapse describes the field's OWN node, which a spent level is no
        # longer, so it cannot answer for the level underneath it either.
        collapse = field.collapse and not consumed

        if simple in self.settings.map_types and args:
            # The mirror of the sequence shape check below is not made: gson's
            # own map adapter reads an array of entry pairs, so a JSON array
            # against a declared map is a shape the decode accepts.
            val_raw, _ = _content_type(self.settings, _parse_type(args[-1]))
            target = self._resolve(val_raw, field.owner)
            if target and isinstance(value, dict):
                for k, v in value.items():
                    self.walk_class(self._unwrap(v, field), target,
                                    "%s.<%s>" % (path, k), depth + 1)
            return

        if simple in self.settings.seq_types and args:
            # The shape question is asked before the element type resolves: a
            # JSON object where a collection is declared is a decode that throws
            # whatever the element type turns out to be, and an opaque element
            # returns here otherwise.
            items = self._sequence_items(value, field, path, collapse)
            el_raw, _ = _content_type(self.settings, _parse_type(args[0]))
            target = self._resolve(el_raw, field.owner)
            if items is None or target is None:
                return
            for i, item in enumerate(items):
                item = self._unwrap(item, field)
                if isinstance(item, dict):
                    self.walk_class(item, target, "%s[%d]" % (path, i), depth + 1)
            return

        target = self._resolve(raw, field.owner)
        if target is None:
            if isinstance(value, dict) and simple not in self.settings.opaque:
                self.unresolved.add("%s (%s)" % (path, raw))
            return
        if isinstance(value, dict):
            self.walk_class(value, target, path, depth + 1)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    self.walk_class(item, target, "%s[%d]" % (path, i), depth + 1)

    def _inside(self, simple: str, args: list[str]) -> tuple[str, list[str]] | None:
        """The type one level inside a declared collection.

        Args:
            simple: the collection's simple name
            args: its generic arguments

        Returns:
            the (raw name, generic arguments) pair the level holds, or None when
            the type spends no level of its own
        """
        if not args:
            return None
        if simple in self.settings.map_types:
            return _content_type(self.settings, _parse_type(args[-1]))
        if simple in self.settings.seq_types:
            return _content_type(self.settings, _parse_type(args[0]))
        return None

    def _descends_inside(self, field: _Field) -> bool:
        """Whether a capture filters the keys inside the field's own node.

        Args:
            field: the field being descended into

        Returns:
            True for a @Capture(descend = true) carrying a filter
        """
        return bool(self.settings.on("capture") and field.capture and field.descend)

    def _filter_inner(self, value: dict, field: _Field, path: str) -> dict:
        """Applies a descend capture's filter to the keys inside its own node.

        An inner key the filter rejects is dropped outright by the factory -
        not bound and not overflowed - so it is reported.

        Args:
            value: the object the field bound
            field: the descend capture
            path: the report path of the object

        Returns:
            the entries the filter selected
        """
        matcher = re.compile(field.capture)
        kept = {}
        for key, inner in value.items():
            if matcher.search(key):
                kept[key] = inner
            else:
                self.missing.append(("%s.%s" % (path, key), _describe(inner)))
        return kept

    def _sequence_items(self, value: object, field: _Field, path: str,
                        collapse: bool) -> Iterable | None:
        """The elements to walk for a sequence-typed field.

        Args:
            value: the JSON value the field bound
            field: the sequence field
            path: the report path of the value
            collapse: whether @Collapse answers for the level being walked

        Returns:
            the elements, or None when there are none to walk
        """
        if isinstance(value, dict):
            if collapse or not self.settings.on("collapse"):
                # @Collapse makes the wire node an object whose KEYS are entry
                # identifiers, so its values are the collection's elements.
                return value.values()
            # Without it the collection adapter throws at decode time, and
            # walking the values anyway reports the subtree fully mapped - a
            # clean verdict over a decode that cannot run.
            self.mismatches.append({
                "path": path,
                "field": "%s.%s" % (field.owner.name if field.owner else "?", field.name),
                "detail": "sequence field bound a JSON object and carries no @Collapse",
            })
            return None
        return value if isinstance(value, list) else None

    def _unwrap(self, entry: object, field: _Field) -> object:
        """Steps through the wrapper level @Flatten names inside one entry.

        An entry that is already collapsed, or whose wrapper lacks the named
        member, is left alone - which is what the factory does. A sibling of the
        named member is not reported: it is dropped on write by declared
        contract rather than lost.

        Args:
            entry: one map value or list element
            field: the field that bound the collection

        Returns:
            what the declared element type actually describes
        """
        if not self.settings.on("flatten") or field.flatten is None:
            return entry
        if isinstance(entry, dict) and field.flatten in entry:
            return entry[field.flatten]
        return entry


# --------------------------------------------------------------------------
# Projecting both sides onto one line grammar.
#
# The walk above descends and reports in one pass, so it never holds either
# side whole and it answers one direction: the JSON keys no field binds. The
# other direction - the fields no JSON key reaches - needs both sides
# materialised, so it is built here, beside the walk rather than inside it.
#
# Both projections emit PATHS and nothing else. No type, no value, no count:
# the JSON side knows `int 5` where the Java side knows `Integer`, and a line
# carrying anything the two sides derive separately manufactures a finding out
# of a formatting difference. `path_line` is therefore the one place they have
# to agree, and both call it.
#
# A segment is a key, `[]` for an array element, or `{}` for any key at that
# level - a map's entries or the siblings a @Capture claims. Depth is the
# number of segments on both sides, which is what makes a max_depth cut land in
# the same place on each.
# --------------------------------------------------------------------------

#: An array element, on either side.
_ELEMENT = "[]"
#: Any key at this level: a map entry's key, or a sibling a @Capture claims.
_ANY_KEY = "{}"
_WILDCARDS = (_ELEMENT, _ANY_KEY)


def path_line(label: str, segments: Sequence[str]) -> str:
    """Renders one path in the grammar both projections share.

    Args:
        label: the root the path hangs off, which is the walk's own root label
        segments: the path, each entry a key, `[]` or `{}`

    Returns:
        the line, a wildcard written tight against what it sits under and a key
        written with a leading dot
    """
    out = label
    for segment in segments:
        out += segment if segment in _WILDCARDS else "." + segment
    return out


def _json_paths(node: object, max_depth: int) -> set[tuple[str, ...]]:
    """Every path the document holds, as segment tuples.

    An array contributes one `[]` path however many elements it has, and its
    elements are merged onto that one path, so a capture of a thousand entries
    projects the shape once. An empty array still contributes its `[]`: the
    array is there, and what is missing is only what was inside it.

    Args:
        node: the decoded JSON node the audit started from
        max_depth: the segment count a path is cut at

    Returns:
        the paths
    """
    paths: set[tuple[str, ...]] = set()

    def visit(value: object, segments: tuple[str, ...]) -> None:
        if len(segments) >= max_depth:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_segments = segments + (key,)
                paths.add(child_segments)
                visit(child, child_segments)
        elif isinstance(value, list):
            element = segments + (_ELEMENT,)
            paths.add(element)
            for item in value:
                visit(item, element)

    visit(node, ())
    return paths


def project_json(node: object, label: str, max_depth: int = 12) -> list[str]:
    """Projects a JSON document onto sorted lines in the shared grammar.

    Args:
        node: the decoded JSON node the audit started from.
        label: the root label every line opens with.
        max_depth: the segment count a path is cut at.

    Returns:
        the lines, sorted and unique - the left side of the diff.
    """
    return sorted(path_line(label, path) for path in _json_paths(node, max_depth))


class _Shape:
    """The paths a class graph can bind, and the filters behind its wildcards."""

    __slots__ = ("paths", "captures")

    def __init__(self) -> None:
        self.paths: set[tuple[str, ...]] = set()
        # prefix -> the compiled filters of the captures that put a `{}` there.
        # A map's `{}` registers nothing, because a map claims every key; a
        # capture's claim is exactly its filter, so alignment asks the filter
        # rather than assuming the whole level.
        self.captures: dict[tuple[str, ...], list[re.Pattern]] = defaultdict(list)


def _java_shape(audit: _Auditor, cls: _JClass, max_depth: int) -> _Shape:
    """Walks a class graph alone and collects every path it can bind.

    The binding rules are the auditor's own - the same inherited fields, the
    same @Key and @Extract exclusions, the same capture classification, the
    same wrapper unwrapping - because a projection that decided any of them for
    itself would report the difference between two parsers as a finding about
    the wire.

    Recursion terminates on ``max_depth``, the same cut the JSON side takes, so
    a self-referential DTO projects as deep as the document is read. A class
    already open on this path is pruned only where it recurs having spent no
    segment at all, which is the one shape that has no cut to reach; a type
    that legitimately appears under two different fields is projected under
    both.

    Args:
        audit: the auditor whose settings and resolution the projection borrows
        cls: the class the walk starts from
        max_depth: the segment count a path is cut at

    Returns:
        the shape
    """
    shape = _Shape()
    settings = audit.settings

    def unwrap(type_pair: tuple[str, list[str]], field: _Field,
               outermost: bool) -> tuple[list[str], str]:
        """The wildcard segments a declared type spends, and what is inside them.

        A collection of collections spends one level each, so
        Map<String, List<Run>> is `{}[]` and the class walked underneath is
        Run. Reading only the outer level leaves the inner one unaccounted for
        and every key below it looks like a key nothing binds.
        """
        raw, args = _content_type(settings, type_pair)
        simple = raw.split(".")[-1]
        if simple in settings.map_types and args:
            segment, inside = _ANY_KEY, _parse_type(args[-1])
        elif simple in settings.seq_types and args:
            # @Collapse says the wire node is an OBJECT whose keys identify the
            # entries, so an element sits under a key rather than at an index.
            # Projecting one as an array reports every entry of it twice - once
            # as a key nothing binds and once as an element nothing feeds. It
            # describes the field's own node, so only the outermost level.
            segment = _ANY_KEY if (field.collapse and outermost) else _ELEMENT
            inside = _parse_type(args[0])
        else:
            return [], raw
        rest, target = unwrap(inside, field, False)
        return [segment] + rest, target

    def descend(field: _Field, segments: tuple[str, ...],
                chain: frozenset[tuple[str, int]], consumed: bool = False) -> None:
        """Adds the levels one field's type spends and walks what is inside it."""
        if len(segments) >= max_depth:
            return
        levels, target = unwrap(field.type, field, True)
        # A capture's collection is where the matched siblings ACCUMULATE, and
        # the key it matched is already the level the wire spends on it, so the
        # node under that key is one entry rather than the whole collection.
        if consumed and levels:
            levels = levels[1:]
        # @Flatten names a wrapper level the wire puts inside each entry, so
        # the element type describes what is under that key rather than the
        # entry itself.
        if levels and settings.on("flatten") and field.flatten:
            levels = levels + [field.flatten]
        inner = segments
        for level in levels:
            # The cut is tested per level rather than once: a type spending
            # several of them would otherwise reach past a depth the JSON side
            # was cut at, and every segment past the cut reads as a phantom of
            # the cut's own making.
            if len(inner) >= max_depth:
                return
            inner = inner + (level,)
            shape.paths.add(inner)
        visit(audit._resolve(target, field.owner), inner, chain)

    def visit(owner: _JClass | None, prefix: tuple[str, ...],
              chain: frozenset[tuple[str, int]]) -> None:
        """Adds every key one class binds, under the prefix it was reached at."""
        if owner is None or owner.is_enum or len(prefix) >= max_depth:
            return
        # A class is pruned only where it recurs having spent no segment, which
        # is the one shape that cannot terminate. A self-referential DTO does
        # spend one per level, so it projects to the depth the JSON side is also
        # cut at - pruning it at the first repeat instead reports the wire's own
        # second level as a path nothing can bind, which the walk contradicts.
        here = (owner.name, len(prefix))
        if here in chain:
            return
        chain = chain | {here}
        for field in audit.inherited_fields(owner):
            # A memo an accessor fills, not a binding: gson never reaches it,
            # so a path under it would be a phantom of this tool's own making.
            if field.is_key or field.is_transient:
                continue
            if settings.on("extract") and field.is_extract:
                continue
            if audit._is_capture(field):
                shape.captures[prefix].append(re.compile(field.capture))
                wild = prefix + (_ANY_KEY,)
                shape.paths.add(wild)
                descend(field, wild, chain, consumed=True)
                continue
            for key in field.keys(settings):
                segments = prefix
                # Every level of a dotted @SerializedPath is a wire object of
                # its own, so each prefix is a path the JSON side also holds.
                for part in key.split(".") if key else ():
                    if len(segments) >= max_depth:
                        break
                    segments = segments + (part,)
                    shape.paths.add(segments)
                descend(field, segments, chain)

    visit(cls, (), frozenset())
    return shape


def project_java(root_class: _JClass, label: str, audit: _Auditor,
                 max_depth: int = 12) -> list[str]:
    """Projects a class graph onto sorted lines in the shared grammar.

    Args:
        root_class: the class the walk starts from.
        label: the root label every line opens with.
        audit: the auditor whose settings and type resolution the walk borrows.
        max_depth: the segment count a path is cut at.

    Returns:
        the lines, sorted and unique - the right side of the diff.
    """
    return sorted(path_line(label, path)
                  for path in _java_shape(audit, root_class, max_depth).paths)


def _align(left: set[tuple[str, ...]], shape: _Shape) -> set[tuple[str, ...]]:
    """Collapses the document's concrete keys onto the wildcards that claim them.

    A map key and a captured sibling are data on the wire and structure in the
    Java, so comparing them literally reports every entry of every map twice -
    once as a key nothing binds and once as a field nothing feeds. A key is
    rewritten to `{}` only where the class graph put a `{}` at that exact
    prefix AND, when the wildcard came from a @Capture, its filter matches -
    which is the same predicate the walk applies.

    Once a prefix leaves the class graph nothing below it can be rewritten, so
    an unmapped subtree stays whole and concrete.

    Args:
        left: the document's paths
        shape: the class graph's paths and capture filters

    Returns:
        the document's paths, rewritten
    """
    out: set[tuple[str, ...]] = set()
    for path in left:
        aligned: tuple[str, ...] = ()
        for segment in path:
            concrete = aligned + (segment,)
            wild = aligned + (_ANY_KEY,)
            if (segment not in _WILDCARDS and concrete not in shape.paths
                    and wild in shape.paths
                    and _wildcard_admits(shape.captures.get(aligned), segment)):
                aligned = wild
            else:
                aligned = concrete
        out.add(aligned)
    return out


def _wildcard_admits(filters: list[re.Pattern] | None, key: str) -> bool:
    """Whether the wildcard at a prefix claims one concrete key.

    Args:
        filters: the capture filters registered at the prefix, or None when the
            wildcard is a map's
        key: the document's key

    Returns:
        True for a map, which claims every key, and for a capture whose filter
        matches - and False for a capture that rejects it, because a rejected
        key is dropped by the factory and is a finding rather than an entry
    """
    if not filters:
        return True
    return any(rx.search(key) for rx in filters)


# --------------------------------------------------------------------------
# Selecting the node to audit.
#
# Three narrowings, applied in order: `node` picks one subtree, `union` merges
# many into one template, `section` keeps one top-level key of the result.
# --------------------------------------------------------------------------


def _segments(expr: str) -> list[str]:
    """Splits a path expression into segments.

    Args:
        expr: a dotted path expression, possibly empty

    Returns:
        the segments, empty for an empty expression
    """
    return expr.split(".") if expr else []


def _step(node: object, segment: str) -> tuple[object, bool]:
    """Takes one plain segment of a path expression.

    Args:
        node: the value to step from
        segment: an object key, or a list index when every character is a digit

    Returns:
        (the child, True) or (None, False) when the segment names nothing
    """
    if isinstance(node, dict) and segment in node:
        return node[segment], True
    if isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
        return node[int(segment)], True
    return None, False


def _select_node(doc: object, expr: str) -> tuple[object, str | None]:
    """Narrows a document to the single node a path expression names.

    Args:
        doc: the decoded document
        expr: a dotted path, empty for the document itself

    Returns:
        (the node, None), or (None, the segment that named nothing)
    """
    node = doc
    for segment in _segments(expr):
        node, found = _step(node, segment)
        if not found:
            return None, segment
    return node, None


def _select_all(doc: object, expr: str) -> list:
    """Collects every node a path expression matches.

    Wildcards fan out: `[]` takes every element of an array and `{}` takes
    every value of an object. A branch where a segment names nothing is
    dropped rather than raising, because a fixture where one sample lacks a
    subtree is the ordinary case this exists for.

    Args:
        doc: the decoded document
        expr: a dotted path expression

    Returns:
        the matched nodes, in document order
    """
    nodes: list = [doc]
    for segment in _segments(expr):
        nxt: list = []
        for node in nodes:
            if segment == "[]":
                if isinstance(node, list):
                    nxt.extend(node)
            elif segment == "{}":
                if isinstance(node, dict):
                    nxt.extend(node.values())
            else:
                child, found = _step(node, segment)
                if found:
                    nxt.append(child)
        nodes = nxt
        if not nodes:
            return []
    return nodes


def _merge_into(dst: dict, src: dict) -> None:
    """Folds one sample into a template object.

    A scalar is taken from the first sample that carries a non-null one, an
    object is merged recursively, and a list of objects collapses onto a single
    element-0 template - so the union walks each shape once rather than once
    per sample.

    Args:
        dst: the template being built
        src: one sample
    """
    for key, value in src.items():
        if isinstance(value, dict):
            if not isinstance(dst.get(key), dict):
                dst[key] = {}
            _merge_into(dst[key], value)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            if not isinstance(dst.get(key), list) or not dst[key]:
                dst[key] = [{}]
            for item in value:
                if isinstance(item, dict):
                    _merge_into(dst[key][0], item)
        elif key not in dst or dst[key] is None:
            dst[key] = value


def _union(nodes: Sequence) -> dict:
    """Merges every matched node into one template object.

    Args:
        nodes: the nodes a union expression matched

    Returns:
        the template, holding every key any sample carried
    """
    template: dict = {}
    for node in nodes:
        if isinstance(node, dict):
            _merge_into(template, node)
    return template


def _section_of(path: str, label: str) -> str:
    """The section a reported path is grouped under.

    Args:
        path: the report path, which opens with the root label
        label: the root label the walk started from

    Returns:
        the first path segment after the label, or the whole path when it has
        no segment after it
    """
    if path.startswith(label + "."):
        rest = path[len(label) + 1:]
    elif "." in path:
        rest = path.split(".", 1)[1]
    else:
        return path
    return rest.split(".", 1)[0]


# --------------------------------------------------------------------------
# The public surface.
# --------------------------------------------------------------------------


def _capped(items: Sequence, cap: int) -> tuple[list, bool]:
    """Cuts a list to the row cap and says whether it was cut.

    Args:
        items: the rows
        cap: the row cap, 0 for uncapped

    Returns:
        (the kept rows, whether anything was dropped)
    """
    if cap > 0 and len(items) > cap:
        return list(items[:cap]), True
    return list(items), False


def _precondition(echo: dict, message: str) -> dict:
    """Builds the result of a run that never audited anything.

    Args:
        echo: the inputs, echoed
        message: one sentence naming what was not found and where it looked

    Returns:
        the result dict, which exit_code maps onto 2
    """
    return {**echo, "ok": False, "status": "precondition", "error": message}


def _strict_features(strict: Sequence[str]) -> tuple[set[str], list[str]]:
    """Expands the strictness argument.

    Args:
        strict: the requested feature names, or "all"

    Returns:
        (the features to apply, the names that are not features)
    """
    wanted: set[str] = set()
    unknown: list[str] = []
    for feature in strict:
        feature = feature.strip()
        if not feature:
            continue
        if feature == "all":
            wanted |= set(_STRICT_FEATURES)
        elif feature in _STRICT_FEATURES:
            wanted.add(feature)
        else:
            unknown.append(feature)
    return wanted, unknown


def json_diff(
    json_path: str,
    src: str,
    root: str,
    node: str = "",
    union: str = "",
    section: str | None = None,
    show_mapped: bool = False,
    show_unresolved: bool = False,
    max_depth: int = 12,
    cap: int = 200,
    opaque: Sequence[str] = (),
    map_types: Sequence[str] = (),
    seq_types: Sequence[str] = (),
    wrapper_types: Sequence[str] = (),
    strict: Sequence[str] = (),
    phantom: bool = False,
    fail_on_phantom: bool = False,
) -> dict:
    """Walks a JSON capture against a Java class graph and reports unmapped keys.

    Args:
        json_path: the JSON capture to audit.
        src: the Java source root whose classes are parsed.
        root: the class the walk starts from, by simple or nested name.
        node: dotted path narrowing the document to one subtree. Empty audits
            the document itself.
        union: path expression whose matched nodes are merged into one template
            before the walk, so a key optional in every sample is still
            audited. `[]` matches every array element and `{}` every object
            value.
        section: keep only this top-level key of the node. It narrows the
            capture alone, so it is refused together with phantom.
        show_mapped: also return the keys a field does map.
        show_unresolved: also return object-valued fields whose type did not
            parse.
        max_depth: how deep the parallel walk descends.
        cap: rows per returned list. 0 is uncapped.
        opaque: type names to add to the built-in never-descend set. An entry
            written with a leading '-' is removed from it instead.
        map_types: collection type names whose LAST type argument describes a
            JSON object's values. Added and subtracted like opaque.
        seq_types: collection type names whose FIRST type argument describes an
            element. Added and subtracted like opaque.
        wrapper_types: type names unwrapped to the type inside them. Added and
            subtracted like opaque.
        strict: strictness switches to turn on, or "all". Each corrects one
            annotation's handling and each is capable of changing the verdict,
            so all of them default off.
        phantom: also project both sides and report the fields no JSON key
            reaches. Off by default because it is a second walk over the whole
            class graph and because the projection it returns is unbounded. It
            projects from `root`, which `section` does not narrow, so the two
            together are a precondition failure rather than a report.
        fail_on_phantom: let that second direction decide the exit code.

    Returns:
        The inputs echoed - json, src, root, node, union, section, show_mapped,
        show_unresolved, max_depth, strict - then ok, the counts unmapped /
        mapped / unresolved, and
        sections: one entry per first path segment, each carrying count and a
        capped keys list of path/kind rows.

        ok is True only when nothing was reported, which means no unmapped key
        AND no shape mismatch: a sequence field that bound a JSON object cannot
        decode at all, so it is a red verdict even though it names no key.

        Every list is capped at `cap` rows while the count beside it is whole,
        so truncated is what says a list is short. mapped_keys is populated
        only under show_mapped and unresolved_types only under show_unresolved;
        their counts are reported either way.

        Three diagnostics ride along and none of them is a finding about the
        JSON: empty_classes names classes and records that parsed no fields at
        all, which is what a declaration the line regex could not see looks
        like; ambiguous_types names type names that resolved globally with more
        than one candidate, where the winner is whichever file sorted first;
        unreadable_files names sources that did not decode.

        Under phantom, three more: phantoms, the capped list of paths the class
        graph can bind and the document never fed; phantom_total beside it; and
        projection, holding the two whole sorted line lists the unified diff and
        the editor hand-off are built from. That projection is the one place
        bulk is allowed into the return, because capping it would drop lines
        from one side and manufacture findings on the other.

        ok never moves for a phantom. It answers the question the wire asks -
        keys arrived that nothing binds - and a field nothing feeds is a
        different question a caller opts into by asking for the exit code to
        follow it.

        A precondition failure - unreadable capture, source root that parsed no
        classes, unknown root class, path expression that matches nothing -
        sets status "precondition" and a top-level error, and reports no
        counts at all.
    """
    features, unknown = _strict_features(strict)
    echo = {
        "json": os.path.abspath(json_path),
        "src": os.path.abspath(src),
        "root": root,
        "node": node,
        "union": union,
        "section": section,
        # Echoed because a renderer reads the dict and nothing else, and an
        # empty mapped_keys otherwise reads the same whether the list was not
        # asked for or was asked for and came back empty. The MAPPED block is
        # printed on the strength of the request, not of the count.
        "show_mapped": show_mapped,
        "show_unresolved": show_unresolved,
        "max_depth": max_depth,
        "strict": sorted(features),
        "phantom": phantom,
        "fail_on_phantom": fail_on_phantom,
    }
    if unknown:
        return _precondition(echo, "unknown strictness %s; known: %s"
                             % (", ".join(sorted(unknown)), ", ".join(sorted(_STRICT_FEATURES))))
    if section and (phantom or fail_on_phantom):
        # A section narrows the capture and nothing else - the class graph is
        # still projected from the root - so every field outside it comes back
        # as a phantom of the narrowing. Refusing is the only honest answer
        # available: reporting them would be an answer to a question nobody
        # asked, and dropping them silently would hide the real ones.
        return _precondition(echo, f"section '{section}' narrows the capture but not the"
                                   f" class graph, so every field outside it would read"
                                   f" as a phantom; project the whole node instead")

    settings = _Settings(
        opaque=_type_set(_OPAQUE, opaque),
        map_types=_type_set(_MAP_TYPES, map_types),
        seq_types=_type_set(_SEQ_TYPES, seq_types),
        wrapper_types=_type_set(_WRAPPER_TYPES, wrapper_types),
        strict=frozenset(features),
    )

    try:
        with open(json_path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError as exc:
        return _precondition(echo, f"capture '{json_path}' not readable ({exc})")
    except json.JSONDecodeError as exc:
        return _precondition(echo, f"capture '{json_path}' is not JSON ({exc})")

    if not os.path.isdir(src):
        return _precondition(echo, f"source root '{src}' is not a directory")

    target, failed = _select_node(doc, node)
    if failed is not None:
        return _precondition(echo, f"node path '{node}' names nothing at segment '{failed}'")

    matched = 0
    if union:
        nodes = _select_all(target, union)
        if not nodes:
            return _precondition(echo, f"union expression '{union}' matched nothing"
                                       f" under node '{node or '(document)'}'")
        matched = len(nodes)
        target = _union(nodes)
        # _union keeps only the objects, so an expression landing on scalars
        # merges to an empty template and the walk reads every key of nothing:
        # zero unmapped, a clean verdict, and no class ever examined.
        if not any(isinstance(sample, dict) for sample in nodes):
            return _precondition(echo, f"union expression '{union}' matched {matched}"
                                       f" node{'' if matched == 1 else 's'},"
                                       f" none of them a JSON object")

    if section:
        if not isinstance(target, dict) or section not in target:
            return _precondition(echo, f"section '{section}' is not a key of"
                                       f" node '{node or '(document)'}'")
        target = {section: target[section]}

    if not isinstance(target, dict):
        # A class binds the keys of an object, so anything else audits nothing
        # and would report that as clean.
        where = f"node '{node}'" if node else f"capture '{json_path}'"
        return _precondition(echo, f"{where} is {_describe(target)}, not a JSON object")

    sources = _parse_sources(src)
    if not sources.count:
        return _precondition(echo, f"no Java classes parsed under '{src}'")
    start = sources.classes.get(root)
    if start is None:
        return _precondition(echo, f"root class '{root}' not found under '{src}'")
    sources.note_ambiguity(root, start)

    audit = _Auditor(sources, settings, show_mapped, max_depth)
    audit.walk_class(target, start, root, 0)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for path, kind in audit.missing:
        grouped[_section_of(path, root)].append({"path": path, "kind": kind})

    truncated = False
    sections: list[dict] = []
    for name in sorted(grouped):
        rows = grouped[name]
        kept, cut = _capped(rows, cap)
        truncated = truncated or cut
        sections.append({"section": name, "count": len(rows), "keys": kept,
                         "truncated": cut})

    def keep(items: Sequence) -> list:
        nonlocal truncated
        kept, cut = _capped(items, cap)
        truncated = truncated or cut
        return kept

    projected: dict = {}
    if phantom:
        # After the walk, so the ambiguity ledger the result carries already
        # holds what the walk resolved. The projection resolves every field's
        # type rather than only the ones a key reached, so it can add entries
        # of its own - which is a fuller answer to the same question, not a
        # different one.
        shape = _java_shape(audit, start, max_depth)
        aligned = _align(_json_paths(target, max_depth), shape)
        phantoms = sorted(shape.paths - aligned)
        wire_only = sorted(aligned - shape.paths)
        projected = {
            "phantoms": keep([path_line(root, p) for p in phantoms]),
            "phantom_total": len(phantoms),
            "projection": {
                "left": [path_line(root, p) for p in sorted(aligned)],
                "right": [path_line(root, p) for p in sorted(shape.paths)],
                "wire_only_total": len(wire_only),
            },
        }

    ambiguous = [sources.ambiguous[name] for name in sorted(sources.ambiguous)]
    return {
        **echo,
        "ok": not audit.missing and not audit.mismatches,
        "unmapped": len(audit.missing),
        "mapped": audit.mapped_count,
        "unresolved": len(audit.unresolved),
        "sections": sections,
        "unmapped_total": len(audit.missing),
        "mapped_keys": keep(audit.mapped) if show_mapped else [],
        "unresolved_types": keep(sorted(audit.unresolved)) if show_unresolved else [],
        "shape_mismatches": keep(audit.mismatches),
        "shape_mismatch_total": len(audit.mismatches),
        "classes": sources.count,
        "union_matched": matched,
        "empty_classes": keep(sources.empty_classes),
        "empty_class_total": len(sources.empty_classes),
        "ambiguous_types": keep(ambiguous),
        "ambiguous_total": len(ambiguous),
        "unreadable_files": sources.unreadable,
        "truncated": truncated,
        **projected,
    }


def phantom_is_red(result: dict) -> bool:
    """Whether the phantom direction is what makes this run fail.

    The gate's verdict is about keys the wire sends that nothing binds. A
    phantom is the opposite question - a field no key reaches - and it is a new
    failure class that every existing caller has calibrated against the first
    one, so it changes an exit code only where fail_on_phantom asked it to.

    Args:
        result: a dict returned by json_diff.

    Returns:
        True when phantoms were found AND the run asked for them to count.
    """
    return bool(result.get("fail_on_phantom") and result.get("phantom_total"))


def exit_code(result: dict) -> int:
    """Maps a json_diff result onto the toolsmith 0/1/2 convention.

    Lives here rather than in the CLI because the mapping is a property of the
    result and the MCP caller wants it as much as the shell does. The split
    matters more here than a plain `if result.get("error")` can express:
    unmapped keys are a red gate, where a source root that parsed nothing never
    ran the audit at all and reporting that as "the classes cover nothing" is
    the worst available reading of a gate.

    Args:
        result: a dict returned by json_diff.

    Returns:
        0 when every JSON key maps to a field, 1 when unmapped keys or shape
        mismatches were found (a red gate), 2 for a precondition failure - an
        unreadable capture, a source root that parsed no classes, a root class
        that is not there, or a path expression that names nothing.

        Phantoms reach this only under fail_on_phantom, where they are a 1 like
        any other red verdict.
    """
    if result.get("status") == "precondition":
        return 2
    return 0 if result.get("ok") and not phantom_is_red(result) else 1


# --------------------------------------------------------------------------
# Choosing a format.
#
# Detection is a pure function of an environment mapping and one boolean, and
# every front end also takes an explicit override, because a default that flips
# silently under a pipe makes a CI failure impossible to reproduce by hand.
# TERM is deliberately not consulted: an agent harness sets it while stdout is
# not a tty, so it says nothing about who is reading.
# --------------------------------------------------------------------------

#: The report formats, in the order the CLI should offer them. "gate" is
#: produced by detection AND spellable by name, because reproducing a red CI
#: run by hand means asking for the format that CI used.
FORMATS = ("agent", "human", "gate", "diff", "json")

#: The values a CI-shaped variable carries when it means the opposite of what
#: its name says. A vendor that sets one sets it to something true, but the
#: ci-info convention every Node tool follows also honours an exported
#: `CI=false`, and users of several hosts are told to export exactly that. A
#: developer who did gets a run that prints nothing at all on a clean gate and
#: one stderr line on a red one, which is unreadable as an answer.
_NOT_SET = ("", "0", "false")


def env_flag(env: Mapping[str, str], name: str) -> bool:
    """Whether an environment variable is set to something meaning yes.

    Args:
        env: the process environment.
        name: the variable to read.

    Returns:
        False when the variable is unset, empty, "0" or "false" in any case.
    """
    return env.get(name, "").strip().lower() not in _NOT_SET


def detect_format(env: Mapping[str, str], isatty: bool) -> str:
    """Picks the format a run that was given no explicit one should use.

    Args:
        env: the process environment.
        isatty: whether the stream the report is going to is a terminal.

    Returns:
        one of FORMATS. CI wins over everything, because a machine reading an
        exit code wants no report body at all; an agent harness comes next; a
        terminal reads the human report; and anything else - a pipe, a file, a
        cron job - gets the agent report, which is the one format that is
        bounded, plain and complete on one stream.
    """
    if env_flag(env, "CI") or env_flag(env, "GITHUB_ACTIONS"):
        return "gate"
    if env.get("CLAUDECODE"):
        return "agent"
    return "human" if isatty else "agent"


def use_color(env: Mapping[str, str], isatty: bool) -> bool:
    """Says whether the human report may carry colour.

    Args:
        env: the process environment.
        isatty: whether the stream the report is going to is a terminal.

    Returns:
        True only for a terminal whose NO_COLOR is unset or empty. The
        published rule is present AND non-empty regardless of the value, so
        `NO_COLOR=` is how a caller un-sets one it inherited - reading presence
        alone would make that spelling the one thing it cannot express.
    """
    return isatty and not env.get("NO_COLOR")


# --------------------------------------------------------------------------
# Rendering.
#
# Every renderer takes a result dict and returns (stdout, stderr). It reads
# that dict and nothing else: no re-walk, no second read of the capture, no
# environment lookup. Each stream is "" when it carries nothing and ends in a
# newline when it carries anything, so a caller writes it out unchanged.
#
# Anything iterated for output is sorted or already came out of the walk in a
# fixed order, because two runs of one capture have to render one byte string.
# --------------------------------------------------------------------------

#: Rows of findings the agent report prints before it defers the rest to a
#: flag. An agent's next move after reading this list is to open a file, and a
#: list longer than a screen gets re-summarised rather than read - which pays
#: for the same content twice. Fifty rows is roughly a Grep result an agent
#: reads whole, against the 6,977 a --show-mapped run can hold, and the counts
#: line above the rows stays exact whatever the cap drops.
AGENT_ROWS = 50

# Whole formatted lines are painted, never a field inside one: the report's
# "%-72s" column counts the escape bytes as width and a painted path silently
# knocks the second column out of alignment.
_ANSI = {"head": "\033[1m", "red": "\033[31m", "green": "\033[32m",
         "section": "\033[36m", "off": "\033[0m"}


def _paint(text: str, colour: str, on: bool) -> str:
    """Wraps a whole line in an ANSI colour when colour is on.

    Args:
        text: the formatted line
        colour: a key of _ANSI
        on: whether to paint at all

    Returns:
        the line, painted or untouched
    """
    if not on or not text:
        return text
    return _ANSI[colour] + text + _ANSI["off"]


def _lines(lines: Sequence[str]) -> str:
    """Joins rendered lines into one stream's text.

    Args:
        lines: the lines, without newlines

    Returns:
        the text ending in a newline, or "" when there are no lines
    """
    return "\n".join(lines) + "\n" if lines else ""


def _verdict(result: dict) -> str:
    """The one word a report leads with.

    Args:
        result: a dict returned by json_diff

    Returns:
        PRECONDITION, CLEAN, UNMAPPED or PHANTOM. UNMAPPED names the red state
        rather than the finding kind, and the counts beside it are what say
        whether the keys or the shapes fired. PHANTOM is the one state the
        counts cannot name on their own: every key mapped, and the run asked
        for the other direction to decide.
    """
    if result.get("status") == "precondition":
        return "PRECONDITION"
    if not result.get("ok"):
        return "UNMAPPED"
    return "PHANTOM" if phantom_is_red(result) else "CLEAN"


def _counts(result: dict) -> str:
    """The counts every non-human format leads with.

    Args:
        result: a dict returned by json_diff

    Returns:
        one line of name=value pairs, uncapped counts throughout. phantom
        appears only on a run that projected the other direction, so a count of
        zero there means zero rather than not asked
    """
    line = ("unmapped=%d mismatch=%d mapped=%d unresolved=%d classes=%d"
            % (result.get("unmapped", 0), result.get("shape_mismatch_total", 0),
               result.get("mapped", 0), result.get("unresolved", 0),
               result.get("classes", 0)))
    if result.get("phantom"):
        line += " phantom=%d" % result.get("phantom_total", 0)
    return line


def _precondition_text(result: dict) -> str:
    """The single line a run that never audited anything reports.

    Args:
        result: a dict returned by json_diff

    Returns:
        the verdict word and the error sentence
    """
    return "DIFF: PRECONDITION %s" % result.get("error", "")


def _diagnostics(result: dict) -> list[str]:
    """The parse diagnostics as short phrases, the non-zero ones only.

    Args:
        result: a dict returned by json_diff

    Returns:
        one phrase per diagnostic that fired, each naming the result key that
        holds the rows
    """
    notes: list[str] = []
    if result.get("empty_class_total"):
        notes.append("%d classes and records parsed no fields (empty_classes)"
                     % result["empty_class_total"])
    if result.get("ambiguous_total"):
        notes.append("%d type names resolved with more than one candidate"
                     " (ambiguous_types)" % result["ambiguous_total"])
    if result.get("unreadable_files"):
        notes.append("%d source files did not decode (unreadable_files)"
                     % len(result["unreadable_files"]))
    return notes


def _human_notes(result: dict) -> list[str]:
    """The commentary the human report cannot carry on stdout.

    Args:
        result: a dict returned by json_diff

    Returns:
        the lines for stderr - shape mismatches first, then the diagnostics and
        the truncation warning
    """
    notes: list[str] = []
    shown = result.get("shape_mismatches", ())
    total = result.get("shape_mismatch_total", 0)
    if total:
        # A mismatch is a red verdict that names no JSON key, so the report
        # body above prints a header of zero and every row of it is mapped. It
        # would be invisible if it were not said here.
        notes.append("%d shape mismatches - a red verdict the key report cannot show:" % total)
        notes.extend("   %s  %s: %s" % (row["path"], row["field"], row["detail"])
                     for row in shown)
        if total > len(shown):
            notes.append("   ... %d more" % (total - len(shown)))
    if result.get("phantom_total"):
        # The body above is the wire's side alone and has no row shape for a
        # field, so the count and the format that shows them is all this
        # report can honestly say about the other direction.
        notes.append("%d fields no JSON key feeds - rerun with --format diff for them"
                     % result["phantom_total"])
    diagnostics = _diagnostics(result)
    if diagnostics:
        notes.append("note: " + "; ".join(diagnostics))
    if result.get("truncated"):
        notes.append("note: key lists were capped and the counts above were not;"
                     " rerun with --cap 0 for every row")
    return notes


def render_human(result: dict, *, color: bool = False) -> tuple[str, str]:
    """Renders the aligned report a person reads.

    The uncoloured stdout bytes are an interface rather than a presentation
    choice - the header spellings, the blank line before each section, the
    72-column path field and the trailing newline are all pinned against
    captured baselines - so everything this format has to add rides on stderr
    instead. The MAPPED block is printed whenever it was asked for, including
    when the count is zero, which is why the request is echoed in the result.

    Args:
        result: a dict returned by json_diff.
        color: paint the headers. Ask use_color rather than deciding here, and
            leave it False for anything a byte comparison will read.

    Returns:
        (the report body, the mismatches and diagnostics for stderr).
    """
    if result.get("status") == "precondition":
        return "", _lines([_precondition_text(result)])

    out: list[str] = []
    if result.get("show_mapped"):
        out.append(_paint("=== MAPPED (%d) ===" % result["mapped"], "head", color))
        out.extend("  " + line for line in result["mapped_keys"])
        out.append("")
    if result.get("show_unresolved") and result.get("unresolved"):
        out.append(_paint("=== UNRESOLVED TYPES (%d) ===" % result["unresolved"],
                          "head", color))
        out.extend("  " + line for line in result["unresolved_types"])
        out.append("")

    unmapped = result.get("unmapped", 0)
    out.append(_paint("=== UNMAPPED JSON KEYS (%d) ===" % unmapped,
                      "red" if unmapped else "green", color))
    for section in result.get("sections", ()):
        out.append("")
        out.append(_paint("-- %s (%d)" % (section["section"], section["count"]),
                          "section", color))
        # Order within a section is the walk's own and is not re-sorted: it is
        # the order the keys sit in on the wire, which is how a reader finds
        # the row again in the capture.
        out.extend("   %-72s %s" % (row["path"], row["kind"]) for row in section["keys"])
    return _lines(out), _lines(_human_notes(result))


#: The order findings are printed in, worst first. A plain sort of the rendered
#: lines would put a phantom above an unmapped key on the alphabet alone, and
#: the row cap would then spend itself on the direction that does not fail the
#: gate.
_FINDING_RANK = {"mismatch": 0, "unmapped": 1, "phantom": 2}


def _findings(result: dict) -> list[str]:
    """Every finding as one flat line, worst kind first and sorted within a kind.

    Args:
        result: a dict returned by json_diff

    Returns:
        the rows, kind first so the sort groups them and two runs of one
        capture render one byte string
    """
    findings = ["unmapped  %s  %s" % (row["path"], row["kind"])
                for section in result.get("sections", ())
                for row in section["keys"]]
    findings.extend("mismatch  %s  %s - %s" % (row["path"], row["field"], row["detail"])
                    for row in result.get("shape_mismatches", ()))
    findings.extend("phantom  %s  no JSON key feeds it" % line
                    for line in result.get("phantoms", ()))
    findings.sort(key=lambda line: (_FINDING_RANK[line.split(" ", 1)[0]], line))
    return findings


def render_agent(result: dict, *, rows: int = AGENT_ROWS) -> tuple[str, str]:
    """Renders counts and one line per finding, capped.

    Counts lead, so the verdict is the first thing read and is exact whatever
    the cap drops. Findings follow as `kind  path  detail` rather than as JSON,
    which costs roughly half the tokens over a pipe for the same content, and
    nothing here is coloured, boxed or animated - the reader is a program
    quoting the text back into its own context.

    The diagnostics ride on stdout here where the human format puts them on
    stderr, because that report's stdout is pinned byte for byte and this one
    is not, and an empty class is what explains a phantom unmapped key.

    Args:
        result: a dict returned by json_diff.
        rows: findings to print before deferring the rest. 0 prints them all.

    Returns:
        (the report, ""), except a precondition, which is one line on stderr.
    """
    if result.get("status") == "precondition":
        return "", _lines([_precondition_text(result)])

    lines = ["DIFF: %s %s" % (_verdict(result), _counts(result))]
    findings = _findings(result)
    shown = findings if rows <= 0 else findings[:rows]
    lines.extend(shown)

    # Counted against the totals rather than against the rows in hand, so a
    # list the walk already capped is included in what is deferred, and the
    # flag named is the one that actually widens the narrower of the two caps.
    hidden = (result.get("unmapped", 0) + result.get("shape_mismatch_total", 0)
              + result.get("phantom_total", 0) - len(shown))
    if hidden > 0:
        flag = "--cap 0 --rows 0" if result.get("truncated") else "--rows 0"
        lines.append("... +%d more (%s shows all)" % (hidden, flag))
    diagnostics = _diagnostics(result)
    if diagnostics:
        lines.append("note: " + "; ".join(diagnostics))
    return _lines(lines), ""


def render_gate(result: dict) -> tuple[str, str]:
    """Renders nothing on stdout; the exit code carries the verdict.

    A pass prints nothing at all, on either stream: the exit code already says
    it and a green build log is one nobody reads. A failure prints ONE line to
    stderr - the counts, the worst three sections, and the format to rerun in -
    because a gate that prints nothing on red leaves a CI run with an exit code
    and no way to tell a broken capture from a new wire key. It stays on stderr
    so that a job piping stdout to an artifact still gets an empty file, which
    is what "quiet" has to mean for the format that CI chooses by itself.

    Args:
        result: a dict returned by json_diff.

    Returns:
        ("", "") on a clean run, otherwise ("", one line).
    """
    if result.get("status") == "precondition":
        return "", _lines([_precondition_text(result)])
    if result.get("ok") and not phantom_is_red(result):
        return "", ""

    sections = result.get("sections", ())
    worst = sorted(sections, key=lambda s: (-s["count"], s["section"]))[:3]
    detail = ", ".join("%s %d" % (s["section"], s["count"]) for s in worst)
    if len(sections) > len(worst):
        detail += ", ..."
    line = "DIFF: %s %s" % (_verdict(result), _counts(result))
    if detail:
        line += " (%s)" % detail
    # A gate that failed on phantoms alone has no section rows to rerun for,
    # and the human report does not carry that direction at all.
    rerun = ("diff for the projection"
             if result.get("ok") and phantom_is_red(result) else "human for the rows")
    return "", _lines([line + " - rerun with --format %s" % rerun])


def render_json(result: dict) -> tuple[str, str]:
    """Renders the result dict as JSON.

    The dict unaltered, which is what makes it the format an MCP caller and a
    script share: no cap applied here, no key renamed, no row dropped. Keys
    keep the order json_diff built them in - inputs, verdict, counts, payload,
    diagnostics - because that order is fixed in code and sorting them would
    scatter the echo through the counts for no gain.

    Args:
        result: a dict returned by json_diff.

    Returns:
        (the JSON text, ""). Non-ASCII is written through rather than escaped,
        since a key out of a foreign capture is more readable as itself.
    """
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n", ""


def _projection_of(result: dict, which: str) -> dict:
    """The projection a diff needs, or a refusal naming how to get one.

    Args:
        result: a dict returned by json_diff
        which: names the run in the message, since a two-run diff has two of
            them and only one of them may be missing its side

    Returns:
        the projection dict

    Raises:
        ValueError: the run did not project. Refusing is the point: a unified
            diff built from a result that holds one side reads as though the
            other side were empty, and a caller cannot tell that apart from a
            working report - it lands in a pipeline as a false clean.
    """
    projection = result.get("projection")
    if not projection:
        raise ValueError(
            "%s carries no projection: a result holds the JSON keys no field"
            " binds and, without --phantom, not the fields no key reaches."
            " Rerun with --phantom, or use --format human for the report and"
            " --format json for the result itself" % which)
    return projection


def render_diff(result: dict, other: dict | None = None) -> tuple[str, str]:
    """Renders the two projections against each other as a unified diff.

    The report IS the diff, so nothing here decides what a finding is: a `-`
    line is a path the document holds and the class graph cannot bind, and a
    `+` line is a path the class graph can bind and the document never fed. The
    counts ride on stderr so stdout stays a diff a pager or an editor can take
    whole.

    With `other`, the two runs' document sides are compared instead, which
    answers what moved between two captures rather than what one capture and
    its classes disagree about.

    Args:
        result: a dict returned by json_diff, run with phantom on.
        other: an earlier run to compare this one's document side against.

    Returns:
        (the unified diff, one counts line).

    Raises:
        ValueError: either run carries no projection.
    """
    if result.get("status") == "precondition":
        return "", _lines([_precondition_text(result)])

    projection = _projection_of(result, "this run")
    if other is None:
        left, right = projection["left"], projection["right"]
        from_file = "wire: %s" % result.get("json", "")
        to_file = "java: %s under %s" % (result.get("root", ""), result.get("src", ""))
        summary = "DIFF: %s %s" % (_verdict(result), _counts(result))
    else:
        left = _projection_of(other, "the other run")["left"]
        right = projection["left"]
        from_file = "wire: %s" % other.get("json", "")
        to_file = "wire: %s" % result.get("json", "")
        summary = ("DIFF: %d paths against %d" % (len(left), len(right)))

    body = list(difflib.unified_diff(left, right, from_file, to_file, n=1, lineterm=""))
    return _lines(body), _lines([summary])


def render(result: dict, fmt: str = "agent", *, color: bool = False,
           rows: int = AGENT_ROWS, other: dict | None = None) -> tuple[str, str]:
    """Renders a result in one of the formats FORMATS names.

    Args:
        result: a dict returned by json_diff.
        fmt: one of FORMATS.
        color: paint the human report. Ignored by every other format.
        rows: the agent report's finding cap. Ignored by every other format.
        other: the run a diff compares against.

    Returns:
        (stdout text, stderr text). Each is "" when it carries nothing and ends
        in a newline when it carries anything, so a caller writes it out with
        no newline handling of its own.

    Raises:
        ValueError: the format is not one of FORMATS, or fmt is "diff" and the
            run carried no projection. The CLI constrains the format with
            argparse choices, so reaching the first is a caller's bug rather
            than a user's input.
    """
    if fmt == "human":
        return render_human(result, color=color)
    if fmt == "agent":
        return render_agent(result, rows=rows)
    if fmt == "gate":
        return render_gate(result)
    if fmt == "json":
        return render_json(result)
    if fmt == "diff":
        return render_diff(result, other)
    raise ValueError("unknown format '%s'; known: %s" % (fmt, ", ".join(FORMATS)))


# --------------------------------------------------------------------------
# Editor hand-off.
#
# `idea64 diff LEFT RIGHT` opens the IDE's own differences viewer and attaches
# to a running instance, so the two projections read as a diff rather than as
# two files. Three properties of that call are not obvious and each is load
# bearing: the paths must be ABSOLUTE, because a relative one resolves against
# the launcher's own bin directory; both must be FILES, because a directory
# fails with a @NotNull parameter error out of the IDE; and the launcher is
# slow to find, so the resolved path is cached beside the module inventory.
#
# This is CLI-only, like `jitpack pins` and `java locate`: it opens a window on
# somebody's screen, and the MCP caller has no screen to open it on. The guard
# is parameter absence - server.py imports this MODULE, so an import check would
# pass anyway; what holds is that no argument of `java_json_diff` selects a
# launch and `json_diff` itself never calls `open_diff`. The environment checks
# below cover the headless cases a CLI still runs in.
# --------------------------------------------------------------------------

#: Names an explicit launcher, and the answer this reports when it finds none.
EDITOR_ENV = "TOOLSMITH_IDEA"

#: Suppresses the launch while still writing the two files. CI sets neither and
#: is checked separately, because a build agent has no screen either.
NO_LAUNCH_ENV = "TOOLSMITH_NO_LAUNCH"

#: Beside modules.json in the same .toolsmith directory, written by the same
#: discovery that owns that directory rather than by a second cache of its own.
EDITOR_CACHE_FILENAME = "editor.json"

_LAUNCHER_NAMES = ("idea64", "idea", "idea.sh", "idea.bat")

# Glob patterns per platform, in the order they are tried. A pattern matching
# several installs takes the last one sorted, which is why the answer is cached
# and why EDITOR_ENV exists: on a machine carrying two IDEs the caller settles
# it once rather than the sort settling it every time.
_LAUNCHER_GLOBS: dict[str, tuple[str, ...]] = {
    "win32": (
        r"C:\Program Files\JetBrains\*\bin\idea64.exe",
        r"C:\Program Files (x86)\JetBrains\*\bin\idea64.exe",
        r"%LOCALAPPDATA%\JetBrains\Toolbox\apps\*\*\*\bin\idea64.exe",
        r"%LOCALAPPDATA%\Programs\*\bin\idea64.exe",
    ),
    "darwin": (
        "/Applications/IntelliJ IDEA*.app/Contents/MacOS/idea",
        "~/Applications/IntelliJ IDEA*.app/Contents/MacOS/idea",
    ),
    "linux": (
        "/opt/*/bin/idea.sh",
        "/usr/local/*/bin/idea.sh",
        "~/.local/share/JetBrains/Toolbox/apps/*/*/*/bin/idea.sh",
    ),
}


def _editor_cache(root: str | os.PathLike | None) -> Path | None:
    """The file the resolved launcher path is cached in.

    Args:
        root: an explicit workspace root, or None to resolve one

    Returns:
        the path, or None when no workspace root resolves - the probe still
        works there, it just pays for itself again next time
    """
    resolved = discovery.resolve_root(root)
    if resolved is None:
        return None
    return resolved / discovery.CACHE_DIRNAME / EDITOR_CACHE_FILENAME


def _read_editor_cache(path: Path) -> str | None:
    """The launcher a previous probe recorded, if it is still there.

    Args:
        path: the cache file

    Returns:
        the launcher path, or None when the file is absent, unreadable, or
        names a launcher that has since been uninstalled
    """
    try:
        launcher = json.loads(path.read_text(encoding="utf-8")).get("idea")
    except (OSError, ValueError, AttributeError):
        return None
    return launcher if launcher and os.path.isfile(launcher) else None


def _write_editor_cache(path: Path | None, launcher: str) -> None:
    """Records a probed launcher, and stays quiet when it cannot.

    Args:
        path: the cache file, or None when there is no workspace root
        launcher: the resolved launcher path
    """
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"idea": launcher}, indent=2), encoding="utf-8")
    except OSError:
        # A cache that cannot be written costs a probe, not a run.
        pass


def find_editor(root: str | os.PathLike | None = None, refresh: bool = False) -> dict:
    """Resolves the IntelliJ launcher, cheaply after the first time.

    Order: EDITOR_ENV, the cache, PATH, then the platform's install locations.
    An env override that names nothing is an error rather than a fallthrough,
    because silently probing past what the caller said would resolve a DIFFERENT
    IDE than the one they asked for.

    Args:
        root: an explicit workspace root whose .toolsmith holds the cache.
        refresh: probe again and rewrite the cache.

    Returns:
        launcher, source ("env", "cache", "path" or "scan") and searched - the
        places that were looked at, in order. On a failure launcher is None and
        error names EDITOR_ENV, so the report says what to set rather than only
        that nothing was found.
    """
    searched: list[str] = []
    override = os.environ.get(EDITOR_ENV)
    if override:
        searched.append("%s=%s" % (EDITOR_ENV, override))
        if os.path.isfile(override):
            return {"launcher": override, "source": "env", "searched": searched}
        return {"launcher": None, "source": None, "searched": searched,
                "error": "%s names '%s', which is not a file" % (EDITOR_ENV, override)}

    cache = _editor_cache(root)
    if cache is not None:
        searched.append(cache.as_posix())
        if not refresh:
            cached = _read_editor_cache(cache)
            if cached:
                return {"launcher": cached, "source": "cache", "searched": searched}

    for name in _LAUNCHER_NAMES:
        searched.append("PATH: " + name)
        hit = shutil.which(name)
        if hit:
            _write_editor_cache(cache, hit)
            return {"launcher": hit, "source": "path", "searched": searched}

    for pattern in _LAUNCHER_GLOBS.get(sys.platform, _LAUNCHER_GLOBS["linux"]):
        expanded = os.path.expanduser(os.path.expandvars(pattern))
        searched.append(expanded)
        hits = sorted(glob.glob(expanded))
        if hits:
            _write_editor_cache(cache, hits[-1])
            return {"launcher": hits[-1], "source": "scan", "searched": searched}

    return {"launcher": None, "source": None, "searched": searched,
            "error": "no IntelliJ launcher found in %d places; set %s to one"
                     % (len(searched), EDITOR_ENV)}


def _projection_dir(out_dir: str | os.PathLike | None) -> Path:
    """The directory the two projection files are written to.

    The platform temp directory rather than any Claude or workspace convention:
    the files are an argument to a viewer, they are rewritten on every run, and
    a tool nobody has configured still has somewhere to put them.

    Args:
        out_dir: an explicit directory, or None for the default

    Returns:
        the directory, created
    """
    target = Path(out_dir) if out_dir else Path(tempfile.gettempdir()) / "toolsmith-json_diff"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _slug(text: str) -> str:
    """A filename-safe spelling of a root class name or a capture stem.

    Args:
        text: the name

    Returns:
        the name with every character a path separator could read replaced
    """
    return re.sub(r"[^\w.-]+", "_", text) or "projection"


def open_diff(result: dict, root: str | os.PathLike | None = None,
              out_dir: str | os.PathLike | None = None, launch: bool = True) -> dict:
    """Writes both projections to files and hands them to IntelliJ's diff viewer.

    The file names are the only label the viewer gives the two sides, so they
    say which is which: the capture's own stem against the root class's, each
    suffixed with the side it holds.

    The launch does not block. A viewer is a thing a person then reads for a
    while, and a CLI that waited on it would hold the terminal for the length
    of the reading.

    Args:
        result: a dict returned by json_diff, run with phantom on.
        root: an explicit workspace root whose .toolsmith holds the launcher
            cache.
        out_dir: where to write the two files. Defaults to a fixed directory
            under the platform temp directory, so a rerun overwrites rather
            than accumulating.
        launch: hand the two files to the viewer. False writes them and stops,
            which is the durable half and is worth having on a machine with no
            IDE at all.

    Returns:
        left, right (the two absolute paths), launcher, launched, and searched
        when a probe ran. A run that wrote the files and could not launch
        carries error and launched False - the paths are still there, so the
        answer is degraded rather than lost.
    """
    projection = result.get("projection")
    if not projection:
        return {"launched": False,
                "error": "the run carries no projection; rerun with phantom on"}

    directory = _projection_dir(out_dir)
    stem = _slug(Path(result.get("json", "capture")).stem)
    left = directory / ("%s.wire.txt" % stem)
    right = directory / ("%s.bindings.txt" % _slug(result.get("root", "java")))
    left.write_text("\n".join(projection["left"]) + "\n", encoding="utf-8")
    right.write_text("\n".join(projection["right"]) + "\n", encoding="utf-8")
    written = {"left": str(left.resolve()), "right": str(right.resolve())}

    if not launch:
        return {**written, "launched": False}
    headless = NO_LAUNCH_ENV if env_flag(os.environ, NO_LAUNCH_ENV) else (
        "CI" if env_flag(os.environ, "CI") else None)
    if headless:
        return {**written, "launched": False,
                "error": "%s is set, so the files were written and nothing was opened"
                         % headless}

    editor = find_editor(root)
    if not editor.get("launcher"):
        return {**written, "launched": False, "launcher": None,
                "searched": editor["searched"], "error": editor["error"]}

    # Detached, so closing the shell does not close the IDE it just attached to.
    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        extra["start_new_session"] = True
    try:
        subprocess.Popen([editor["launcher"], "diff", written["left"], written["right"]],
                         close_fds=True, **extra)
    except OSError as exc:
        return {**written, "launched": False, "launcher": editor["launcher"],
                "error": "launching '%s' failed (%s)" % (editor["launcher"], exc)}
    return {**written, "launched": True, "launcher": editor["launcher"],
            "source": editor["source"]}
