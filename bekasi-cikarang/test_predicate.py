#!/usr/bin/env python3
"""Unit test predicate ekstraksi (tanpa data besar).

Jalankan: cd data/bekasi-cikarang && python test_predicate.py
"""
from extract_bekasi_cikarang import match, CIKARANG_KEC

KAB_BEKASI = {"kode_kemendagri": {"provinsi": "32", "kabupaten": "32.16"}}
KOTA_BEKASI = {"kode_kemendagri": {"provinsi": "32", "kabupaten": "32.75"}}
KAB_BANDUNG = {"kode_kemendagri": {"provinsi": "32", "kabupaten": "32.04"}}

KEC_CIKARANG_PUSAT = {"kode": "32.16.20", "kecamatan": "Cikarang Pusat",
                      "kode_kemendagri": {"kecamatan": "32.16.20"}}
KEC_TARUMAJAYA = {"kode": "32.16.01", "kecamatan": "Tarumajaya",
                  "kode_kemendagri": {"kecamatan": "32.16.01"}}
KEC_JATIASIH = {"kode": "32.75.09", "kecamatan": "Jatiasih",
                "kode_kemendagri": {"kecamatan": "32.75.09"}}
KEC_PLACEHOLDER = {"kode": "52.01", "kecamatan": "",
                   "kode_kemendagri": {"kecamatan": ""}}
KEC_PONOROGO = {"kode": "35.02.01", "kecamatan": "Slahung",
                "kode_kemendagri": {"kecamatan": "35.02.01"}}
# regresi: kecamatan Jawa Barat di LUAR Bekasi harus ditolak (bukan hanya "provinsi 32")
KEC_MAJALAYA_JABAR = {"kode": "32.04.33", "kecamatan": "Majalaya",
                      "kode_kemendagri": {"kecamatan": "32.04.33"}}

DESA_SUKAMAHI = {"kode": "32.16.20.2002", "kecamatan": "Cikarang Pusat",
                 "kode_kemendagri": {"desa": "32.16.20.2002"}}
DESA_JATIKARYA = {"kode": "32.75.10.1002", "kecamatan": "Jatisampurna",
                  "kode_kemendagri": {"desa": "32.75.10.1002"}}
DESA_PLACEHOLDER = {"kode": "", "kecamatan": "", "kode_kemendagri": {"desa": ""}}
DESA_MAJALAYA_JABAR = {"kode": "32.04.33.2001", "kecamatan": "Majalaya",
                       "kode_kemendagri": {"desa": "32.04.33.2001"}}

ROAD_BEKASI = {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi", "kecamatan": "Tarumajaya"}
ROAD_KOTA_BEKASI = {"provinsi": "Jawa Barat", "kabupaten_kota": "Kota Bekasi", "kecamatan": "Bekasi Selatan"}
ROAD_CIKARANG = {"provinsi": "Jawa Barat", "kabupaten_kota": "Bekasi", "kecamatan": "Cikarang Selatan"}
ROAD_BANTEN = {"provinsi": "Banten", "kabupaten_kota": "Bekasi", "kecamatan": "Tarumajaya"}
ROAD_BANDUNG = {"provinsi": "Jawa Barat", "kabupaten_kota": "Bandung", "kecamatan": "Cikarang"}
ROAD_NULL = {"provinsi": None, "kabupaten_kota": None, "kecamatan": None}

CASES = [
    # (kind, fitur, scope, harapan)
    ("provinsi", KAB_BEKASI, "bekasi", True),
    ("provinsi", KOTA_BEKASI, "bekasi", True),
    ("provinsi", KAB_BANDUNG, "bekasi", False),
    ("kabupaten_kota", KAB_BEKASI, "bekasi", True),
    ("kabupaten_kota", KAB_BANDUNG, "bekasi", False),
    ("kecamatan", KEC_CIKARANG_PUSAT, "bekasi", True),
    ("kecamatan", KEC_TARUMAJAYA, "bekasi", True),
    ("kecamatan", KEC_JATIASIH, "bekasi", True),
    ("kecamatan", KEC_CIKARANG_PUSAT, "cikarang", True),
    ("kecamatan", KEC_TARUMAJAYA, "cikarang", False),
    ("kecamatan", KEC_PLACEHOLDER, "bekasi", False),
    ("kecamatan", KEC_PONOROGO, "bekasi", False),
    ("kecamatan", KEC_MAJALAYA_JABAR, "bekasi", False),
    ("desa_kelurahan", DESA_SUKAMAHI, "bekasi", True),
    ("desa_kelurahan", DESA_SUKAMAHI, "cikarang", True),
    ("desa_kelurahan", DESA_JATIKARYA, "bekasi", True),
    ("desa_kelurahan", DESA_JATIKARYA, "cikarang", False),
    ("desa_kelurahan", DESA_PLACEHOLDER, "bekasi", False),
    ("desa_kelurahan", DESA_MAJALAYA_JABAR, "bekasi", False),
    ("roads", ROAD_BEKASI, "bekasi", True),
    ("roads", ROAD_KOTA_BEKASI, "bekasi", True),
    ("roads", ROAD_BANTEN, "bekasi", False),
    ("roads", ROAD_BANDUNG, "bekasi", False),
    ("roads", ROAD_CIKARANG, "cikarang", True),
    ("roads", ROAD_BEKASI, "cikarang", False),
    ("roads", ROAD_NULL, "bekasi", False),
    ("roads", ROAD_CIKARANG, "bekasi", True),
]


def main() -> int:
    failures = []
    for kind, feat, scope, want in CASES:
        got = match(kind, feat, scope)
        if got != want:
            failures.append(f"{kind} scope={scope} fitur={feat} -> {got}, harap {want}")
    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    assert len(CIKARANG_KEC) == 5, "CIKARANG_KEC harus 5 kecamatan"
    print(f"OK ({len(CASES)} assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())