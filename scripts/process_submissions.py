# -*- coding: utf-8 -*-
"""Verify app firmware submissions and promote them to firmware/*.json.

Submissions are written by the Worker under submissions/{hash}.json after only a host
allowlist + path-context check. Here we do the TRUST step: Range-fetch the real Google
OTA package and derive ALL structured fields from it (client metadata is never trusted),
sanitize the notes, then write the verified firmware under the device's group/model and
delete the submission. Unreachable/invalid submissions are left for retry (or dropped if
clearly bad).
"""
import hashlib
import json
import os
import re

from range_zip import fetch_ota_metadata
from sanitize import sanitize_html

ROOT = "OTA/Google"
SUB = "submissions"
OTA_HOSTS = {"android.googleapis.com", "ota.googlezip.net", "dl.google.com"}


def slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).lower()


def fp_hash(fp):
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]


def device_from_fp(fp):
    try:
        return fp.split("/")[2].split(":")[0]
    except IndexError:
        return ""


def allowed(url):
    m = re.match(r"^https://([^/]+)/", url or "")
    if not m:
        return False
    host = m.group(1).lower()
    return host in OTA_HOSTS or host.endswith(".googlezip.net")


def process_one(path, sub):
    ota_url = sub.get("otaUrl", "")
    if not allowed(ota_url):
        print(f"  reject (host): {path}")
        os.remove(path)
        return
    try:
        meta, size = fetch_ota_metadata(ota_url)
    except Exception as e:
        print(f"  verify failed (leave pending): {path}: {e}")
        return  # transient/geo: retry next run
    post_build = meta.get("post-build")
    if not post_build:
        print(f"  no post-build, drop: {path}")
        os.remove(path)
        return

    manufacturer = sub.get("manufacturer") or ""
    brand = sub.get("brand") or (post_build.split("/")[0] if post_build else "")
    group = slug(manufacturer or brand)
    model = slug(sub.get("model") or "")
    if not group or not model:
        print(f"  bad path context, drop: {path}")
        os.remove(path)
        return

    fw_dir = os.path.join(ROOT, group, model, "firmware")
    os.makedirs(fw_dir, exist_ok=True)
    fwp = os.path.join(fw_dir, fp_hash(post_build) + ".json")
    archive_urls = []
    if os.path.exists(fwp):
        try:
            archive_urls = json.load(open(fwp, encoding="utf-8")).get("archiveUrls", [])
        except Exception:
            pass

    post_ts = meta.get("post-timestamp")
    # incremental carries a pre-build; for a full OTA fall back to the submitter's build
    # so the chain still links by fingerprint.
    pre_build = meta.get("pre-build") or sub.get("fingerprint", "")
    device = device_from_fp(post_build)
    fw = {
        "preBuild": pre_build,
        "postBuild": post_build,
        "postTimestamp": int(post_ts) if str(post_ts).isdigit() else None,
        "otaUrl": ota_url,
        "sizeBytes": size,
        "securityPatch": meta.get("post-security-patch-level"),
        "sdk": meta.get("post-sdk-level"),
        "device": device,
        "checkinDevice": device_from_fp(sub.get("fingerprint", "")) or device,
        "title": sanitize_html(sub.get("title", ""), 200),
        "description": sanitize_html(sub.get("description", ""), 4000),
        "otaType": "incremental" if meta.get("pre-build") else "full",
        "source": "app",
        "archiveUrls": archive_urls,
    }
    json.dump(fw, open(fwp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    os.remove(path)
    print(f"  verified {group}/{model}/firmware/{os.path.basename(fwp)} ({fw['otaType']} -> {post_build})")


def main():
    if not os.path.isdir(SUB):
        return
    for name in sorted(os.listdir(SUB)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(SUB, name)
        try:
            sub = json.load(open(path, encoding="utf-8"))
        except Exception:
            os.remove(path)
            continue
        print(f"submission {name}")
        process_one(path, sub)


if __name__ == "__main__":
    main()
