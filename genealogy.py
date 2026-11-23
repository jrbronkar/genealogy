"""Anonymous, read-only WikiTree ancestry research."""
import argparse
import csv
from collections import Counter, deque
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import webbrowser
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from storyboard import timeline

API = "https://api.wikitree.com/api.php"
SITE = "https://www.wikitree.com/wiki/"
ROLES = ("Father", "Mother")
PROFILE_FIELDS = (
    "Id", "Name", "FirstName", "MiddleName", "LastNameAtBirth", "LastNameCurrent",
    "BirthDate", "BirthLocation", "DeathDate", "DeathLocation", "Suffix", "DataStatus",
)
PHOTO_FIELDS = ("PageId", "ImageName", "Type", "Width", "Height", "URL", "URL_300", "URL_75")
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
    def __init__(self, delay=0.25, progress=None):
        self.delay = delay
        self.progress = progress

    def fetch(self, action, key=None, **params):
        query_params = dict(action=action, appId="genealogy-cli", **params)
        if key is not None:
            query_params["key"] = key
        query = urlencode(query_params)
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
                wait = max(2 ** attempt, int(retry_after) if retry_after.isdecimal() else 0)
                if self.progress:
                    reason = f"HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__
                    self.progress(f"[WikiTree] {action} failed ({reason}). Retrying in {wait}s (attempt {attempt + 2}/3).")
                sleep(wait)
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


def search_people(client, name, birth_location=None, progress=None):
    if "," in name:
        surname, given = (part.strip() for part in name.split(",", 1))
    else:
        parts = name.split()
        surname = parts[-1] if parts else ""
        given = " ".join(parts[:-1])
    if not surname:
        raise ValueError("Enter a surname or a full name.")
    params = {"LastName": surname}
    if given:
        params["FirstName"] = given.split()[0]
    if birth_location:
        params["BirthLocation"] = birth_location
    results = {}
    for start in range(0, 200, 50):
        if progress:
            progress(f"[Search] Asking WikiTree for matches, page {start // 50 + 1}.")
        record, _ = client.fetch(
            "searchPerson", **params, start=start, limit=50,
            fields="Name,FirstName,MiddleName,LastNameAtBirth,LastNameCurrent,Suffix,Privacy,IsLiving,BirthDate,DeathDate,BirthLocation",
        )
        matches, total = record.get("matches"), record.get("total")
        if not isinstance(matches, list) or not str(total).isdecimal():
            raise APIError("searchPerson returned an unexpected result")
        for person in matches:
            if not isinstance(person, dict) or not public_deceased(person):
                continue
            key = person.get("Name", "")
            if isinstance(key, str) and "-" in key and KEY.fullmatch(key):
                results[key] = person
        if start + 50 >= int(total) or not matches:
            break
    return list(results.values()), int(total) > 200


def select_profile(matches):
    for index, person in enumerate(matches, 1):
        key = person["Name"]
        name = " ".join(
            str(person[field]) for field in ("FirstName", "MiddleName", "LastNameAtBirth", "Suffix") if person.get(field)
        ) or key
        birth = str(person.get("BirthDate") or "")[:4]
        death = person["DeathDate"][:4]
        place = person.get("BirthLocation") or ""
        # Keep terminal control characters from upstream profile text out of the menu.
        label = f"{index:>3}. {name} ({birth or '?'}–{death})  {place}  [{key}]"
        print("".join(char for char in label if char.isprintable()), file=sys.stderr)
    while True:
        print("Select a profile number, or press Enter to cancel: ", end="", file=sys.stderr, flush=True)
        selection = sys.stdin.readline().strip()
        if not selection:
            return None
        if selection.isascii() and selection.isdecimal() and len(selection) <= 3 and 1 <= int(selection) <= len(matches):
            return matches[int(selection) - 1]["Name"]
        print("Enter a number from the list.", file=sys.stderr)


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


def collect(client, seeds, depth, research_text=True, progress=None, atlas_biographies=None):
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
            if progress:
                target = f"{role.lower()} of {child}" if child else f"starting profile {key}"
                progress(f"[Ancestry] Fetching {target}, generation {len(ids)}/{depth} ({len(queue)} queued).")
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
            if progress:
                progress("[Ancestry] Profile unavailable or outside the public, deceased scope.")
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
            if progress:
                full_name = " ".join(
                    str(profile[field]) for field in ("FirstName", "MiddleName", "LastNameAtBirth", "Suffix") if profile.get(field)
                ) or name
                progress(f"[Ancestry] Found {full_name} [{name}]. Reading biography and citations.")
            bio = person.get("bio") or ""
            if atlas_biographies is not None and bio:
                atlas_biographies[name] = {"biography_wikitext": bio, "provenance": origin}
            if research_text:
                profile["biography_wikitext"] = bio
            for index, (kind, value) in enumerate(dict.fromkeys(citations(bio)), 1):
                citation = {"profile": name, "ordinal": index, "kind": kind, "provenance": origin}
                if research_text:
                    citation["wikitext"] = value
                sources.append(citation)
            attachments = photo_metadata(client, name, url, person, origin, research_text, gaps, progress)
            photos.extend(attachments)
            if progress:
                progress(f"[Ancestry] {name} processed. Found {len(attachments)} photo entries.")
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


