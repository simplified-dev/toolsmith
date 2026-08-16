"""Tests for the JSON-against-Java walk, its verdicts and its report formats.

Fully offline and self-contained: every behavioural test builds a Java source
tree and a JSON document of a few lines in tmp_path, so nothing here depends on
a checkout of the module whose capture the tool was written for. A fixture that
small is also readable as the Java it is about, which is what lets one test pin
one annotation.

What the annotation tests pin is a verdict rather than an output. Each
annotation was read against the gson factory that implements it and classified
as honoured - the walk's answer depends on it - or inert - the walk's answer is
the same whether it is there or not. An honoured verdict earns a test that
fails if the handling is dropped; an inert one earns a test that the annotation
changes NOTHING about key coverage, because an inert verdict is a claim and an
untested claim rots. The strictness switches each correct one annotation's
handling and each defaults OFF, so both sides of every switch are pinned: the
default is what a capture was called clean under.

The renderers read a result dict and nothing else, so most of those tests build
one by hand rather than walking a source tree - the dict is the interface under
test and a hand-built one can hold a shape no small fixture produces, such as
sixty findings or a truncated list. Later sections run real walks to prove the
dict a renderer is handed is the dict json_diff actually returns.

Format detection takes an environment mapping and a boolean rather than
reading os.environ and sys.stdout, which is what lets every branch of it be
driven here without monkeypatching a global. The editor hand-off goes through
one injected subprocess seam for the same reason, so no test launches anything.
"""
from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path

import pytest

from toolsmith import discovery, json_diff as jd


def _result(**over) -> dict:
    """A result dict shaped exactly as json_diff returns one, with overrides."""
    base = {
        "json": "capture.json",
        "src": "src",
        "root": "Member",
        "node": "",
        "union": "",
        "section": None,
        "show_mapped": False,
        "show_unresolved": False,
        "max_depth": 12,
        "strict": [],
        "ok": True,
        "unmapped": 0,
        "mapped": 12,
        "unresolved": 0,
        "sections": [],
        "unmapped_total": 0,
        "mapped_keys": [],
        "unresolved_types": [],
        "shape_mismatches": [],
        "shape_mismatch_total": 0,
        "classes": 3,
        "union_matched": 0,
        "empty_classes": [],
        "empty_class_total": 0,
        "ambiguous_types": [],
        "ambiguous_total": 0,
        "unreadable_files": [],
        "truncated": False,
    }
    base.update(over)
    return base


def _section(name: str, *rows: tuple[str, str]) -> dict:
    """One unmapped section carrying the given path/kind rows."""
    return {"section": name, "count": len(rows),
            "keys": [{"path": path, "kind": kind} for path, kind in rows],
            "truncated": False}


def _fixture(tmp_path: Path, document: str) -> tuple[str, str]:
    """Writes a two-class source tree and a capture, and returns (json, src)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "Member.java").write_text(
        "package demo;\n\n"
        "public class Member {\n"
        "    private int level;\n"
        "    private Stats stats;\n"
        "}\n", encoding="utf-8")
    (src / "Stats.java").write_text(
        "package demo;\n\n"
        "public class Stats {\n"
        "    private int kills;\n"
        "}\n", encoding="utf-8")
    capture = tmp_path / "capture.json"
    capture.write_text(document, encoding="utf-8")
    return str(capture), str(src)


def _tree(tmp_path: Path, document: str, *, at: str = "", **classes) -> tuple[str, str]:
    """Writes a capture and one .java file per named class, and returns (json, src).

    A class is given either as its member LINES, wrapped in a public class of
    that name, or as a whole compilation unit where the declaration itself is
    the point - a record, an enum, a superclass. Every annotation belongs on a
    line of its own: the parser reads a line opening with '@' as annotation text
    and looks for no field declaration on it.

    Args:
        tmp_path: the test's temporary directory
        document: the JSON capture's text
        at: a subdirectory, for a test building two trees to compare
        classes: name -> member lines, or name -> a whole compilation unit

    Returns:
        (the capture path, the source root)
    """
    base = tmp_path / at if at else tmp_path
    src = base / "src"
    src.mkdir(parents=True, exist_ok=True)
    for name, body in classes.items():
        text = body if isinstance(body, str) else (
            "public class %s {\n%s}\n" % (name, "".join("    %s\n" % line for line in body)))
        (src / ("%s.java" % name)).write_text("package demo;\n\n" + text, encoding="utf-8")
    capture = base / "capture.json"
    capture.write_text(document, encoding="utf-8")
    return str(capture), str(src)


def _walk(tmp_path: Path, document: str, *, root: str = "Member", at: str = "",
          classes: dict | None = None, **options) -> dict:
    """Builds a tree and walks it, with the lists uncapped and the mapped keys on.

    Args:
        tmp_path: the test's temporary directory
        document: the JSON capture's text
        root: the class the walk starts from
        at: a subdirectory, for a test building two trees to compare
        classes: name -> member lines, or name -> a whole compilation unit
        options: passed through to json_diff

    Returns:
        the result dict
    """
    capture, src = _tree(tmp_path, document, at=at, **(classes or {}))
    options.setdefault("cap", 0)
    options.setdefault("show_mapped", True)
    return jd.json_diff(capture, src, root=root, **options)


def _paths(result: dict) -> list[str]:
    """The reported paths, flattened out of the per-section grouping."""
    return [row["path"] for section in result["sections"] for row in section["keys"]]


def _coverage(result: dict) -> tuple:
    """Everything a result says about key coverage, without the echoed inputs.

    Two runs over trees differing only by one annotation compare equal here when
    that annotation changed nothing, which is exactly what an inert verdict
    claims and the only way to state it.
    """
    return (result["ok"], _paths(result), result["mapped_keys"],
            result["shape_mismatch_total"])


# --------------------------------------------------------------------------
# The walk's core contract. A key with a field is mapped, a key without one is
# reported, and the wire key a field claims is whatever its annotations say -
# never its Java name once something else has spoken for it.
# --------------------------------------------------------------------------

def test_a_key_no_field_binds_is_reported_and_one_a_field_binds_is_not(tmp_path):
    result = _walk(tmp_path, '{"level": 1, "bonus": 2}',
                   classes={"Member": ["private int level;"]})

    assert _paths(result) == ["Member.bonus"]
    assert result["mapped_keys"] == ["Member.level -> Member.level"]
    assert result["ok"] is False
    assert jd.exit_code(result) == 1


def test_a_clean_walk_is_a_zero(tmp_path):
    result = _walk(tmp_path, '{"level": 1}', classes={"Member": ["private int level;"]})

    assert result["ok"] is True
    assert jd.exit_code(result) == 0
    assert result["sections"] == []


def test_a_serialized_name_binds_the_wire_key_and_leaves_the_java_name_unbound(tmp_path):
    """Gson's own rename: without it the walk looks for a camelCase key the wire
    never sends, and reports every key it does send."""
    result = _walk(tmp_path, '{"skill_tree": 1, "skillTree": 2}',
                   classes={"Member": ['@SerializedName("skill_tree")',
                                       "private int skillTree;"]})

    assert result["mapped_keys"] == ["Member.skill_tree -> Member.skillTree"]
    assert _paths(result) == ["Member.skillTree"]


def test_a_serialized_path_binds_a_chain_of_keys_rather_than_one(tmp_path):
    """Every segment is an object to enter; only the last one is the field's key."""
    result = _walk(tmp_path, '{"profile": {"a": 1}, "a": 2}',
                   classes={"Member": ['@SerializedPath("profile.a")', "private int a;"]})

    assert result["mapped_keys"] == ["Member.profile.a -> Member.a"]
    assert _paths(result) == ["Member.a"]


def test_two_fields_sharing_a_serialized_path_prefix_find_the_one_object(tmp_path):
    """A shared prefix is a find-or-create: the first field builds the object and
    every later one reuses it. Two objects where there should be one is the last
    write winning, and it reads here as a sibling nothing binds."""
    result = _walk(tmp_path, '{"profile": {"a": 1, "b": 2}}',
                   classes={"Member": ['@SerializedPath("profile.a")', "private int a;",
                                       '@SerializedPath("profile.b")', "private int b;"]})

    assert result["ok"] is True
    assert result["mapped_keys"] == ["Member.profile.a -> Member.a",
                                     "Member.profile.b -> Member.b"]


def test_a_field_declared_on_a_superclass_binds_its_key_too(tmp_path):
    result = _walk(tmp_path, '{"level": 1, "id": "x"}', classes={
        "Member": "public class Member extends Base {\n    private int level;\n}\n",
        "Base": "public class Base {\n    private String id;\n}\n"})

    assert result["ok"] is True
    assert sorted(result["mapped_keys"]) == ["Member.id -> Member.id",
                                             "Member.level -> Member.level"]


# --------------------------------------------------------------------------
# One test per honoured annotation: the walk's answer depends on it, so each
# one here fails if its handling is dropped. Where a strictness switch owns the
# handling, both sides are pinned - the default is the answer a capture was
# called clean under, and the switch is the fix that can change that answer.
# --------------------------------------------------------------------------

