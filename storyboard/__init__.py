"""Build a self-contained local family timeline and cache WikiTree thumbnails."""
from collections import defaultdict
from hashlib import sha256
from html import unescape
from http.client import HTTPException
import json
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from storyboard.places import lookup_place

HOST = "https://www.wikitree.com"
THUMBNAIL = re.compile(r"/photo\.php/thumb/[A-Za-z0-9/%_.-]+\.(?:jpe?g|png|gif|webp)", re.I)
ORIGINAL = re.compile(r"/photo\.php/[0-9a-f]/[0-9a-f]{2}/[^/?#]+\.(?:jpe?g|png|gif|webp|tiff?|bmp)", re.I)
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


def cached_photo(path, directory):
    if path:
        prefix = sha256(path.encode()).hexdigest()[:20]
        for cached in directory.glob(prefix + ".*"):
            if cached.is_file():
                return "images/" + cached.name
    return None


def download_photo(path, directory, opener):
    limit = 32 * 1024 * 1024 if ORIGINAL.fullmatch(path) else 2_000_000
    prefix = sha256(path.encode()).hexdigest()[:20]
    cached = cached_photo(path, directory)
    if cached:
        return cached
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


def describe_photos(rows, image_dir=None):
    photos = []
    for photo in rows:
        metadata = photo["metadata"]
        thumb = thumbnail_path(photo)
        original = original_path(photo)
        local = None
        if image_dir is not None:
            local = cached_photo(thumb, image_dir) or cached_photo(original, image_dir)
        source = metadata.get("URL")
        if not isinstance(source, str) or not source.startswith("/photo/"):
            source = thumb or original
        source_url = HOST + source if isinstance(source, str) and source.startswith("/") else photo["provenance"]["profile_url"]
        photos.append({
            "image": local,
            "preview": HOST + quote(thumb, safe="/%") if thumb else None,
            "title": metadata.get("Title") or ("Source image" if metadata.get("Type") == "source" else "WikiTree image"),
            "date": metadata.get("Date"),
            "place": metadata.get("Location"),
            "source": source_url,
            "identity": original or source_url,
        })
    distinct = {}
    for photo in photos:
        previous = distinct.get(photo["identity"])
        if previous is None:
            distinct[photo["identity"]] = photo
            continue
        preferred = photo if photo["date"] or previous["title"] == "WikiTree image" else previous
        distinct[photo["identity"]] = {**preferred, "image": photo["image"] or previous["image"]}
    return list(distinct.values())


def dated_attachment_events(profile, photos, name, branches, married_dates):
    events = []
    birth_year = (profile.get("BirthDate") or "")[:4]
    death_year = (profile.get("DeathDate") or "")[:4]
    source = profile["provenance"]["profile_url"]
    for photo in photos:
        if photo.get("date") in married_dates and re.search(r"\bmarriage\b", photo.get("title") or "", re.I):
            continue
        if photo.get("date") and photo.get("place") and "0000" < birth_year <= photo["date"][:4] <= death_year:
            coords, precision = lookup_place(photo["place"])
            place = photo["place"]
            clean_place = ", ".join(part.strip() for part in place.split(",") if part.strip()) if place else place
            events.append({
                "date": photo["date"], "place": clean_place, "activity": photo["title"],
                "evidence": "Dated WikiTree attachment",
                "name": name, "profile": profile["Name"], "branches": branches, "coords": coords,
                "precision": precision, "source": photo.get("source") or source,
            })
    return events


WIKI_EXTERNAL = re.compile(r"\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\]", re.I)


def readable_wikitext(text):
    text = re.sub(r"\[\[(?:image|file|category):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", lambda match: match[2] or match[1], text)
    text = WIKI_EXTERNAL.sub(lambda match: match[2] or match[1], text)
    text = re.sub(r"<(?:br\b[^>]*|/p)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]*>|\{\{[^{}]*\}\}", "", text)
    return " ".join(unescape(text).replace("''", "").split())


def biography_paragraphs(text):
    if not text:
        return []
    text = re.split(
        r"^\s*=+\s*(?:sources|references|acknowledgments|acknowledgements)\s*=+\s*$",
        text, maxsplit=1, flags=re.I | re.M,
    )[0]
    text = re.sub(r"<ref\b[^>]*?(?:/>|>.*?</ref\s*>)", "", text, flags=re.I | re.S)
    text = re.sub(r"^\s*=+.*?=+\s*$", "", text, flags=re.M)
    return [cleaned for block in re.split(r"\n\s*\n", text)
            for cleaned in [readable_wikitext(block)] if cleaned
            and not re.search(r"\b(?:DNA kit|FTDNA kit|mitoYDNA kit|survived by|survivors include)\b", cleaned, re.I)]


def citation_links(citations):
    links = {}
    for citation in citations:
        if citation.get("kind") != "inline_ref":
            continue
        for match in WIKI_EXTERNAL.finditer(citation.get("wikitext", "")):
            url, label = match.groups()
            links.setdefault(url, readable_wikitext(label or url))
    return [{"url": url, "label": label} for url, label in links.items()]