def include_starting_children(client, data, research_text=True, progress=None, atlas_biographies=None):
    roots = [row["profile"] for row in data["ancestry_paths"] if row["branch"] == "seed"]
    known = {profile["Name"] for profile in data["profiles"]}
    existing_links = {(link["parent"], link["child"]) for link in data["relationships"]}
    for root in dict.fromkeys(roots):
        if progress:
            progress(f"[Family] Checking WikiTree for public children of {root}.")
        try:
            result, source = client.fetch(
                "getRelatives", keys=root, getChildren=1, fields="Name,Privacy,IsLiving,DeathDate",
            )
        except Unavailable:
            data["gaps"].append({"seed": root, "branch": "Child", "child": root, "reason": "child_listing_unavailable"})
            continue
        items = result.get("items")
        if not isinstance(items, list):
            raise APIError("getRelatives omitted its items")
        for item in items:
            children = item["person"].get("Children") or {}
            if isinstance(children, dict):
                children = children.values()
            for child in children:
                key = child.get("Name")
                if not isinstance(key, str) or "-" not in key or not KEY.fullmatch(key) or not public_deceased(child):
                    continue
                if key not in known:
                    details = collect(client, (key,), 0, research_text, progress, atlas_biographies)
                    if not details["profiles"] or details["profiles"][0]["Name"] != key:
                        continue
                    for table in ("profiles", "citations", "photos"):
                        data[table].extend(details[table])
                    known.add(key)
                if (root, key) not in existing_links:
                    data["relationships"].append({
                        "child": key, "parent": root, "role": "parent", "status": None,
                        "biological_status": None, "assessment": "tree_assertion_review_required",
                        "provenance": {**source, "profile_url": SITE + quote(root, safe="")},
                    })
                    existing_links.add((root, key))
                path = {"seed": root, "branch": "Child", "profile": key}
                if path not in data["ancestry_paths"]:
                    data["ancestry_paths"].append(path)


def add_marriage_records(client, data, progress=None):
    known = {profile["Name"] for profile in data["profiles"]}
    keys = sorted(known)
    marriages = {}
    for start in range(0, len(keys), 50):
        if progress:
            progress(f"[Family] Checking recorded marriages for profiles {start + 1}–{min(start + 50, len(keys))}.")
        try:
            envelope, origin = client.fetch(
                "getRelatives", keys=",".join(keys[start:start + 50]),
                getSpouses=1, fields="Name,Privacy,IsLiving,DeathDate",
            )
        except Unavailable:
            continue
        for item in envelope.get("items", []):
            person = item["person"]
            key = person.get("Name")
            if key not in known:
                continue
            spouses = person.get("Spouses") or {}
            if isinstance(spouses, dict):
                spouses = spouses.values()
            for spouse in spouses:
                other = spouse.get("Name")
                if other == key or other not in known or not public_deceased(spouse):
                    continue
                if str(spouse.get("do_not_display", "0")) not in {"0", "False"}:
                    continue
                pair = tuple(sorted((key, other)))
                date = spouse.get("marriage_date")
                if not isinstance(date, str) or not re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}", date):
                    date = None
                place = spouse.get("marriage_location") or None
                if pair not in marriages or (date and not marriages[pair]["date"]):
                    marriages[pair] = {
                        "first": pair[0], "second": pair[1], "date": date, "place": place,
                        "provenance": {**origin, "profile_url": SITE + quote(key, safe="")},
                    }
    data["marriages"] = [marriages[pair] for pair in sorted(marriages)]


def photo_metadata(client, name, url, person, origin, research_text, gaps, progress=None):
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
        if progress:
            progress(f"[Photos] Fetching attachment metadata for {name}, page {start // 10 + 1}.")
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
            fields = PHOTO_FIELDS
            if research_text:
                fields += ("Title", "Location", "Date")
            else:
                birth_year = str(person.get("BirthDate") or "")[:4]
                death_year = str(person.get("DeathDate") or "")[:4]
                photo_year = str(photo.get("Date") or "")[:4]
                if len(birth_year) == len(death_year) == len(photo_year) == 4 and "0000" < birth_year <= photo_year <= death_year:
                    fields += ("Date", "Location")
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
    if not evidence:
        return None
    return {
        "profile": name, "priority": "branch_research",
        "assessment": "unverified_lead", "evidence": evidence, "birth_location": birth,
        "death_location": death, "us_presence_by": by_date, "arrival_date": None,
        "paths": [], "provenance": profile["provenance"], "supplemental_sources": [],
    }


def lead_order(lead):
    return (lead["us_presence_by"] is None, (lead["us_presence_by"] or "").replace("-00", "-99"), lead["profile"])


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
    "marriages": ("first", "second", "date", "place", "provenance"),
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