def test_the_named_parameter_forms_of_serialized_name_need_the_names_switch(tmp_path):
    """Off, only `@SerializedName("x")` is read, so a `value =` site falls back
    to the Java field name and the key the wire really sends reports unmapped.
    On, the value and every alternate bind the one field."""
    document = '{"started_by": "a", "startedBy": "b", "started_at": 1}'
    classes = {"Member": ['@SerializedName(value = "started_by", alternate = {"startedBy"})',
                          "private String startedBy;",
                          '@SerializedName("started_at")', "private long startedAt;"]}

    lenient = _walk(tmp_path, document, at="off", classes=classes)
    assert _paths(lenient) == ["Member.started_by"]
    assert lenient["mapped_keys"] == ["Member.startedBy -> Member.startedBy",
                                      "Member.started_at -> Member.startedAt"]

    strict = _walk(tmp_path, document, at="on", classes=classes, strict=["names"])
    assert strict["ok"] is True
    assert strict["mapped_keys"] == ["Member.started_by -> Member.startedBy",
                                     "Member.startedBy -> Member.startedBy",
                                     "Member.started_at -> Member.startedAt"]


def test_a_path_beats_a_name_on_one_field_only_under_the_names_switch(tmp_path):
    """Off, whichever annotation is written first wins, so the answer depends on
    the order the two were typed in. On, the path wins: it is the chain of wire
    keys, where the name only spells the flattened key the delegate sees."""
    document = '{"flat": 1, "profile": {"a": 1}}'
    classes = {"Member": ['@SerializedName("flat")', '@SerializedPath("profile.a")',
                          "private int a;"]}

    lenient = _walk(tmp_path, document, at="off", classes=classes)
    assert lenient["mapped_keys"] == ["Member.flat -> Member.a"]
    assert _paths(lenient) == ["Member.profile"]

    strict = _walk(tmp_path, document, at="on", classes=classes, strict=["names"])
    assert strict["mapped_keys"] == ["Member.profile.a -> Member.a"]
    assert _paths(strict) == ["Member.flat"]


def test_an_extract_field_binds_no_wire_key_of_its_own_under_the_switch(tmp_path):
    """Its value names a JAVA field and a key inside that field's overflow, so
    reading it as a wire path invents a binding on a Java name the wire never
    sends. Off, that invented binding claims a key; on, the field binds nothing
    at all - and it must not fall back to its own Java name either."""
    document = '{"treeGifts": {"gift": "x"}, "gift": "y"}'
    classes = {"Member": ['@Extract("treeGifts.gift")', "private String gift;",
                          '@SerializedName("tree_gifts")', "private Gifts treeGifts;"],
               "Gifts": ["private int total;"]}

    lenient = _walk(tmp_path, document, at="off", classes=classes)
    assert lenient["mapped_keys"] == ["Member.treeGifts.gift -> Member.gift"]
    assert _paths(lenient) == ["Member.gift"]

    strict = _walk(tmp_path, document, at="on", classes=classes, strict=["extract"])
    assert strict["mapped_keys"] == []
    assert _paths(strict) == ["Member.gift", "Member.treeGifts"]


def test_a_catch_all_capture_claims_every_sibling_no_field_spoke_for(tmp_path):
    """A class carrying one can never report an unmapped key at its own level."""
    result = _walk(tmp_path, '{"anything": 1, "other": {"deep": 2}}', classes={
        "Member": ["@Capture", "private ConcurrentMap<String, Integer> extras;"]})

    assert result["ok"] is True
    assert result["mapped_keys"] == ["Member.anything -> Member.extras (@Capture)",
                                     "Member.other -> Member.extras (@Capture)"]


def test_a_filtered_capture_claims_what_it_matches_and_leaves_the_rest(tmp_path):
    """A declared field's key is matched first, then the filter, and a sibling
    neither of them claims is a finding."""
    result = _walk(tmp_path, '{"highest_wave_hot": 1, "stray": 2, "level": 3}', classes={
        "Member": ['@Capture(filter = "^highest_wave_")',
                   "private ConcurrentMap<String, Integer> waves;",
                   "private int level;"]})

    assert _paths(result) == ["Member.stray"]
    assert result["mapped_keys"] == ["Member.highest_wave_hot -> Member.waves (@Capture)",
                                     "Member.level -> Member.level"]


def test_the_capture_switch_tries_a_filtered_capture_before_the_catch_all(tmp_path):
    """The factory classifies filtered captures first whatever order they were
    declared in. Off, declaration order decides, so a catch-all written first
    swallows the filtered keys and descends them against its own value type."""
    document = '{"boss_a": {"kills": 1}}'
    classes = {"Member": ["@Capture", "private ConcurrentMap<String, Integer> extras;",
                          '@Capture(filter = "^boss_")',
                          "private ConcurrentMap<String, Boss> bosses;"],
               "Boss": ["private int kills;"]}

    lenient = _walk(tmp_path, document, at="off", classes=classes)
    assert lenient["mapped_keys"] == ["Member.boss_a -> Member.extras (@Capture)"]

    strict = _walk(tmp_path, document, at="on", classes=classes, strict=["capture"])
    assert strict["mapped_keys"] == ["Member.boss_a -> Member.bosses (@Capture)",
                                     "Member.boss_a.kills -> Boss.kills"]


def test_the_capture_switch_reads_descend_and_reports_an_inner_key_it_rejects(tmp_path):
    """A descend capture consumes its own key and filters the keys INSIDE it, and
    an inner key the filter rejects is dropped by the factory outright - not
    bound and not overflowed. Off, descent is inferred from the field carrying a
    name and the inner keys are never looked at."""
    document = '{"claimed_levels": {"level_1": true, "bonus": true}}'
    classes = {"Member": ['@SerializedName("claimed_levels")',
                          '@Capture(filter = "^level_", descend = true)',
                          "private ConcurrentMap<String, Boolean> levels;"]}

    lenient = _walk(tmp_path, document, at="off", classes=classes)
    assert lenient["ok"] is True
    assert lenient["mapped_keys"] == ["Member.claimed_levels -> Member.levels"]

    strict = _walk(tmp_path, document, at="on", classes=classes, strict=["capture"])
    assert _paths(strict) == ["Member.claimed_levels.bonus"]
    assert strict["mapped_keys"] == ["Member.claimed_levels -> Member.levels"]


COLLAPSED = {"Member": ["@Collapse", "private ConcurrentList<Contest> contests;"],
             "Contest": ["@Key", "private transient String id;", "private int collected;"]}

#: The same two classes with the annotation missing, which is the shape that
#: cannot decode: the collection adapter throws BEGIN_ARRAY-but-was-BEGIN_OBJECT.
UNCOLLAPSED = {"Member": ["private ConcurrentList<Contest> contests;"],
               "Contest": ["private int collected;"]}


def test_collapse_makes_an_object_of_entry_keys_a_collection_of_elements(tmp_path):
    """The wire node is an object whose KEYS identify the entries, so its values
    are the collection's elements and each one is walked as the declared type."""
    result = _walk(tmp_path, '{"contests": {"229:5_31:INK_SACK:3": {"collected": 5}}}',
                   classes=COLLAPSED, strict=["collapse"])

    assert result["ok"] is True
    assert result["mapped_keys"] == ["Member.contests -> Member.contests",
                                     "Member.contests[0].collected -> Contest.collected"]


def test_a_sequence_over_an_object_without_collapse_is_a_shape_mismatch(tmp_path):
    """This is the one verdict that is not about a key: the decode cannot run at
    all. Off, the object's values are walked as elements anyway, which reports
    the subtree as ordinary findings and calls the shape itself fine."""
    document = '{"contests": {"a": {"collected": 5, "oops": 1}}}'

    lenient = _walk(tmp_path, document, at="off", classes=UNCOLLAPSED)
    assert _paths(lenient) == ["Member.contests[0].oops"]
    assert lenient["shape_mismatch_total"] == 0

    strict = _walk(tmp_path, document, at="on", classes=UNCOLLAPSED, strict=["collapse"])
    assert strict["shape_mismatches"] == [{
        "path": "Member.contests", "field": "Member.contests",
        "detail": "sequence field bound a JSON object and carries no @Collapse"}]
    # Red on a run that reported no key at all, which is what ok being more than
    # `unmapped == 0` is for.
    assert strict["unmapped"] == 0
    assert strict["ok"] is False
    assert jd.exit_code(strict) == 1


def test_the_sequence_shape_is_judged_before_the_element_type_resolves(tmp_path):
    """A collection over a JSON object throws at decode whatever the element type
    is, and the commonest spelling of it - `ConcurrentList<Integer>` - is the one
    whose element resolves to nothing. Asking the element first returns above the
    check and reports the worst shape there is as clean."""
    opaque = {"Member": ["private ConcurrentList<Integer> journals;"]}
    result = _walk(tmp_path, '{"journals": {"a": 1, "b": 2}}', classes=opaque,
                   strict=["collapse"])

    assert result["shape_mismatches"] == [{
        "path": "Member.journals", "field": "Member.journals",
        "detail": "sequence field bound a JSON object and carries no @Collapse"}]
    assert result["ok"] is False
    assert jd.exit_code(result) == 1


def test_a_key_field_binds_nothing_because_the_entry_key_fills_it(tmp_path):
    """It is injected from the enclosing @Collapse entry's key. Left in the
    binding table it would claim a wire key of that name inside the value
    object, and the value object is exactly where that key does not belong."""
    result = _walk(tmp_path, '{"contests": {"a": {"id": "a", "collected": 5}}}',
                   classes=COLLAPSED)

    assert _paths(result) == ["Member.contests[0].id"]
    assert result["mapped_keys"] == ["Member.contests -> Member.contests",
                                     "Member.contests[0].collected -> Contest.collected"]


