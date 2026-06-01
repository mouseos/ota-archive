# -*- coding: utf-8 -*-
"""Regenerate index.json for every model (single writer, so concurrent Worker
getprop commits never race on a shared file).

index.json = {
  "firmware": [ {preBuild, postBuild, postTimestamp, otaUrl, sizeBytes,
                 securityPatch, sdk, title, description, otaType, archiveUrls}, ... ],
  "builds":   [ <ro.build.fingerprint>, ... ]   # recorded getprops, for the app precheck
}

The app fetches this once and walks the chain in-memory by matching its current
fingerprint against firmware[].preBuild (no per-firmware requests, no timestamps).
"""
import json
import os

ROOT = "OTA/Google"
FW_FIELDS = ["preBuild", "postBuild", "postTimestamp", "otaUrl", "sizeBytes",
             "securityPatch", "sdk", "title", "description", "otaType", "archiveUrls"]


def regen_model(model_dir):
    fw_dir = os.path.join(model_dir, "firmware")
    gp_dir = os.path.join(model_dir, "getprop")
    firmware = []
    if os.path.isdir(fw_dir):
        for n in sorted(os.listdir(fw_dir)):
            if not n.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(fw_dir, n), encoding="utf-8"))
            except Exception:
                continue
            firmware.append({k: d.get(k) for k in FW_FIELDS})
    firmware.sort(key=lambda e: (e.get("postTimestamp") or 0))
    builds = []
    if os.path.isdir(gp_dir):
        for n in sorted(os.listdir(gp_dir)):
            if not n.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(gp_dir, n), encoding="utf-8"))
            except Exception:
                continue
            fp = d.get("ro.build.fingerprint")
            if fp:
                builds.append(fp)
    index = {"firmware": firmware, "builds": sorted(set(builds))}
    json.dump(index, open(os.path.join(model_dir, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"regen {model_dir}: {len(firmware)} firmware, {len(builds)} builds")


def main():
    if not os.path.isdir(ROOT):
        return
    for group in sorted(os.listdir(ROOT)):
        gp = os.path.join(ROOT, group)
        if not os.path.isdir(gp):
            continue
        for model in sorted(os.listdir(gp)):
            mp = os.path.join(gp, model)
            if os.path.isdir(mp):
                regen_model(mp)


if __name__ == "__main__":
    main()
