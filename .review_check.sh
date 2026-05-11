#!/bin/bash
set -e
TOKEN="${GITHUB_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "No GITHUB_TOKEN"
  exit 1
fi

REPO="Exploratory-Data-Science/Automated-Edge-Discovery"
HEAD="02dae2baf91577d946c4a2da2f16083089eb0a1"
AUTH="Authorization: token $TOKEN"
JSON_ACCEPT="Accept: application/vnd.github.v3+json"

echo "=== Check runs for $HEAD ==="
curl -s -H "$AUTH" -H "$JSON_ACCEPT" \
  "https://api.github.com/repos/$REPO/commits/$HEAD/check-runs" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'Count: {len(d.get(\"check_runs\",[]))}')
for cr in d.get('check_runs',[]):
    print(f'  {cr[\"name\"]}: {cr[\"status\"]}/{cr[\"conclusion\"]}')
"

echo ""
echo "=== PR #187 ==="
curl -s -H "$AUTH" -H "$JSON_ACCEPT" \
  "https://api.github.com/repos/$REPO/pulls/187" | python3 -c "
import sys,json
pr=json.load(sys.stdin)
print(f'state={pr[\"state\"]} head={pr[\"head\"][\"sha\"][:8]} mergeable={pr.get(\"mergeable\")} draft={pr.get(\"draft\")}')
print(f'title: {pr[\"title\"]}')
"

echo ""
echo "=== Reviews ==="
curl -s -H "$AUTH" -H "$JSON_ACCEPT" \
  "https://api.github.com/repos/$REPO/pulls/187/reviews" | python3 -c "
import sys,json
reviews=json.load(sys.stdin)
print(f'Count: {len(reviews)}')
for r in reviews[-5:]:
    body=str(r.get('body','') or'')[:80]
    print(f'  {r[\"user\"][\"login\"]}/{r[\"state\"]}/{r[\"commit_id\"][:8]}/{body}')
"

echo ""
echo "=== Commit comments ==="
curl -s -H "$AUTH" -H "$JSON_ACCEPT" \
  "https://api.github.com/repos/$REPO/commits/$HEAD/comments" | python3 -c "
import sys,json
comments=json.load(sys.stdin)
print(f'Count: {len(comments)}')
for c in comments:
    print(f'  {c[\"user\"][\"login\"]}/{c[\"created_at\"]}/{str(c[\"body\"])[:100]}')
"

echo ""
echo "=== PR review comments ==="
curl -s -H "$AUTH" -H "$JSON_ACCEPT" \
  "https://api.github.com/repos/$REPO/pulls/187/comments" | python3 -c "
import sys,json
prc=json.load(sys.stdin)
print(f'Count: {len(prc)}')
for pc in prc:
    print(f'  {pc[\"user\"][\"login\"]}/{pc[\"created_at\"]}/{str(pc[\"body\"])[:100]}')
"