def test_a_key_field_annotation_does_not_match_a_sibling_named_key_field(tmp_path):
    """@KeyField is a different annotation on the same source tree, and a loose
    @Key pattern picks it up - which silently drops a field that really binds."""
    result = _walk(tmp_path, '{"id": "x"}', classes={
        "Member": ["@KeyField(strictKeys = true)", "private String id;"]})

    assert result["ok"] is True
    assert result["mapped_keys"] == ["Member.id -> Member.id"]


FLATTENED = {"Member": ['@Flatten("current")',
                        "private ConcurrentMap<String, Essence> essence;"],
             "Essence": ["private int amount;"]}


def test_the_flatten_switch_consumes_the_wrapper_level_inside_each_entry(tmp_path):
    """Off, the wrapper is walked against the element type, which is wrong in
    both directions at once: the wrapper's own member reports unmapped once per
    entry, and the real keys inside it are never checked against the class."""
    document = '{"essence": {"WITHER": {"current": {"amount": 5}}}}'

    lenient = _walk(tmp_path, document, at="off", classes=FLATTENED)
    assert _paths(lenient) == ["Member.essence.<WITHER>.current"]
    assert lenient["mapped_keys"] == ["Member.essence -> Member.essence"]

    strict = _walk(tmp_path, document, at="on", classes=FLATTENED, strict=["flatten"])
    assert strict["ok"] is True
    assert strict["mapped_keys"] == ["Member.essence -> Member.essence",
                                     "Member.essence.<WITHER>.amount -> Essence.amount"]


def test_the_wrapper_level_is_consumed_inside_a_collection_entry_too(tmp_path):
    """@Flatten sits on a map or a collection alike, and the level it names is
    inside each ENTRY either way - so the sequence branch spends it as well."""
    document = '{"runs": [{"data": {"score": 1}}]}'
    classes = {"Member": ['@Flatten("data")', "private ConcurrentList<Run> runs;"],
               "Run": ["private int score;"]}

    lenient = _walk(tmp_path, document, at="off", classes=classes)
    assert _paths(lenient) == ["Member.runs[0].data"]

    strict = _walk(tmp_path, document, at="on", classes=classes, strict=["flatten"])
    assert strict["ok"] is True
    assert strict["mapped_keys"] == ["Member.runs -> Member.runs",
                                     "Member.runs[0].score -> Run.score"]


def test_a_sibling_of_the_wrapper_member_is_dropped_rather_than_reported(tmp_path):
    """Pins a silence rather than an answer. The factory drops a wrapper sibling
    on write by declared contract, and the switch steps straight past it, so a
    key that really is going nowhere reads as clean here. What it must not do is
    reach the element class, where it would be a finding about the wrong class.
    """
    result = _walk(tmp_path, '{"essence": {"WITHER": {"current": {"amount": 5},'
                             ' "stray": 9}}}', classes=FLATTENED, strict=["flatten"])

    assert result["ok"] is True
    assert result["mapped_keys"] == ["Member.essence -> Member.essence",
                                     "Member.essence.<WITHER>.amount -> Essence.amount"]


def test_an_entry_whose_wrapper_is_already_gone_is_left_alone(tmp_path):
    """Which is what unwrap does, so a mixed capture reads the same either way."""
    result = _walk(tmp_path, '{"essence": {"WITHER": {"amount": 5}}}',
                   classes=FLATTENED, strict=["flatten"])

    assert result["ok"] is True
    assert result["mapped_keys"] == ["Member.essence -> Member.essence",
                                     "Member.essence.<WITHER>.amount -> Essence.amount"]


# --------------------------------------------------------------------------
# One test per inert annotation. An inert verdict says the walk's answer is the
# same whether the annotation is there or not, which is only worth anything as
# a comparison: each of these walks two trees differing by that one line.
# --------------------------------------------------------------------------

def _both_ways(tmp_path: Path, document: str, marked: dict, plain: dict) -> tuple:
    """Walks two trees differing by one annotation and returns both verdicts."""
    return (_coverage(_walk(tmp_path, document, at="marked", classes=marked)),
            _coverage(_walk(tmp_path, document, at="plain", classes=plain)))


def test_split_changes_no_key_because_it_divides_a_value(tmp_path):
    """It consumes exactly the key it would consume without it and splits that
    key's STRING value into a pair. A string carries no child keys, so the walk
    never reaches anything the annotation moved."""
    marked, plain = _both_ways(
        tmp_path, '{"combat_level": "0-60"}',
        {"Member": ['@Split("-")', '@SerializedName("combat_level")',
                    "private Pair<Integer, Integer> combatLevel;"]},
        {"Member": ['@SerializedName("combat_level")',
                    "private Pair<Integer, Integer> combatLevel;"]})

    assert marked == plain
    assert marked[0] is True
    assert marked[2] == ["Member.combat_level -> Member.combatLevel"]


def test_fallback_changes_no_key_because_it_marks_an_enum_constant(tmp_path):
    """It decides which constant an unrecognised NAME resolves to. The walk
    returns on an enum without reading its body, so no key's answer moves."""
    document = '{"mayor": "cole"}'
    marked, plain = _both_ways(
        tmp_path, document,
        {"Member": ["private Mayor mayor;"],
         "Mayor": "public enum Mayor {\n    @Fallback\n    UNKNOWN,\n    DERPY\n}\n"},
        {"Member": ["private Mayor mayor;"],
         "Mayor": "public enum Mayor {\n    UNKNOWN,\n    DERPY\n}\n"})

    assert marked == plain
    assert marked[0] is True
    assert marked[2] == ["Member.mayor -> Member.mayor"]


def test_lenient_changes_no_key_because_the_field_claims_them_all(tmp_path):
    """It routes an entry's VALUE to the declared collection or to overflow, and
    overflow merges back on write. Reporting an overflowed entry unmapped would
    be a false positive - the key is bound and nothing is lost."""
    marked, plain = _both_ways(
        tmp_path, '{"stats": {"a": 1, "last_killed_mob": "zombie"}}',
        {"Member": ["@Lenient", "private ConcurrentMap<String, Integer> stats;"]},
        {"Member": ["private ConcurrentMap<String, Integer> stats;"]})

    assert marked == plain
    assert marked[0] is True
    assert marked[1] == []


# --------------------------------------------------------------------------
# Narrowing the document. The per-module script this replaces audited one
# hard-coded node of one hard-coded shape; the generalisation is that the whole
# document is the default and every narrowing is an argument.
# --------------------------------------------------------------------------

#: Two profiles whose members carry one key each that the other does not. Read
#: singly, each sample calls the other's key nothing at all.
SAMPLES = ('{"profiles": [{"members": {"a": {"level": 1, "alpha": 1}}},'
           ' {"members": {"b": {"level": 2, "beta": 2}}}]}')
LEVEL = {"Member": ["private int level;"]}


def test_the_whole_document_is_the_default_node(tmp_path):
    """No narrowing audits the root object, so the container key is itself a
    finding - which is the honest answer for a class that binds no such key."""
    result = _walk(tmp_path, SAMPLES, classes=LEVEL)

    assert _paths(result) == ["Member.profiles"]
    assert result["node"] == "" and result["union"] == ""


def test_node_narrows_to_one_subtree_and_an_index_is_a_segment(tmp_path):
    result = _walk(tmp_path, SAMPLES, classes=LEVEL, node="profiles.0.members.a")

    assert _paths(result) == ["Member.alpha"]
    assert result["mapped_keys"] == ["Member.level -> Member.level"]


def test_the_union_audits_a_key_only_one_sample_carries(tmp_path):
    """Its whole reason to exist: a key optional in every individual sample is
    audited once against the merged template, so no sample's absence hides it."""
    result = _walk(tmp_path, SAMPLES, classes=LEVEL, union="profiles.[].members.{}")

    assert _paths(result) == ["Member.alpha", "Member.beta"]
    assert result["union_matched"] == 2
    assert result["mapped_keys"] == ["Member.level -> Member.level"]


def test_section_keeps_one_top_level_key_of_what_the_others_produced(tmp_path):
    document = '{"a": {"x": 1}, "b": {"y": 2}}'
    classes = {"Member": ["private int nothing;"]}

    assert _paths(_walk(tmp_path, document, at="all", classes=classes)) == ["Member.a",
                                                                           "Member.b"]
    narrowed = _walk(tmp_path, document, at="one", classes=classes, section="b")
    assert _paths(narrowed) == ["Member.b"]


def test_a_path_expression_that_names_nothing_never_ran_the_audit(tmp_path):
    """Three separate 2s: reporting any of them as "the classes cover nothing"
    is the worst available reading of a gate."""
    missing_node = _walk(tmp_path, SAMPLES, classes=LEVEL, at="n", node="profiles.7")
    assert missing_node["status"] == "precondition"
    assert "names nothing at segment '7'" in missing_node["error"]
    assert jd.exit_code(missing_node) == 2

    missing_union = _walk(tmp_path, SAMPLES, classes=LEVEL, at="u", union="nope.[]")
    assert "matched nothing" in missing_union["error"]
    assert jd.exit_code(missing_union) == 2

    missing_section = _walk(tmp_path, SAMPLES, classes=LEVEL, at="s", section="zz")
    assert "is not a key of" in missing_section["error"]
    assert jd.exit_code(missing_section) == 2


