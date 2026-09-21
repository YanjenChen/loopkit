#!/usr/bin/env bash
# Create a toy "GPU placer" repository for trying loopkit by hand.
#
#   bash tests/fixtures/make-toy-placer.sh <dir>
#
# The placer puts 10 cells on a line at x = i * spread and sleeps to simulate
# work. src/params.json holds spread and sleep: a smaller spread lowers the
# wirelength (hpwl) until cells overlap (illegal); a shorter sleep lowers the
# runtime. The repository has a submodule and no .loopkit/ yet, so
# /loopkit:init can set everything up from a request such as
# "優化hpwl與runtime，約束是cell不能重疊".
set -euo pipefail

DIR="${1:?usage: make-toy-placer.sh <dir>}"
if [ -e "$DIR" ]; then
  echo "error: $DIR already exists" >&2
  exit 1
fi
mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"

SUB="$DIR-sublib"
mkdir -p "$SUB"
git -C "$SUB" init -q
echo "shared helpers" > "$SUB/README.md"
git -C "$SUB" add -A
git -C "$SUB" commit -qm "sublib"

mkdir -p "$DIR/src" "$DIR/bench" "$DIR/tools"
cat > "$DIR/src/placer.py" <<'EOF'
"""A toy placer: cell i goes to x = i * spread; sleep stands in for the real work."""
import json
import os
import sys
import time

params = json.load(open(os.path.join(os.path.dirname(__file__), 'params.json')))
time.sleep(params['sleep'])
with open(sys.argv[1], 'w') as out:
    for i in range(10):
        out.write('c%d %r\n' % (i, i * params['spread']))
EOF
printf '{"spread": 1.5, "sleep": 0.4}\n' > "$DIR/src/params.json"
cat > "$DIR/tools/check.py" <<'EOF'
"""Legality and wirelength checker: reads a placement and the nets, prints JSON."""
import json
import sys

placement, nets = sys.argv[1], sys.argv[2]
x = {}
for line in open(placement):
    name, pos = line.split()
    x[name] = float(pos)
hpwl = sum(abs(x[a] - x[b]) for a, b in (line.split() for line in open(nets)))
xs = sorted(x.values())
overlaps = sum(1 for p, q in zip(xs, xs[1:]) if q - p < 1.0)
print(json.dumps({'hpwl': hpwl, 'overlaps': overlaps}))
EOF
printf 'c0 c1\nc1 c2\nc2 c3\nc3 c9\n' > "$DIR/bench/nets.txt"
cat > "$DIR/README.md" <<'EOF'
# toy placer

Run: `python3 src/placer.py out.txt`, then `python3 tools/check.py out.txt bench/nets.txt`.
Cells are 1.0 wide; any two closer than 1.0 overlap.
EOF
printf '__pycache__/\n*.o\n' > "$DIR/.gitignore"
git -C "$DIR" init -q
git -C "$DIR" -c protocol.file.allow=always submodule add -q "$SUB" vendor/sublib
git -C "$DIR" add -A
git -C "$DIR" commit -qm "toy placer"
echo "toy placer repository: $DIR (submodule source: $SUB)"
