# -*- coding: utf-8 -*-
"""Resolve firmware chains from collected getprop, keyed by build fingerprint.

Layout per model:  OTA/Google/{group}/{model}/
  getprop/{sha1(fingerprint)}.json   # device builds (seeds), written by the Worker
  firmware/{sha1(postBuild)}.json     # one OTA, identified by the build it PRODUCES
  index.json                          # regenerated aggregate (regen_index.py)

The chain links by fingerprint, the only id present in BOTH the device props
(ro.build.fingerprint) and OTA metadata (pre-build/post-build), so resolution works
even when ro.build.date.utc is missing. A firmware's identity is its post-build (the
build it installs); pre-build is just the build it updates from.

checkin keeps both fixes used by the app:
  - ro.build.date.utc as the build timestamp when present (else 0)
  - ro.product.device fallback when ro.product.model yields no update_url

Usage: python resolve.py OTA/Google
       python resolve.py OTA/Google/SHARP/SBM801FJ
"""
import gzip
import hashlib
import json
import os
import sys

import requests

from checkin_pb2 import AndroidCheckinRequest, AndroidCheckinResponse
from logs_pb2 import AndroidCheckinProto, AndroidBuildProto
from range_zip import fetch_ota_metadata
from sanitize import sanitize_html

ROOT = "OTA/Google"
CHECKIN_URL = "https://android.clients.google.com/checkin"


def fp_hash(fp):
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]


def device_from_fp(fp):
    try:
        return fp.split("/")[2].split(":")[0]
    except IndexError:
        return ""


def ota_type(meta):
    # Incremental OTAs carry a pre-build (the specific source); full OTAs do not.
    return "incremental" if meta.get("pre-build") else "full"


def _checkin(fingerprint, device, build_utc):
    req = AndroidCheckinRequest()
    req.digest = "1-da39a3ee5e6b4b0d3255bfef95601890afd80709"
    req.locale = "en_US"
    req.version = 3
    b = AndroidBuildProto()
    b.id = fingerprint
    b.device = device
    try:
        b.timestamp = int(build_utc)
    except (TypeError, ValueError):
        b.timestamp = 0
    cp = AndroidCheckinProto()
    cp.build.MergeFrom(b)
    req.checkin.MergeFrom(cp)
    body = gzip.compress(req.SerializeToString())
    try:
        r = requests.post(
            CHECKIN_URL, data=body,
            headers={"Content-Encoding": "gzip", "Content-Type": "application/x-protobuf"},
            timeout=60,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    resp = AndroidCheckinResponse()
    resp.ParseFromString(r.content)
    out = {"url": "", "title": "", "description": ""}
    for s in resp.setting:
        n = s.name.decode("utf-8", "replace")
        v = s.value.decode("utf-8", "replace")
        if n == "update_url":
            out["url"] = v
        elif n == "update_title":
            out["title"] = v
        elif n == "update_description":
            out["description"] = v
    return out if out["url"] else None


def check_with_fallback(fp, model, device, build_utc):
    """Try ro.product.model first, fall back to ro.product.device."""
    tried = []
    for dev in (model, device):
        if dev and dev not in tried:
            tried.append(dev)
            r = _checkin(fp, dev, build_utc)
            if r:
                return r, dev
    return None, None


def write_firmware(fw_dir, pre_build, meta, size, url, result, used_dev, device):
    post_build = meta.get("post-build")
    post_ts = meta.get("post-timestamp")
    if not post_build:
        return None
    os.makedirs(fw_dir, exist_ok=True)
    fwp = os.path.join(fw_dir, fp_hash(post_build) + ".json")
    archive_urls = []
    if os.path.exists(fwp):
        try:
            archive_urls = json.load(open(fwp, encoding="utf-8")).get("archiveUrls", [])
        except Exception:
            pass
    fw = {
        "preBuild": pre_build,                          # build this OTA updates FROM
        "postBuild": post_build,                        # identity: the build it installs
        "postTimestamp": int(post_ts) if str(post_ts).isdigit() else None,
        "otaUrl": url,
        "sizeBytes": size,
        "securityPatch": meta.get("post-security-patch-level"),
        "sdk": meta.get("post-sdk-level"),
        "device": device,
        "checkinDevice": used_dev,
        "title": sanitize_html(result.get("title"), 200),
        "description": sanitize_html(result.get("description"), 4000),
        "otaType": ota_type(meta),
        "source": "checkin",
        "archiveUrls": archive_urls,
    }
    json.dump(fw, open(fwp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"  wrote firmware/{os.path.basename(fwp)}  ({fw['otaType']} -> {post_build})")
    return post_build, post_ts


def chain_from(model_dir, props, seen):
    fp = props.get("ro.build.fingerprint", "")
    model = props.get("ro.product.model", "")
    device = props.get("ro.product.device", "") or device_from_fp(fp)
    utc = props.get("ro.build.date.utc", "")
    build_utc = utc if str(utc).isdigit() else "0"
    fw_dir = os.path.join(model_dir, "firmware")
    while fp and fp not in seen:
        seen.add(fp)
        result, used_dev = check_with_fallback(fp, model, device, build_utc)
        if not result or not result.get("url"):
            print(f"  no further update from {fp}")
            break
        url = result["url"]
        try:
            meta, size = fetch_ota_metadata(url)
        except Exception as e:
            print(f"  metadata fetch failed: {e}")
            break
        nxt = write_firmware(fw_dir, fp, meta, size, url, result, used_dev, device)
        if not nxt:
            break
        post_build, post_ts = nxt
        fp = post_build
        device = device_from_fp(fp)
        build_utc = str(post_ts) if str(post_ts).isdigit() else "0"


def resolve_model(model_dir):
    gp_dir = os.path.join(model_dir, "getprop")
    if not os.path.isdir(gp_dir):
        return
    seen = set()  # shared across this model's getprops: never re-walk a known build
    for name in sorted(os.listdir(gp_dir)):
        if not name.endswith(".json"):
            continue
        try:
            props = json.load(open(os.path.join(gp_dir, name), encoding="utf-8"))
        except Exception as e:
            print(f"  bad getprop {name}: {e}")
            continue
        chain_from(model_dir, props, seen)


def iter_model_dirs(base):
    if os.path.isdir(os.path.join(base, "getprop")) or \
       os.path.isdir(os.path.join(base, "firmware")):
        yield base
        return
    for group in sorted(os.listdir(base)):
        gp = os.path.join(base, group)
        if not os.path.isdir(gp):
            continue
        for model in sorted(os.listdir(gp)):
            mp = os.path.join(gp, model)
            if os.path.isdir(mp):
                yield mp


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else ROOT
    if not os.path.isdir(target):
        print(f"no such dir: {target}")
        return
    for model_dir in iter_model_dirs(target):
        print(f"resolving {model_dir}")
        resolve_model(model_dir)


if __name__ == "__main__":
    main()
