#!/bin/sh
# Refuse to commit personal data: local paths, the Mac/Pi user name, or a non-noreply author email.
# Same idea as the release check in BuffasBilverkstad (tools/package-macos.sh).
#   tools/privacy-check.sh          check staged files (used by .githooks/pre-commit)
#   tools/privacy-check.sh --all    check every tracked file
set -eu
cd "$(git rev-parse --show-toplevel)"
fail=0

email=$(git config user.email || true)
case "$email" in
	*@users.noreply.github.com) ;;
	*) echo "privacy: author email '$email' is not a GitHub noreply address" >&2; fail=1 ;;
esac

if [ "${1:-}" = "--all" ]; then files=$(git ls-files); else files=$(git diff --cached --name-only --diff-filter=ACMR); fi
pat="/Users/|/home/$(id -un)|$(id -un)|@icloud\.com|@me\.com|@gmail\.com"
for f in $files; do
	[ "$f" = tools/privacy-check.sh ] && continue
	hits=$(git show ":$f" 2>/dev/null | grep -naIE "$pat" || true)
	[ -z "$hits" ] || { echo "$hits" | sed "s|^|$f:|" >&2; fail=1; }
done

# Private data that only belongs on the Pi
for f in $files; do
	case "$f" in
		*.sqlite|*.sqlite-*|*.db|outputs/*|*.log|.env|*.local.json) echo "privacy: $f must not be committed" >&2; fail=1 ;;
	esac
done

[ $fail -eq 0 ] || { echo "privacy check failed (above); nothing committed" >&2; exit 1; }
