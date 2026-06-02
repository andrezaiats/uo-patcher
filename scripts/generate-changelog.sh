#!/usr/bin/env bash
#
# Generates CHANGELOG.md from conventional commits, grouped by date.
# Usage: scripts/generate-changelog.sh [output-file]

set -euo pipefail

OUTPUT="${1:-CHANGELOG.md}"
REPO_URL="https://github.com/andrezaiats/uo-patcher"

# Conventional commit pattern
CC_PATTERN='^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?(!)?: .+'

# Map type prefixes to section headers
type_header() {
    case "$1" in
        feat)     echo "Features" ;;
        fix)      echo "Bug Fixes" ;;
        docs)     echo "Documentation" ;;
        perf)     echo "Performance" ;;
        refactor) echo "Refactor" ;;
        style)    echo "Styling" ;;
        test)     echo "Testing" ;;
        build)    echo "Build" ;;
        ci)       echo "CI" ;;
        chore)    echo "Miscellaneous" ;;
        revert)   echo "Reverted" ;;
        *)        echo "Other" ;;
    esac
}

{
    echo "# Changelog"
    echo ""
    echo "All notable changes to this project will be documented in this file."
    echo "Format follows [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)."
    echo ""

    current_date=""
    current_type=""
    declare -A seen_types

    # Read commits newest-first: date, hash, subject
    while IFS='|' read -r date hash subject; do
        # Skip non-conventional commits
        if ! echo "$subject" | grep -qE "$CC_PATTERN"; then
            continue
        fi

        # Extract type and rest of message
        type=$(echo "$subject" | sed -E 's/^([a-z]+)(\([^)]*\))?(!)?: .*/\1/')
        scope=$(echo "$subject" | sed -nE 's/^[a-z]+\(([^)]+)\)(!)?: .*/\1/p')
        breaking=$(echo "$subject" | sed -nE 's/^[a-z]+(\([^)]*\))?(!): .*/!/p')
        message=$(echo "$subject" | sed -E 's/^[a-z]+(\([^)]*\))?(!)?: //')
        short_hash="${hash:0:7}"

        # New date section
        if [ "$date" != "$current_date" ]; then
            if [ -n "$current_date" ]; then
                echo ""
            fi
            echo "## $date"
            echo ""
            current_date="$date"
            current_type=""
            seen_types=()
        fi

        # New type subsection
        header=$(type_header "$type")
        if [ "${seen_types[$type]:-}" != "1" ]; then
            if [ -n "$current_type" ]; then
                echo ""
            fi
            echo "### $header"
            echo ""
            current_type="$type"
            seen_types[$type]="1"
        fi

        # Format the entry
        entry="- "
        if [ -n "$scope" ]; then
            entry+="**${scope}:** "
        fi
        if [ -n "$breaking" ]; then
            entry+="[**BREAKING**] "
        fi
        entry+="${message} ([${short_hash}](${REPO_URL}/commit/${hash}))"
        echo "$entry"

    done < <(git log --format="%ad|%H|%s" --date=format:"%Y-%m-%d" --no-merges)

    echo ""
} > "$OUTPUT"

echo "Generated $OUTPUT"
