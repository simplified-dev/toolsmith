#!/usr/bin/env bash
# modules.sh - module-alias table for the shell helpers (gw / jtally). Mirrors the
# machine-readable inventory in toolsmith (src/toolsmith/modules.py: ALIASES + PACKAGE_ROOTS,
# and notes/analysis/module-inventory.md); keep them in sync when a module is added.
#
# Install: place at ~/.claude/bin/modules.sh (sourced by gw and jtally).
# Purpose: kill the ~5900 hand-typed `cd "W:/Workspace/Java/Simplified/.../module"`
# prefixes by mapping a short alias to the module's absolute directory in ONE place.
#
# Usage (from another script):  source "<dir>/modules.sh"; simplified_module_dir ar
#
# Override the root without editing this file:  export SIMPLIFIED_ROOT=/some/other/path

: "${SIMPLIFIED_ROOT:=W:/Workspace/Java/Simplified}"

# simplified_module_dir <alias> -> echoes absolute module dir, or empty if unknown.
simplified_module_dir() {
    local a="$1" r="$SIMPLIFIED_ROOT"
    case "$a" in
        ar)           echo "$r/Minecraft-Library/asset-renderer" ;;
        mt)           echo "$r/Minecraft-Library/minecraft-text" ;;
        nbt)          echo "$r/Minecraft-Library/nbt-factory" ;;
        vrh)          echo "$r/Minecraft-Library/vanilla-reference-harness" ;;
        github)       echo "$r/Simplified-Api/github" ;;
        hypixel)      echo "$r/Simplified-Api/hypixel" ;;
        mojang)       echo "$r/Simplified-Api/mojang" ;;
        skyblockdata) echo "$r/Simplified-Api/skyblock" ;;
        annotations)  echo "$r/Simplified-Dev/annotations" ;;
        client)       echo "$r/Simplified-Dev/client" ;;
        coll)         echo "$r/Simplified-Dev/collections" ;;
        dataflow)     echo "$r/Simplified-Dev/dataflow" ;;
        d4j)          echo "$r/Simplified-Dev/discord4j-framework" ;;
        expression)   echo "$r/Simplified-Dev/expression" ;;
        gson)         echo "$r/Simplified-Dev/gson-extras" ;;
        image)        echo "$r/Simplified-Dev/image" ;;
        manager)      echo "$r/Simplified-Dev/manager" ;;
        pers)         echo "$r/Simplified-Dev/persistence" ;;
        refl)         echo "$r/Simplified-Dev/reflection" ;;
        scheduler)    echo "$r/Simplified-Dev/scheduler" ;;
        spring)       echo "$r/Simplified-Dev/spring-framework" ;;
        utils)        echo "$r/Simplified-Dev/utils" ;;
        yaml)         echo "$r/Simplified-Dev/yaml" ;;
        toolsmith)    echo "$r/Simplified-Dev/toolsmith" ;;
        sbsapi)       echo "$r/SkyBlock-Simplified/sbs-api" ;;
        bot)          echo "$r/SkyBlock-Simplified/simplified-bot" ;;
        data)         echo "$r/SkyBlock-Simplified/simplified-data" ;;
        srv)          echo "$r/SkyBlock-Simplified/simplified-server" ;;
        *)            echo "" ;;
    esac
}

# simplified_module_aliases -> prints the known alias list (for usage/errors).
simplified_module_aliases() {
    echo "ar mt nbt vrh github hypixel mojang skyblockdata annotations client coll dataflow d4j expression gson image manager pers refl scheduler spring utils yaml toolsmith sbsapi bot data srv"
}