def test_an_expression_that_lands_on_anything_but_an_object_never_ran_the_audit(tmp_path):
    """A class binds the keys of an object, so an expression resolving to scalars
    or arrays hands the walk nothing to classify. That is a 2 for the same reason
    the misses above are: zero keys examined reported as zero keys unmapped is a
    gate that passes because it looked at nothing."""
    scalars = _walk(tmp_path, SAMPLES, classes=LEVEL, at="u",
                    union="profiles.[].members.{}.level")
    assert scalars["status"] == "precondition"
    assert scalars["error"] == ("union expression 'profiles.[].members.{}.level'"
                                " matched 2 nodes, none of them a JSON object")
    assert jd.exit_code(scalars) == 2

    array = _walk(tmp_path, SAMPLES, classes=LEVEL, at="n", node="profiles")
    assert array["error"] == "node 'profiles' is arr[2], not a JSON object"
    assert jd.exit_code(array) == 2

    # A union reaching one object among scalars still has an object to audit.
    mixed = _walk(tmp_path, '{"xs": [1, {"level": 1, "alpha": 2}]}', classes=LEVEL,
                  at="m", union="xs.[]")
    assert _paths(mixed) == ["Member.alpha"]


def test_an_unknown_strictness_switch_names_the_ones_that_exist(tmp_path):
    result = _walk(tmp_path, '{"level": 1}', classes=LEVEL, strict=["nope"])

    assert jd.exit_code(result) == 2
    assert "unknown strictness nope" in result["error"]
    for feature in jd._STRICT_FEATURES:
        assert feature in result["error"]


# --------------------------------------------------------------------------
# The vocabulary the caller owns. The built-in sets are the JDK and gson names
# alone, so another workspace's date model and another workspace's spelling of
# a concurrent list are arguments rather than edits to this file.
# --------------------------------------------------------------------------

def test_a_type_the_source_root_does_not_hold_is_reported_until_it_is_opaque(tmp_path):
    """Absence is how a source root that is too narrow announces itself, so it
    is a report rather than a silent skip - and naming the type is the answer."""
    document = '{"lastDeath": {"realTime": 1}}'
    classes = {"Member": ["private SkyBlockDate lastDeath;"]}

    reported = _walk(tmp_path, document, at="off", classes=classes, show_unresolved=True)
    assert reported["unresolved_types"] == ["Member.lastDeath (SkyBlockDate)"]

    declared = _walk(tmp_path, document, at="on", classes=classes, show_unresolved=True,
                     opaque=["SkyBlockDate"])
    assert declared["unresolved_types"] == []
    assert declared["ok"] is True


def test_a_leading_hyphen_takes_a_type_out_of_the_built_in_set(tmp_path):
    """One mechanism for both directions, and a Java type name cannot carry a
    hyphen - so a leading one is never part of a name."""
    document = '{"stats": {"a": 1}}'
    classes = {"Member": ["private Integer stats;"]}

    assert _walk(tmp_path, document, at="off", classes=classes,
                 show_unresolved=True)["unresolved_types"] == []
    assert _walk(tmp_path, document, at="on", classes=classes, show_unresolved=True,
                 opaque=["-Integer"])["unresolved_types"] == ["Member.stats (Integer)"]


def test_a_collection_type_the_walk_has_never_heard_of_hides_its_contents(tmp_path):
    """Nothing under it is reported at all, which reads exactly like a clean
    subtree. Naming the type is what turns the elements back into a walk."""
    document = '{"runs": [{"kills": 1, "oops": 2}]}'
    classes = {"Member": ["private FunkyList<Stats> runs;"], "Stats": ["private int kills;"]}

    assert _walk(tmp_path, document, at="off", classes=classes)["ok"] is True
    named = _walk(tmp_path, document, at="on", classes=classes, seq_types=["FunkyList"])
    assert _paths(named) == ["Member.runs[0].oops"]
    assert named["mapped_keys"] == ["Member.runs -> Member.runs",
                                    "Member.runs[0].kills -> Stats.kills"]


def test_a_map_type_takes_its_last_argument_as_the_value(tmp_path):
    document = '{"pets": {"a": {"kills": 1, "oops": 2}}}'
    classes = {"Member": ["private FunkyMap<String, Stats> pets;"],
               "Stats": ["private int kills;"]}

    named = _walk(tmp_path, document, classes=classes, map_types=["FunkyMap"])
    assert _paths(named) == ["Member.pets.<a>.oops"]
    assert named["mapped_keys"] == ["Member.pets -> Member.pets",
                                    "Member.pets.<a>.kills -> Stats.kills"]


# --------------------------------------------------------------------------
# The diagnostics. None of them is a finding about the JSON: each names a way
# the JAVA was read that would otherwise reach the report as unmapped keys.
# --------------------------------------------------------------------------

def test_a_class_that_parsed_no_fields_is_reported_beside_the_keys_it_lost(tmp_path):
    """A declaration wrapped across two lines matches nothing, and a record's
    components live in the header where the field regex does not look at all.
    Both report every key they hold unmapped, and the kind is what tells the two
    rows apart."""
    result = _walk(tmp_path, '{"pet": {"name": "x"}, "wrapped": {"kills": 1}}', classes={
        "Member": ["private Pet pet;", "private Wrapped wrapped;"],
        "Pet": "public record Pet(String name) {\n}\n",
        "Wrapped": "public class Wrapped {\n    private\n        int kills;\n}\n"})

    assert _paths(result) == ["Member.pet.name", "Member.wrapped.kills"]
    assert [(row["class"], row["kind"]) for row in result["empty_classes"]] == [
        ("Pet", "record"), ("Wrapped", "class")]
    assert result["empty_class_total"] == 2
    assert all(row["file"].endswith(".java") for row in result["empty_classes"])


def test_a_simple_name_two_files_declare_is_reported_with_both_candidates(tmp_path):
    """The winner is whichever file sorted first, and walking the right JSON
    against the wrong class reports its keys unmapped - which reads exactly like
    a gap in the DTO. The ledger is what tells the two apart."""
    result = _walk(tmp_path, '{"stats": {"beta_only": 1}}', classes={
        "Member": ["private Stats stats;"],
        "Alpha": "public class Alpha {\n    public static class Stats {\n"
                 "        private int alpha_only;\n    }\n}\n",
        "Beta": "public class Beta {\n    public static class Stats {\n"
                "        private int beta_only;\n    }\n}\n"})

    assert result["ambiguous_total"] == 1
    entry = result["ambiguous_types"][0]
    assert entry["name"] == "Stats"
    assert entry["chosen"] == "Alpha.Stats"
    assert [row["class"] for row in entry["candidates"]] == ["Alpha.Stats", "Beta.Stats"]
    assert entry["candidates"][0]["file"] != entry["candidates"][1]["file"]
    # The finding the wrong resolution produced, which the entry above explains.
    assert _paths(result) == ["Member.stats.beta_only"]


def test_a_source_file_that_did_not_decode_is_named_rather_than_skipped(tmp_path):
    capture, src = _tree(tmp_path, '{"level": 1}', Member=["private int level;"])
    Path(src, "Bad.java").write_bytes(b"package demo;\n\xff\xfe\npublic class Bad {\n}\n")

    result = jd.json_diff(capture, src, root="Member", cap=0)

    assert result["ok"] is True
    assert [Path(row["file"]).name for row in result["unreadable_files"]] == ["Bad.java"]
    assert result["unreadable_files"][0]["error"]


# --------------------------------------------------------------------------
# Format detection. A default that flips under a pipe makes a red CI run
# impossible to reproduce by hand, so the whole rule is one pure function and
# every branch of it is pinned here.
# --------------------------------------------------------------------------

def test_ci_takes_the_gate_over_every_other_signal():
    """CI is checked first on purpose: a machine reads the exit code."""
    assert jd.detect_format({"CI": "true", "CLAUDECODE": "1"}, True) == "gate"
    assert jd.detect_format({"GITHUB_ACTIONS": "true"}, False) == "gate"


def test_an_empty_ci_value_is_not_ci():
    assert jd.detect_format({"CI": "", "GITHUB_ACTIONS": ""}, False) == "agent"


def test_a_ci_variable_that_says_false_is_not_ci():
    """The gate format prints nothing on a pass, so reading `CI=false` as CI
    answers a developer who followed the ci-info convention with silence."""
    for value in ("false", "False", "FALSE", "0", " false "):
        assert jd.detect_format({"CI": value}, True) == "human", value
        assert jd.detect_format({"CI": value, "CLAUDECODE": "1"}, True) == "agent", value
    assert jd.detect_format({"GITHUB_ACTIONS": "false"}, False) == "agent"
    assert jd.env_flag({"CI": "true"}, "CI") is True
    assert jd.env_flag({}, "CI") is False


def test_an_agent_harness_takes_the_agent_format_even_on_a_terminal():
    assert jd.detect_format({"CLAUDECODE": "1"}, True) == "agent"
    assert jd.detect_format({"CLAUDECODE": "1"}, False) == "agent"


def test_a_bare_terminal_reads_the_human_report():
    assert jd.detect_format({}, True) == "human"


