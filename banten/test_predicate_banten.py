#!/usr/bin/env python3
"""Unit test predicate Banten — tanpa data besar.

    cd "D:/Berijalan/repo/geoservice/data/banten" && python test_predicate_banten.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_banten import match, EXPECTED_MATCH  # noqa: E402

CASES = []


def case(desc, expect, kind, feature):
    CASES.append((desc, expect, kind, feature))


def kk(prov, kab="", kec="", desa="", kode=""):
    return {
        "kode": kode,
        "kode_kemendagri": {
            "provinsi": prov, "kabupaten": kab, "kecamatan": kec, "desa": desa,
        },
    }


# --- provinsi.json / kabupaten_kota.json (baris berisi kabupaten/kota) ------
case("Kab. Tangerang 36.03", True, "provinsi", kk("36", "36.03"))
case("Kota Tangerang Selatan 36.74", True, "kabupaten_kota", kk("36", "36.74"))
case("Kab. Pandeglang 36.01", True, "kabupaten_kota", kk("36", "36.01"))
case("Kab. Bekasi 32.16 (provinsi lain)", False, "provinsi", kk("32", "32.16"))
case("Kota Bandung 32.73 (provinsi lain)", False, "kabupaten_kota", kk("32", "32.73"))
case("provinsi 36 tapi kabupaten kosong", False, "kabupaten_kota", kk("36", ""))
case("provinsi 36 tapi kabupaten 37.01 (tak sinkron)", False, "provinsi", kk("36", "37.01"))

# --- kecamatan.json --------------------------------------------------------
case("Kec. Ciledug 36.71.06", True, "kecamatan", kk("36", "36.71", "36.71.06"))
case("Kec. Cibitung 32.16.07 (Jabar)", False, "kecamatan", kk("32", "32.16", "32.16.07"))
case("placeholder TIPADM=999 (kode 52.01)", False, "kecamatan", kk("52", "52.01", "", "", "52.01"))
case("kode kabupaten di file kecamatan (1 titik)", False, "kecamatan", kk("36", "36.03", "", "", "36.03"))

# --- desa_kelurahan.json ---------------------------------------------------
case("Desa 36.03.01.2001", True, "desa_kelurahan", kk("36", "36.03", "36.03.01", "36.03.01.2001"))
case("Kelurahan 36.74.01.1001", True, "desa_kelurahan", kk("36", "36.74", "36.74.01", "36.74.01.1001"))
case("Desa 32.16.20.2002 (Jabar)", False, "desa_kelurahan", kk("32", "32.16", "32.16.20", "32.16.20.2002"))
case("kode kecamatan di file desa (2 titik)", False, "desa_kelurahan", kk("36", "36.03", "36.03.01", "", "36.03.01"))

# --- roads (atribusi hanya nama provinsi) ----------------------------------
case("road provinsi Banten", True, "roads", {"provinsi": "Banten", "kabupaten_kota": "Lebak"})
case("road provinsi Jawa Barat", False, "roads", {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi"})
case("road provinsi Banten, kabupaten kosong", True, "roads", {"provinsi": "Banten", "kabupaten_kota": ""})

# --- guard sanity ----------------------------------------------------------
case("EXPECTED_MATCH lengkap", True, None, None)

failed = 0
for desc, expect, kind, feature in CASES:
    if kind is None:
        ok = set(EXPECTED_MATCH) == {"provinsi", "kabupaten_kota", "kecamatan", "desa_kelurahan", "roads"}
    else:
        ok = match(kind, feature) is expect
    if not ok:
        failed += 1
        print(f"FAIL  {desc} (harap={expect})")
    else:
        print(f"ok    {desc}")

print(f"\n{'OK' if not failed else 'GAGAL'} ({len(CASES) - failed}/{len(CASES)} assertions)")
sys.exit(1 if failed else 0)