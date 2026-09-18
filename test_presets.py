#!/usr/bin/env python3
"""Unit test predicate preset jakarta & cikarang — tanpa data besar.

    cd "D:/Berijalan/repo/geoservice/data" && python test_presets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_gis_subset import PRESETS, match  # noqa: E402

JAK = PRESETS["jakarta"]
CIK = PRESETS["cikarang"]

CASES = []


def case(desc, expect, preset, kind, feature):
    CASES.append((desc, expect, preset, kind, feature))


def kk(prov, kab="", kec="", desa="", kode="", kec_name=None):
    f = {
        "kode": kode,
        "kode_kemendagri": {"provinsi": prov, "kabupaten": kab, "kecamatan": kec, "desa": desa},
    }
    if kec_name is not None:
        f["kecamatan"] = kec_name
    return f


# ============ JAKARTA (seluruh provinsi 31) ================================
case("Jakarta Pusat 31.01", True, JAK, "provinsi", kk("31", "31.01"))
case("Jakarta Timur 31.75", True, JAK, "kabupaten_kota", kk("31", "31.75"))
case("Kep. Seribu 31.71", True, JAK, "kabupaten_kota", kk("31", "31.71"))
case("Kab. Bekasi 32.16 (provinsi lain)", False, JAK, "provinsi", kk("32", "32.16"))
case("provinsi 31 tapi kabupaten kosong", False, JAK, "kabupaten_kota", kk("31", ""))
case("provinsi 31 tapi kabupaten 32.16 (tak sinkron)", False, JAK, "provinsi", kk("31", "32.16"))

case("Kec. Gambir 31.01.01", True, JAK, "kecamatan", kk("31", "31.01", "31.01.01"))
case("Kec. Cibitung 32.16.07 (Jabar)", False, JAK, "kecamatan", kk("32", "32.16", "32.16.07"))
case("placeholder kode 52.01 (1 titik)", False, JAK, "kecamatan", kk("52", "52.01", "", "", "52.01"))

case("Kelurahan 31.01.01.1001", True, JAK, "desa_kelurahan", kk("31", "31.01", "31.01.01", "31.01.01.1001"))
case("Desa 32.16.20.2002 (Jabar)", False, JAK, "desa_kelurahan", kk("32", "32.16", "32.16.20", "32.16.20.2002"))

case("road provinsi DKI (nama panjang, benar)", True, JAK, "roads",
     {"provinsi": "Daerah Khusus Ibukota Jakarta", "kabupaten_kota": "Kota Administrasi Jakarta Timur"})
case("road provinsi 'DKI Jakarta' (nama pendek, BUKAN di dataset)", False, JAK, "roads",
     {"provinsi": "DKI Jakarta", "kabupaten_kota": "Kota Administrasi Jakarta Timur"})
case("road provinsi Jawa Barat", False, JAK, "roads", {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi"})

# ============ CIKARANG (5 kecamatan di Kab. Bekasi 32.16) ==================
case("Kec. Cikarang Utara 32.16.07", True, CIK, "kecamatan",
     kk("32", "32.16", "32.16.07", kec_name="Cikarang Utara"))
case("Kec. Cibitung 32.16.20 (Bekasi tapi bukan Cikarang)", False, CIK, "kecamatan",
     kk("32", "32.16", "32.16.20", kec_name="Cibitung"))
case("Kec. Cikarang Utara tapi Kota Bekasi 32.75", False, CIK, "kecamatan",
     kk("32", "32.75", "32.75.01", kec_name="Cikarang Utara"))

case("Desa Cikarang Barat 32.16.07.2001", True, CIK, "desa_kelurahan",
     kk("32", "32.16", "32.16.07", "32.16.07.2001", kec_name="Cikarang Barat"))
case("Desa Cibitung (nama kecamatan tidak masuk set)", False, CIK, "desa_kelurahan",
     kk("32", "32.16", "32.16.20", "32.16.20.2002", kec_name="Cibitung"))
case("Desa kode 32.75 (Kota Bekasi)", False, CIK, "desa_kelurahan",
     kk("32", "32.75", "32.75.01", "32.75.01.1001", kec_name="Cikarang Barat"))

case("Kab. Bekasi 32.16", True, CIK, "provinsi", kk("32", "32.16"))
case("Kota Bekasi 32.75 (di luar scope)", False, CIK, "kabupaten_kota", kk("32", "32.75"))

case("road Jabar/Bekasi/Cikarang Utara", True, CIK, "roads",
     {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi", "kecamatan": "Cikarang Utara"})
case("road Jabar/Kota Bekasi (di luar scope)", False, CIK, "roads",
     {"provinsi": "Jawa Barat", "kabupaten_kota": "Kota Bekasi", "kecamatan": "Cikarang Utara"})
case("road Jabar/Bekasi/Cibitung (bukan Cikarang)", False, CIK, "roads",
     {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi", "kecamatan": "Cibitung"})
case("road Jabar/Bekasi/kecamatan kosong", False, CIK, "roads",
     {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi", "kecamatan": ""})

failed = 0
for desc, expect, preset, kind, feature in CASES:
    got = match(kind, feature, preset)
    if got is not expect:
        failed += 1
        print(f"FAIL  [{preset['key']}] {desc} (harap={expect}, dapat={got})")
    else:
        print(f"ok    [{preset['key']}] {desc}")

# guard sanity: every preset needs an expected count for all 5 kinds
ALL = {"provinsi", "kabupaten_kota", "kecamatan", "desa_kelurahan", "roads"}
for key, preset in PRESETS.items():
    ok = set(preset["expected"]) == ALL
    if not ok:
        failed += 1
        print(f"FAIL  preset '{key}' expected tidak lengkap: {sorted(preset['expected'])}")
    else:
        print(f"ok    preset '{key}' expected lengkap: {preset['expected']}")

print(f"\n{'OK' if not failed else 'GAGAL'} ({len(CASES) + len(PRESETS) - failed}/{len(CASES) + len(PRESETS)} assertions)")
sys.exit(1 if failed else 0)