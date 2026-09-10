#!/usr/bin/env bash
# Reference worker adapter: hand a MiniFleet packet to the Codex CLI.
#
#   python3 -m minifleet run --intent intents/linksvc.json \
#       --dispatcher "command:./examples/codex_worker.sh"
#
# MiniFleet calls this as:  <script> <packet.md> <result.json>
# The script must leave the worker's edits inside the packet's worktree and write a
# result JSON of the form {"status": "submitted", "files": [...], "notes": "..."}.
set -euo pipefail

packet="${1:?packet path required}"
result="${2:?result path required}"

worktree="$(python3 - "$packet" <<'PY'
import json, re, sys
text = open(sys.argv[1]).read()
match = re.search(r"^Run:.*$", text, re.M)
json_path = sys.argv[1].replace(".md", ".json")
print(json.load(open(json_path))["worktree"])
PY
)"

prompt="$(cat "$packet")"

cd "$worktree"
codex exec --sandbox workspace-write --skip-git-repo-check "$prompt"

python3 - "$result" "$worktree" <<'PY'
import json, subprocess, sys
result_path, worktree = sys.argv[1], sys.argv[2]
files = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=worktree,
                       capture_output=True, text=True).stdout.splitlines()
payload = {
    "status": "submitted",
    "files": sorted(line[3:] for line in files if line.strip()),
    "notes": "codex exec worker",
}
open(result_path, "w").write(json.dumps(payload, indent=2))
PY
