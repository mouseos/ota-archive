# -*- coding: utf-8 -*-
"""Regenerate timestamps.json and index.json for every model (single writer,
so concurrent Worker getprop commits never race on a shared file).

timestamps.json : sorted list of known ro.build.date.utc / post-timestamps
index.json      : [{ts, hasGetprop, hasFirmware, archived}]
"""
import json
import os

ROOT = "OTA/Google"


def regen_model(model_dir):
    entries = []
    tss = []
    for d in sorted(os.listdir(model_dir)):
        p = os.path.join(model_dir, d)
        if not (d.isdigit() and os.path.isdir(p)):
            continue
        has_gp = os.path.exists(os.path.join(p, "getprop.json"))
        fw_path = os.path.join(p, "firmware.json")
        has_fw = os.path.exists(fw_path)
        archived = False
        if has_fw:
            try:
                archived = bool(json.load(open(fw_path, encoding="utf-8")).get("archiveUrls"))
            except Exception:
                archived = False
        tss.append(int(d))
        entries.append({"ts": int(d), "hasGetprop": has_gp,
                        "hasFirmware": has_fw, "archived": archived})
    tss.sort()
    entries.sort(key=lambda e: e["ts"])
    json.dump(tss, open(os.path.join(model_dir, "timestamps.json"), "w"),
              separators=(",", ":"))
    json.dump(entries, open(os.path.join(model_dir, "index.json"), "w"),
              ensure_ascii=False, indent=0)


def main():
    if not os.path.isdir(ROOT):
        return
    for brand in sorted(os.listdir(ROOT)):
        bp = os.path.join(ROOT, brand)
        if not os.path.isdir(bp):
            continue
        for model in sorted(os.listdir(bp)):
            mp = os.path.join(bp, model)
            if os.path.isdir(mp):
                regen_model(mp)
                print(f"regen {mp}")


if __name__ == "__main__":
    main()
