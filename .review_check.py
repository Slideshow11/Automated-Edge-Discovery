#!/usr/bin/env python3
import subprocess, json, os, urllib.request

token = os.environ.get('GITHUB_TOKEN', '')
if not token:
    print('No GITHUB_TOKEN env var')
    exit(1)

AUTH = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}
REPO = 'Exploratory-Data-Science/Automated-Edge-Discovery'
HEAD = '02dae2baf91577d946c4a2da2f16083089eb0a1'

# Check-runs
req = urllib.request.Request(f'https://api.github.com/repos/{REPO}/commits/{HEAD}/check-runs', headers=AUTH)
with urllib.request.urlopen(req, timeout=10) as r:
    d = json.loads(r.read())
print(f'Check runs: {len(d.get("check_runs",[]))}')
for cr in d.get('check_runs', []):
    print(f'  {cr["name"]}: {cr["status"]}/{cr["conclusion"]}')

# PR details
req2 = urllib.request.Request(f'https://api.github.com/repos/{REPO}/pulls/187', headers=AUTH)
with urllib.request.urlopen(req2, timeout=10) as r:
    pr = json.loads(r.read())
print(f'\nPR #187: state={pr["state"]}, head={pr["head"]["sha"][:8]}, mergeable={pr.get("mergeable")}, draft={pr.get("draft")}')
print(f'  title: {pr["title"]}')

# Reviews
req3 = urllib.request.Request(f'https://api.github.com/repos/{REPO}/pulls/187/reviews', headers=AUTH)
with urllib.request.urlopen(req3, timeout=10) as r:
    reviews = json.loads(r.read())
print(f'\nReviews: {len(reviews)}')
for rv in reviews[-5:]:
    print(f'  {rv["user"]["login"]} / {rv["state"]} / {rv["commit_id"][:8]} / {str(rv.get("body","") or "")[:80]}')

# Commit comments
req4 = urllib.request.Request(f'https://api.github.com/repos/{REPO}/commits/{HEAD}/comments', headers=AUTH)
with urllib.request.urlopen(req4, timeout=10) as r:
    comments = json.loads(r.read())
print(f'\nCommit comments: {len(comments)}')
for c in comments:
    print(f'  {c["user"]["login"]} / {c["created_at"]} / {str(c["body"])[:100]}')

# PR review comments
req5 = urllib.request.Request(f'https://api.github.com/repos/{REPO}/pulls/187/comments', headers=AUTH)
with urllib.request.urlopen(req5, timeout=10) as r:
    prc = json.loads(r.read())
print(f'\nPR review comments: {len(prc)}')
for pc in prc:
    print(f'  {pc["user"]["login"]} / {pc["created_at"]} / {str(pc["body"])[:100]}')