def test_anything_that_is_not_a_terminal_falls_to_the_agent_report():
    """A pipe, a file and a cron job all land here, and none of them wants colour."""
    assert jd.detect_format({}, False) == "agent"


def test_term_is_not_a_terminal_signal():
    """TERM is set in an agent harness whose stdout is not a tty, so it is never read."""
    assert jd.detect_format({"TERM": "xterm-256color"}, False) == "agent"
    assert jd.use_color({"TERM": "xterm-256color"}, False) is False


def test_colour_needs_a_terminal_and_no_no_color():
    assert jd.use_color({}, True) is True
    assert jd.use_color({}, False) is False


def test_no_color_disables_colour_at_any_non_empty_value():
    """The published rule is present AND non-empty regardless of the value, so
    `NO_COLOR=` is the spelling that un-sets one inherited from a parent."""
    assert jd.use_color({"NO_COLOR": "0"}, True) is False
    assert jd.use_color({"NO_COLOR": "1"}, True) is False
    assert jd.use_color({"NO_COLOR": "no"}, True) is False
    assert jd.use_color({"NO_COLOR": ""}, True) is True



# --------------------------------------------------------------------------
# The human report. Its uncoloured bytes are an interface pinned against
# captures taken from the script this replaces, so every assertion here is
# about exact text rather than about content being present somewhere.
# --------------------------------------------------------------------------

def test_a_clean_run_renders_one_header_and_a_trailing_newline():
    out, err = jd.render_human(_result())
    assert out == "=== UNMAPPED JSON KEYS (0) ===\n"
    assert err == ""


def test_a_section_is_preceded_by_a_blank_line_and_its_rows_are_column_aligned():
    """The 72-column path field and the blank line before the header are captured bytes."""
    result = _result(ok=False, unmapped=1, unmapped_total=1,
                     sections=[_section("events", ("Member.events.egg", "int 7"))])
    out, _ = jd.render_human(result)
    assert out == ("=== UNMAPPED JSON KEYS (1) ===\n"
                   "\n"
                   "-- events (1)\n"
                   "   " + "Member.events.egg".ljust(72) + " int 7\n")


def test_the_mapped_block_is_printed_on_the_request_and_not_on_the_count():
    """An empty mapped_keys reads the same either way, which is why the request is echoed."""
    asked = jd.render_human(_result(show_mapped=True, mapped=0))[0]
    assert asked == "=== MAPPED (0) ===\n\n=== UNMAPPED JSON KEYS (0) ===\n"
    assert "=== MAPPED" not in jd.render_human(_result(mapped=0))[0]


def test_the_unresolved_block_needs_the_request_and_a_count():
    result = _result(show_unresolved=True, unresolved=1,
                     unresolved_types=["Member.pet (Pet)"])
    assert jd.render_human(result)[0].startswith(
        "=== UNRESOLVED TYPES (1) ===\n  Member.pet (Pet)\n\n")
    assert "UNRESOLVED" not in jd.render_human(_result(show_unresolved=True))[0]


def test_colour_strips_back_to_the_uncoloured_bytes():
    """Whole lines are painted, so the alignment inside a row cannot shift."""
    result = _result(ok=False, unmapped=1, unmapped_total=1,
                     sections=[_section("events", ("Member.events.egg", "int 7"))])
    painted, _ = jd.render_human(result, color=True)
    plain, _ = jd.render_human(result)
    assert painted != plain
    for code in ("\033[1m", "\033[31m", "\033[32m", "\033[36m", "\033[0m"):
        painted = painted.replace(code, "")
    assert painted == plain


def test_a_shape_mismatch_reaches_stderr_because_the_body_cannot_show_it():
    """It is a red verdict naming no JSON key, so a body of zero unmapped is the whole report."""
    result = _result(ok=False, shape_mismatch_total=1, shape_mismatches=[
        {"path": "Member.pets", "field": "Member.pets",
         "detail": "sequence field bound a JSON object and carries no @Collapse"}])
    out, err = jd.render_human(result)
    assert out == "=== UNMAPPED JSON KEYS (0) ===\n"
    assert "1 shape mismatches" in err
    assert "Member.pets" in err
    assert jd.exit_code(result) == 1


def test_the_diagnostics_and_the_truncation_warning_stay_off_stdout():
    result = _result(truncated=True, empty_class_total=2, ambiguous_total=1,
                     unreadable_files=[{"file": "a.java", "error": "boom"}])
    out, err = jd.render_human(result)
    assert out == "=== UNMAPPED JSON KEYS (0) ===\n"
    assert "empty_classes" in err and "ambiguous_types" in err
    assert "unreadable_files" in err and "--cap 0" in err


def test_a_precondition_says_so_on_stderr_and_prints_no_report():
    result = _result(status="precondition", ok=False,
                     error="root class 'Nope' not found under 'src'")
    out, err = jd.render_human(result)
    assert out == ""
    assert err == "DIFF: PRECONDITION root class 'Nope' not found under 'src'\n"



# --------------------------------------------------------------------------
# The agent report. Bounded output is its whole reason to exist, so the cap and
# what it says about what it dropped are the assertions that matter.
# --------------------------------------------------------------------------

def _many(count: int) -> dict:
    """A result carrying `count` unmapped rows across one section."""
    rows = [("Member.events.k%03d" % i, "int %d" % i) for i in range(count)]
    return _result(ok=False, unmapped=count, unmapped_total=count,
                   sections=[_section("events", *rows)])


def test_the_counts_line_comes_first_and_is_exact():
    lines = jd.render_agent(_many(3))[0].splitlines()
    assert lines[0] == ("DIFF: UNMAPPED unmapped=3 mismatch=0 mapped=12"
                        " unresolved=0 classes=3")


def test_a_clean_run_is_one_line():
    assert jd.render_agent(_result())[0] == ("DIFF: CLEAN unmapped=0 mismatch=0"
                                             " mapped=12 unresolved=0 classes=3\n")


def test_the_finding_list_is_capped_and_names_the_flag_that_shows_the_rest():
    """The hypixel capture yields four findings, so nothing there exercises the cap."""
    lines = jd.render_agent(_many(60))[0].splitlines()
    assert len(lines) == 1 + jd.AGENT_ROWS + 1
    assert lines[1].startswith("unmapped  Member.events.k000")
    assert lines[-1] == "... +10 more (--rows 0 shows all)"


def test_rows_zero_prints_every_finding():
    lines = jd.render_agent(_many(60), rows=0)[0].splitlines()
    assert len(lines) == 61
    assert not lines[-1].startswith("...")


def test_a_walk_that_was_already_capped_names_both_flags():
    """--rows alone widens nothing when the rows never left the walk."""
    result = _many(60)
    result["sections"][0]["keys"] = result["sections"][0]["keys"][:5]
    result["truncated"] = True
    lines = jd.render_agent(result, rows=0)[0].splitlines()
    assert lines[-1] == "... +55 more (--cap 0 --rows 0 shows all)"


def test_findings_are_sorted_so_two_runs_of_one_capture_render_one_string():
    result = _result(ok=False, unmapped=2, unmapped_total=2,
                     shape_mismatch_total=1,
                     sections=[_section("z", ("Member.z", "int 1")),
                               _section("a", ("Member.a", "int 2"))],
                     shape_mismatches=[{"path": "Member.pets",
                                        "field": "Member.pets",
                                        "detail": "no @Collapse"}])
    lines = jd.render_agent(result)[0].splitlines()[1:]
    assert lines == ["mismatch  Member.pets  Member.pets - no @Collapse",
                     "unmapped  Member.a  int 2",
                     "unmapped  Member.z  int 1"]


def test_the_diagnostics_ride_on_stdout_here():
    """This report's stdout is not pinned, and an empty class is what explains a phantom key."""
    out, err = jd.render_agent(_result(empty_class_total=15))
    assert out.splitlines()[-1] == ("note: 15 classes and records parsed no"
                                    " fields (empty_classes)")
    assert err == ""


def test_a_precondition_carries_no_report_body():
    out, err = jd.render_agent(_result(status="precondition", ok=False,
                                       error="capture 'x.json' is not JSON"))
    assert out == ""
    assert err == "DIFF: PRECONDITION capture 'x.json' is not JSON\n"



# --------------------------------------------------------------------------
# The gate. Quiet means quiet on stdout whatever happened, and a red run still
# has to say enough that a CI log is diagnosable.
# --------------------------------------------------------------------------

def test_a_green_gate_prints_nothing_on_either_stream():
    assert jd.render_gate(_result()) == ("", "")


def test_a_red_gate_prints_one_stderr_line_with_the_worst_sections():
    """An exit code with no line at all leaves a broken capture and a new wire key alike."""
    result = _result(ok=False, unmapped=4, unmapped_total=4, sections=[
        _section("events", ("Member.events.a", "int 1")),
        _section("player_data", ("Member.player_data.b", "int 2")),
        _section("skill_tree", ("Member.skill_tree.c", "obj{1}"),
                 ("Member.skill_tree.d", "obj{1}")),
    ])
    out, err = jd.render_gate(result)
    assert out == ""
    assert err == ("DIFF: UNMAPPED unmapped=4 mismatch=0 mapped=12 unresolved=0"
                   " classes=3 (skill_tree 2, events 1, player_data 1)"
                   " - rerun with --format human for the rows\n")


