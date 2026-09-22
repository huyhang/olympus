#!/usr/bin/env bash
# Re-vendor the librarian contract from a Nineveh checkout.
#
#     scripts/refresh-contract.sh [path-to-nineveh]
#
# Regenerates Nineveh's contracts, copies the librarian slice here, and
# rewrites contracts/SOURCE. Review `git diff contracts/` afterwards: that
# diff is the whole point of vendoring rather than fetching at build time.
set -euo pipefail

NINEVEH="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../nineveh" && pwd)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLICE="$NINEVEH/docs/librarian-openapi.json"

[ -d "$NINEVEH" ] || { echo "No Nineveh checkout at $NINEVEH" >&2; exit 1; }

# Regenerate first, so a contract that was never exported after an API change
# is caught here rather than copied forward stale.
if [ -x "$NINEVEH/.venv/bin/python" ]; then
  (cd "$NINEVEH" && .venv/bin/python scripts/export-openapi.py >/dev/null)
else
  echo "note: no venv in $NINEVEH — copying whatever is committed there" >&2
fi

[ -f "$SLICE" ] || { echo "No librarian contract at $SLICE" >&2; exit 1; }
cp "$SLICE" "$HERE/contracts/librarian-openapi.json"

COMMIT="$(git -C "$NINEVEH" rev-parse HEAD)"
DIRTY=""
git -C "$NINEVEH" diff --quiet || DIRTY="  (uncommitted changes present)"

python3 - "$HERE" "$COMMIT" "$DIRTY" <<'PY'
import datetime, hashlib, json, sys
from pathlib import Path

here, commit, dirty = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
contract = here / "contracts" / "librarian-openapi.json"
document = json.loads(contract.read_text())
operations = sum(
    1 for item in document["paths"].values() for method in item
    if method in {"get", "post", "put", "patch", "delete"}
)
(here / "contracts" / "SOURCE").write_text(
    f"""# Provenance for librarian-openapi.json. Refresh with scripts/refresh-contract.sh.
source:      nineveh
commit:      {commit}{dirty}
version:     {document["info"]["version"]}
operations:  {operations}
sha256:      {hashlib.sha256(contract.read_bytes()).hexdigest()}
taken:       {datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}

# Pin on sha256, not on version. Nineveh's info.version tracks its package and
# bumps on releases that leave these operations untouched; the hash changes
# exactly when the surface Cleo calls changes.
"""
)
print(f"vendored {operations} operations from {commit[:12]}{dirty}")
PY
