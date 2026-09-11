#!/bin/bash
set -euo pipefail
last=$(git log -1 --format=%ct)
now=$(date +%s)
if (( now - last < 40 * 86400 )); then exit 0; fi
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
date -u +%FT%TZ > .github/activity
git add .github/activity
git commit -m 'Maintain scheduled upstream checks [skip ci]'
git push origin HEAD:main