def test_the_gate_names_three_sections_and_then_stops():
    result = _result(ok=False, unmapped=4, unmapped_total=4,
                     sections=[_section(name, ("Member.%s.k" % name, "int 1"))
                               for name in ("a", "b", "c", "d")])
    assert "(a 1, b 1, c 1, ...)" in jd.render_gate(result)[1]


def test_a_precondition_is_the_one_thing_a_gate_always_says():
    out, err = jd.render_gate(_result(status="precondition", ok=False,
                                      error="source root 'src' is not a directory"))
    assert out == ""
    assert err.startswith("DIFF: PRECONDITION source root")



# --------------------------------------------------------------------------
# json, diff, and the dispatcher.
# --------------------------------------------------------------------------

def test_the_json_format_alters_nothing():
    result = _many(3)
    out, err = jd.render_json(result)
    assert jsonlib.loads(out) == result
    assert list(jsonlib.loads(out)) == list(result)
    assert err == ""


def test_the_diff_format_refuses_rather_than_rendering_one_side():
    """A one-sided report under this name lands in a pipeline as a false clean."""
    with pytest.raises(ValueError) as caught:
        jd.render_diff(_result())
    assert "--phantom" in str(caught.value)
    assert "--format human" in str(caught.value)
    with pytest.raises(ValueError):
        jd.render(_result(), "diff", other=_result())


def test_a_two_run_diff_names_whichever_side_is_missing_its_projection():
    """Only one of the two runs may be the one that never projected."""
    projected = _result(phantom=True, projection={"left": [], "right": []})
    with pytest.raises(ValueError) as caught:
        jd.render_diff(projected, other=_result())
    assert "the other run" in str(caught.value)


def test_every_format_name_routes_to_its_own_renderer():
    result = _many(2)
    assert jd.render(result, "human") == jd.render_human(result)
    assert jd.render(result, "agent") == jd.render_agent(result)
    assert jd.render(result, "gate") == jd.render_gate(result)
    assert jd.render(result, "json") == jd.render_json(result)
    assert set(jd.FORMATS) == {"agent", "human", "gate", "diff", "json"}


def test_the_gate_format_is_spellable_and_not_only_detected():
    """Reproducing a red CI run by hand means asking for the format CI used."""
    assert "gate" in jd.FORMATS
    assert jd.render(_result(), "gate") == ("", "")


def test_an_unknown_format_names_the_ones_that_exist():
    with pytest.raises(ValueError) as caught:
        jd.render(_result(), "pretty")
    assert "pretty" in str(caught.value)
    for name in jd.FORMATS:
        assert name in str(caught.value)


def test_the_default_format_is_the_bounded_one():
    assert jd.render(_many(60)) == jd.render_agent(_many(60))


def test_two_runs_of_one_capture_render_the_same_bytes_in_every_format(tmp_path):
    """Sets are walked in several places on the way to a report, and a set's
    iteration order is stable within a process but not across one. A report that
    reorders between runs makes every diff of two reports unreadable."""
    capture, src = _tree(tmp_path, '{"level": 1, "extra": 2, "stats": {"kills": 1, "x": 2}}',
                         Member=["private int level;", "private Stats stats;"],
                         Stats=["private int kills;"])
    options = dict(root="Member", cap=0, show_mapped=True, show_unresolved=True, phantom=True)

    first = jd.json_diff(capture, src, **options)
    second = jd.json_diff(capture, src, **options)

    assert first == second
    for fmt in ("agent", "human", "gate", "diff", "json"):
        assert jd.render(first, fmt) == jd.render(second, fmt)



# --------------------------------------------------------------------------
# The walk and the renderers together. The dicts above are hand-built, so one
# real run proves they are the shape json_diff produces.
# --------------------------------------------------------------------------

def test_a_real_run_renders_in_every_format(tmp_path):
    capture, src = _fixture(tmp_path, '{"level": 3, "stats":'
                                      ' {"kills": 1, "deaths": 2}}')
    result = jd.json_diff(capture, src, root="Member", cap=0)
    assert result["unmapped"] == 1
    assert jd.exit_code(result) == 1
    assert jd.render_human(result)[0] == (
        "=== UNMAPPED JSON KEYS (1) ===\n"
        "\n"
        "-- stats (1)\n"
        "   " + "Member.stats.deaths".ljust(72) + " int 2\n")
    assert "unmapped  Member.stats.deaths  int 2" in jd.render_agent(result)[0]
    assert "unmapped=1" in jd.render_gate(result)[1]
    assert jsonlib.loads(jd.render_json(result)[0])["unmapped"] == 1


def test_the_request_for_a_list_survives_into_the_result(tmp_path):
    """render_human prints the MAPPED block off the echo, so the walk has to carry it."""
    capture, src = _fixture(tmp_path, '{"level": 3}')
    asked = jd.json_diff(capture, src, root="Member", show_mapped=True, cap=0)
    assert asked["show_mapped"] is True
    assert asked["show_unresolved"] is False
    assert jd.render_human(asked)[0].startswith("=== MAPPED (1) ===\n"
                                                "  Member.level -> Member.level\n")
    plain = jd.json_diff(capture, src, root="Member", cap=0)
    assert plain["show_mapped"] is False
    assert "=== MAPPED" not in jd.render_human(plain)[0]


def test_a_precondition_from_a_real_run_renders_as_one(tmp_path):
    capture, src = _fixture(tmp_path, '{"level": 3}')
    result = jd.json_diff(capture, src, root="Nope")
    assert jd.exit_code(result) == 2
    assert jd.render_agent(result) == ("", "DIFF: PRECONDITION %s\n" % result["error"])
    assert jd.render_human(result)[0] == ""



# --------------------------------------------------------------------------
# The projection and the direction it opens. Both sides go through one line
# grammar, so the assertions that matter are that a shape spells the same on
# each of them - a formatting difference between the two projections would
# arrive as a finding about the wire.
# --------------------------------------------------------------------------

def test_the_line_grammar_writes_a_key_with_a_dot_and_a_wildcard_without_one():
    assert jd.path_line("M", ("a", "b")) == "M.a.b"
    assert jd.path_line("M", ("pets", "{}", "name")) == "M.pets{}.name"
    assert jd.path_line("M", ("runs", "[]", "score")) == "M.runs[].score"
    assert jd.path_line("M", ()) == "M"


def test_the_document_projects_every_path_once_however_many_samples_carry_it():
    lines = jd.project_json({"a": {"b": 1}, "runs": [{"x": 1}, {"x": 2, "y": 3}]}, "M")
    assert lines == ["M.a", "M.a.b", "M.runs", "M.runs[]",
                     "M.runs[].x", "M.runs[].y"]


def test_an_empty_array_still_says_an_array_is_there():
    """What is missing is what was inside it, which is a different finding."""
    assert jd.project_json({"runs": []}, "M") == ["M.runs", "M.runs[]"]


def test_max_depth_cuts_both_sides_at_the_same_segment_count():
    deep = {"a": {"b": {"c": {"d": 1}}}}
    assert jd.project_json(deep, "M", max_depth=2) == ["M.a", "M.a.b"]


def test_a_field_no_json_key_reaches_is_a_phantom(tmp_path):
    capture, src = _fixture(tmp_path, '{"level": 3}')
    result = jd.json_diff(capture, src, root="Member", cap=0, phantom=True)
    assert result["phantoms"] == ["Member.stats", "Member.stats.kills"]
    assert result["phantom_total"] == 2
    assert result["projection"]["left"] == ["Member.level"]


def test_a_phantom_does_not_move_the_gate_unless_it_was_asked_to(tmp_path):
    """Every existing caller calibrated on the direction the wire asks about."""
    capture, src = _fixture(tmp_path, '{"level": 3}')
    quiet = jd.json_diff(capture, src, root="Member", cap=0, phantom=True)
    assert quiet["ok"] is True
    assert jd.exit_code(quiet) == 0
    assert jd.render_gate(quiet) == ("", "")

    loud = jd.json_diff(capture, src, root="Member", cap=0, phantom=True,
                        fail_on_phantom=True)
    assert loud["ok"] is True
    assert jd.exit_code(loud) == 1
    assert "PHANTOM" in jd.render_gate(loud)[1]
    assert "--format diff" in jd.render_gate(loud)[1]


def test_asking_for_the_direction_without_the_flag_finds_nothing_to_fail_on(tmp_path):
    """fail_on_phantom alone projects nothing, so it can only ever pass."""
    capture, src = _fixture(tmp_path, '{"level": 3}')
    result = jd.json_diff(capture, src, root="Member", cap=0, fail_on_phantom=True)
    assert "phantoms" not in result
    assert jd.exit_code(result) == 0


def test_the_counts_line_carries_phantom_only_on_a_run_that_projected():
    assert "phantom=" not in jd.render_agent(_result())[0]
    line = jd.render_agent(_result(phantom=True, phantom_total=0))[0]
    assert "phantom=0" in line


