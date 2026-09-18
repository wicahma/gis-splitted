#!/usr/bin/env python3
"""Bangun `desa_kelurahan.jakarta.json` dari BIG ArcGIS REST API.

Latar belakang
--------------
File nasional `data/desa_kelurahan.json` TIDAK memuat DKI Jakarta sama sekali
(0 dari 267 kelurahan), padahal Jakarta punya 267 kelurahan dan tidak punya desa.
File ini mengisi celah itu dari sumber yang SAMA dengan file nasional, yaitu
BIG ArcGIS REST (skrip asal: build-boundaries-big.mjs).

Sumber
------
https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH/BATAS_DESAKEL_AR/MapServer/0
  - codeField KDEPUM  (kode Kemendagri kelurahan/desa, "31.xx.xx.1xxx")
  - nameField WADMKD

Aturan penyaringan (disamakan dengan file nasional)
---------------------------------------------------
Buang baris placeholder: KDEPUM kosong / tidak berformat 4 segmen
(contoh nyata: WADMKD "Area Tidak Terdefinisi", KDEPUM null, TIPADM 999).
  BIG KDPPUM=31 → 268 baris; setelah dibuang placeholder → 267 (== angka resmi).

Catatan penting
---------------
- `kode_pos` DIKOSONGKAN: layer desa BIG tidak punya field kode pos. Di file
  nasional field ini diisi dari join terpisah (bukan dari BIG). Untuk Address
  Cleansing field ini memang tidak divalidasi (postcode dari Nominatim).
- `lat`/`lng` = centroid poligon (shoelace cincin luar; MultiPolygon = rata-rata
  berbobot luas), meniru fungsi centroid skrip asal.
- Geometri disimpan sebagai GeoJSON dengan z=0 agar berbentuk sama dengan file
  nasional (BIG sendiri hanya mengirim 2D).
- Snapshot BIG saat ini bisa berbeda dari snapshot yang dipakai file nasional
  (terbukti: 36 kode Kab. Serang ada di BIG tapi tidak ada di file nasional).

Jalankan:  python build_jakarta_kelurahan.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "jakarta" / "desa_kelurahan.jakarta.json"

QUERY_URL = ("https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH"
             "/BATAS_DESAKEL_AR/MapServer/0/query")
WHERE = "KDPPUM = '31'"
PROVINSI_CODE = "31"
PAGE_SIZE = 100
UA = "Mozilla/5.0 (compatible; geoservice-data-prep)"
EXPECTED_TOTAL = 267
# jumlah per kabupaten/kota (resmi Kemendagri)
EXPECTED_PER_KAB = {
    "31.01": 6, "31.71": 44, "31.72": 31, "31.73": 56, "31.74": 65, "31.75": 65,
}


# --- fetch ------------------------------------------------------------------

def fetch_page(offset: int) -> list[dict]:
    import urllib.parse
    params = {
        "where": WHERE,
        "outFields": "*",
        "returnGeometry": "true",
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE_SIZE),
        "f": "json",
    }
    url = QUERY_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "features" not in payload:
        raise SystemExit(f"FATAL: respons tanpa 'features': {str(payload)[:400]}")
    if "error" in payload:
        raise SystemExit(f"FATAL: ArcGIS error: {payload['error']}")
    return payload["features"]


def fetch_all() -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        page = fetch_page(offset)
        if not page:
            break
        out.extend(page)
        print(f"   ...{len(out)} baris", flush=True)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.2)
    return out


# --- geometri ---------------------------------------------------------------

def signed_area(ring: list) -> float:
    """Area bertanda (shoelace) cincin tertutup."""
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        a += x1 * y2 - x2 * y1
    return a * 0.5


def ring_centroid(ring: list) -> dict | None:
    """Centroid cincin ala skrip asal (shoelace)."""
    if not ring or len(ring) < 3:
        return None
    area = cx = cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    area *= 0.5
    if abs(area) < sys.float_info.epsilon:
        return None
    return {"lng": cx / (6 * area), "lat": cy / (6 * area)}


def rings_to_geojson(rings: list) -> dict:
    """Ubah `rings` esriJSON menjadi geometri GeoJSON (Polygon/MultiPolygon).

    Konvensi ArcGIS: cincin luar diikuti lubangnya. Cincin luar bernilai area
    bertanda negatif (searah jarum jam). Koordinat ditulis 3D (z=0) agar sama
    dengan file nasional.
    """
    def as_ring(r):
        out = []
        for pt in r:
            x, y = float(pt[0]), float(pt[1])
            out.append([x, y, 0])
        if out and out[0] != out[-1]:
            out.append(list(out[0]))
        return out

    polygons: list[list[list]] = []
    for r in rings:
        if not r or len(r) < 4:
            continue
        outer = signed_area(r) < 0
        if outer or not polygons:
            polygons.append([as_ring(r)])
        else:
            polygons[-1].append(as_ring(r))

    if not polygons:
        return {"type": "Polygon", "coordinates": []}
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def geometry_centroid(geom: dict) -> dict | None:
    """Centroid ala skrip asal: Polygon → cincin luar; MultiPolygon → berbobot luas."""
    if geom["type"] == "Polygon":
        if not geom["coordinates"]:
            return None
        return ring_centroid(geom["coordinates"][0])
    if geom["type"] == "MultiPolygon":
        wlng = wlat = wsum = 0.0
        for poly in geom["coordinates"]:
            if not poly:
                continue
            c = ring_centroid(poly[0])
            if not c:
                continue
            weight = abs(signed_area(poly[0]))
            if not (weight > 0):
                continue
            wlng += c["lng"] * weight
            wlat += c["lat"] * weight
            wsum += weight
        if wsum <= 0:
            return None
        return {"lng": wlng / wsum, "lat": wlat / wsum}
    return None


# --- build ------------------------------------------------------------------

def build(features: list[dict]) -> tuple[list[dict], dict]:
    records: list[dict] = []
    skipped: list[dict] = []
    next_id = 1

    for f in features:
        attrs = f.get("attributes") or {}
        kode = (attrs.get("KDEPUM") or "").strip()
        nama = (attrs.get("WADMKD") or "").strip()

        # Aturan file nasional: buang placeholder (KDEPUM bukan format level-4)
        if kode.count(".") != 3 or not kode.startswith(PROVINSI_CODE + ".") or not nama:
            skipped.append({"KDEPUM": kode or None, "WADMKD": nama or None,
                            "TIPADM": attrs.get("TIPADM")})
            continue

        geom = rings_to_geojson((f.get("geometry") or {}).get("rings") or [])
        cen = geometry_centroid(geom)
        if cen is None:
            skipped.append({"KDEPUM": kode, "WADMKD": nama, "alasan": "centroid gagal"})
            continue

        records.append({
            "id": next_id,
            "kode": kode,
            "nama": nama,
            "level": "desa_kelurahan",
            "provinsi": attrs.get("WADMPR") or "",
            "kabupaten_kota": attrs.get("WADMKK") or "",
            "kecamatan": attrs.get("WADMKC") or "",
            "desa_kelurahan": nama,
            "kode_bps": {"provinsi": "", "kabupaten": "", "kecamatan": "", "desa": ""},
            "kode_kemendagri": {
                "provinsi": attrs.get("KDPPUM") or "",
                "kabupaten": attrs.get("KDPKAB") or "",
                "kecamatan": attrs.get("KDCPUM") or "",
                "desa": kode,
            },
            "lat": cen["lat"],
            "lng": cen["lng"],
            "geometry": geom,
            "raw_properties": {k: v for k, v in attrs.items() if k != "SHAPE"},
            "kode_pos": "",
        })
        next_id += 1

    return records, {"skipped": skipped}


# --- main -------------------------------------------------------------------

def main() -> int:
    print(f"Mengambil dari BIG: {WHERE}")
    features = fetch_all()
    print(f"Total baris mentah BIG: {len(features)}")

    records, meta = build(features)

    # --- validasi ---
    problems: list[str] = []
    if len(records) != EXPECTED_TOTAL:
        problems.append(f"jumlah {len(records)} != harapan {EXPECTED_TOTAL}")

    codes = [r["kode"] for r in records]
    if len(set(codes)) != len(codes):
        problems.append("ada kode duplikat")
    if any(r["kode"].count(".") != 3 or not r["kode"].startswith("31.") for r in records):
        problems.append("ada kode tidak berformat 31.xx.xx.xxxx")

    per_kab: dict[str, int] = {}
    for r in records:
        kab = r["kode_kemendagri"]["kabupaten"]
        per_kab[kab] = per_kab.get(kab, 0) + 1
    for kab, want in EXPECTED_PER_KAB.items():
        if per_kab.get(kab, 0) != want:
            problems.append(f"{kab}: {per_kab.get(kab,0)} != {want}")

    for r in records:
        g = r["geometry"]
        if g["type"] not in ("Polygon", "MultiPolygon") or not g["coordinates"]:
            problems.append(f"{r['kode']}: geometri kosong/tidak didukung")
            break
        if not (-90 <= r["lat"] <= 90 and -180 <= r["lng"] <= 180):
            problems.append(f"{r['kode']}: centroid di luar rentang")
            break

    print(f"\nDibangun : {len(records)} kelurahan")
    print(f"Dibuang  : {len(meta['skipped'])} baris placeholder")
    for s in meta["skipped"]:
        print(f"    - {s}")
    print("Per kabupaten/kota:")
    for kab in sorted(per_kab):
        mark = "OK" if per_kab[kab] == EXPECTED_PER_KAB.get(kab) else "!!"
        print(f"    {kab}: {per_kab[kab]:3d}  {mark}")

    if problems:
        print("\nVALIDASI GAGAL:")
        for p in problems:
            print("   -", p)
        return 2

    # --- tulis ---
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "type": "FeatureCollection",
        "level": "desa_kelurahan",
        "count": len(records),
        "features": records,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(", ", ": "))
    print(f"\nVALIDASI OK — ditulis: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())