"""Anonymous, read-only WikiTree ancestry research."""
import argparse
import csv
from collections import Counter, deque
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from storyboard import timeline

API = "https://api.wikitree.com/api.php"
SITE = "https://www.wikitree.com/wiki/"
LEWIS = "Bronkar-39"
SEEDS = ("Bronkar-5", LEWIS)
ROLES = ("Father", "Mother")
PROFILE_FIELDS = (
    "Id", "Name", "FirstName", "MiddleName", "LastNameAtBirth", "LastNameCurrent",
    "BirthDate", "BirthLocation", "DeathDate", "DeathLocation", "DataStatus",
)
PHOTO_FIELDS = ("PageId", "ImageName", "Type", "Width", "Height", "URL", "URL_300", "URL_75")
ONTARIO_MANIFEST = "https://www.immigrantships.net/v6/1800v6/ontario18410622.html"
SHEFFIELD_ARRIVALS = {
    "Sheffield-188": ("June 22nd 1841", "1841-06-22"),
    "Sheffield-522": ("June 21st 1841", "1841-06-21"),
    "Stratton-321": ("In 1841, Ann and her family immigrate", "1841-00-00"),
}
KEY = re.compile(r"(?:[^\s/?#&=]+-)?[1-9][0-9]*")
HEADING = re.compile(r"^\s*=+\s*([^=]+?)\s*=+\s*$", re.M)
REF = re.compile(r"<ref\b(?![^>]*?/>)([^>]*)>(.*?)</ref\s*>", re.I | re.S)
MIGRATION = re.compile(r"\b(?:immigrat\w*|emigrat\w*|passenger list|arrived (?:at|in)|naturaliz\w*|natural ised)\b", re.I)
US = re.compile(r"\b(?:United States|U\.?S\.?A\.?|USA|Ohio|Oklahoma|Pennsylvania|Virginia|California|New York|Maryland|Illinois|Indiana|Kansas|Iowa|Missouri)\b", re.I)
FOREIGN = re.compile(
    r"(?:^|,\s*)(?:France|Germany|England|Scotland|Wales|Ireland|United Kingdom|"
    r"Netherlands|Switzerland|Sweden|Norway|Denmark|Italy|Canada|Poland|Belgium|Austria|"
    r"Spain|Portugal|Finland|Mexico|Australia|New Zealand)\s*$", re.I,
)


class APIError(Exception):
    """Transport failure or unexpected API response."""


class Unavailable(APIError):
    """WikiTree returned an application-level error for this record."""


class WikiTree:
    def __init__(self, delay=0.25):
        self.delay = delay

    def fetch(self, action, key, **params):
        query = urlencode(dict(action=action, key=key, appId="bronkar-genealogy", **params))
        url = f"{API}?{query}"
        for attempt in range(3):
            sleep(self.delay)
            try:
                with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=30) as response:
                    payload = json.load(response)
                break
            except (HTTPError, URLError, TimeoutError) as error:
                transient = not isinstance(error, HTTPError) or error.code in (429, 500, 502, 503, 504)
                if attempt == 2 or not transient:
                    raise APIError(f"{action} transport failed ({type(error).__name__})") from error
                retry_after = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) else ""
                sleep(max(2 ** attempt, int(retry_after) if retry_after.isdecimal() else 0))
            except (ValueError, UnicodeError) as error:
                raise APIError(f"{action} returned invalid JSON") from error
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise APIError(f"{action} returned an unexpected envelope")
        record = payload[0]
        if "status" not in record:
            raise APIError(f"{action} omitted status")
        if str(record["status"]) != "0":
            raise Unavailable(f"{action} reported an unavailable record")
        # Use the canonical exported profile URL for attribution. A request may
        # contain a numeric lookup ID that is unnecessary in a research export.
        return record, {
            "endpoint": API, "action": action, "parameters": params,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }


def public_deceased(person):
    return (
        str(person.get("Privacy")) in {"50", "60"}
        and str(person.get("IsLiving")) in {"0", "False"}
        and bool(re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}", str(person.get("DeathDate"))))
        and int(person["DeathDate"][:4]) <= datetime.now(timezone.utc).year
    )


