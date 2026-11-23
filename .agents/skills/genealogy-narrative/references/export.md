# Genealogy export

A run directory (or the zip from the atlas **Download data** button) contains:

| File | Role |
| --- | --- |
| `research.json` | All tables, plus `schema_version`, `depth`, `research_text_included`, and `query` |
| `profiles.csv`, `relationships.csv`, `citations.csv`, `photos.csv`, `marriages.csv`, `immigration_leads.csv`, `branch_summary.csv`, `gaps.csv`, `ancestry_paths.csv` | The same rows. Nested values are JSON strings |
| `storyboard.html` | Atlas page. Present when a storyboard was built |
| `images/`, `photo_downloads.json` | Local image copies, when a download was requested |

`query` is either `{ "profile_ids": ["WikiTree-ID"] }` or `{ "name": "...", "birth_place": "..." }`.

## research_text_included

The CLI fetches each biography on every run. It uses the text for migration keywords and, when a storyboard is built, for the atlas. Writing that wording into `research.json` and the CSVs is separate.

Current `genealogy.py` writes it by default. `--no-research-text` leaves it out. An older run can still have `research_text_included: false`. Then:

- `profiles` have no `biography_wikitext`
- `citations` have `profile`, `ordinal`, `kind`, and `provenance`, and no `wikitext`
- photo metadata omits `Title`, and omits `Date` and `Location` when the photo year falls outside the person's birth and death years

When the flag is false and `storyboard.html` exists, cleaned biography paragraphs may still be embedded. Extract the JSON inside `<script id="tree">`. Each `people[]` entry has `biography` (array of plain paragraphs), `bibliography` (`url` and `label` for external links found in inline refs), `birth`, `death`, `key`, and `name`. Those paragraphs already drop the sources section, reference tags, DNA-kit lines, and lines matching "survived by" or "survivors include". Treat them as the atlas's shortened text, not the full biography. Say that the JSON omitted the wording.

## Fields

**profiles.** `Name` (WikiTree ID), `FirstName`, `MiddleName`, `LastNameAtBirth`, `LastNameCurrent`, `Suffix`, `BirthDate`, `BirthLocation`, `DeathDate`, `DeathLocation`, `DataStatus`, `provenance`, and `biography_wikitext` when text was kept.

Dates are `YYYY-MM-DD`. `00` means that part was not recorded.

`DataStatus` for dates and places: `""` not selected, `guess`, `certain`, `before`, `after`. For `Father` and `Mother`: `5` non-biological, `10` uncertain, `20` confident, `30` confirmed with DNA. Blank means unmarked. The same numbers apply to `BioFather` and `BioMother`, without `5`.

**relationships.** `child`, `parent`, `role` (`father`, `mother`, or `parent` for a child of the seed), `status`, `biological_status`, `assessment`, `provenance`. `status` is the child's `DataStatus` for that parent.

**ancestry_paths.** `seed`, `branch`, `profile`. `branch` is `seed`, or `Father` / `Mother` segments, or `Child` for a child of the seed. One profile on several branches is the same person reached more than once.

**marriages.** `first`, `second`, `date`, `place`, `provenance`. These are spouse links returned for people in the export.

**citations.** `profile`, `ordinal`, `kind` (`inline_ref` or `sources_section`), `wikitext` when text was kept, `provenance`.

**photos.** `profile`, `kind` (`primary_photo` or `attached_photo`), `metadata`, `provenance`. Attached metadata includes `PageId`, `ImageName`, `Type`, `URL`, and size fields. `Title`, `Location`, and `Date` appear when research text was kept, or when the photo year sits inside the lifespan.

**immigration_leads.** `profile`, `priority`, `assessment` (`unverified_lead`), `evidence` (`foreign_birth_and_US_death` and/or `migration_keyword_in_profile_wikitext`), places, `us_presence_by`, `arrival_date` (empty), `paths`, `provenance`.

**branch_summary.** Per seed and side. `earliest_immigrant` is empty. `earliest_presence_by` is the earliest `us_presence_by` on that side. `assessment` is `requires_record_verification`.

**gaps.** `seed`, `branch`, `child`, `reason`. Reasons include `depth_limit`, `missing_parent`, `unavailable_or_excluded`, `cycle`, `child_listing_unavailable`, `photo_listing_unavailable`, `photo_pagination_incomplete`.

**provenance.** `endpoint`, `action`, `parameters`, `profile_url`, `retrieved_at` (UTC).

The export keeps public, deceased profiles with a recorded death year, their parent chain to `depth`, and public deceased children of the seeds. Biography text can still name living people. Leave those people out of the narrative.
