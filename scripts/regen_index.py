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
import csv
import io
import json
import os
import re
import urllib.request

ROOT = "OTA/Google"
SUPPORTED_DEVICES_URL = "https://storage.googleapis.com/play_public/supported_devices.csv"


def load_device_map():
    """Official Google Play device list: codename/model -> marketing name + retail
    branding. Best-effort (network); returns {} on failure. No images, text only."""
    try:
        data = urllib.request.urlopen(SUPPORTED_DEVICES_URL, timeout=60).read()
    except Exception as e:
        print(f"supported_devices.csv fetch failed ({e}); skipping enrichment")
        return {}
    try:
        text = data.decode("utf-16")
    except Exception:
        text = data.decode("utf-8", "replace")
    by_device = {}  # codename -> [ {marketingName, retailBranding, model} ]  (codenames can repeat)
    by_model = {}   # model -> {marketingName, retailBranding}
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header: Retail Branding, Marketing Name, Device, Model
    for row in reader:
        if len(row) < 4:
            continue
        brand, marketing, device, model = (c.strip() for c in row[:4])
        info = {"marketingName": marketing, "retailBranding": brand, "model": model}
        if device:
            by_device.setdefault(device.lower(), []).append(info)
        mk = model.lower()
        if mk and mk not in by_model:
            by_model[mk] = {"marketingName": marketing, "retailBranding": brand}
    print(f"loaded {len(by_device)} device codenames from supported_devices.csv")
    return {"by_device": by_device, "by_model": by_model}


def official_name(dmap, devices, display_model, model, manufacturer, brand):
    """Resolve a device to its official marketing name / retail branding. Codenames can
    be reused by unrelated devices (e.g. 'bullhead' = Nexus 5X AND a Blackshark), so when
    a codename is ambiguous and we can't disambiguate by model/manufacturer, return {}
    rather than a wrong name."""
    bd, bm = dmap.get("by_device", {}), dmap.get("by_model", {})
    dm_l, mk_l = display_model.lower(), model.lower()
    mfr_l, br_l = (manufacturer or "").lower(), (brand or "").lower()
    for d in [x.lower() for x in devices if x]:
        cands = bd.get(d)
        if not cands:
            continue
        if len(cands) == 1:
            return cands[0]
        for c in cands:
            if c["model"].lower() in (dm_l, mk_l):
                return c
        for c in cands:
            if c["retailBranding"].lower() in (mfr_l, br_l) and (mfr_l or br_l):
                return c
        return {}  # ambiguous codename, no reliable match
    for key in (dm_l, mk_l):
        if key in bm:
            return bm[key]
    return {}
FW_FIELDS = ["preBuild", "postBuild", "postTimestamp", "otaUrl", "sizeBytes",
             "securityPatch", "sdk", "title", "description", "otaType", "source",
             "abType", "requiredCacheBytes", "partitions", "hasFirmware", "hasApex",
             "archiveUrls"]
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


def regen_model(group, model, model_dir, dmap):
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

    # Official Google Play marketing name / retail branding (by codename, then model).
    dev_list = list(devices)
    if not dev_list:
        fp = (latest.get("postBuild") or (builds[0] if builds else "")) or ""
        p = fp.split("/")
        if len(p) >= 3:
            dev_list = [p[2].split(":")[0]]
    official = official_name(dmap, dev_list, display_model, model, manufacturer, brand)
    marketing_name = official.get("marketingName", "")
    retail_branding = official.get("retailBranding", "")

    # Android release(s) parsed from the fingerprints (brand/product/device:RELEASE/...).
    def android_rel(fp):
        p = (fp or "").split("/")
        return p[2].split(":")[1] if len(p) >= 3 and ":" in p[2] else ""
    rels = set()
    for f in firmware:
        for fp in (f.get("postBuild"), f.get("preBuild")):
            r = android_rel(fp)
            if r:
                rels.add(r)
    for b in builds:
        r = android_rel(b)
        if r:
            rels.add(r)
    android_versions = sorted(rels, key=lambda x: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", x) if t])
    sdks = sorted({str(f.get("sdk")) for f in firmware if f.get("sdk")},
                  key=lambda x: (len(x), x))
    tss = [f.get("postTimestamp") for f in firmware if f.get("postTimestamp")]
    device_codename = dev_list[0] if dev_list else ""
    product_name = disp.get("ro.product.name", "")
    lb = (latest.get("postBuild") or "").split("/")
    latest_build_id = lb[3] if len(lb) >= 5 else ""

    summary = {
        "group": group, "model": model,
        "displayModel": display_model, "manufacturer": manufacturer, "brand": brand,
        "marketingName": marketing_name, "retailBranding": retail_branding,
        "device": device_codename, "productName": product_name,
        "androidVersions": android_versions, "sdks": sdks,
        "firmwareCount": len(firmware),
        "archivedCount": sum(1 for f in firmware if f.get("archiveUrls")),
        "otaTypes": sorted({f.get("otaType") for f in firmware if f.get("otaType")}),
        "abTypes": sorted({f.get("abType") for f in firmware if f.get("abType")}),
        "latestBuild": latest.get("postBuild"),
        "latestBuildId": latest_build_id,
        "latestSecurityPatch": latest.get("securityPatch"),
        "latestSdk": latest.get("sdk"),
        "firstTimestamp": min(tss) if tss else None,
        "latestTimestamp": max(tss) if tss else None,
    }

    parts = [group, model, display_model, manufacturer, brand, marketing_name, retail_branding]
    parts += devices + list(builds)
    parts += [f.get("postBuild") or "" for f in firmware]
    parts += [f.get("preBuild") or "" for f in firmware]
    parts += [f.get("securityPatch") or "" for f in firmware]
    parts += [f.get("title") or "" for f in firmware]
    parts += [strip_html(f.get("description")) for f in firmware]
    parts += [f.get("abType") or "" for f in firmware]
    parts += [" ".join(f.get("partitions") or []) for f in firmware]
    text = " ".join(p for p in parts if p).lower()
    text = re.sub(r"\s+", " ", text).strip()
    search = {
        "group": group, "model": model,
        "displayModel": display_model, "manufacturer": manufacturer, "brand": brand,
        "marketingName": marketing_name, "retailBranding": retail_branding,
        "firmwareCount": len(firmware),
        "latestSecurityPatch": summary["latestSecurityPatch"],
        "text": text,
    }
    return summary, search


def main():
    if not os.path.isdir(ROOT):
        return
    dmap = load_device_map()
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
            summary, s = regen_model(group, model, mp, dmap)
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
