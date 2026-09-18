#!/usr/bin/env python3
"""Ekstraksi subset GIS Bekasi-Cikarang (read-only terhadap sumber).

Menyalin VERBATIM fitur yang lolos filter dari data/ ke folder script ini,
tanpa menyentuh file asli (dibuka hanya mode "rb", (size, mtime) diverifikasi
sebelum & sesudah, sha256 sumber dihitung dalam pass yang sama).

Rencana: D:/hermes/.hermes/plans/2026-09-18_122708-bekasi-cikarang-gis-subset.md

Contoh:
    python extract_bekasi_cikarang.py --scope bekasi
    python extract_bekasi_cikarang.py --only kecamatan --only roads
    python extract_bekasi_cikarang.py --scope cikarang --out ./cikarang-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent
WIB = timezone(timedelta(hours=7))

# --- Filter: kode Kemendagri (otoritatif) -----------------------------------
JABAR = "32"                                  # Jawa Barat
KABUPATEN = ("32.16", "32.75")                # Kab. Bekasi, Kota Bekasi
KABUPATEN_NAMES = ("Bekasi", "Kota Bekasi")   # roads: atribusi hanya nama
PROVINSI_NAME = "Jawa Barat"
CIKARANG_KEC = frozenset({
    "Cikarang Barat", "Cikarang Pusat", "Cikarang Selatan",
    "Cikarang Timur", "Cikarang Utara",
})

# folder sumber: filename, jumlah fitur di header (dipakai sebagai penjaga integritas)
COLLECTIONS = (
    ("provinsi", "provinsi.json", 539),
    ("kabupaten_kota", "kabupaten_kota.json", 540),
    ("kecamatan", "kecamatan.json", 6932),
    ("desa_kelurahan", "desa_kelurahan.json", 74000),
)
ROADS = ("roads", "roads_indonesia_complete.json")

CHUNK = 16 << 20
COUNT_PAD = 12
_STRUCT = re.compile(rb'[{}"]')
_STRING = re.compile(rb'(?:[^"\\]|\\.)*"')


# --- Predicate --------------------------------------------------------------

def _kode(f: dict, level: str) -> str:
    kk = f.get("kode_kemendagri") or {}
    return str(kk.get(level) or f.get("kode") or "").strip()


def match(kind: str, f: dict, scope: str) -> bool:
    """True bila fitur *f* berelasi dengan scope yang diminta."""
    if kind in ("provinsi", "kabupaten_kota"):
        kk = f.get("kode_kemendagri") or {}
        return kk.get("provinsi") == JABAR and kk.get("kabupaten") in KABUPATEN
    if kind == "kecamatan":
        kode = _kode(f, "kecamatan")
        ok = kode.count(".") == 2 and kode[:5] in KABUPATEN
        return ok and (scope == "bekasi" or f.get("kecamatan") in CIKARANG_KEC)
    if kind == "desa_kelurahan":
        kode = _kode(f, "desa")
        ok = kode.count(".") == 3 and kode[:5] in KABUPATEN
        return ok and (scope == "bekasi" or f.get("kecamatan") in CIKARANG_KEC)
    if kind == "roads":
        if f.get("provinsi") != PROVINSI_NAME or f.get("kabupaten_kota") not in KABUPATEN_NAMES:
            return False
        return scope == "bekasi" or f.get("kecamatan") in CIKARANG_KEC
    raise ValueError(f"kind tidak dikenal: {kind}")


# --- Streaming reader -------------------------------------------------------

class FeatureStream:
    """Iterator fitur untuk file FeatureCollection satu-baris.

    Splitter brace-depth yang sadar-string: fitur ada di depth 2 (objek luar =
    depth 1). Fitur memuat objek bersarang ``raw_properties``, jadi pemisahan
    berbasis pola ``},{`` tidak sah. sha256 & jumlah byte dihitung sambil jalan.
    Penjaga integritas: caller WAJIB mencocokkan jumlah fitur hasil parse dengan
    ``count`` di header sumber; kalau splitter salah pisah, angka itu tidak cocok.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.sha = hashlib.sha256()
        self.digest: str | None = None
        self.read_bytes = 0
        self.features = 0

    def __iter__(self):
        feat_level = 2
        depth = 0
        capturing = False
        in_string = False
        buf = bytearray()
        start = 0
        off = 0
        with open(self.path, "rb") as fh:          # READ-ONLY
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                self.sha.update(chunk)
                self.read_bytes += len(chunk)
                pos, n = 0, len(chunk)
                while pos < n:
                    if in_string:
                        m2 = _STRING.match(chunk, pos)
                        if m2 is None:
                            if capturing:
                                buf += chunk[pos:]
                            pos = n
                            break
                        if capturing:
                            buf += chunk[pos:m2.end()]
                        in_string = False
                        pos = m2.end()
                        continue
                    m = _STRUCT.search(chunk, pos)
                    if m is None:
                        if capturing:
                            buf += chunk[pos:]
                        pos = n
                        break
                    i = m.start()
                    c = chunk[i:i + 1]
                    if capturing:
                        buf += chunk[pos:i]
                    if c == b'"':
                        if capturing:
                            buf += c
                        m2 = _STRING.match(chunk, i + 1)
                        if m2 is None:                 # string melewati batas chunk
                            in_string = True
                            pos = n
                            break
                        if capturing:
                            buf += chunk[i + 1:m2.end()]
                        pos = m2.end()
                        continue
                    if c == b"{":
                        depth += 1
                        if depth == feat_level and not capturing:
                            capturing, buf, start = True, bytearray(), off + i
                            buf += c
                        elif capturing:
                            buf += c
                    else:
                        depth -= 1
                        if capturing:
                            buf += c
                        if depth == feat_level - 1 and capturing:
                            self.features += 1
                            yield start, bytes(buf)
                            capturing, buf = False, bytearray()
                    pos = i + 1
                off += n
        self.digest = self.sha.hexdigest()


# --- Ekstraksi --------------------------------------------------------------

def _header(level: str) -> bytes:
    return b'{"type":"FeatureCollection","level":"' + level.encode() + b'","count":'


def extract_collection(kind: str, filename: str, expected: int, scope: str, out_dir: Path) -> dict:
    src = DATA_DIR / filename
    out_path = out_dir / f"{kind}.bekasi-cikarang.json"
    stream = FeatureStream(src)
    t0 = time.time()
    matched = parsed = 0
    level = None
    pad_pos = 0
    wrote_header = False

    with open(out_path, "wb") as out:
        for _off, raw in stream:
            parsed += 1
            try:
                obj = json.loads(raw)
            except Exception as exc:
                out.close()
                out_path.unlink(missing_ok=True)
                raise SystemExit(f"FATAL: parse gagal di fitur #{parsed} {filename}: {exc}")
            if level is None:
                level = str(obj.get("level") or kind)
            if not wrote_header:
                head = _header(level)
                out.write(head)
                pad_pos = len(head)
                out.write(b" " * COUNT_PAD)
                out.write(b',"features":[')
                wrote_header = True
            if match(kind, obj, scope):
                if matched:
                    out.write(b",")
                out.write(raw)
                matched += 1
        if not wrote_header:
            out.write(_header(kind))
            pad_pos = len(_header(kind))
            out.write(b" " * COUNT_PAD)
            out.write(b',"features":[')
        out.write(b"]}")
        out.seek(pad_pos)
        out.write(f"{matched:<{COUNT_PAD}}".encode())
        out.flush()
        os.fsync(out.fileno())

    if parsed != expected:
        out_path.unlink(missing_ok=True)
        raise SystemExit(
            f"FATAL: {filename} terparse {parsed} fitur, header sumber menyatakan {expected}. "
            "Splitter tidak konsisten — output dibatalkan."
        )
    return {
        "file": out_path.name, "features": matched,
        "source": filename, "source_features": parsed, "source_bytes": stream.read_bytes,
        "source_sha256": stream.digest, "output_bytes": out_path.stat().st_size,
        "seconds": round(time.time() - t0, 1),
    }


def extract_roads(scope: str, out_dir: Path) -> dict:
    kind, filename = ROADS
    src = DATA_DIR / filename
    out_path = out_dir / f"{kind}.bekasi-cikarang.json"
    sha = hashlib.sha256()
    t0 = time.time()
    lines = feature_lines = parsed = matched = anomalies = 0

    with open(src, "rb") as fh, open(out_path, "wb") as out:   # sumber READ-ONLY
        out.write(b"[\n")
        for lineno, line in enumerate(fh, 1):
            sha.update(line)
            lines += 1
            s = line.strip()
            if not s or s in (b"[", b"]"):
                continue
            if s.startswith(b","):
                s = s[1:]
            if s.endswith(b","):
                s = s[:-1]
            if not s.startswith(b"{"):
                anomalies += 1
                if anomalies <= 5:
                    print(f"    ! baris {lineno} bukan fitur: {s[:70]!r}")
                continue
            feature_lines += 1
            if b"Bekasi" not in s and b"Cikarang" not in s:      # prefilter murah
                continue
            parsed += 1
            try:
                obj = json.loads(s)
            except Exception as exc:
                out.close()
                out_path.unlink(missing_ok=True)
                raise SystemExit(f"FATAL: parse gagal di baris {lineno}: {exc}")
            if match("roads", obj, scope):
                if matched:
                    out.write(b",\n")
                out.write(s)
                matched += 1
        out.write(b"\n]")
        out.flush()
        os.fsync(out.fileno())

    if anomalies:
        print(f"    ! {anomalies} baris tidak terduga (lihat di atas) — periksa sebelum memakai output")
    return {
        "file": out_path.name, "features": matched,
        "source": filename, "source_features": feature_lines,
        "source_bytes": src.stat().st_size,
        "source_sha256": sha.hexdigest(), "output_bytes": out_path.stat().st_size,
        "lines": lines, "prefiltered": parsed, "anomalies": anomalies,
        "seconds": round(time.time() - t0, 1),
    }


# --- main -------------------------------------------------------------------

def _stamp(paths) -> dict:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ekstraksi subset GIS Bekasi-Cikarang (read-only).")
    ap.add_argument("--scope", choices=("bekasi", "cikarang"), default="bekasi",
                    help="bekasi = Kab. Bekasi + Kota Bekasi (default); cikarang = 5 kecamatan Cikarang")
    ap.add_argument("--out", default=str(HERE), help="folder output (default: folder script ini)")
    ap.add_argument("--only", action="append", choices=[k for k, _, _ in COLLECTIONS] + ["roads"],
                    help="batasi ke jenis tertentu (boleh diulang)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).resolve()
    data_dir = DATA_DIR.resolve()
    if out_dir == data_dir:
        raise SystemExit("FATAL: folder output tidak boleh sama dengan folder sumber.")
    if not data_dir.is_dir():
        raise SystemExit(f"FATAL: folder sumber tidak ditemukan: {data_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    kinds = args.only or [k for k, _, _ in COLLECTIONS] + ["roads"]
    sources = [DATA_DIR / fn for _, fn, _ in COLLECTIONS] + [DATA_DIR / ROADS[1]]
    plan_missing = [p.name for p in sources if not p.is_file()]
    if plan_missing:
        raise SystemExit(f"FATAL: sumber hilang: {', '.join(plan_missing)}")
    before = _stamp(sources)

    print(f"Scope      : {args.scope}")
    print(f"Output     : {out_dir}")
    print(f"Jenis      : {', '.join(kinds)}")
    print()

    outputs = []
    t0 = time.time()
    for kind, filename, expected in COLLECTIONS:
        if kind in kinds:
            print(f"-> {filename} ...", flush=True)
            res = extract_collection(kind, filename, expected, args.scope, out_dir)
            print(f"   {res['features']}/{res['source_features']} fitur -> {res['file']} "
                  f"({res['output_bytes'] / 1e6:.2f} MB, {res['seconds']}s)")
            outputs.append(res)
    if "roads" in kinds:
        print(f"-> {ROADS[1]} ...", flush=True)
        res = extract_roads(args.scope, out_dir)
        print(f"   {res['features']}/{res['source_features']} fitur -> {res['file']} "
              f"({res['output_bytes'] / 1e6:.2f} MB, {res['seconds']}s)")
        outputs.append(res)

    after = _stamp(sources)
    changed = [p for p in before if before[p] != after[p]]
    if changed:
        print("\nFATAL: file sumber berubah selama proses:")
        for p in changed:
            print(f"  {p}: {before[p]} -> {after[p]}")
        return 2

    manifest = {
        "generated_at": datetime.now(WIB).isoformat(timespec="seconds"),
        "scope": args.scope,
        "predicate": {
            "provinsi": "32 Jawa Barat",
            "kabupaten_kode": list(KABUPATEN),
            "kabupaten_nama": list(KABUPATEN_NAMES),
            "cikarang_kecamatan": sorted(CIKARANG_KEC) if args.scope == "cikarang" else None,
            "catatan": "kecamatan/desa difilter lewat kode_kemendagri; roads lewat nama (tanpa kode)",
        },
        "sources": [
            {"file": fn, "bytes": before[str(DATA_DIR / fn)][0],
             "mtime_ns": before[str(DATA_DIR / fn)][1],
             "sha256": next((o["source_sha256"] for o in outputs if o["source"] == fn), None),
             "features": next((o["source_features"] for o in outputs if o["source"] == fn), None)}
            for fn in [fn for _, fn, _ in COLLECTIONS] + [ROADS[1]]
            if any(o["source"] == fn for o in outputs)
        ],
        "outputs": [{"file": o["file"], "bytes": o["output_bytes"], "features": o["features"],
                     "seconds": o["seconds"]} for o in outputs],
        "runtime_seconds": round(time.time() - t0, 1),
        "notes": "file sumber hanya dibaca (mode rb); tidak ada penulisan di luar folder output",
    }
    man_path = out_dir / "manifest.json"
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"\nSumber tidak berubah: {len(before)} file OK")
    print(f"Manifest: {man_path}")
    print(f"Total waktu: {manifest['runtime_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())