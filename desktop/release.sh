#!/usr/bin/env bash
# Build a signed desktop release and publish it on GitHub, where installed
# copies find it (plugins.updater.endpoints in src-tauri/tauri.conf.json).
#
#   desktop/release.sh 0.1.1
#
# Needs the updater signing key (see "Desktop auto-update" in the README) and
# `gh auth login`. Builds one universal macOS app (Apple silicon and Intel).
set -euo pipefail
cd "$(dirname "$0")"

VERSION=${1:?usage: desktop/release.sh <version, e.g. 0.1.1>}
TAG="desktop-v${VERSION}"
# latest.json lives on this one fixed release, so other releases in the repo
# (which GitHub may mark "Latest") can't break the update feed.
FEED_TAG=desktop-latest
REPO=Guazey/huron-capstone
KEY=${TAURI_KEY_FILE:-$HOME/.tauri/market-sidebar.key}
BUNDLE="src-tauri/target/universal-apple-darwin/release/bundle/macos/Market Sidebar.app.tar.gz"
BUMPED=(package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock)

fail() { echo "$*" >&2; exit 1; }
[[ -f "$KEY" ]] || fail "No signing key at $KEY"
! grep -q PUBKEY_PENDING src-tauri/tauri.conf.json || fail "Put the public key in src-tauri/tauri.conf.json first."
[[ -z "$(git status --porcelain)" ]] || fail "Commit or stash your changes first."
gh auth status >/dev/null 2>&1 || fail "Run gh auth login first."
! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || fail "Tag $TAG already exists."
! gh release view "$TAG" -R "$REPO" >/dev/null 2>&1 || fail "Release $TAG already exists."

step() { printf '\n== %s\n' "$*"; }

step "1/4 version $VERSION"
# Until the release commit exists, a failure puts the version back.
trap 'git checkout -- "${BUMPED[@]}"; echo "Failed; version files restored." >&2' ERR
npm version "$VERSION" --no-git-tag-version --allow-same-version >/dev/null
python3 - "$VERSION" <<'PYEOF'
import re, sys
version = sys.argv[1]
# The first match in each file is the app's own version.
for path, pattern, replacement in (
    ("src-tauri/tauri.conf.json", r'"version": "[^"]*"', f'"version": "{version}"'),
    ("src-tauri/Cargo.toml", r'(?m)^version = "[^"]*"', f'version = "{version}"'),
):
    text = open(path).read()
    open(path, "w").write(re.sub(pattern, replacement, text, count=1))
PYEOF

step "2/4 signed universal build"
rustup target add aarch64-apple-darwin x86_64-apple-darwin >/dev/null
rm -f "$BUNDLE" "$BUNDLE.sig"   # never publish a previous build by mistake
# The password stays in this shell: never on the command line or in a file.
read -rsp "Signing key password: " TAURI_SIGNING_PRIVATE_KEY_PASSWORD; echo
TAURI_SIGNING_PRIVATE_KEY=$(cat "$KEY")
export TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PASSWORD
npm run build -- --target universal-apple-darwin --config '{"bundle":{"createUpdaterArtifacts":true}}'

step "3/4 latest.json"
OUT=$(mktemp -d)
ASSET="market-sidebar_${VERSION}_universal.app.tar.gz"
cp "$BUNDLE" "$OUT/$ASSET"
python3 - "$VERSION" "https://github.com/$REPO/releases/download/$TAG/$ASSET" \
  "$BUNDLE.sig" "$OUT/latest.json" <<'PYEOF'
import json, sys
from datetime import datetime, timezone
version, url, sig_path, out = sys.argv[1:]
build = {"signature": open(sig_path).read().strip(), "url": url}
json.dump({
    "version": version,
    "notes": f"Market Sidebar {version}",
    "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    # One universal build serves both Mac architectures.
    "platforms": {"darwin-aarch64": build, "darwin-x86_64": build},
}, open(out, "w"), indent=2)
PYEOF

step "4/4 tag and publish $TAG"
if [[ -n "$(git status --porcelain)" ]]; then
  git commit -qam "Desktop v${VERSION}"
fi
trap - ERR
git tag "$TAG"
git push -q origin HEAD "$TAG"
gh release create "$TAG" -R "$REPO" --verify-tag --title "Market Sidebar $VERSION" \
  --notes "Desktop app $VERSION. Installed copies update from here." "$OUT/$ASSET"
# Point installed copies at it last, once the build it names is downloadable.
if ! gh release view "$FEED_TAG" -R "$REPO" >/dev/null 2>&1; then
  gh release create "$FEED_TAG" -R "$REPO" --prerelease --title "Desktop update feed" \
    --notes "latest.json for the desktop app's auto-update. Don't delete."
fi
gh release upload "$FEED_TAG" -R "$REPO" --clobber "$OUT/latest.json"
echo "  published. Installed apps pick it up within 6 hours, or at once via the menu bar: Check for updates."