def citations(bio):
    for match in REF.finditer(bio):
        if match[2].strip():
            yield "inline_ref", match[2].strip()
    headings = list(HEADING.finditer(bio))
    for index, heading in enumerate(headings):
        if heading[1].strip().casefold() != "sources":
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(bio)
        section = REF.sub("", bio[heading.end():end])
        for line in section.splitlines():
            line = line.strip()
            if line and not re.fullmatch(r"<references\s*/\s*>|\{\{reflist\}\}", line, re.I):
                yield "sources_section", re.sub(r"^[*#]+\s*", "", line)


def collect(client, seeds, depth, research_text=False):
    profiles, relationships, sources, photos, gaps, leads = {}, {}, [], [], [], {}
    cache = {}
    seen_paths = set()
    ancestry_paths = []
    roots = {}
    queue = deque((seed, None, None, (), ()) for seed in seeds)

    while queue:
        key, child, role, path, ids = queue.popleft()
        seed = path[0] if path else key
        branch = "/".join(path[1:] + ((role,) if role else ())) or "seed"
        context = {"seed": seed, "branch": branch}
        if key not in cache:
            try:
                envelope, provenance = client.fetch(
                    "getPerson", key, resolveRedirect=1, bioFormat="wiki",
                    fields=",".join((*PROFILE_FIELDS, *ROLES, "Privacy", "IsLiving", "Bio", "Photo", "PhotoData")),
                )
                person = envelope.get("person")
                if not isinstance(person, dict):
                    raise APIError("getPerson omitted person")
                cache[key] = (person, provenance) if public_deceased(person) else None
                if cache[key] and person.get("Id"):
                    cache[str(person["Id"])] = cache[key]
            except Unavailable:
                cache[key] = None
        if cache[key] is None:
            gaps.append({**context, "child": child, "reason": "unavailable_or_excluded"})
            continue
        person, provenance = cache[key]
        name = person.get("Name")
        if not isinstance(name, str) or "-" not in name or not KEY.fullmatch(name):
            raise APIError("getPerson omitted a valid WikiTree ID")
        if name in ids:
            gaps.append({**context, "child": child, "reason": "cycle"})
            continue
        if (seed, branch, name) in seen_paths:
            continue
        seen_paths.add((seed, branch, name))
        if child is None:
            roots[seed] = name
        ancestry_paths.append({"seed": roots[seed], "branch": branch, "profile": name})
        url = SITE + quote(name, safe="")
        origin = {**provenance, "profile_url": url}
        if name not in profiles:
            status = person.get("DataStatus") or {}
            profile = {field: person.get(field) for field in PROFILE_FIELDS if field not in ("Id", "DataStatus")}
            profile["DataStatus"] = {field: status[field] for field in (*PROFILE_FIELDS, *ROLES, "BioFather", "BioMother") if field in status}
            profile["provenance"] = origin
            profiles[name] = profile
            bio = person.get("bio") or ""
            if research_text:
                profile["biography_wikitext"] = bio
            for index, (kind, value) in enumerate(dict.fromkeys(citations(bio)), 1):
                citation = {"profile": name, "ordinal": index, "kind": kind, "provenance": origin}
                if research_text:
                    citation["wikitext"] = value
                sources.append(citation)
            photos.extend(photo_metadata(client, name, url, person, origin, research_text, gaps))
            leads[name] = migration_lead(profile, bio)
        if child:
            status = profiles[child]["DataStatus"]
            marker = status.get(role)
            biological = status.get(f"Bio{role}")
            relationships[child, role, name] = {
                "child": child, "parent": name, "role": role.lower(), "status": marker,
                "biological_status": biological, "assessment": "tree_assertion_review_required",
                "provenance": profiles[child]["provenance"],
            }
        if leads[name]:
            leads[name]["paths"].append(context)
        for parent_role in ROLES:
            next_path = (*path, role) if path else (seed,)
            parent_branch = "/".join((*next_path[1:], parent_role))
            parent_id = str(person.get(parent_role))
            reason = "missing_parent"
            if parent_id.isdecimal() and int(parent_id) > 0:
                if len(ids) < depth:
                    queue.append((parent_id, name, parent_role, next_path, (*ids, name)))
                    continue
                reason = "depth_limit"
            gaps.append({"seed": seed, "branch": parent_branch, "child": name, "reason": reason})

    candidates = sorted((lead for lead in leads.values() if lead), key=lead_order)
    return {
        "schema_version": 1, "requested_seed_count": len(seeds), "depth": depth,
        "research_text_included": research_text, "profiles": list(profiles.values()),
        "relationships": list(relationships.values()), "citations": sources, "photos": photos,
        "immigration_leads": candidates, "branch_summary": summarize(candidates, gaps, roots, seeds),
        "gaps": [{**gap, **({"seed": roots.get(gap["seed"])} if "seed" in gap else {})} for gap in gaps],
        "ancestry_paths": ancestry_paths,
    }