def _graph(tmp_path: Path, document: str) -> tuple[str, str]:
    """A map-valued field and a self-referential one, plus a capture."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "Member.java").write_text(
        "package demo;\n\n"
        "public class Member {\n"
        "    private ConcurrentMap<String, Stats> pets;\n"
        "    private Node tree;\n"
        "}\n", encoding="utf-8")
    (src / "Stats.java").write_text(
        "package demo;\n\npublic class Stats {\n"
        "    private int kills;\n}\n", encoding="utf-8")
    (src / "Node.java").write_text(
        "package demo;\n\npublic class Node {\n"
        "    private String name;\n"
        "    private Node child;\n}\n", encoding="utf-8")
    capture = tmp_path / "capture.json"
    capture.write_text(document, encoding="utf-8")
    return str(capture), str(src)


def test_the_class_graph_projects_in_the_same_grammar_as_the_document(tmp_path):
    """The two sides are separate walks, so the grammar is the whole contract."""
    capture, src = _fixture(tmp_path, '{"level": 3, "stats": {"kills": 1}}')
    sources = jd._parse_sources(src)
    audit = jd._Auditor(sources, jd._Settings(
        opaque=jd._OPAQUE, map_types=jd._MAP_TYPES, seq_types=jd._SEQ_TYPES,
        wrapper_types=jd._WRAPPER_TYPES, strict=frozenset()))
    java = jd.project_java(sources.classes["Member"], "Member", audit)
    assert java == ["Member.level", "Member.stats", "Member.stats.kills"]
    assert jd.project_json(jsonlib.loads(Path(capture).read_text(encoding="utf-8")),
                           "Member") == java


def test_a_map_key_is_data_and_collapses_onto_the_wildcard_that_claims_it(tmp_path):
    """Compared literally, every entry of every map is a finding twice over."""
    capture, src = _graph(tmp_path, '{"pets": {"a": {"kills": 1},'
                                    ' "b": {"kills": 2}}}')
    result = jd.json_diff(capture, src, root="Member", cap=0, phantom=True)
    assert result["projection"]["left"] == ["Member.pets", "Member.pets{}",
                                            "Member.pets{}.kills"]
    assert result["projection"]["wire_only_total"] == 0
    assert result["unmapped"] == 0


def test_a_collection_of_collections_spends_one_level_each(tmp_path):
    """Reading the outer level only leaves every key below the inner one adrift."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "Member.java").write_text(
        "package demo;\n\npublic class Member {\n"
        "    private ConcurrentMap<String, ConcurrentList<Stats>> runs;\n}\n",
        encoding="utf-8")
    (src / "Stats.java").write_text(
        "package demo;\n\npublic class Stats {\n    private int kills;\n}\n",
        encoding="utf-8")
    capture = tmp_path / "capture.json"
    capture.write_text('{"runs": {"floor_1": [{"kills": 1}]}}', encoding="utf-8")
    result = jd.json_diff(str(capture), str(src), root="Member", cap=0, phantom=True)
    assert result["projection"]["right"] == ["Member.runs", "Member.runs{}",
                                             "Member.runs{}[]", "Member.runs{}[].kills"]
    assert result["projection"]["wire_only_total"] == 0
    assert result["phantom_total"] == 0


def test_a_class_reachable_from_itself_stops_where_the_document_side_stops(tmp_path):
    """max_depth is the terminator, and it is the one both sides take.

    Pruning at the first repeat instead would stop the class graph one level
    below the document, so a path the walk demonstrably binds comes back as a
    wire-only line - a claim the same result contradicts."""
    capture, src = _graph(tmp_path, '{"tree": {"name": "a",'
                                    ' "child": {"name": "b", "oops": 1}}}')
    result = jd.json_diff(capture, src, root="Member", cap=0, phantom=True,
                          max_depth=4, show_mapped=True)
    right = result["projection"]["right"]
    assert "Member.tree.child.child" in right
    assert "Member.tree.child.name -> Node.name" in result["mapped_keys"]

    # Nothing runs past the cut, and the only line the document holds that the
    # graph cannot bind is the one the walk also called unmapped.
    assert max(line.count(".") for line in right) == 4
    assert result["projection"]["left"] == sorted(
        ["Member.tree", "Member.tree.name", "Member.tree.child",
         "Member.tree.child.name", "Member.tree.child.oops"])
    assert [line for line in result["projection"]["left"] if line not in right] == \
        ["Member.tree.child.oops"]
    assert _paths(result) == ["Member.tree.child.oops"]


def test_neither_side_of_the_projection_runs_past_max_depth(tmp_path):
    """A type that spends several segments must be cut inside the run of them.

    Cutting once before it and then adding them all puts the class graph past a
    depth the document was cut at, and every segment past the cut is a phantom
    the cut invented rather than one the DTOs hold."""
    dotted = _walk(tmp_path, '{"a": {"b": {"c": 1}}}', at="dotted", root="Root",
                   max_depth=2, phantom=True, classes={
                       "Root": ['@SerializedPath("a.b.c")', "private int deep;"]})
    assert dotted["projection"]["right"] == ["Root.a", "Root.a.b"]
    assert dotted["phantoms"] == []

    generic = _walk(tmp_path, '{"grid": {"a": [{"v": 1}]}}', at="generic", root="Root",
                    max_depth=2, phantom=True, classes={
                        "Root": ["private ConcurrentMap<String, ConcurrentList<Leaf>> grid;"],
                        "Leaf": ["private int v;"]})
    assert generic["projection"]["right"] == ["Root.grid", "Root.grid{}"]
    assert generic["phantoms"] == []


def test_a_section_is_refused_together_with_a_projection(tmp_path):
    """A section narrows the capture and not the class graph, so the two of them
    would report every field outside the section as a phantom of the narrowing.
    Answering that is worse than refusing, since nothing in the report says the
    count is about the flag rather than about the DTOs."""
    capture, src = _fixture(tmp_path, '{"level": 3, "stats": {"kills": 1}}')

    for options in ({"phantom": True}, {"fail_on_phantom": True}):
        result = jd.json_diff(capture, src, root="Member", section="stats", **options)
        assert result["status"] == "precondition"
        assert "narrows the capture but not the class graph" in result["error"]
        assert jd.exit_code(result) == 2

    # Either one alone still runs.
    assert jd.json_diff(capture, src, root="Member", section="stats")["ok"] is True
    assert jd.json_diff(capture, src, root="Member", phantom=True)["ok"] is True


def test_the_diff_is_the_report(tmp_path):
    """A minus line is a key nothing binds and a plus line is a field nothing feeds."""
    capture, src = _fixture(tmp_path, '{"level": 3, "extra": 1}')
    result = jd.json_diff(capture, src, root="Member", cap=0, phantom=True)
    out, err = jd.render_diff(result)
    assert "-Member.extra" in out
    assert "+Member.stats" in out
    assert " Member.level" in out
    assert err.startswith("DIFF: UNMAPPED ")
    assert "phantom=2" in err


def test_a_phantom_never_outranks_an_unmapped_key_in_the_capped_list():
    """The row cap must not be spent on the direction that does not gate."""
    result = _many(3)
    result["phantoms"] = ["Member.aaa", "Member.bbb"]
    result["phantom_total"] = 2
    rows = jd.render_agent(result)[0].splitlines()[1:]
    assert [r.split(" ", 1)[0] for r in rows] == ["unmapped"] * 3 + ["phantom"] * 2



def test_a_collapsed_collection_projects_as_keys_and_a_plain_one_as_elements(tmp_path):
    """@Collapse says the entries sit under keys rather than at indices.
    Projected as an array, every entry of it is reported twice - once as a key
    nothing binds and once as an element nothing feeds."""
    collapsed = _walk(tmp_path, '{"contests": {"229:5": {"collected": 5}}}',
                      at="collapsed", classes=COLLAPSED, phantom=True)
    assert collapsed["projection"]["right"] == ["Member.contests", "Member.contests{}",
                                                "Member.contests{}.collected"]
    assert collapsed["projection"]["left"] == collapsed["projection"]["right"]
    assert collapsed["phantom_total"] == 0

    plain = _walk(tmp_path, '{"contests": [{"collected": 5}]}',
                  at="plain", classes=UNCOLLAPSED, phantom=True)
    assert plain["projection"]["right"] == ["Member.contests", "Member.contests[]",
                                            "Member.contests[].collected"]
    assert plain["phantom_total"] == 0


def test_a_transient_field_is_no_phantom_because_gson_never_reaches_it(tmp_path):
    """A memo an accessor fills is not a binding, so a path under it would be a
    phantom of this tool's own making."""
    result = _walk(tmp_path, '{"level": 1}', phantom=True, classes={
        "Member": ["private int level;", "private transient Stats memo;"],
        "Stats": ["private int kills;"]})

    assert result["projection"]["right"] == ["Member.level"]
    assert result["phantoms"] == []


def test_a_key_a_capture_filter_rejects_stays_concrete_on_the_wire_side(tmp_path):
    """A capture's claim is exactly its filter, so alignment asks the filter
    rather than assuming the whole level - the same predicate the walk applies.
    Folded onto the wildcard anyway, a rejected key would read as bound."""
    result = _walk(tmp_path, '{"highest_wave_hot": 1, "stray": 2}', phantom=True, classes={
        "Member": ['@Capture(filter = "^highest_wave_")',
                   "private ConcurrentMap<String, Integer> waves;"]})

    assert result["projection"]["left"] == ["Member.stray", "Member{}"]
    assert result["projection"]["right"] == ["Member{}"]
    assert result["projection"]["wire_only_total"] == 1
    assert _paths(result) == ["Member.stray"]


