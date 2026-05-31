# -*- coding: utf-8 -*-
"""Print a JSON array of firmware.json paths that still need archive.org upload
(archiveUrls empty). Used to build the GitHub Actions upload matrix."""
import json
import os

ROOT = "OTA/Google"
pending = []
for dirpath, _dirs, files in os.walk(ROOT):
    if "firmware.json" in files:
        p = os.path.join(dirpath, "firmware.json").replace("\\", "/")
        try:
            fw = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not fw.get("archiveUrls") and fw.get("otaUrl"):
            pending.append(p)
print(json.dumps(pending, separators=(",", ":")))
