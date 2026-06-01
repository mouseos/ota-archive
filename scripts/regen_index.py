# -*- coding: utf-8 -*-
"""Regenerate per-model index.json plus the site-wide catalog.json / search.json
(single writer, so concurrent Worker getprop commits never race on shared files).

Per model:  OTA/Google/{group}/{model}/index.json
  { "firmware": [ {preBuild, postBuild, postTimestamp, otaUrl, sizeBytes,
                   securityPatch, sdk, title, description, otaType, archiveUrls}, ... ],
    "builds":   [ <ro.build.fingerprint>, ... ] }   # for the app precheck

Site-wide (for a static distribution site reading jsDelivr; jsDelivr can't list dirs):
  OTA/Google/catalog.json  — {groups:[{group, models:[{model, displayModel, manufacturer,
                              brand, firmwareCount, archivedCount, otaTypes, latest*}]}]}
  OTA/Google/search.json   — [{group, model, displayModel, manufacturer, brand,
                              firmwareCount, latestSecurityPatch, text}]  (text = lowercased
                              haystack of names/builds/patches/titles/descriptions for
                              client-side substring search)
"""
import json
import os
import re

ROOT = "OTA/Google"
FW_FIELDS = ["preBuild", "postBuild", "postTimestamp", "otaUrl", "sizeBytes",
             "securityPatch", "sdk", "title", "description", "otaType", "archiveUrls"]
_TAG = re.compile(r"<[^>]+>")


def strip_html(s):
    return _TAG.sub(" ", s or "").replace("&nbsp;", " ")


def load_jsons(d):
    out = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                try:
                    out.append(json.load(open(os.path.join(d, n), encoding="utf-8")))
                except Exception:
                    pass
    return out


def regen_model(group, model, model_dir):
    fw_raw = load_jsons(os.path.join(model_dir, "firmware"))
    getprops = load_jsons(os.path.join(model_dir, "getprop"))

    firmware = sorted(({k: f.get(k) for k in FW_FIELDS} for f in fw_raw),
                      key=lambda e: (e.get("postTimestamp") or 0))
    builds = sorted({g.get("ro.build.fingerprint") for g in getprops
                     if g.get("ro.build.fingerprint")})
    json.dump({"firmware": firmware, "builds": builds},
              open(os.path.join(model_dir, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    disp = getprops[0] if getprops else {}
    manufacturer = disp.get("ro.product.manufacturer", "")
    brand = disp.get("ro.product.brand", "")
    display_model = disp.get("ro.product.model", model)
    devices = sorted({g.get("ro.product.device", "") for g in getprops
                      if g.get("ro.product.device")})
    latest = firmware[-1] if firmware else {}

    summary = {
        "group": group, "model": model,
        "displayModel": display_model, "manufacturer": manufacturer, "brand": brand,
        "firmwareCount": len(firmware),
        "archivedCount": sum(1 for f in firmware if f.get("archiveUrls")),
        "otaTypes": sorted({f.get("otaType") for f in firmware if f.get("otaType")}),
        "latestBuild": latest.get("postBuild"),
        "latestSecurityPatch": latest.get("securityPatch"),
        "latestSdk": latest.get("sdk"),
        "latestTimestamp": latest.get("postTimestamp"),
    }

    parts = [group, model, display_model, manufacturer, brand]
    parts += devices + list(builds)
    parts += [f.get("postBuild") or "" for f in firmware]
    parts += [f.get("preBuild") or "" for f in firmware]
    parts += [f.get("securityPatch") or "" for f in firmware]
    parts += [f.get("title") or "" for f in firmware]
    parts += [strip_html(f.get("description")) for f in firmware]
    text = " ".join(p for p in parts if p).lower()
    text = re.sub(r"\s+", " ", text).strip()
    search = {
        "group": group, "model": model,
        "displayModel": display_model, "manufacturer": manufacturer, "brand": brand,
        "firmwareCount": len(firmware),
        "latestSecurityPatch": summary["latestSecurityPatch"],
        "text": text,
    }
    return summary, search


def main():
    if not os.path.isdir(ROOT):
        return
    groups = {}
    search = []
    for group in sorted(os.listdir(ROOT)):
        gp = os.path.join(ROOT, group)
        if not os.path.isdir(gp):
            continue
        for model in sorted(os.listdir(gp)):
            mp = os.path.join(gp, model)
            if not os.path.isdir(mp):
                continue
            summary, s = regen_model(group, model, mp)
            # per-model index.json is always written (the app precheck uses builds[]),
            # but the browse catalog/search only list models that actually have firmware.
            if summary["firmwareCount"] > 0:
                groups.setdefault(group, []).append(summary)
                search.append(s)
            print(f"regen {mp}: {summary['firmwareCount']} firmware")
    catalog = {"groups": [{"group": g, "models": groups[g]} for g in sorted(groups)]}
    json.dump(catalog, open(os.path.join(ROOT, "catalog.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump(search, open(os.path.join(ROOT, "search.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"catalog: {len(groups)} groups | search: {len(search)} models")


if __name__ == "__main__":
    main()