def photo_metadata(client, name, url, person, origin, research_text, gaps):
    rows = []
    if isinstance(person.get("PhotoData"), dict):
        data = person["PhotoData"]
        rows.append({
            "profile": name, "kind": "primary_photo",
            "metadata": {key: data[key] for key in ("path", "url", "width", "height", "orig_width", "orig_height") if key in data},
            "provenance": origin,
        })
    start = 0
    seen = set()
    while True:
        try:
            record, provenance = client.fetch("getPhotos", name, start=start, limit=10)
        except Unavailable:
            gaps.append({"child": name, "reason": "photo_listing_unavailable"})
            return rows
        page = record.get("photos")
        if not isinstance(page, list):
            raise APIError("getPhotos omitted its photo list")
        if not page:
            return rows
        for photo in page:
            if not isinstance(photo, dict) or not photo.get("PageId"):
                raise APIError("getPhotos returned an invalid photo record")
            if photo["PageId"] in seen:
                continue
            seen.add(photo["PageId"])
            fields = PHOTO_FIELDS + (("Title", "Location", "Date") if research_text else ())
            rows.append({
                "profile": name, "kind": "attached_photo",
                "metadata": {key: photo[key] for key in fields if key in photo},
                "provenance": {**provenance, "profile_url": url},
            })
        if len(page) < 10:
            return rows
        if int(record.get("start", -1)) != start or len(seen) < start + len(page):
            gaps.append({"child": name, "reason": "photo_pagination_incomplete"})
            return rows
        start += len(page)


def migration_lead(profile, bio):
    name = profile["Name"]
    birth, death = (profile[field] or "" for field in ("BirthLocation", "DeathLocation"))
    text_match = MIGRATION.search(bio) and not US.search(birth)
    evidence = []
    by_date = None
    if FOREIGN.search(birth) and US.search(death):
        evidence.append("foreign_birth_and_US_death")
        by_date = profile["DeathDate"]
    if text_match:
        evidence.append("migration_keyword_in_profile_wikitext")
    focus = name == LEWIS
    if focus and "26 Aug 1812" in bio and "Ohio Militia" in bio and re.search(r"born[^.]*\bFrance\b", bio, re.I):
        evidence.append("profile_reports_French_birth_and_Ohio_militia_service")
        by_date = "1812-08-26"
    arrival = None
    supplemental_sources = []
    if name in SHEFFIELD_ARRIVALS:
        wording, reported_date = SHEFFIELD_ARRIVALS[name]
        if wording.casefold() in bio.casefold() and "Ontario" in bio:
            arrival = reported_date
            # Related profiles attribute the same voyage to different days.
            by_date = "1841-06-22"
            evidence.append("profile_reports_Ontario_voyage_in_1841")
            supplemental_sources.append({
                "url": ONTARIO_MANIFEST, "type": "manifest_transcription",
                "date": by_date, "date_meaning": "manifest_sworn_in_New_York",
                "note": "Entries 106–110 list the Sheffield family. Profile linkage remains subject to review.",
            })
    if not evidence and not focus:
        return None
    return {
        "profile": name, "priority": "lewis_bronkar_sr" if focus else "branch_research",
        "assessment": "unverified_lead", "evidence": evidence, "birth_location": birth,
        "death_location": death, "us_presence_by": by_date, "arrival_date": arrival,
        "paths": [], "provenance": profile["provenance"], "supplemental_sources": supplemental_sources,
    }