def timeline(data, run, download=False, originals=False, progress=None, biographies=None):
    biographies = biographies or {}
    download = download or originals
    photos = defaultdict(list)
    cache = {}
    opener = build_opener(NoRedirect())
    image_dir = run / "images"
    if download:
        image_dir.mkdir(exist_ok=True)
    download_log = []
    if download and progress:
        kind = "full-size images" if originals else "thumbnails"
        progress(f"[Photos] Preparing to download {kind} from {len(data['photos'])} photo entries.")
    for index, photo in enumerate(data["photos"], 1):
        metadata = photo["metadata"]
        thumb = thumbnail_path(photo)
        image_path = original_path(photo) if originals else thumb
        if download and image_path and image_path not in cache:
            if progress:
                title = metadata.get("ImageName") or image_path.rsplit("/", 1)[-1]
                progress(f"[Photos] Downloading entry {index}/{len(data['photos'])} for {photo['profile']} ({title}).")
            cache[image_path] = download_photo(image_path, image_dir, opener)
            if progress and not cache[image_path]:
                progress(f"[Photos] Entry {index} unavailable. Continuing with the remaining images.")
        local_image = cache.get(image_path) if download else (cached_photo(thumb, image_dir) or cached_photo(original_path(photo), image_dir))
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
            "preview": HOST + quote(thumb, safe="/%") if thumb else None,
            "title": metadata.get("Title") or ("Source image" if metadata.get("Type") == "source" else "WikiTree image"),
            "date": metadata.get("Date"),
            "place": metadata.get("Location"),
            "source": source_url,
            "identity": original_path(photo) or source_url,
        })
    if download:
        (run / "photo_downloads.json").write_text(
            json.dumps(download_log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )

    if progress:
        progress("[Storyboard] Building the timeline, map, and photo gallery.")
    paths = defaultdict(set)
    for row in data["ancestry_paths"]:
        first = row["branch"].split("/")[0]
        paths[row["profile"]].add(f'{row["seed"]} · {first}')
    events = []
    people = []
    beyond_depth = {gap["child"] for gap in data.get("gaps", []) if gap["reason"] == "depth_limit"}
    citations_by_person = defaultdict(list)
    for citation in data.get("citations", []):
        citations_by_person[citation["profile"]].append(citation)
    display_names = {
        profile["Name"]: " ".join(profile[field] for field in ("FirstName", "MiddleName", "LastNameAtBirth", "Suffix")
                                 if profile.get(field)) or profile["Name"]
        for profile in data["profiles"]
    }
    marriages_by_person = defaultdict(list)
    for marriage in data.get("marriages", []):
        first, second = marriage["first"], marriage["second"]
        if marriage.get("date") and first in display_names and second in display_names:
            marriages_by_person[first].append((marriage, second))
            marriages_by_person[second].append((marriage, first))
    for profile in data["profiles"]:
        key = profile["Name"]
        name = display_names[key]
        distinct = {}
        for photo in photos[key]:
            previous = distinct.get(photo["identity"])
            if previous is None:
                distinct[photo["identity"]] = photo
                continue
            preferred = photo if photo["date"] or previous["title"] == "WikiTree image" else previous
            distinct[photo["identity"]] = {**preferred, "image": photo["image"] or previous["image"]}
        linked = list(distinct.values())
        biography = biographies.get(key, profile)
        people.append({
            "key": key, "name": name, "birth": profile.get("BirthDate"), "death": profile.get("DeathDate"),
            "source": profile["provenance"]["profile_url"], "photos": linked,
            "photos_loaded": key in set(data.get("photo_profiles_fetched") or []),
            "biography": biography_paragraphs(biography.get("biography_wikitext")),
            "biography_source": biography["provenance"],
            "bibliography": citation_links(citations_by_person[key]),
            "has_more_parents": key in beyond_depth,
            "starting_profile": any(branch.endswith(" · seed") for branch in paths[key]),
        })

        def add(date, place, activity, evidence, source=None):
            if not date or not re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}", date):
                return
            coords, precision = lookup_place(place)
            clean_place = ", ".join(part.strip() for part in place.split(",") if part.strip()) if place else place
            events.append({
                "date": date, "place": clean_place, "activity": activity, "evidence": evidence,
                "name": name, "profile": key, "branches": sorted(paths[key]), "coords": coords, "precision": precision,
                "source": source or profile["provenance"]["profile_url"],
            })

        for field, activity in (("Birth", "Born"), ("Death", "Died")):
            add(profile.get(field + "Date"), profile.get(field + "Location"), activity,
                profile["DataStatus"].get(field + "Date") or "WikiTree profile")
        married_dates = set()
        for marriage, spouse in marriages_by_person[key]:
            married_dates.add(marriage["date"])
            add(marriage["date"], marriage.get("place"), "Marriage to " + display_names[spouse],
                "WikiTree marriage listing", marriage["provenance"].get("profile_url"))
        events.extend(dated_attachment_events(profile, linked, name, sorted(paths[key]), married_dates))
    events.sort(key=lambda event: (event["date"], event["profile"], event["activity"]))
    directory = Path(__file__).parent
    template = (directory / "template.html").read_text(encoding="utf-8")
    tree = {"query": data.get("query"), "people": people, "marriages": data.get("marriages", []), "relationships": [
        [link["parent"], link["child"]] for link in data["relationships"]
    ]}
    blocks = {
        "__EVENTS__": json.dumps(events, ensure_ascii=False).replace("<", "\\u003c"),
        "__TREE__": json.dumps(tree, ensure_ascii=False).replace("<", "\\u003c"),
        "__WORLD__": (directory / "world.json").read_text(encoding="utf-8"),
        "__SCRIPT__": (directory / "atlas.js").read_text(encoding="utf-8"),
    }
    template = re.sub(r"__(?:EVENTS|TREE|WORLD|SCRIPT)__", lambda match: blocks[match[0]], template)
    (run / "storyboard.html").write_text(template, encoding="utf-8")
    return sum(path is not None for path in cache.values()), sum(row["status"] == "unavailable" for row in download_log)
