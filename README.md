# Bronkar genealogy

Research public ancestors of Gladden A. Bronkar (`Bronkar-5`) and Lewis Dezira Bronkar Sr. (`Bronkar-39`) through the official WikiTree API. The CLI writes structured research tables and can build a local family atlas with a timeline scrubber, branch and person filters, approximate geographic stops, and historical images.

## Run it

Python 3.10+ is supported. The CLI uses the standard library. Live collection and photo downloads require internet access.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m unittest discover -v

bronkar-genealogy --depth 2 --out exports
bronkar-genealogy --depth 6 --research-text --download-photos --out exports
bronkar-genealogy --seed Bronkar-39 --depth 4 --research-text --storyboard
```

You can also run `python3 bronkar_genealogy.py` from the checkout. The CLI prints a new UTC-stamped export directory. Open its `storyboard.html` directly in a browser. Use the slider, arrow keys on the focused slider, or the Previous and Next buttons. The map shows dated stops for the selected person. Photo links open the corresponding WikiTree image page. Downloaded thumbnails work offline. `--storyboard` on its own creates an atlas with source links. Add `--research-text` to use the dated captions attached by WikiTree contributors on the timeline.

The timeline includes profile birth and death events, dated source attachments with places, and a few attributed research events for Lewis and the Sheffield family. Approximate map points come from a short regional lookup table. Unmatched places still appear as text. Connecting lines indicate the order of recorded places for one person.

## API verification

Anonymous GET probes against both seeds returned HTTP 200 and one-object JSON arrays. `getPerson` and `getBio` returned a numeric `status: 0`. A request for the `Bio` field with `bioFormat=wiki` returned lowercase `bio`, including inline references and source sections. The `Father` and `Mother` fields contained numeric lookup IDs or null. The open profiles returned `Privacy: 60` and `IsLiving: 0`. `getPhotos` returned an independent list of attachments, including for Gladden, whose primary `PhotoData` was null. Photo results included relative WikiTree image and thumbnail paths and offset metadata. [Official API documentation](https://github.com/wikitree/wikitree-api)

Reproduce the preflight with the same anonymous endpoints. These commands print raw API responses locally.

```sh
for seed in Bronkar-5 Bronkar-39; do
  curl --fail --silent --show-error --get https://api.wikitree.com/api.php \
    --data-urlencode action=getPerson --data-urlencode "key=$seed" \
    --data-urlencode appId=bronkar-genealogy --data-urlencode bioFormat=wiki \
    --data-urlencode fields=Id,Name,Privacy,IsLiving,DeathDate,Father,Mother,DataStatus,Bio,Photo,PhotoData
  curl --fail --silent --show-error --get https://api.wikitree.com/api.php \
    --data-urlencode action=getBio --data-urlencode "key=$seed" \
    --data-urlencode appId=bronkar-genealogy --data-urlencode bioFormat=wiki
  curl --fail --silent --show-error --get https://api.wikitree.com/api.php \
    --data-urlencode action=getPhotos --data-urlencode "key=$seed" \
    --data-urlencode appId=bronkar-genealogy --data-urlencode start=0 --data-urlencode limit=10
done
```

## Outputs and privacy

`research.json` contains profiles, parent assertions, citations, photo metadata, immigration leads, branch summaries, full ancestry paths, and coverage gaps. Each of those tables also has a CSV. Nested CSV values are JSON. Partial dates preserve WikiTree's zero month or day. Provenance includes the profile URL, endpoint, API action, parameters, and UTC retrieval timestamp. Parent rows preserve WikiTree's relationship and biological-status markers and flag each link for evidence review. Coverage gaps include unavailable or filtered profiles, missing parents, and depth limits.

The CLI exports only profiles explicitly marked public or open and deceased, with a recorded death year. It follows parent IDs only. The default omits free-text biographies, citation bodies, and photo captions. `--research-text` preserves them locally as WikiTree markup and attachment metadata. Before sharing exports containing text or images, review them for living relatives' details and image reuse terms. Each run has a private directory on Unix, and `exports/` is ignored by Git. The CLI has no publishing step.

## Immigration evidence to pursue

- **Lewis Dezira Bronkar Sr.** WikiTree reports a birth around 1783 in France and Ohio militia service from August 26, 1812. This gives a lead for U.S. presence by that date. The 1860 census citation supports the French birthplace claim and the Ohio residence. Obtain the underlying militia and pension records, then check Muskingum County court records and early tax lists for the earliest dated appearance and name variants. His French locality, arrival date, and port remain open. [WikiTree profile](https://www.wikitree.com/wiki/Bronkar-39) · [1860 census citation](https://www.familysearch.org/ark:/61903/1:1:MCGL-XRT) · [War of 1812 pension index citation](https://www.familysearch.org/ark:/61903/1:1:X8CX-5NR)
- **Gladden's maternal branch.** WikiTree associates Thomas Sheffield, Ann Stratton, and Elisha Sheffield with the Ontario voyage to New York in 1841. Related profiles give June 21 and June 22. The manifest transcription lists the family at entries 106–110 and dates the sworn document June 22. Verify the exact landing date and the ancestral parent links independently. [Thomas Sheffield](https://www.wikitree.com/wiki/Sheffield-188) · [Ann Stratton](https://www.wikitree.com/wiki/Stratton-321) · [Elisha Sheffield](https://www.wikitree.com/wiki/Sheffield-522) · [Ontario manifest transcription](https://www.immigrantships.net/v6/1800v6/ontario18410622.html)
- **Lewis's own parental branches.** The seed API responses had null parent fields. Start with independent evidence for his identity and origins before attaching either parent.

Branch summaries rank candidates by the earliest dated U.S. presence lead available in the collected data. Birth and death locations can suggest migration and give an upper bound for presence. Keyword-only leads require review. A presence date can come from a much later record than arrival. The `earliest_immigrant` field remains unset until records and links are independently verified.
