"""Build a self-contained local family timeline and cache WikiTree thumbnails."""
from collections import defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "https://www.wikitree.com"
THUMBNAIL = re.compile(r"/photo\.php/thumb/[A-Za-z0-9/%_.-]+\.(?:jpe?g|png|gif|webp)", re.I)
PLACES = (
    (r"washington.*muskingum", -81.94, 39.99),
    (r"zanesville|muskingum", -82.01, 39.94),
    (r"crowell|fairview.*major", -98.48, 36.27),
    (r"whittier", -118.03, 33.98),
    (r"^france$|, france$", 2.21, 46.23),
    (r"ohio", -82.79, 40.36),
    (r"oklahoma", -97.09, 35.50),
    (r"california", -119.42, 36.78),
    (r"virginia", -79.46, 37.43),
    (r"pennsylvania", -77.19, 41.20),
    (r"new york(?: city)?$", -74.01, 40.71),
    (r"herkimer|new york", -74.92, 43.05),
    (r"wisconsin", -89.62, 44.62),
    (r"nebraska", -99.80, 41.49),
    (r"maryland", -76.61, 39.00),
    (r"illinois", -89.40, 40.63),
    (r"indiana", -86.13, 40.27),
    (r"iowa", -93.50, 42.03),
    (r"missouri", -92.29, 37.96),
    (r"kansas", -98.48, 39.01),
    (r"germany", 10.45, 51.17),
    (r"england", -1.17, 52.66),
    (r"ireland", -8.24, 53.41),
    (r"scotland", -4.20, 56.49),
    (r"switzerland", 8.23, 46.82),
    (r"netherlands", 5.29, 52.13),
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def thumbnail_path(row):
    metadata = row["metadata"]
    path = metadata.get("URL_300") or metadata.get("url")
    if isinstance(path, str) and THUMBNAIL.fullmatch(path) and ".." not in path:
        return path
    return None


def download_photo(path, directory, opener):
    prefix = sha256(path.encode()).hexdigest()[:20]
    for cached in directory.glob(prefix + ".*"):
        return "images/" + cached.name
    url = HOST + path
    try:
        request = Request(url, headers={"Accept": "image/*"})
        with opener.open(request, timeout=12) as response:
            content_type = response.headers.get_content_type()
            content = response.read(2_000_001)
    except (URLError, TimeoutError):
        return None
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
    if content_type not in extensions or not content or len(content) > 2_000_000:
        return None
    name = prefix + extensions[content_type]
    (directory / name).write_bytes(content)
    return "images/" + name


def timeline(data, run, download=False):
    photos = defaultdict(list)
    cache = {}
    opener = build_opener(NoRedirect())
    image_dir = run / "images"
    if download:
        image_dir.mkdir(exist_ok=True)
    for photo in data["photos"]:
        thumb = thumbnail_path(photo)
        if not thumb:
            continue
        if download and thumb not in cache:
            cache[thumb] = download_photo(thumb, image_dir, opener)
        metadata = photo["metadata"]
        source = metadata.get("URL") or thumb
        if not isinstance(source, str) or not source.startswith("/photo"):
            source = thumb
        photos[photo["profile"]].append({
            "image": cache.get(thumb),
            "title": metadata.get("Title") or ("Source image" if metadata.get("Type") == "source" else "WikiTree image"),
            "date": metadata.get("Date"),
            "place": metadata.get("Location"),
            "source": HOST + source,
            "remote_thumbnail": HOST + thumb,
        })

    paths = defaultdict(set)
    for row in data["ancestry_paths"]:
        first = row["branch"].split("/")[0]
        paths[row["profile"]].add(f'{row["seed"]} · {first}')
    leads = {lead["profile"]: lead for lead in data["immigration_leads"]}
    events = []
    for profile in data["profiles"]:
        key = profile["Name"]
        name = " ".join(profile[field] for field in ("FirstName", "MiddleName", "LastNameAtBirth") if profile.get(field)) or key
        linked = photos[key]

        def add(date, place, activity, evidence, images=linked, record=None):
            if not date or not re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}", date):
                return
            coords = next(([x, y] for pattern, x, y in PLACES if re.search(pattern, place or "", re.I)), None)
            events.append({
                "date": date, "place": place, "activity": activity, "evidence": evidence,
                "name": name, "profile": key, "branches": sorted(paths[key]), "coords": coords,
                "source": profile["provenance"]["profile_url"], "photos": images, "record": record,
            })

        for field, activity in (("Birth", "Born"), ("Death", "Died")):
            add(profile.get(field + "Date"), profile.get(field + "Location"), activity,
                profile["DataStatus"].get(field + "Date") or "WikiTree profile")
        for photo in linked:
            if photo["date"] and photo["place"]:
                add(photo["date"], photo["place"], photo["title"], "Dated WikiTree attachment", [photo])
        lead = leads.get(key, {})
        if "profile_reports_Ontario_voyage_in_1841" in lead.get("evidence", []):
            add(lead["us_presence_by"], "New York City", "Ontario passenger manifest sworn",
                "The transcription lists the Sheffield family. WikiTree biographies differ on the arrival day.",
                [photo for photo in linked if re.search(r"ontario|manifest", photo["title"], re.I)] or linked,
                lead["supplemental_sources"][0]["url"])
        if key == "Bronkar-39" and lead.get("us_presence_by") == "1812-08-26":
            add(lead["us_presence_by"], "Ohio, United States", "Began serving in the Ohio militia",
                "Reported by WikiTree biography. Arrival date and port remain open.",
                [photo for photo in linked if re.search(r"pension|1812", photo["title"], re.I)] or linked)
            bio = profile.get("biography_wikitext", "")
            if "was a gardener born in France" in bio and "30 June" in bio:
                add("1860-06-30", "Washington Township, Muskingum, Ohio", "Recorded as a gardener in the 1860 census",
                    "Reported by WikiTree biography.",
                    [photo for photo in linked if "1860" in photo["title"]] or linked)
    events.sort(key=lambda event: (event["date"], event["profile"], event["activity"]))
    payload = json.dumps(events, ensure_ascii=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    (run / "storyboard.html").write_text(template.replace("__EVENTS__", payload), encoding="utf-8")
    return sum(path is not None for path in cache.values()), sum(path is None for path in cache.values())
