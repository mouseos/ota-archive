# -*- coding: utf-8 -*-
"""Import Google OTA firmware discovered by otachecker.com into the archive.

otachecker.com is a Google-OTA-only catalog. Its API gives us, per OTA, the real
Google package URL (ota.googlezip.net / dl.google.com) plus the pre/post build
fingerprints — so we never need a device checkin (no geo restriction). We use it
purely for DISCOVERY; the actual metadata is re-derived here by Range-fetching the
real ZIP (otachecker's own parse is never trusted), exactly like app submissions.

Discovery API (polite, low rate):
  GET /api/list/filters                       -> {"brands": [[name, count], ...]}
  GET /api/ota/list?brand=B&pg=N              -> {"data": [{id, fingerprint, ...}], "pagination": {...}}
  GET /api/ota?id=ID                          -> {"ota_info": {url, referenced_fingerprints, ...}}

Resumable + batched (GitHub Actions 6h limit): a state file records processed
otachecker ids so each run only fetches/analyses new ones, up to OTACHECKER_LIMIT.

Env:
  OTACHECKER_BRANDS  comma list of brands, or "all"  (default: a major-brands set)
  OTACHECKER_LIMIT   max NEW OTAs to analyse this run (default 150)

Usage: python import_otachecker.py
"""
import hashlib
import json
import os
import sys
import time

import requests

from range_zip import fetch_ota_metadata, derive_fields
from sanitize import sanitize_html

ROOT = "OTA/Google"
API = "https://otachecker.com"
STATE = os.path.join(ROOT, ".otachecker.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ota-archive-importer"
OTA_HOSTS = ("ota.googlezip.net", "dl.google.com", "android.googleapis.com")

# Sensible default first slice; override with OTACHECKER_BRANDS=all to take everything.
DEFAULT_BRANDS = ["Nokia", "OnePlus", "Infinix", "google", "TECNO"]

_session = requests.Session()
_session.headers["User-Agent"] = UA


def slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip()).strip("-").lower()


def fp_hash(fp):
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]


def api(path, params=None, tries=5):
    """GET JSON with polite delay + backoff on 429/5xx."""
    for attempt in range(tries):
        time.sleep(0.7)  # politeness
        try:
            r = _session.get(f"{API}{path}", params=params, timeout=60)
        except requests.RequestException as e:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 10 * (attempt + 1)
            print(f"  {path} -> {r.status_code}; wait {wait}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"{path} -> HTTP {r.status_code}")
    raise RuntimeError(f"{path}: gave up after {tries} tries")


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"done": []}


def save_state(state):
    json.dump(state, open(STATE, "w", encoding="utf-8"), separators=(",", ":"))


def brand_ota_ids(brand):
    """All otachecker OTA ids for a brand (paged)."""
    ids = []
    page = 0
    while True:
        d = api("/api/ota/list", {"brand": brand, "pg": page})
        for rec in d.get("data", []):
            if "id" in rec:
                ids.append(rec["id"])
        pg = d.get("pagination", {})
        if page >= (pg.get("pages", 1) - 1):
            break
        page += 1
    return ids


def write_firmware(meta, size, entries, url, oid, info):
    post_build = meta.get("post-build")
    if not post_build:
        print(f"    id {oid}: no post-build in metadata, skip")
        return False
    brand = post_build.split("/")[0]
    device = ""
    try:
        device = post_build.split("/")[2].split(":")[0]
    except IndexError:
        pass
    group, model = slug(brand), slug(device)
    if not group or not model:
        print(f"    id {oid}: bad fingerprint {post_build}, skip")
        return False

    fw_dir = os.path.join(ROOT, group, model, "firmware")
    fwp = os.path.join(fw_dir, fp_hash(post_build) + ".json")
    is_full = not meta.get("pre-build")
    archive_urls = []
    if os.path.exists(fwp):
        try:
            existing = json.load(open(fwp, encoding="utf-8"))
        except Exception:
            existing = {}
        archive_urls = existing.get("archiveUrls", [])
        # Prefer a full OTA over an incremental for the same target build; don't
        # downgrade a full we already have (empty preBuild) to an incremental.
        if not is_full and not existing.get("preBuild"):
            print(f"    id {oid}: {post_build} already has a full OTA, skip incremental")
            return False

    extra = derive_fields(url, meta, entries)
    post_ts = meta.get("post-timestamp")
    fw = {
        "preBuild": meta.get("pre-build") or "",
        "postBuild": post_build,
        "postTimestamp": int(post_ts) if str(post_ts).isdigit() else None,
        "otaUrl": url,
        "sizeBytes": size,
        "securityPatch": meta.get("post-security-patch-level"),
        "sdk": meta.get("post-sdk-level"),
        "device": device,
        "checkinDevice": device,
        "title": sanitize_html((info.get("title") or "").replace("﻿", ""), 200),
        "description": sanitize_html((info.get("description") or "").replace("﻿", ""), 4000),
        "otaType": "incremental" if meta.get("pre-build") else "full",
        "abType": extra.get("abType"),
        "requiredCacheBytes": extra.get("requiredCacheBytes"),
        "partitions": extra.get("partitions", []),
        "hasFirmware": extra.get("hasFirmware", False),
        "hasApex": extra.get("hasApex", False),
        "source": "otachecker",
        "sourceId": oid,
        "archiveUrls": archive_urls,
    }
    os.makedirs(fw_dir, exist_ok=True)
    json.dump(fw, open(fwp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"    id {oid}: {group}/{model}/{os.path.basename(fwp)} ({fw['otaType']} -> {post_build})")
    return True


def import_one(oid):
    d = api("/api/ota", {"id": oid})
    info = d.get("ota_info", {})
    url = info.get("url")
    if not url or not any(h in url for h in OTA_HOSTS):
        print(f"    id {oid}: no usable Google url, skip")
        return False
    try:
        meta, size, entries = fetch_ota_metadata(url)
    except Exception as e:
        print(f"    id {oid}: metadata fetch failed: {e}")
        return False
    return write_firmware(meta, size, entries, url, oid, info)


def main():
    env_brands = os.environ.get("OTACHECKER_BRANDS", "").strip()
    limit = int(os.environ.get("OTACHECKER_LIMIT", "150"))
    if env_brands.lower() == "all":
        brands = [b[0] for b in api("/api/list/filters").get("brands", [])]
    elif env_brands:
        brands = [b.strip() for b in env_brands.split(",") if b.strip()]
    else:
        brands = DEFAULT_BRANDS

    state = load_state()
    done = set(state.get("done", []))
    print(f"brands={brands} limit={limit} already_done={len(done)}")

    processed = 0
    for brand in brands:
        if processed >= limit:
            break
        try:
            ids = brand_ota_ids(brand)
        except Exception as e:
            print(f"  brand {brand}: list failed: {e}")
            continue
        new_ids = [i for i in ids if i not in done]
        print(f"  brand {brand}: {len(ids)} total, {len(new_ids)} new")
        for oid in new_ids:
            if processed >= limit:
                break
            try:
                import_one(oid)
            except Exception as e:
                print(f"    id {oid}: error {e}")
            done.add(oid)
            processed += 1
            if processed % 20 == 0:
                state["done"] = sorted(done)
                save_state(state)

    state["done"] = sorted(done)
    save_state(state)
    print(f"processed {processed} new OTAs this run; done set now {len(done)}")


if __name__ == "__main__":
    main()
