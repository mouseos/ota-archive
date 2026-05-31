# -*- coding: utf-8 -*-
"""Fetch only META-INF/com/android/metadata from a (large) OTA zip via HTTP Range,
without downloading the whole file. Returns (values_dict, total_size_bytes)."""
import struct
import zlib

import requests

_TIMEOUT = 60


def _content_length(url):
    h = requests.head(url, timeout=_TIMEOUT, allow_redirects=True)
    n = int(h.headers.get("Content-Length", "0"))
    if n > 0:
        return n
    r = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=_TIMEOUT)
    cr = r.headers.get("Content-Range", "")
    if "/" in cr:
        return int(cr.rsplit("/", 1)[1])
    raise RuntimeError("cannot determine OTA size")


def _get_range(url, start, end):
    r = requests.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=_TIMEOUT)
    if r.status_code != 206:
        raise RuntimeError(f"range {start}-{end} -> HTTP {r.status_code}")
    return r.content


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def fetch_ota_metadata(url):
    total = _content_length(url)
    tail_size = min(total, 65557)
    tail = _get_range(url, total - tail_size, total - 1)
    eocd = tail.rfind(b"\x50\x4b\x05\x06")
    if eocd < 0:
        raise RuntimeError("EOCD not found")
    cd_size = _u32(tail, eocd + 12)
    cd_off = _u32(tail, eocd + 16)
    cd = _get_range(url, cd_off, cd_off + cd_size - 1)

    target = None
    off = 0
    while off + 46 <= len(cd):
        if cd[off:off + 4] != b"\x50\x4b\x01\x02":
            break
        method = _u16(cd, off + 10)
        csize = _u32(cd, off + 20)
        nlen = _u16(cd, off + 28)
        elen = _u16(cd, off + 30)
        clen = _u16(cd, off + 32)
        loff = _u32(cd, off + 42)
        name = cd[off + 46:off + 46 + nlen].decode("utf-8", "replace").replace("\\", "/")
        if name in ("META-INF/com/android/metadata", "metadata"):
            target = (method, csize, loff)
            break
        off += 46 + nlen + elen + clen
    if not target:
        raise RuntimeError("metadata entry not found")

    method, csize, loff = target
    lh = _get_range(url, loff, loff + 29)
    if _u32(lh, 0) != 0x04034B50:
        raise RuntimeError("bad local header")
    data_off = loff + 30 + _u16(lh, 26) + _u16(lh, 28)
    comp = _get_range(url, data_off, data_off + csize - 1)
    raw = comp if method == 0 else zlib.decompress(comp, -15)
    text = raw.decode("utf-8", "replace")

    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k] = v
    return values, total