def test_the_capture_switch_spends_the_matched_key_as_the_collections_own_level(tmp_path):
    """A capture's matched KEY is the level the wire spends on the collection, so
    the node under it is one entry.

    Off, the whole declared type is descended against that entry, so a
    class-valued capture never checks the entry's own keys: a key the element
    class does not declare goes unreported, and a nested one lands a level
    deeper than the wire - `boss_a.<inner>.oops` where the finding is
    `boss_a.inner`. The projection spends that level in either position, which
    is why the unspent key shows up there as a wire-only line the walk contradicts.
    """
    classes = {"Member": ['@Capture(filter = "^boss_")',
                          "private ConcurrentMap<String, Boss> bosses;"],
               "Boss": ["private int kills;"]}

    flat = _walk(tmp_path, '{"boss_a": {"kills": 1, "oops": 2}}', at="flat",
                 classes=classes, phantom=True)
    assert _paths(flat) == []
    assert flat["projection"]["left"] == ["Member{}", "Member{}.kills", "Member{}.oops"]
    assert flat["projection"]["wire_only_total"] == 1

    strict = _walk(tmp_path, '{"boss_a": {"kills": 1, "oops": 2}}', at="flat_on",
                   classes=classes, phantom=True, strict=["capture"])
    assert _paths(strict) == ["Member.boss_a.oops"]
    assert strict["mapped_keys"] == ["Member.boss_a -> Member.bosses (@Capture)",
                                     "Member.boss_a.kills -> Boss.kills"]

    nested = _walk(tmp_path, '{"boss_a": {"inner": {"kills": 1, "oops": 2}}}', at="nested",
                   classes=classes)
    assert _paths(nested) == ["Member.boss_a.<inner>.oops"]

    nested_on = _walk(tmp_path, '{"boss_a": {"inner": {"kills": 1, "oops": 2}}}', at="nested_on",
                      classes=classes, strict=["capture"])
    assert _paths(nested_on) == ["Member.boss_a.inner"]


# --------------------------------------------------------------------------
# The editor hand-off. Nothing here launches anything: the two files are the
# durable half and the launch is one Popen behind them, so what is worth
# pinning is that the files land and that a machine with no IDE says where it
# looked rather than failing obscurely.
# --------------------------------------------------------------------------

def _projected(tmp_path: Path) -> dict:
    """A real run carrying a projection."""
    capture, src = _fixture(tmp_path, '{"level": 3, "extra": 1}')
    return jd.json_diff(capture, src, root="Member", cap=0, phantom=True)


def test_an_explicit_launcher_is_taken_over_every_probe(tmp_path, monkeypatch):
    launcher = tmp_path / "idea64.exe"
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setenv(jd.EDITOR_ENV, str(launcher))
    found = jd.find_editor()
    assert found["launcher"] == str(launcher)
    assert found["source"] == "env"


def test_an_override_that_names_nothing_is_an_error_and_not_a_fallthrough(monkeypatch):
    """Probing past it would open a different IDE than the one asked for."""
    monkeypatch.setenv(jd.EDITOR_ENV, "W:/nowhere/idea64.exe")
    found = jd.find_editor()
    assert found["launcher"] is None
    assert jd.EDITOR_ENV in found["error"]
    assert found["searched"] == ["%s=W:/nowhere/idea64.exe" % jd.EDITOR_ENV]


def test_both_projections_are_written_as_files_named_for_what_they_hold(tmp_path):
    result = _projected(tmp_path)
    opened = jd.open_diff(result, out_dir=tmp_path / "out", launch=False)
    left, right = Path(opened["left"]), Path(opened["right"])
    assert left.name == "capture.wire.txt"
    assert right.name == "Member.bindings.txt"
    assert left.is_absolute() and right.is_absolute()
    assert left.read_text(encoding="utf-8").splitlines() == result["projection"]["left"]
    assert right.read_text(encoding="utf-8").splitlines() == result["projection"]["right"]
    assert opened["launched"] is False


def test_a_headless_run_still_writes_the_files(tmp_path, monkeypatch):
    monkeypatch.setenv(jd.NO_LAUNCH_ENV, "1")
    opened = jd.open_diff(_projected(tmp_path), out_dir=tmp_path / "out")
    assert opened["launched"] is False
    assert jd.NO_LAUNCH_ENV in opened["error"]
    assert Path(opened["left"]).is_file()


def test_a_run_that_never_projected_has_nothing_to_open(tmp_path):
    opened = jd.open_diff(_result(), out_dir=tmp_path / "out", launch=False)
    assert opened["launched"] is False
    assert "phantom" in opened["error"]
    assert "left" not in opened


class _FakeSubprocess:
    """Stands in for the subprocess module the launch goes through.

    One injected seam is what keeps the suite from opening a window on whoever
    is running it, and it is the only way to see the argv the viewer is handed.

    Args:
        error: raised instead of starting anything, for the failure path
    """

    DETACHED_PROCESS = 8

    def __init__(self, error: OSError | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.error = error

    def Popen(self, argv, **kwargs):  # noqa: N802 - the name subprocess gives it
        self.calls.append((list(argv), kwargs))
        if self.error is not None:
            raise self.error
        return object()


@pytest.fixture
def launcher(tmp_path, monkeypatch) -> Path:
    """A directory on PATH holding a launcher, with every override cleared.

    Both spellings are written because resolution is the platform's own: Windows
    finds idea64.exe through PATHEXT and a POSIX lookup finds the extensionless
    one. PATH holds this directory alone, so a real IDE on the machine running
    the suite cannot be what a test resolved.
    """
    for name in (jd.EDITOR_ENV, jd.NO_LAUNCH_ENV, "CI"):
        monkeypatch.delenv(name, raising=False)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("idea64", "idea64.exe"):
        target = binaries / name
        target.write_text("", encoding="utf-8")
        target.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries))
    return binaries


def test_the_probed_launcher_is_cached_beside_the_module_inventory(tmp_path, launcher,
                                                                   monkeypatch):
    """The probe is slow enough to be worth not repeating, and the cache lives in
    the .toolsmith directory discovery already owns rather than in one of its
    own. The second call is the assertion: it looks at the cache and stops."""
    workspace = tmp_path / "workspace"

    cold = jd.find_editor(root=workspace)
    assert cold["source"] == "path"
    assert Path(cold["launcher"]).parent == launcher
    cache = workspace / discovery.CACHE_DIRNAME / jd.EDITOR_CACHE_FILENAME
    assert jsonlib.loads(cache.read_text(encoding="utf-8")) == {"idea": cold["launcher"]}

    monkeypatch.setenv("PATH", str(tmp_path / "nothing-at-all"))
    warm = jd.find_editor(root=workspace)
    assert warm["source"] == "cache"
    assert warm["launcher"] == cold["launcher"]
    assert warm["searched"] == [entry for entry in cold["searched"]
                                if not entry.startswith("PATH:")]


def test_the_launch_hands_both_files_to_the_viewer_and_never_waits(tmp_path, launcher,
                                                                   monkeypatch):
    """Absolute paths and two files, because a relative one resolves against the
    launcher's own bin directory and a directory fails inside the IDE. Detached,
    because closing the shell would otherwise close the IDE it just attached to."""
    seam = _FakeSubprocess()
    monkeypatch.setattr(jd, "subprocess", seam)
    monkeypatch.setenv(jd.EDITOR_ENV, str(launcher / "idea64.exe"))

    opened = jd.open_diff(_projected(tmp_path), out_dir=tmp_path / "out")

    assert opened["launched"] is True
    assert len(seam.calls) == 1
    argv, kwargs = seam.calls[0]
    assert argv == [str(launcher / "idea64.exe"), "diff", opened["left"], opened["right"]]
    assert Path(argv[2]).is_absolute() and Path(argv[3]).is_file()
    assert kwargs["close_fds"] is True
    assert kwargs["creationflags" if sys.platform == "win32" else "start_new_session"]


def test_a_launch_that_failed_still_leaves_both_files_behind(tmp_path, launcher, monkeypatch):
    """The files are the durable half, so the answer is degraded rather than lost."""
    monkeypatch.setattr(jd, "subprocess", _FakeSubprocess(OSError("nothing there")))
    monkeypatch.setenv(jd.EDITOR_ENV, str(launcher / "idea64.exe"))

    opened = jd.open_diff(_projected(tmp_path), out_dir=tmp_path / "out")

    assert opened["launched"] is False
    assert "nothing there" in opened["error"]
    assert Path(opened["left"]).is_file() and Path(opened["right"]).is_file()


def test_no_mcp_argument_can_reach_the_editor_hand_off():
    """It opens a window on somebody's screen and an agent has none.

    The guard is parameter absence rather than the import graph: server.py
    imports the json_diff MODULE, so an import check would pass while a tool
    argument selected the launch. What has to hold is that no argument does,
    and that the function the tool calls never reaches it either.
    """
    import asyncio
    import inspect

    from toolsmith import server

    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    arguments = set(tools["java_json_diff"].inputSchema["properties"])

    assert arguments.isdisjoint({"open", "launch", "editor", "open_diff"})
    assert "open_diff" not in inspect.getsource(jd.json_diff)


def test_the_command_line_is_where_the_hand_off_is_asked_for():
    """The other half of the same guard: it is reachable, just not from there."""
    from toolsmith import cli

    invocation = ["java", "json_diff", "--json", "c.json", "--src", "src", "--root", "M"]

    assert cli.build_parser().parse_args(invocation + ["--open"]).open is True
    assert cli.build_parser().parse_args(invocation).open is False
