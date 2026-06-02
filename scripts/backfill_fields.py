# -*- coding: utf-8 -*-
"""One-off migration: add the OTA-derived fields (abType, requiredCacheBytes,
partitions, hasFirmware, hasApex) to existing firmware/*.json that predate them.

Range-fetches each firmware's otaUrl (no checkin) and merges the new fields in place,
preserving everything else. Idempotent and resumable: skips firmware that already carry
abType unless --force is given.

Usage: python backfill_fields.py [OTA/Google[/group/model]] [--force]
"""
import json
import os
import sys

from range_zip import fetch_ota_metadata, derive_fields

ROOT = "OTA/Google"


def iter_firmware(base):
    for dirpath, _, files in os.walk(base):
        if os.path.basename(dirpath) != "firmware":
            continue
        for n in sorted(files):
            if n.endswith(".json"):
                yield os.path.join(dirpath, n)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    base = args[0] if args else ROOT
    if not os.path.isdir(base):
        print(f"no such dir: {base}")
        return
    done = skip = fail = 0
    for path in iter_firmware(base):
        try:
            fw = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"  bad json {path}: {e}")
            continue
        if fw.get("abType") and not force:
            skip += 1
            continue
        url = fw.get("otaUrl")
        if not url:
            continue
        try:
            meta, size, entries = fetch_ota_metadata(url)
            extra = derive_fields(url, meta, entries)
        except Exception as e:
            print(f"  fetch failed {path}: {e}")
            fail += 1
            continue
        fw["sizeBytes"] = size
        fw["abType"] = extra.get("abType")
        fw["requiredCacheBytes"] = extra.get("requiredCacheBytes")
        fw["partitions"] = extra.get("partitions", [])
        fw["hasFirmware"] = extra.get("hasFirmware", False)
        fw["hasApex"] = extra.get("hasApex", False)
        json.dump(fw, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        done += 1
        print(f"  {extra.get('abType')}  parts={','.join(extra.get('partitions') or []) or '-'}  {path}")
    print(f"backfilled {done}, skipped {skip}, failed {fail}")


if __name__ == "__main__":
    main()
