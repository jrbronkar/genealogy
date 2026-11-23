"""Conservative, offline lookup of locations as written in genealogy records."""
from collections import defaultdict
from functools import lru_cache
import gzip
import json
from pathlib import Path
from statistics import median
import unicodedata

STATE_NAMES = (
    "AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|"
    "DE:Delaware|DC:District of Columbia|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|"
    "IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|"
    "MI:Michigan|MN:Minnesota|MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|"
    "NH:New Hampshire|NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|"
    "OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|PR:Puerto Rico|RI:Rhode Island|SC:South Carolina|"
    "SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|WA:Washington|"
    "WV:West Virginia|WI:Wisconsin|WY:Wyoming"
)
STATES = dict(part.split(":") for part in STATE_NAMES.split("|"))
STATE_CODES = {name.casefold(): code for code, name in STATES.items()}
STATE_CODES["washington state"] = "WA"
COUNTRY_ALIASES = {
    "united states": "US", "usa": "US", "u.s.a.": "US", "u.s.": "US",
    "uk": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "holland": "NL",
}
REGION_POINTS = {
    "england": (-1.17, 52.66), "scotland": (-4.20, 56.49), "wales": (-3.78, 52.30),
    "northern ireland": (-6.69, 54.66), "ontario": (-84.70, 49.41), "quebec": (-73.80, 52.94),
}
CANADA_REGIONS = {"ontario", "quebec"}


def normalized(value):
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(char)).strip()


def state_code(part):
    prefix, _, name = part.partition(" of ")
    if prefix in {"province", "colony", "commonwealth", "territory", "state"} and name in STATE_CODES:
        return STATE_CODES[name]
    return STATE_CODES.get(part)


@lru_cache(maxsize=1)
def place_index():
    with gzip.open(Path(__file__).with_name("places.json.gz"), "rt", encoding="utf-8") as archive:
        data = json.load(archive)
    us, state_points, international, countries, names = defaultdict(set), defaultdict(list), defaultdict(list), {}, {}
    for state, formal, name, lon, lat in data["us"]:
        point = (lon, lat)
        state_points[state].append(point)
        for label in (formal, name):
            us[state, normalized(label)].add(point)
    townships = defaultdict(set)
    for state, county, township, lon, lat in data["townships"]:
        county = normalized(county)
        townships[state, normalized(township)].add((county, (lon, lat)))
    for name, ascii_name, country, region, code, lon, lat in data["global"]:
        for label in {normalized(name), normalized(ascii_name or name)}:
            international[code, label].append((normalized(region or ""), (lon, lat)))
        if country:
            names[normalized(country)] = code
    for country, code, lon, lat in data["countries"]:
        names[normalized(country)] = code
        countries[code] = (lon, lat)
    regions = {
        state: (median(point[0] for point in locations), median(point[1] for point in locations))
        for state, locations in state_points.items()
    }
    return us, regions, townships, international, countries, {**names, **COUNTRY_ALIASES}


def lookup_place(place):
    if not place:
        return None, None
    original = [part.strip() for part in place.split(",") if part.strip()]
    if not original:
        return None, None
    parts = [normalized(part) for part in original]
    us, regions, townships, international, countries, names = place_index()
    if len(parts) > 1 and parts[-1] in {"british colonial america", "colonial america"} and state_code(parts[-2]):
        original.pop()
        parts.pop()
    country = names.get(parts[-1])
    context = parts[:-1] if country else parts
    if not country and parts[-1] in CANADA_REGIONS:
        country = "CA"
    state = None
    if context and country in (None, "US"):
        state = state_code(context[-1])
        state_position = -2 if parts != context else -1
        if state is None and original[state_position] in STATES:
            state = original[state_position]
        if state and (country == "US" or len(context) > 1 or parts[-1] not in names):
            city = "new york" if parts[0] == "new york city" else parts[0]
            if city.endswith(" township"):
                candidates = townships.get((state, city), ())
                qualifiers = set(context[1:-1])
                township_matches = {
                    point for county, point in candidates
                    if not qualifiers or county in qualifiers or county.rsplit(" county", 1)[0] in qualifiers
                }
                if len(township_matches) == 1:
                    return next(iter(township_matches)), "township"
            matches = us[state, city] if len(context) > 1 else set()
            if len(matches) == 1:
                return next(iter(matches)), "city"
            if state in regions:
                return regions[state], "state"
    if country and context:
        matches = international[country, parts[0]]
        qualified = {point for region, point in matches if region and region in context[1:]}
        matches = qualified or {point for _, point in matches}
        if len(matches) == 1:
            return next(iter(matches)), "city"
    region = next((part for part in reversed(parts) if part in REGION_POINTS), None)
    if region:
        return REGION_POINTS[region], "region"
    if country in countries:
        return countries[country], "country"
    return None, None
