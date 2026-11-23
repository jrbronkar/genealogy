# Genealogy CLI

Research the ancestry of any public, deceased WikiTree profile. Enter a name, choose a match, and explore the person's tree in a local timeline.

## Run it

From this repository, run the script with Python 3.10+ and internet access. The script uses the standard library.

```sh
python3 genealogy.py
python3 genealogy.py --name "Samuel Clemens" --depth 3 --research-text --download-originals
python3 genealogy.py --name "Samuel Clemens" --birth-place Missouri
python3 genealogy.py --seed Clemens-1 --depth 4 --research-text --storyboard
python3 -m unittest discover -v
```

In an interactive terminal, the first command asks for a name. Choose the numbered match using its dates, birthplace, and WikiTree ID, or press Enter to cancel. The search uses the first given name and surname. Use `"de la Cruz, Maria"` for a multiword surname. It checks up to 200 WikiTree results per search and displays eligible public, deceased profiles. A narrower birthplace or a known WikiTree ID helps with common names.

Name searches generate a storyboard automatically. The CLI prints a new UTC-stamped export directory. On macOS, run `open` with the path to its `storyboard.html`, or open that file directly from Finder. Use the slider, arrow keys on the focused slider, or Previous and Next. The map shows dated stops for the selected person. Photo links open their WikiTree pages. Add `--download-photos` for offline thumbnails and `--research-text` for biography, citation text, and dated photo captions.

To install the shorter `genealogy` command, use a virtual environment.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
genealogy --name "Samuel Clemens"
```

## Download ancestor photos

`--download-originals` follows the same ancestor crawl as the research export and saves each distinct available full-size image to the run's `images/` folder. Set `--depth` to the number of parent generations to visit (0–8, default 5). Each public, deceased profile visited is checked for a primary image and every page of attached images, including historical documents. `--download-photos` follows the same tree and downloads smaller thumbnails.

`photo_downloads.json` records the source page, requested image URL, local filename, and status for each photo entry. Unavailable URLs, unsupported formats, and originals over 32 MiB appear as unavailable. The download and storyboard stay local.

The timeline includes profile birth and death events, dated source attachments with places. Approximate map points come from a short regional lookup table. Unmatched places still appear as text. Connecting lines indicate the order of recorded places for one person.

## API verification

The official anonymous API returns one-object JSON arrays. `getPerson` provides profile fields and numeric parent lookup IDs. Requesting `Bio` with `bioFormat=wiki` returns the biography as `bio`. `getPhotos` lists attachments independently of the primary photo. [Official API documentation](https://github.com/wikitree/wikitree-api)

Reproduce the preflight with the same anonymous endpoints. These commands print raw API responses locally.

```sh
for seed in Clemens-10 Clemens-1; do
  curl --fail --silent --show-error --get https://api.wikitree.com/api.php \
    --data-urlencode action=getPerson --data-urlencode "key=$seed" \
    --data-urlencode appId=genealogy-cli --data-urlencode bioFormat=wiki \
    --data-urlencode fields=Id,Name,Privacy,IsLiving,DeathDate,Father,Mother,DataStatus,Bio,Photo,PhotoData
  curl --fail --silent --show-error --get https://api.wikitree.com/api.php \
    --data-urlencode action=getBio --data-urlencode "key=$seed" \
    --data-urlencode appId=genealogy-cli --data-urlencode bioFormat=wiki
  curl --fail --silent --show-error --get https://api.wikitree.com/api.php \
    --data-urlencode action=getPhotos --data-urlencode "key=$seed" \
    --data-urlencode appId=genealogy-cli --data-urlencode start=0 --data-urlencode limit=10
done
```

## Outputs and privacy

`research.json` contains profiles, parent assertions, citations, photo metadata, immigration leads, branch summaries, full ancestry paths, and coverage gaps. Each of those tables also has a CSV. Nested CSV values are JSON. Partial dates preserve WikiTree's zero month or day. Provenance includes the profile URL, endpoint, API action, parameters, and UTC retrieval timestamp. Parent rows preserve WikiTree's relationship and biological-status markers and flag each link for evidence review. Coverage gaps include unavailable or filtered profiles, missing parents, and depth limits.

The CLI exports only profiles explicitly marked public or open and deceased, with a recorded death year. It follows parent IDs only. The default omits free-text biographies, citation bodies, and photo captions. `--research-text` preserves them locally as WikiTree markup and attachment metadata. Before sharing exports containing text or images, review them for living relatives' details and image reuse terms. Each run has a private directory on Unix, and `exports/` is ignored by Git. The CLI has no publishing step.

## Immigration leads

Branch summaries rank candidates by the earliest dated U.S. presence lead available in the collected data. Birth and death locations can suggest migration and give an upper bound for presence. Keyword-only leads require review. A presence date can come from a much later record than arrival. The `earliest_immigrant` field remains unset until records and links are independently verified.
