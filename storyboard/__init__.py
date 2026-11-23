"""Build a self-contained local family timeline and cache WikiTree thumbnails."""
from collections import defaultdict
from hashlib import sha256
from http.client import HTTPException
import json
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "https://www.wikitree.com"
THUMBNAIL = re.compile(r"/photo\.php/thumb/[A-Za-z0-9/%_.-]+\.(?:jpe?g|png|gif|webp)", re.I)
ORIGINAL = re.compile(r"/photo\.php/[0-9a-f]/[0-9a-f]{2}/[^/?#]+\.(?:jpe?g|png|gif|webp|tiff?|bmp)", re.I)
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
    for field in ("URL_300", "URL_75", "url"):
        path = metadata.get(field)
        if isinstance(path, str) and ".." not in path and (THUMBNAIL.fullmatch(path) or ORIGINAL.fullmatch(path)):
            return path
    return None


def original_path(row):
    metadata = row["metadata"]
    for field in ("path", "URL_300", "URL_75"):
        path = metadata.get(field)
        if isinstance(path, str) and ORIGINAL.fullmatch(path) and ".." not in path:
            return path
    thumb = thumbnail_path(row)
    if thumb and THUMBNAIL.fullmatch(thumb):
        path = thumb.rsplit("/", 1)[0].replace("/thumb/", "/", 1)
        if ORIGINAL.fullmatch(path):
            return path
    return None


def download_photo(path, directory, opener):
    limit = 32 * 1024 * 1024 if ORIGINAL.fullmatch(path) else 2_000_000
    prefix = sha256(path.encode()).hexdigest()[:20]
    for cached in directory.glob(prefix + ".*"):
        return "images/" + cached.name
    url = HOST + quote(path, safe="/%")
    try:
        request = Request(url, headers={"Accept": "image/*"})
        with opener.open(request, timeout=12) as response:
            content_type = response.headers.get_content_type()
            content = response.read(limit + 1)
    except (URLError, TimeoutError, HTTPException):
        return None
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "image/tiff": ".tif",
                  "image/bmp": ".bmp"}
    if content_type not in extensions or not content or len(content) > limit:
        return None
    name = prefix + extensions[content_type]
    (directory / name).write_bytes(content)
    return "images/" + name


def timeline(data, run, download=False, originals=False):
    download = download or originals
    photos = defaultdict(list)
    cache = {}
    opener = build_opener(NoRedirect())
    image_dir = run / "images"
    if download:
        image_dir.mkdir(exist_ok=True)
    download_log = []
    for photo in data["photos"]:
        metadata = photo["metadata"]
        thumb = thumbnail_path(photo)
        image_path = original_path(photo) if originals else thumb
        if download and image_path and image_path not in cache:
            cache[image_path] = download_photo(image_path, image_dir, opener)
        local_image = cache.get(image_path)
        source = metadata.get("URL")
        if not isinstance(source, str) or not source.startswith("/photo/"):
            source = thumb or image_path
        source_url = HOST + source if source else photo["provenance"]["profile_url"]
        if download:
            download_log.append({
                "profile": photo["profile"], "kind": photo["kind"], "source_page": source_url,
                "image_url": HOST + quote(image_path, safe="/%") if image_path else None, "file": local_image,
                "status": "downloaded" if local_image else "unavailable",
            })
        photos[photo["profile"]].append({
            "image": local_image,
            "title": metadata.get("Title") or ("Source image" if metadata.get("Type") == "source" else "WikiTree image"),
            "date": metadata.get("Date"),
            "place": metadata.get("Location"),
            "source": source_url,
        })
    if download:
        (run / "photo_downloads.json").write_text(
            json.dumps(download_log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )

    paths = defaultdict(set)
    for row in data["ancestry_paths"]:
        first = row["branch"].split("/")[0]
        paths[row["profile"]].add(f'{row["seed"]} · {first}')
    events = []
    for profile in data["profiles"]:
        key = profile["Name"]
        name = " ".join(profile[field] for field in ("FirstName", "MiddleName", "LastNameAtBirth") if profile.get(field)) or key
        linked = photos[key]

        def add(date, place, activity, evidence, images=linked):
            if not date or not re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}", date):
                return
            coords = next(([x, y] for pattern, x, y in PLACES if re.search(pattern, place or "", re.I)), None)
            events.append({
                "date": date, "place": place, "activity": activity, "evidence": evidence,
                "name": name, "profile": key, "branches": sorted(paths[key]), "coords": coords,
                "source": profile["provenance"]["profile_url"], "photos": images,
            })

        for field, activity in (("Birth", "Born"), ("Death", "Died")):
            add(profile.get(field + "Date"), profile.get(field + "Location"), activity,
                profile["DataStatus"].get(field + "Date") or "WikiTree profile")
        for photo in linked:
            if photo["date"] and photo["place"]:
                add(photo["date"], photo["place"], photo["title"], "Dated WikiTree attachment", [photo])
    events.sort(key=lambda event: (event["date"], event["profile"], event["activity"]))
    payload = json.dumps(events, ensure_ascii=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    (run / "storyboard.html").write_text(template.replace("__EVENTS__", payload), encoding="utf-8")
    return sum(path is not None for path in cache.values()), sum(row["status"] == "unavailable" for row in download_log)
