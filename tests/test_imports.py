"""Tests for the IntelliJ-Default import reorderer."""
from __future__ import annotations

from toolsmith.imports import process_file, reorder_text

SCRAMBLED = "\n".join([
    "package com.x;",
    "",
    "import java.util.List;",
    "import static org.junit.Assert.assertEquals;",
    "import org.junit.Test;",
    "import javax.annotation.Nullable;",
    "import java.io.IOException;",
    "import static java.util.Comparator.comparing;",
    "import dev.simplified.Foo;",
    "",
    "class X {}",
])

# group 1 (other, ASCII) | blank | group 2 (javax then java) | blank | group 3 (static)
EXPECTED_IMPORTS = [
    "import dev.simplified.Foo;",
    "import org.junit.Test;",
    "import javax.annotation.Nullable;",
    "import java.io.IOException;",
    "import java.util.List;",
    "import static java.util.Comparator.comparing;",
    "import static org.junit.Assert.assertEquals;",
]


def test_default_layout_order_and_grouping():
    out, changed, skip = reorder_text(SCRAMBLED)
    assert skip is None
    assert changed
    imports = [ln for ln in out.split("\n") if ln.startswith("import")]
    assert imports == EXPECTED_IMPORTS
    # exactly two blank-line separators inside the import region
    region = out.split("\n")
    first = next(i for i, ln in enumerate(region) if ln.startswith("import"))
    last = max(i for i, ln in enumerate(region) if ln.startswith("import"))
    blanks = sum(1 for ln in region[first:last + 1] if ln.strip() == "")
    assert blanks == 2


def test_javax_precedes_java_not_alphabetical():
    out, _, _ = reorder_text(SCRAMBLED)
    imports = [ln for ln in out.split("\n") if ln.startswith("import") and " static " not in ln]
    assert imports.index("import javax.annotation.Nullable;") < imports.index("import java.io.IOException;")


def test_idempotent():
    once, _, _ = reorder_text(SCRAMBLED)
    twice, changed, skip = reorder_text(once)
    assert skip is None
    assert not changed
    assert once == twice


def test_wildcards_preserved():
    src = "package p;\n\nimport java.util.*;\nimport static org.mockito.Mockito.*;\nimport a.B;\n\nclass Y {}"
    out, _, skip = reorder_text(src)
    assert skip is None
    assert "import java.util.*;" in out
    assert "import static org.mockito.Mockito.*;" in out


def test_skip_interleaved_comment():
    src = "package p;\n\nimport a.B;\n// a stray comment\nimport a.C;\n\nclass Z {}"
    out, changed, skip = reorder_text(src)
    assert skip == "non-import line inside import block"
    assert not changed
    assert out == src


def test_crlf_preserved_and_idempotent_on_disk(tmp_path):
    java = tmp_path / "T.java"
    java.write_bytes(SCRAMBLED.replace("\n", "\r\n").encode("utf-8"))
    status, _ = process_file(java, "write")
    assert status == "changed"
    raw = java.read_bytes()
    assert b"\r\n" in raw and b"\n\n" not in raw.replace(b"\r\n", b"\n\n").replace(b"\n\n", b"\r\n")
    # second pass is a no-op
    status2, _ = process_file(java, "write")
    assert status2 == "unchanged"
