# -*- coding: utf-8 -*-
"""Download a resolved OTA package (aria2c, parallel) and upload it to
archive.org via the S3-like API, then record the link in firmware.json.

Usage: python ia_upload.py OTA/Google/{group}/{model}/firmware/{hash}.json
Env:   IA_ACCESS_KEY, IA_SECRET_KEY   (from https://archive.org/account/s3.php)

Designed for GitHub Actions matrix (1 firmware per job) to fit time/disk limits:
downloads to /tmp, uploads, then deletes the local file.
"""
import os
import re
import subprocess
import sys

import requests

IA_S3 = "https://s3.us.archive.org"


def sanitize_id(s):
    return re.sub(r"[^A-Za-z0-9._-]", "-", s).strip("-").lower()


def main():
    fw_path = sys.argv[1]
    import json
    fw = json.load(open(fw_path, encoding="utf-8"))
    if fw.get("archiveUrls"):
        print("already archived; skip")
        return

    parts = fw_path.replace("\\", "/").split("/")
    # OTA/Google/{group}/{model}/firmware/{hash}.json
    group, model = parts[-4], parts[-3]
    post_ts = fw.get("postTimestamp")
    url = fw["otaUrl"]
    # One archive.org item per device; each firmware is stored inside it keeping the
    # original package filename, so versions for the same device stay grouped.
    filename = url.split("?")[0].rsplit("/", 1)[-1] or f"{post_ts}.zip"
    identifier = sanitize_id(f"ota-google-{group}-{model}")

    access = os.environ["IA_ACCESS_KEY"]
    secret = os.environ["IA_SECRET_KEY"]

    local = os.path.join("/tmp", filename)
    print(f"downloading {url} -> {local}")
    subprocess.run(
        ["aria2c", "-x16", "-s16", "--max-tries=3", "--retry-wait=5",
         "-d", "/tmp", "-o", filename, url],
        check=True,
    )

    upload_url = f"{IA_S3}/{identifier}/{filename}"
    print(f"uploading -> {upload_url}")
    with open(local, "rb") as f:
        r = requests.put(
            upload_url,
            data=f,
            headers={
                "authorization": f"LOW {access}:{secret}",
                "x-amz-auto-make-bucket": "1",
                "x-archive-meta-mediatype": "data",
                "x-archive-meta-collection": "opensource",
                "x-archive-meta-title": f"OTA {group} {model}",
                "x-archive-meta-subject": f"{group};{model};android-ota",
            },
            timeout=3600,
        )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"IA upload failed: HTTP {r.status_code} {r.text[:300]}")

    fw["archiveUrls"] = [f"https://archive.org/download/{identifier}/{filename}"]
    json.dump(fw, open(fw_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"archived: {fw['archiveUrls'][0]}")

    try:
        os.remove(local)
    except OSError:
        pass


if __name__ == "__main__":
    main()