def lead_order(lead):
    return (lead["priority"] != "lewis_bronkar_sr", lead["profile"])


def summarize(candidates, gaps, roots, seeds):
    rows = []
    for seed in seeds:
        sides = set(ROLES)
        for gap in gaps:
            if gap.get("seed") == seed and gap.get("branch") != "seed":
                steps = gap["branch"].split("/")
                sides.update("/".join(steps[:level]) for level in range(2, len(steps) + 1))
        for side in sorted(sides, key=lambda path: (path.count("/"), path)):
            prefix = side + "/"
            leads = [
                lead for lead in candidates if any(
                    path["seed"] == seed and (path["branch"] == side or path["branch"].startswith(prefix))
                    for path in lead["paths"]
                )
            ]
            dated = [lead for lead in leads if lead["us_presence_by"]]
            dated.sort(key=lambda lead: lead["us_presence_by"].replace("-00", "-99"))
            earliest = dated[0]["us_presence_by"] if dated else None
            counts = Counter(
                gap["reason"] for gap in gaps
                if gap.get("seed") == seed and (gap["branch"] == side or gap["branch"].startswith(prefix))
            )
            rows.append({
                "seed": roots.get(seed), "branch": side.lower(),
                "candidate_profiles": [lead["profile"] for lead in leads],
                "earliest_dated_presence_leads": [lead["profile"] for lead in dated if lead["us_presence_by"] == earliest],
                "earliest_presence_by": earliest,
                "earliest_immigrant": None, "assessment": "requires_record_verification",
                "coverage_gaps": dict(counts),
            })
    return rows


def export(data, directory):
    directory.mkdir(parents=True, exist_ok=True)
    # Fresh run directory prevents stale research-text files and mixed runs.
    run = directory / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(mode=0o700)
    for table, rows in data.items():
        if not isinstance(rows, list):
            continue
        fields = CSV_FIELDS[table]
        with (run / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: csv_cell(row.get(key)) for key in fields})
    (run / "research.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return run


CSV_FIELDS = {
    "profiles": ("Name", *PROFILE_FIELDS[2:], "biography_wikitext", "provenance"),
    "relationships": ("child", "parent", "role", "status", "biological_status", "assessment", "provenance"),
    "citations": ("profile", "ordinal", "kind", "wikitext", "provenance"),
    "photos": ("profile", "kind", "metadata", "provenance"),
    "immigration_leads": ("profile", "priority", "assessment", "evidence", "birth_location",
                          "death_location", "us_presence_by", "arrival_date", "paths", "provenance", "supplemental_sources"),
    "branch_summary": ("seed", "branch", "candidate_profiles", "earliest_dated_presence_leads",
                       "earliest_presence_by", "earliest_immigrant", "assessment", "coverage_gaps"),
    "gaps": ("seed", "branch", "child", "reason"),
    "ancestry_paths": ("seed", "branch", "profile"),
}


def csv_cell(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", help="WikiTree ID or numeric ID, repeatable")
    parser.add_argument("--depth", type=int, choices=range(0, 9), default=5, metavar="0–8")
    parser.add_argument("--out", type=Path, default=Path("exports"))
    parser.add_argument("--research-text", action="store_true", help="include unreviewed biography, citations and photo captions for local research")
    parser.add_argument("--storyboard", action="store_true", help="generate a local interactive timeline")
    parser.add_argument("--download-photos", action="store_true", help="cache image thumbnails for the local storyboard")
    args = parser.parse_args(argv)
    seeds = tuple(dict.fromkeys(args.seed or SEEDS))
    if any(not KEY.fullmatch(seed) for seed in seeds):
        parser.error("seeds must be WikiTree IDs or positive numeric IDs")
    try:
        data = collect(WikiTree(), seeds, args.depth, args.research_text)
        run = export(data, args.out)
        if args.storyboard or args.download_photos:
            downloaded, failed = timeline(data, run, download=args.download_photos)
            if args.download_photos:
                print(f"WikiTree thumbnails downloaded: {downloaded}; unavailable: {failed}", file=sys.stderr)
        print(run)
        return 0 if data["profiles"] else 2
    except (APIError, OSError) as error:
        print(f"bronkar-genealogy: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
