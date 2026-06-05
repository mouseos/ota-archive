# -*- coding: utf-8 -*-
"""Print a JSON array of firmware/*.json paths that still need archive.org upload
(archiveUrls empty). Used to build the GitHub Actions upload matrix.

Capped at UPLOAD_BATCH (default 80) per run so the matrix stays under GitHub's
256-job limit and a single pipeline run fits the 6h window; the rest are picked up
by the next scheduled run. Oldest builds first (postTimestamp asc) for steady,
deterministic progress through a large backlog."""
import json
import os

ROOT = "OTA/Google"
BATCH = int(os.environ.get("UPLOAD_BATCH", "150"))
MAX_FAILURES = int(os.environ.get("UPLOAD_MAX_FAILURES", "5"))

pending = []
for dirpath, _dirs, files in os.walk(ROOT):
    if os.path.basename(dirpath) != "firmware":
        continue
    for f in sorted(files):
        if not f.endswith(".json"):
            continue
        p = os.path.join(dirpath, f).replace("\\", "/")
        try:
            fw = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (not fw.get("archiveUrls") and fw.get("otaUrl")
                and fw.get("archiveFailures", 0) < MAX_FAILURES):
            pending.append((fw.get("postTimestamp") or 0, p))

pending.sort(key=lambda x: x[0])
paths = [p for _, p in pending[:BATCH]]
print(json.dumps(paths, separators=(",", ":")))
