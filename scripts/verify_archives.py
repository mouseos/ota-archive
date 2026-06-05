# -*- coding: utf-8 -*-
"""Verify archive.org mirrors for all firmware.

For each firmware.json:
  - If archiveUrls is set: HEAD-check each URL. Clear dead ones so the entry
    re-enters the upload queue automatically.
  - Tally unmirrored (otaUrl present, archiveUrls empty).

Exits 0 always (warnings only); use the printed summary in CI.

Usage: python verify_archives.py [OTA/Google]
"""
import json
import os
import sys
import time

import requests

ROOT = "OTA/Google"
_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "ota-archive-verifier"


def head_ok(url: str) -> bool:
    try:
        r = _SESSION.head(url, timeout=15, allow_redirects=True)
        return r.status_code in (200, 206)
    except Exception:
        return False


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else ROOT
    total = unmirrored = dead_cleared = verified_ok = 0
    for dirpath, _dirs, files in os.walk(base):
        if os.path.basename(dirpath) != "firmware":
            continue
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                fw = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            total += 1
            if not fw.get("otaUrl"):
                continue
            urls = fw.get("archiveUrls") or []
            if not urls:
                unmirrored += 1
                continue
            # Check each mirror URL
            alive = [u for u in urls if head_ok(u)]
            time.sleep(0.3)  # be polite
            if len(alive) == len(urls):
                verified_ok += len(alive)
            else:
                dead = set(urls) - set(alive)
                print(f"  DEAD {dead}  in {p}")
                fw["archiveUrls"] = alive
                json.dump(fw, open(p, "w", encoding="utf-8"),
                          indent=2, ensure_ascii=False)
                dead_cleared += len(dead)
                if not alive:
                    unmirrored += 1

    print(f"firmware total  : {total}")
    print(f"verified alive  : {verified_ok}")
    print(f"dead urls cleared: {dead_cleared}")
    print(f"unmirrored      : {unmirrored}")
    if unmirrored > 0:
        print(f"::notice::unmirrored={unmirrored} firmware will be queued for upload")


if __name__ == "__main__":
    main()
