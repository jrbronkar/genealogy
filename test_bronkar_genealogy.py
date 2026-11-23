import csv
from html.parser import HTMLParser
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bronkar_genealogy import WikiTree, Unavailable, citations, collect, export, migration_lead
from storyboard import timeline, thumbnail_path


class MemoryClient:
    def __init__(self, people):
        self.people = people
        self.calls = []

    def fetch(self, action, key, **params):
        self.calls.append((action, key, params))
        origin = {"action": action, "retrieved_at": "2026-01-01T00:00:00Z"}
        if action == "getPerson":
            return {"person": self.people[key]}, origin
        start = params["start"]
        return {"start": start, "photos": [
            {"PageId": str(i), "URL_300": "/photo.php/thumb/a/a1/file.jpg/300px-file.jpg",
             "Title": "Test Census", "Location": "Ohio", "Date": "1850-00-00"}
            for i in range(start, min(11, start + 10))
        ]}, origin


def person(key, ident, **updates):
    return {
        "Name": key, "Id": ident, "Privacy": 60, "IsLiving": 0,
        "BirthDate": "1783-00-00", "BirthLocation": "France", "DeathDate": "1867-03-00",
        "DeathLocation": "Zanesville, Ohio", "DataStatus": {"Father": "uncertain"},
        **updates,
    }


class ResearchTests(unittest.TestCase):
    def dataset(self, research=False):
        client = MemoryClient({
            "Example-1": person("Example-1", 1, Father=2, Mother=3,
                                bio="Public text mentions a living relative.<ref>Citation</ref>"),
            "2": person("Bronkar-39", 2, Father=1, bio="Born in France. Ohio Militia 26 Aug 1812."),
            "3": person("Private-3", 3, Privacy=40, IsLiving=1),
        })
        return collect(client, ("Example-1",), 3, research), client

    def test_collect_provenance_paths_privacy_and_pagination(self):
        data, client = self.dataset()
        self.assertEqual([p["Name"] for p in data["profiles"]], ["Example-1", "Bronkar-39"])
        self.assertEqual(data["relationships"][0]["status"], "uncertain")
        self.assertIn("cycle", [gap["reason"] for gap in data["gaps"]])
        self.assertEqual(len(data["photos"]), 22)
        self.assertTrue(all(call[1] != "Private-3" for call in client.calls))
        self.assertNotIn("living relative", json.dumps(data))
        self.assertNotIn("Private-3", json.dumps(data))
        self.assertEqual(data["immigration_leads"][0]["us_presence_by"], "1812-08-26")
        self.assertIsNone(data["immigration_leads"][0]["arrival_date"])
        self.assertEqual(data["branch_summary"][0]["earliest_dated_presence_leads"], ["Bronkar-39"])

    def test_local_export_and_storyboard(self):
        data, _ = self.dataset(research=True)
        with TemporaryDirectory() as directory:
            run = export(data, Path(directory))
            self.assertEqual(json.loads((run / "research.json").read_text()), data)
            with (run / "citations.csv").open(newline="") as handle:
                self.assertEqual(next(csv.DictReader(handle))["wikitext"], "Citation")
            with patch("storyboard.download_photo", return_value="images/test.jpg") as download:
                self.assertEqual(timeline(data, run, download=True), (1, 0))
                download.assert_called_once()
            page = (run / "storyboard.html").read_text()
            HTMLParser().feed(page)
            embedded = page.split('id="events">')[1].split("</script>")[0]
            events = json.loads(embedded)
            self.assertTrue(any(event["activity"] == "Test Census" for event in events))
            self.assertTrue(any(event["coords"] and event["date"] == "1812-08-26" for event in events))

    def test_citations_and_thumbnail_path(self):
        bio = '== Biography ==\n<ref name="a" /><ref name="a">Source A</ref>\n== Sources ==\n<references />\n* Source B\n== Notes ==\n* Note'
        self.assertEqual(list(citations(bio)), [("inline_ref", "Source A"), ("sources_section", "Source B")])
        self.assertIsNone(thumbnail_path({"metadata": {"URL_300": "//other.example/test.jpg"}}))

    def test_sheffield_reported_date_is_kept_separate(self):
        lead = migration_lead(
            person("Sheffield-522", 4, BirthLocation="England", provenance={}),
            "The Ontario arrived June 21st 1841.",
        )
        self.assertEqual(lead["arrival_date"], "1841-06-21")
        self.assertEqual(lead["us_presence_by"], "1841-06-22")

    def test_wikitree_application_status(self):
        with patch("bronkar_genealogy.urlopen") as request:
            response = request.return_value.__enter__.return_value
            response.read.return_value = b'[{"status":"Permission denied"}]'
            with self.assertRaises(Unavailable):
                WikiTree(delay=0).fetch("getPerson", "Example-1")
            response.read.return_value = b'[{"status":"0","person":{}}]'
            self.assertEqual(WikiTree(delay=0).fetch("getPerson", "Example-1")[0]["person"], {})


if __name__ == "__main__":
    unittest.main()
