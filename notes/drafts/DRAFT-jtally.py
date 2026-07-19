#!/usr/bin/env python3
"""jtally - canonical JUnit test-result tally for the Simplified modules.

Replaces the ~44 hand-authored inline python/awk blocks that each re-parse
build/test-results/test/*.xml for tests/skipped/failures/errors and list the
failing testcases. Output is a single compact, stable block so re-runs never
re-import a different shape.

Usage:
    jtally                 # tally ./build/test-results/test in the cwd
    jtally ar              # tally the asset-renderer module (alias)
    jtally /abs/path       # tally an explicit module dir
    jtally ar --fails 20   # cap the failing-testcase list (default 15)
    jtally ar --dir sub    # results under <module>/sub/build/test-results/test

Exit code: 0 when failures+errors == 0 and at least one XML was found; 1 when any
test failed/errored; 2 when no result XML exists (nothing ran). So it doubles as a
gate: `jtally ar && echo GREEN`.
"""
import glob
import os
import sys
import xml.etree.ElementTree as ET

# Alias map mirrors modules.sh. gw always passes an absolute dir, so this map is
# only exercised for standalone `jtally <alias>` convenience.
ROOT = os.environ.get("SIMPLIFIED_ROOT", "W:/Workspace/Java/Simplified")
ALIASES = {
    "ar": "Minecraft-Library/asset-renderer",
    "mt": "Minecraft-Library/minecraft-text",
    "nbt": "Minecraft-Library/nbt-factory",
    "vrh": "Minecraft-Library/vanilla-reference-harness",
    "d4j": "Simplified-Dev/discord4j-framework",
    "spring": "Simplified-Dev/spring-framework",
    "pers": "Simplified-Dev/persistence",
    "gson": "Simplified-Dev/gson-extras",
    "coll": "Simplified-Dev/collections",
    "ann": "Simplified-Dev/annotations",
    "image": "Simplified-Dev/image",
    "mojang": "Simplified-Api/mojang",
    "hypixel": "Simplified-Api/hypixel",
    "bot": "SkyBlock-Simplified/simplified-bot",
    "srv": "SkyBlock-Simplified/simplified-server",
}


def resolve_module(token):
    if token in ALIASES:
        return os.path.join(ROOT, ALIASES[token])
    return token  # explicit path (absolute or relative to cwd)


def parse_args(argv):
    target, subdir, fails = ".", "", 15
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--fails":
            fails = int(argv[i + 1]); i += 2
        elif a == "--dir":
            subdir = argv[i + 1]; i += 2
        else:
            target = a; i += 1
    return target, subdir, fails


def main(argv):
    target, subdir, fails_cap = parse_args(argv)
    label = target
    module_dir = resolve_module(target)
    # Normalize to forward slashes so display and glob agree on Windows (git-bash
    # module dirs use `/`; os.path.join would otherwise mix in `\`).
    results = os.path.join(module_dir, subdir, "build", "test-results", "test").replace("\\", "/")
    xmls = sorted(glob.glob(results + "/*.xml"))
    if not xmls:
        print(f"jtally: no result XML under {results} (nothing ran)")
        return 2

    tests = skipped = failures = errors = 0
    failing = []
    for path in xmls:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            print(f"jtally: skipping unparseable {os.path.basename(path)}: {exc}")
            continue
        tests += int(root.get("tests", 0))
        skipped += int(root.get("skipped", 0))
        failures += int(root.get("failures", 0))
        errors += int(root.get("errors", 0))
        cls = (root.get("name") or "").rsplit(".", 1)[-1]
        for tc in root.findall("testcase"):
            if tc.find("failure") is not None or tc.find("error") is not None:
                failing.append(f"{cls}::{tc.get('name')}")

    print(f"classes={len(xmls)} tests={tests} skipped={skipped} "
          f"failures={failures} errors={errors}   ({label})")
    for name in failing[:fails_cap]:
        print(f"FAIL {name}")
    extra = len(failing) - fails_cap
    if extra > 0:
        print(f"... (+{extra} more failing)")
    return 1 if (failures + errors) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