def terminal_progress(message):
    print("".join(char for char in message if char.isprintable()), file=sys.stderr, flush=True)


def open_storyboard(page):
    server = subprocess.Popen(
        [sys.executable, "-B", "-m", "storyboard.server", str(page)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    url = ""
    try:
        url = server.stdout.readline().strip()
        if not url.startswith("http://127.0.0.1:"):
            return False
        if sys.platform == "darwin":
            return subprocess.run(["/usr/bin/open", url], check=False).returncode == 0
        return webbrowser.open(url)
    finally:
        server.stdout.close()
        if not url:
            server.terminate()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    entry = parser.add_mutually_exclusive_group()
    entry.add_argument("--seed", action="append", help="WikiTree ID or numeric ID, repeatable")
    entry.add_argument("--name", help="find a public deceased profile by name")
    parser.add_argument("--birth-place", help="narrow name search by birthplace")
    parser.add_argument("--depth", type=int, choices=range(0, 9), default=5, metavar="0–8")
    parser.add_argument("--out", type=Path, default=Path("exports"))
    text = parser.add_mutually_exclusive_group()
    text.add_argument(
        "--research-text", dest="research_text", action="store_true",
        help="keep biography text, citation bodies, and photo captions in the export (the default)",
    )
    text.add_argument(
        "--no-research-text", dest="research_text", action="store_false",
        help="omit biography text, citation bodies, and photo captions",
    )
    parser.set_defaults(research_text=True)
    parser.add_argument("--storyboard", action="store_true", help="generate a local interactive timeline")
    media = parser.add_mutually_exclusive_group()
    media.add_argument("--download-photos", action="store_true", help="download thumbnails throughout the ancestry crawl")
    media.add_argument("--download-originals", action="store_true", help="download full-size images throughout the ancestry crawl")
    parser.add_argument("--no-open", action="store_true", help="save the storyboard without opening a browser")
    args = parser.parse_args(argv)
    if args.birth_place and args.seed:
        parser.error("--birth-place applies to name search")
    if args.seed and any(not KEY.fullmatch(seed) for seed in args.seed):
        parser.error("seeds must be WikiTree IDs or positive numeric IDs")
    client = WikiTree(progress=terminal_progress)
    try:
        search_name = None
        if args.seed:
            seeds = tuple(dict.fromkeys(args.seed))
        else:
            name = args.name
            if name is None:
                if not sys.stdin.isatty():
                    parser.print_help(sys.stderr)
                    return 2
                print("Ancestor name: ", end="", file=sys.stderr, flush=True)
                name = sys.stdin.readline().strip()
                if not name:
                    return 0
            search_name = name
            matches, truncated = search_people(client, name, args.birth_place, terminal_progress)
            if truncated:
                print("Showing public deceased matches from the first 200 results. Narrow the search with --birth-place.", file=sys.stderr)
            if not matches:
                print("No eligible WikiTree matches found. Try a different spelling or birthplace.", file=sys.stderr)
                return 2
            key = select_profile(matches)
            if key is None:
                return 0
            seeds = (key,)
            args.storyboard = True
        terminal_progress(f"[Ancestry] Starting from {', '.join(seeds)} (up to {args.depth} parent generations).")
        build_atlas = args.storyboard or args.download_photos or args.download_originals
        atlas_biographies = {} if build_atlas else None
        data = collect(client, seeds, args.depth, args.research_text, terminal_progress, atlas_biographies)
        include_starting_children(client, data, args.research_text, terminal_progress, atlas_biographies)
        add_marriage_records(client, data, terminal_progress)
        data["query"] = {"name": search_name, "birth_place": args.birth_place} if search_name is not None else {
            "profile_ids": list(dict.fromkeys(row["profile"] for row in data["ancestry_paths"] if row["branch"] == "seed"))
        }
        terminal_progress(
            f"[Export] Writing JSON and CSV for {len(data['profiles'])} profiles, "
            f"{len(data['citations'])} citations, and {len(data['photos'])} photo entries."
        )
        run = export(data, args.out)
        if build_atlas:
            downloaded, failed = timeline(
                data, run, download=args.download_photos, originals=args.download_originals, progress=terminal_progress,
                biographies=atlas_biographies,
            )
            if args.download_photos or args.download_originals:
                print(f"Unique images downloaded: {downloaded}; unavailable photo entries: {failed}", file=sys.stderr)
            page = (run / "storyboard.html").resolve()
            terminal_progress(f"[Storyboard] Saved to {page}")
            if data["profiles"] and not args.no_open:
                terminal_progress("[Storyboard] Opening your default browser.")
                try:
                    opened = open_storyboard(page)
                except (OSError, webbrowser.Error):
                    opened = False
                if not opened:
                    terminal_progress("[Storyboard] Browser launch failed. Open the saved file above.")
        print(run)
        return 0 if data["profiles"] else 2
    except (APIError, OSError, ValueError) as error:
        print(f"genealogy: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
