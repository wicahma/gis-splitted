#!/usr/bin/env python3
"""Ekstraksi subset GIS per-scope dari ``data/`` (read-only terhadap sumber).

Ekstraktor generik berbasis *preset*: satu skrip untuk banyak wilayah, supaya
menambah provinsi/kecamatan baru tidak perlu menyalin 300+ baris kode.
Skrip asli per-folder (``bekasi-cikarang/``, ``banten/``) tetap dipertahankan
apa adanya karena manifest-nya sudah jadi bukti provenance.

Menyalin VERBATIM fitur yang lolos filter. File sumber dibuka hanya mode ``"rb"``;
(size, mtime_ns) diverifikasi sebelum & sesudah; sha256 sumber dihitung dalam
pass yang sama. Jumlah hasil filter yang sudah diukur dipasang sebagai guard
keras: kalau predikat berubah, output dihapus dan skrip abort.

Contoh:
    python extract_gis_subset.py --preset jakarta
    python extract_gis_subset.py --preset cikarang
    python extract_gis_subset.py --preset jakarta --only kecamatan
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
DATA_DIR = HERE
WIB = timezone(timedelta(hours=7))

# --- Preset definisi scope --------------------------------------------------
#
# kabupaten_kode = None  -> seluruh provinsi (semua kabupaten/kota di provinsi itu)
# kabupaten_kode = {…}   -> hanya kabupaten/kota tersebut
# kecamatan_names = {…}  -> batasi lagi ke kecamatan bernama itu (khusus scope sempit)
#
# expected = jumlah hasil filter yang SUDAH DIUKUR dengan grep pada dataset ini.
#            Bukan estimasi. Guard ini yang menangkap regresi predikat.
PRESETS: dict[str, dict] = {
    "jakarta": {
        "key": "jakarta",
        "label": "DKI Jakarta (provinsi 31, seluruh kabupaten/kota)",
        "province_code": "31",
        "province_name": "Daerah Khusus Ibukota Jakarta",   # <- apa adanya di roads
        "kabupaten_kode": None,
        "kabupaten_names": None,
        "kecamatan_names": None,
        "roads_prefilter": b"Jakarta",
        "expected": {"provinsi": 6, "kabupaten_kota": 6, "kecamatan": 44,
                     "desa_kelurahan": 0, "roads": 37305},
        "caveat": (
            "desa_kelurahan.json tidak memuat satu pun baris DKI Jakarta (0 dari 267 kelurahan). "
            "Jakarta TIDAK punya desa (0) — wilayah terkecilnya kelurahan, dan file ini memang wadah "
            "untuk kelurahan (.1xxx, 6.774 baris provinsi lain) maupun desa (.2xxx). Jadi angka 0 ini "
            "adalah gap data sumber (level kelurahan tidak ikut ter-ingest), bukan bug filter: level "
            "kecamatan Jakarta lengkap 44."
        ),
    },
    "cikarang": {
        "key": "cikarang",
        "label": "Cikarang (5 kecamatan di Kab. Bekasi 32.16)",
        "province_code": "32",
        "province_name": "Jawa Barat",
        "kabupaten_kode": {"32.16"},
        "kabupaten_names": {"Bekasi"},
        "kecamatan_names": frozenset({
            "Cikarang Barat", "Cikarang Pusat", "Cikarang Selatan",
            "Cikarang Timur", "Cikarang Utara",
        }),
        "roads_prefilter": b"Cikarang",
        "expected": {"provinsi": 1, "kabupaten_kota": 1, "kecamatan": 5,
                     "desa_kelurahan": 43, "roads": 1557},
        "caveat": None,
    },
}

COLLECTIONS = (
    ("provinsi", "provinsi.json", 539),
    ("kabupaten_kota", "kabupaten_kota.json", 540),
    ("kecamatan", "kecamatan.json", 6932),
    ("desa_kelurahan", "desa_kelurahan.json", 74000),
)
ROADS = ("roads", "roads_indonesia_complete.json")

CHUNK = 16 << 20
COUNT_PAD = 12
_STRUCT = re.compile(rb'[{}\"]')
_STRING = re.compile(rb'(?:[^"\\]|\\.)*"')


# --- Predicate --------------------------------------------------------------

def _kode(f: dict, level: str) -> str:
    """Kode otoritatif dari kode_kemendagri.

    Sengaja HANYA kode_kemendagri: ``kode_bps`` terbukti tidak akurat pada
    dataset ini (mis. ``kode_bps.provinsi`` berisi ``"01.2001"``, kode desa).
    """
    kk = f.get("kode_kemendagri") or {}
    return str(kk.get(level) or f.get("kode") or "").strip()


def match(kind: str, f: dict, p: dict) -> bool:
    """True bila fitur *f* berada dalam scope preset *p*."""
    prov = p["province_code"]
    kab_set = p["kabupaten_kode"]
    kec_set = p["kecamatan_names"]

    if kind in ("provinsi", "kabupaten_kota"):
        kk = f.get("kode_kemendagri") or {}
        if kk.get("provinsi") != prov:
            return False
        kab = str(kk.get("kabupaten") or "")
        if kab_set is None:
            return kab.startswith(prov + ".")
        return kab in kab_set

    if kind == "kecamatan":
        kode = _kode(f, "kecamatan")
        if kode.count(".") != 2 or not kode.startswith(prov + "."):
            return False
        if kab_set is not None and kode[:5] not in kab_set:
            return False
        if kec_set is not None and f.get("kecamatan") not in kec_set:
            return False
        return True

    if kind == "desa_kelurahan":
        kode = _kode(f, "desa")
        if kode.count(".") != 3 or not kode.startswith(prov + "."):
            return False
        if kab_set is not None and kode[:5] not in kab_set:
            return False
        if kec_set is not None and f.get("kecamatan") not in kec_set:
            return False
        return True

    if kind == "roads":
        if f.get("provinsi") != p["province_name"]:
            return False
        if p["kabupaten_names"] is not None and f.get("kabupaten_kota") not in p["kabupaten_names"]:
            return False
        if kec_set is not None and f.get("kecamatan") not in kec_set:
            return False
        return True

    raise ValueError(f"kind tidak dikenal: {kind}")


# --- Streaming reader -------------------------------------------------------

class FeatureStream:
    """Iterator fitur untuk file FeatureCollection satu-baris.

    Splitter brace-depth yang sadar-string: fitur ada di depth 2 (objek luar =
    depth 1). Fitur memuat objek bersarang ``raw_properties``, jadi pemisahan
    berbasis pola ``},{`` tidak sah. sha256 & jumlah byte dihitung sambil jalan.
    Penjaga integritas: jumlah fitur hasil parse WAJIB sama dengan ``count`` di
    header sumber; kalau splitter salah pisah, angka itu tidak cocok.
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


def extract_collection(kind: str, filename: str, source_features: int, p: dict, out_dir: Path) -> dict:
    src = DATA_DIR / filename
    out_path = out_dir / f"{kind}.{p['key']}.json"
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
            if match(kind, obj, p):
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

    if parsed != source_features:
        out_path.unlink(missing_ok=True)
        raise SystemExit(
            f"FATAL: {filename} terparse {parsed} fitur, header sumber menyatakan {source_features}. "
            "Splitter tidak konsisten — output dibatalkan."
        )
    want = p["expected"].get(kind)
    if want is not None and matched != want:
        out_path.unlink(missing_ok=True)
        raise SystemExit(
            f"FATAL: {filename} menghasilkan {matched} fitur untuk preset '{p['key']}', "
            f"diharapkan {want}. Predicate berubah — output dibatalkan."
        )
    return {
        "file": out_path.name, "features": matched,
        "source": filename, "source_features": parsed, "source_bytes": stream.read_bytes,
        "source_sha256": stream.digest, "output_bytes": out_path.stat().st_size,
        "seconds": round(time.time() - t0, 1),
    }


def extract_roads(p: dict, out_dir: Path) -> dict:
    kind, filename = ROADS
    src = DATA_DIR / filename
    out_path = out_dir / f"{kind}.{p['key']}.json"
    sha = hashlib.sha256()
    t0 = time.time()
    lines = feature_lines = parsed = matched = anomalies = 0
    prefilter = p["roads_prefilter"]

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
            if prefilter not in s:                      # prefilter murah
                continue
            parsed += 1
            try:
                obj = json.loads(s)
            except Exception as exc:
                out.close()
                out_path.unlink(missing_ok=True)
                raise SystemExit(f"FATAL: parse gagal di baris {lineno}: {exc}")
            if match("roads", obj, p):
                if matched:
                    out.write(b",\n")
                out.write(s)
                matched += 1
        out.write(b"\n]")
        out.flush()
        os.fsync(out.fileno())

    if anomalies:
        print(f"    ! {anomalies} baris tidak terduga (lihat di atas) — periksa sebelum memakai output")
    want = p["expected"].get("roads")
    if want is not None and matched != want:
        out_path.unlink(missing_ok=True)
        raise SystemExit(
            f"FATAL: roads menghasilkan {matched} fitur untuk preset '{p['key']}', "
            f"diharapkan {want}. Predicate berubah — output dibatalkan."
        )
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
    ap = argparse.ArgumentParser(description="Ekstraksi subset GIS per-scope (read-only).")
    ap.add_argument("--preset", required=True, choices=sorted(PRESETS))
    ap.add_argument("--out", default=None, help="folder output (default: data/<preset>)")
    ap.add_argument("--only", action="append", choices=[k for k, _, _ in COLLECTIONS] + ["roads"],
                    help="batasi ke jenis tertentu (boleh diulang)")
    args = ap.parse_args(argv)

    p = PRESETS[args.preset]
    out_dir = Path(args.out).resolve() if args.out else (DATA_DIR / p["key"])
    data_dir = DATA_DIR.resolve()
    if out_dir == data_dir:
        raise SystemExit("FATAL: folder output tidak boleh sama dengan folder sumber.")
    if not data_dir.is_dir():
        raise SystemExit(f"FATAL: folder sumber tidak ditemukan: {data_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    kinds = args.only or [k for k, _, _ in COLLECTIONS] + ["roads"]
    sources = [DATA_DIR / fn for _, fn, _ in COLLECTIONS] + [DATA_DIR / ROADS[1]]
    missing = [q.name for q in sources if not q.is_file()]
    if missing:
        raise SystemExit(f"FATAL: sumber hilang: {', '.join(missing)}")
    before = _stamp(sources)

    print(f"Preset     : {p['key']} — {p['label']}")
    print(f"Output     : {out_dir}")
    print(f"Jenis      : {', '.join(kinds)}")
    if p.get("caveat"):
        print(f"PERHATIAN  : {p['caveat']}")
    print()

    outputs = []
    t0 = time.time()
    for kind, filename, src_feats in COLLECTIONS:
        if kind in kinds:
            print(f"-> {filename} ...", flush=True)
            res = extract_collection(kind, filename, src_feats, p, out_dir)
            print(f"   {res['features']}/{res['source_features']} fitur -> {res['file']} "
                  f"({res['output_bytes'] / 1e6:.2f} MB, {res['seconds']}s)")
            outputs.append(res)
    if "roads" in kinds:
        print(f"-> {ROADS[1]} ...", flush=True)
        res = extract_roads(p, out_dir)
        print(f"   {res['features']}/{res['source_features']} fitur -> {res['file']} "
              f"({res['output_bytes'] / 1e6:.2f} MB, {res['seconds']}s)")
        outputs.append(res)

    after = _stamp(sources)
    changed = [q for q in before if before[q] != after[q]]
    if changed:
        print("\nFATAL: file sumber berubah selama proses:")
        for q in changed:
            print(f"  {q}: {before[q]} -> {after[q]}")
        return 2

    manifest = {
        "generated_at": datetime.now(WIB).isoformat(timespec="seconds"),
        "preset": p["key"],
        "label": p["label"],
        "predicate": {
            "provinsi_kode": f"{p['province_code']} ({p['province_name']})",
            "kabupaten_kode": sorted(p["kabupaten_kode"]) if p["kabupaten_kode"] else "seluruh provinsi",
            "kabupaten_nama": sorted(p["kabupaten_names"]) if p["kabupaten_names"] else None,
            "kecamatan": sorted(p["kecamatan_names"]) if p["kecamatan_names"] else None,
            "catatan": "provinsi/kabupaten/kecamatan/desa difilter lewat kode_kemendagri; roads lewat nama",
            "kode_bps_diabaikan": "kode_bps terbukti tidak akurat pada dataset ini (mis. berisi kode desa)",
            "expected_match": p["expected"],
        },
        "caveat": p.get("caveat"),
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