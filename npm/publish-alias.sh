#!/usr/bin/env bash
# Publish the gemini-computer-use alias package - but only once the package it
# points at is actually installable.
#
# Why the guard exists: the alias is a thin wrapper whose only dependency is
# `remote-harness-mcp@^<version>`. Publishing it before that version resolves
# makes `npm install gemini-computer-use` fail with ETARGET for everyone,
# including users upgrading from the pre-rename package. And "npm publish
# succeeded" is not proof: a brand-new package name can be held by npm for review
# for a while, during which the registry serves a `0.0.0-stage` placeholder
# instead of the published version.
#
# Usage:
#   npm/publish-alias.sh [extra npm publish flags]
#
# The node package itself is published from the repository root first:
#   npm publish --access public
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALIAS_DIR="$ROOT/npm/gemini-computer-use"

main_version="$(node -p "require('$ROOT/package.json').version")"
alias_version="$(node -p "require('$ALIAS_DIR/package.json').version")"
if [ "$main_version" != "$alias_version" ]; then
    echo "version mismatch: package.json=$main_version, alias=$alias_version" >&2
    echo "the alias ships in lockstep with the node package - bump both" >&2
    exit 1
fi

# Resolvable means the registry answers for that exact version; the review
# placeholder does not.
if ! npm view "remote-harness-mcp@$main_version" version >/dev/null 2>&1; then
    echo "remote-harness-mcp@$main_version is not installable yet." >&2
    echo "Wait for the registry to serve it (a new name can sit in npm review as a" >&2
    echo "0.0.0-stage placeholder), then run this script again." >&2
    exit 1
fi

echo "publishing gemini-computer-use@$alias_version -> remote-harness-mcp@^$main_version"
cd "$ALIAS_DIR"
exec npm publish --access public "$@"
