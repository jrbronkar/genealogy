"""Build the offline place index from the Census archive and Natural Earth GeoJSON."""
import argparse
import csv
import gzip
import io
import json
from pathlib import Path
import re
import zipfile


def census_rows(path):
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as source:
            for row in csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig"), delimiter="|"):
                yield {key.strip(): value.strip() for key, value in row.items()}


def build(census_archive, subdivisions, counties, populated_places, countries):
    us = []
    suffix = re.compile(
        r" (?:city and borough|city and county|city|town|village|borough|municipality|"
        r"unified government|metro government|metropolitan government|CDP)(?: \(balance\))?$"
    )
    for row in census_rows(census_archive):
        name = row["NAME"]
        us.append([row["USPS"], name, suffix.sub("", name), round(float(row["INTPTLONG"]), 4),
                   round(float(row["INTPTLAT"]), 4)])
    county_names = {row["GEOID"]: row["NAME"] for row in census_rows(counties)}
    townships = []
    for row in census_rows(subdivisions):
        county = county_names.get(row["GEOID"][:5])
        if county and row["NAME"].casefold().endswith(" township"):
            townships.append([row["USPS"], county, row["NAME"], round(float(row["INTPTLONG"]), 4),
                              round(float(row["INTPTLAT"]), 4)])
    globe = []
    features = json.loads(populated_places.read_text(encoding="utf-8"))["features"]
    for feature in features:
        p = feature["properties"]
        if p["ISO_A2"] in ("US", "-99") or not p["NAME"]:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        globe.append([p["NAME"], p["NAMEASCII"], p["ADM0NAME"], p["ADM1NAME"],
                      p["ISO_A2"], round(lon, 4), round(lat, 4)])
    country_points = []
    features = json.loads(countries.read_text(encoding="utf-8"))["features"]
    for feature in features:
        p = feature["properties"]
        if p["ISO_A2"] == "-99":
            code = p.get("ISO_A2_EH", "-99")
        else:
            code = p["ISO_A2"]
        if code != "-99" and isinstance(p["LABEL_X"], (float, int)):
            country_points.append([p["ADMIN"], code, round(p["LABEL_X"], 3), round(p["LABEL_Y"], 3)])
    return {"us": us, "townships": townships, "global": globe, "countries": country_points}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("census_archive", type=Path)
    parser.add_argument("subdivisions", type=Path)
    parser.add_argument("counties", type=Path)
    parser.add_argument("populated_places", type=Path)
    parser.add_argument("countries", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = build(args.census_archive, args.subdivisions, args.counties, args.populated_places, args.countries)
    args.output.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))


if __name__ == "__main__":
    main()
