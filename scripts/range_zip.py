# -*- coding: utf-8 -*-
"""Fetch OTA zip metadata + the central-directory entry list via HTTP Range, without
downloading the whole file. Returns (values_dict, total_size_bytes, entries) where each
entry is {name, method, lho (local header offset), csize}. ZIP64-aware.
extract_entry() pulls a single (small) member's decompressed bytes."""
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


def _u64(b, o):
    return int.from_bytes(b[o:o + 8], "little")


def _central_directory(url, total):
    tail_size = min(total, 65557)
    tail = _get_range(url, total - tail_size, total - 1)
    eocd = tail.rfind(b"\x50\x4b\x05\x06")
    if eocd < 0:
        raise RuntimeError("EOCD not found")
    cd_size = _u32(tail, eocd + 12)
    cd_off = _u32(tail, eocd + 16)
    n = _u16(tail, eocd + 10)
    if cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or n == 0xFFFF:
        loc = tail.rfind(b"\x50\x4b\x06\x07")  # ZIP64 EOCD locator
        if loc < 0:
            raise RuntimeError("ZIP64 locator not found")
        z64 = _u64(tail, loc + 8)
        z = _get_range(url, z64, z64 + 55)
        cd_size = _u64(z, 40)
        cd_off = _u64(z, 48)
    return _get_range(url, cd_off, cd_off + cd_size - 1)


def _entries(cd):
    out = []
    off = 0
    while off + 46 <= len(cd) and cd[off:off + 4] == b"\x50\x4b\x01\x02":
        method = _u16(cd, off + 10)
        csize = _u32(cd, off + 20)
        nlen = _u16(cd, off + 28)
        elen = _u16(cd, off + 30)
        clen = _u16(cd, off + 32)
        loff = _u32(cd, off + 42)
        name = cd[off + 46:off + 46 + nlen].decode("utf-8", "replace").replace("\\", "/")
        # ZIP64 extra: csize/loff may be 0xFFFFFFFF -> read from extra field
        if csize == 0xFFFFFFFF or loff == 0xFFFFFFFF:
            extra = cd[off + 46 + nlen:off + 46 + nlen + elen]
            eo = 0
            while eo + 4 <= len(extra):
                hid = _u16(extra, eo)
                hsz = _u16(extra, eo + 2)
                if hid == 0x0001:
                    p = eo + 4
                    usize = _u32(cd, off + 24)
                    if usize == 0xFFFFFFFF:
                        p += 8
                    if csize == 0xFFFFFFFF:
                        csize = _u64(extra, p); p += 8
                    if loff == 0xFFFFFFFF:
                        loff = _u64(extra, p); p += 8
                    break
                eo += 4 + hsz
        out.append({"name": name, "method": method, "lho": loff, "csize": csize})
        off += 46 + nlen + elen + clen
    return out


def fetch_ota_metadata(url):
    total = _content_length(url)
    cd = _central_directory(url, total)
    entries = _entries(cd)
    raw = None
    for cand in ("META-INF/com/android/metadata", "metadata"):
        e = next((x for x in entries if x["name"] == cand), None)
        if e:
            raw = extract_entry(url, e)
            break
    if raw is None:
        raise RuntimeError("metadata entry not found")
    text = raw.decode("utf-8", "replace")
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k] = v
    return values, total, entries


def extract_entry(url, entry):
    loff = entry["lho"]
    lh = _get_range(url, loff, loff + 29)
    if _u32(lh, 0) != 0x04034B50:
        raise RuntimeError("bad local header")
    data_off = loff + 30 + _u16(lh, 26) + _u16(lh, 28)
    comp = _get_range(url, data_off, data_off + entry["csize"] - 1)
    return comp if entry["method"] == 0 else zlib.decompress(comp, -15)


import re

_BLOCK_RE = re.compile(r"^([A-Za-z0-9_]+)\.(?:new\.dat(?:\.br)?|transfer\.list|patch\.dat|img\.p)$")


def _varint(b, o):
    r = s = 0
    while o < len(b):
        x = b[o]; o += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, o
        s += 7
    return r, o


def _caremap_partitions(data):
    """care_map.pb (A/B) is CareMap{ repeated PartitionInfo partitions=1 }, each
    PartitionInfo{ string name=1 }. Raw-scan it without the generated schema."""
    names = set()
    o = 0
    while o < len(data):
        tag, o = _varint(data, o)
        if tag >> 3 == 1 and tag & 7 == 2:
            ln, o = _varint(data, o)
            sub = data[o:o + ln]; o += ln
            p = 0
            while p < len(sub):
                t2, p = _varint(sub, p)
                if t2 & 7 == 2:
                    l2, p = _varint(sub, p)
                    if t2 >> 3 == 1:
                        names.add(sub[p:p + l2].decode("utf-8", "replace"))
                    p += l2
                elif t2 & 7 == 0:
                    _, p = _varint(sub, p)
                else:
                    break
        elif tag & 7 == 0:
            _, o = _varint(data, o)
        else:
            break
    return names


def derive_fields(url, meta, entries):
    """Everything derivable from the OTA package besides the metadata key/values:
    abType (AB/BLOCK/FILE), updated partitions, and whether radio/firmware + APEX
    payloads are bundled."""
    names = [e["name"] for e in entries]
    ab = (meta.get("ota-type") or "").upper()
    parts = set()
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if "/" not in n:
            m = _BLOCK_RE.match(base)
            if m:
                parts.add(m.group(1))
    if ab == "AB" and not parts:
        cm = next((e for e in entries if e["name"] == "care_map.pb"), None)
        if cm:
            try:
                parts |= _caremap_partitions(extract_entry(url, cm))
            except Exception:
                pass
    has_firmware = any(
        n.startswith("firmware-update/") or "NON-HLOS" in n
        or n.endswith("/radio.img") or base_radio(n)
        for n in names
    )
    has_apex = any(n == "apex_info.pb" or n.endswith(".apex") for n in names)
    req = meta.get("ota-required-cache")
    return {
        "abType": ab or None,
        "requiredCacheBytes": int(req) if str(req).isdigit() else None,
        "partitions": sorted(parts),
        "hasFirmware": has_firmware,
        "hasApex": has_apex,
    }


def base_radio(n):
    b = n.rsplit("/", 1)[-1].lower()
    return b in ("radio.img", "modem.img") or b.startswith("radio-")
