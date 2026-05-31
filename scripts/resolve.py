# -*- coding: utf-8 -*-
"""Resolve firmware chains for collected getprop.json files.

For each model dir under OTA/Google/{brand}/{model}/, find the oldest timestamp
folder that has getprop.json but no firmware.json, then run a Google checkin
chain using HTTP-Range metadata, writing firmware.json per discovered version
(post-timestamp).

checkin request includes BOTH fixes used by the app:
  - ro.build.date.utc as the build timestamp (server returns the correct/latest
    OTA instead of an older one)
  - ro.product.device fallback when ro.product.model yields no update_url

Usage: python resolve.py OTA/Google
       python resolve.py OTA/Google/MOONDROP/MD-PH-001
"""
import gzip
import json
import os
import sys

import requests

from checkin_pb2 import AndroidCheckinRequest, AndroidCheckinResponse
from logs_pb2 import AndroidCheckinProto, AndroidBuildProto
from range_zip import fetch_ota_metadata

ROOT = "OTA/Google"
CHECKIN_URL = "https://android.clients.google.com/checkin"


def device_from_fp(fp):
    try:
        return fp.split("/")[2].split(":")[0]
    except IndexError:
        return ""


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


def write_firmware(model_dir, fp, pre_ts, meta, size, url, result, used_dev, device):
    post_build = meta.get("post-build")
    post_ts = meta.get("post-timestamp")
    if not post_build or not post_ts:
        return None
    out_dir = os.path.join(model_dir, str(post_ts))
    os.makedirs(out_dir, exist_ok=True)
    fwp = os.path.join(out_dir, "firmware.json")
    archive_urls = []
    if os.path.exists(fwp):
        try:
            archive_urls = json.load(open(fwp, encoding="utf-8")).get("archiveUrls", [])
        except Exception:
            pass
    fw = {
        "fingerprint": fp,
        "preBuild": meta.get("pre-build"),
        "preTimestamp": int(pre_ts) if str(pre_ts).isdigit() else None,
        "postBuild": post_build,
        "postTimestamp": int(post_ts),
        "otaUrl": url,
        "sizeBytes": size,
        "securityPatch": meta.get("post-security-patch-level"),
        "sdk": meta.get("post-sdk-level"),
        "device": device,
        "checkinDevice": used_dev,
        "title": result.get("title"),
        "description": result.get("description"),
        "archiveUrls": archive_urls,
    }
    json.dump(fw, open(fwp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"  wrote {fwp}  ({post_build})")
    return post_build, post_ts


def chain_from(model_dir, props):
    fp = props.get("ro.build.fingerprint", "")
    model = props.get("ro.product.model", "")
    device = props.get("ro.product.device", "") or device_from_fp(fp)
    pre_ts = props.get("ro.build.date.utc", "")
    seen = set()
    while fp and fp not in seen:
        seen.add(fp)
        result, used_dev = check_with_fallback(fp, model, device, pre_ts)
        if not result or not result.get("url"):
            print(f"  no further update from {fp}")
            break
        url = result["url"]
        try:
            meta, size = fetch_ota_metadata(url)
        except Exception as e:
            print(f"  metadata fetch failed: {e}")
            break
        nxt = write_firmware(model_dir, fp, pre_ts, meta, size, url, result, used_dev, device)
        if not nxt:
            break
        post_build, post_ts = nxt
        fp = post_build
        device = device_from_fp(fp)
        pre_ts = post_ts


def resolve_model(model_dir):
    ts_dirs = sorted(d for d in os.listdir(model_dir)
                     if d.isdigit() and os.path.isdir(os.path.join(model_dir, d)))
    for ts in ts_dirs:
        gp = os.path.join(model_dir, ts, "getprop.json")
        fw = os.path.join(model_dir, ts, "firmware.json")
        if os.path.exists(gp) and not os.path.exists(fw):
            print(f"resolving {model_dir} from ts={ts}")
            try:
                chain_from(model_dir, json.load(open(gp, encoding="utf-8")))
            except Exception as e:
                print(f"  resolve error: {e}")
            return  # one chain per run per model


def iter_model_dirs(base):
    if any(d.isdigit() for d in os.listdir(base)) or \
       os.path.isfile(os.path.join(base, "timestamps.json")):
        yield base
        return
    for brand in sorted(os.listdir(base)):
        bp = os.path.join(base, brand)
        if not os.path.isdir(bp):
            continue
        for model in sorted(os.listdir(bp)):
            mp = os.path.join(bp, model)
            if os.path.isdir(mp):
                yield mp


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else ROOT
    if not os.path.isdir(target):
        print(f"no such dir: {target}")
        return
    for model_dir in iter_model_dirs(target):
        resolve_model(model_dir)


if __name__ == "__main__":
    main()
