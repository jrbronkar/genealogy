---
name: genealogy-narrative
description: >
  Pull a short, playful list of tidbits from a genealogy CLI export: odd wills,
  round trips, superlatives, named historical events, and a coincidence or two.
  Then answer follow-up questions about the same run. Use when the user asks
  for tidbits, a family story, or interesting details from a WikiTree export,
  a genealogy download, or exports/<timestamp>, or runs /genealogy-narrative.
---

# Genealogy narrative

The first reply is a handful of facts worth repeating out loud, from a local export made by this repository's `genealogy.py`. A follow-up stays on that same export.

## Find the export

Use the directory the user named. Otherwise take the newest `genealogy-*` directory or zip in `~/Downloads`, then the newest `exports/*/` directory in this repo. A zip and a directory with the same timestamp are the same run.

Read `research.json`. The good lines are in `biography_wikitext`, citation `wikitext`, and photo `Title`, `Date`, and `Location`. Use the CSVs only to confirm a field. Field meanings and the storyboard fallback are in [references/export.md](references/export.md). Ignore `images/` unless the user asks about a picture.

Do not call WikiTree, and do not re-run `genealogy.py`, unless the user asks for a new export.

## What may be said

State a date, place, name, or marriage only as the export records it. Paraphrase. Do not paste biography or will sentences.

- A date ending in `-00` has no day, and `-00-00` has no month. `guess`, `before`, and `after` mean "about", "by", and "after". Parent `status` is in the export reference: `10` uncertain, `5` non-biological, `30` WikiTree's "confirmed with DNA" mark.
- An empty `arrival_date` stays empty. A foreign birth and a U.S. death mean they were here by `us_presence_by`, not that they arrived that year.
- Skip DNA-kit lines and anyone who is not a deceased profile in this export.
- If the biography and the profile date disagree, keep both, in plain words.

## Voice

Write the way you'd tell a relative something you just found. Short sentences. Contractions. Be amused when the fact is funny. One raised eyebrow is enough.

Don't start a bullet with a source label. Mention the biography, the pension file, or the photo title only when the reader needs to know the fact is softer than a date on the profile.

A letter has to stand alone. Say who wrote it and what was going on. If you can't, leave it out.

## What to hunt

Mix these. Don't let the list become all wars, or all wills.

- Something you can picture. A will that itemizes a meadow, an inn, a boat, or a sum of money. Two wills fretting over the same soggy field are one bullet.
- A round trip. They die in the town or county where they were born, after a census, marriage, or biography puts them in another state or country. A wedding in the next parish does not count.
- A few superlatives, each one a person plus the detail that makes them the winner. Farthest birthplace from the seed, using the atlas place lookup and its precision. Longest span of recorded dates. Earliest birth. Skip a miles lecture. If the pin is only a country, name the country.
- A named historical event. A war, an alarm, a convention, a draft registration, a pension, a ship, a territory. Give the unit and the dates when the file has them. A descendant's SAR or DAR application is not the ancestor's service. A sticker with no unit is a weak hit. Prefer the company, the date, or the pension file.
- A coincidence, as a wink. A birth or death on the same day as a named public event, or near enough that the joke is obvious. Or a recorded town whose years overlap a famous person who was actually living there then. Look the event up if you aren't sure of the date. "Alive during the Civil War" is too wide. Say it's a coincidence. Don't imply they met, fought, or died of it unless the export says so. If the famous date is disputed, skip it. One or two of these is plenty.

Skip bare counts: shared years, surname tallies, citation totals, stacks of census titles.

Keep 8 to 12 bullets. Name the person, the WikiTree ID, and how they connect to the person the export starts from: father, mother, paternal grandfather, maternal 4th great-grandfather. Count Father/Mother steps on `ancestry_paths`. One step is a parent, two is a grandparent, and each step after that adds a great. Say which side the first step is on. A `Child` branch runs the other way. If a profile is only in the file as someone's spouse, say so.

When the age at death is part of the point, a short life, a long one, or the number that makes the story, give it. Use complete dates. If the birth or death is partial, say "about" or leave the age out. Don't hang an age on a bullet that doesn't need one.

## Reply

One easy opening line, so the reader knows whose tree this is. Every person after that is placed against that person. Then the bullets. After a bullet, add one or two sentences of public history only when the reader would otherwise have to go look up the war, the office, the ship, the vote, or the place. Say what that thing was. Don't add a second life story, and don't hang a new claim on the ancestor. Skip the note when the bullet is already clear, such as a will full of meadows. Then ask which person or story to open next.

On a follow-up, explain the thing they asked about, then say what this export actually shows for that person. Do not repeat the list unless the user asks. If the file does not say, say what is missing.

Write a file only if the user asks for one. Do not write it into the export directory. Do not describe what a photo depicts unless the user asked you to look at the image file.
