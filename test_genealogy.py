import csv
from html.parser import HTMLParser
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genealogy import WikiTree, Unavailable, citations, collect, export, migration_lead, search_people, select_profile
from storyboard import original_path, timeline, thumbnail_path


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
            "Example-1": person("Example-1", 1, Father=2, Mother=3, BirthLocation="Ohio",
                                bio="Public text mentions a living relative.<ref>Citation</ref>"),
            "2": person("Ancestor-2", 2, Father=1, bio="Emigrated from France."),
            "3": person("Private-3", 3, Privacy=40, IsLiving=1),
        })
        return collect(client, ("Example-1",), 3, research), client

    def test_collect_provenance_paths_privacy_and_pagination(self):
        data, client = self.dataset()
        self.assertEqual([p["Name"] for p in data["profiles"]], ["Example-1", "Ancestor-2"])
        self.assertEqual(data["relationships"][0]["status"], "uncertain")
        self.assertIn("cycle", [gap["reason"] for gap in data["gaps"]])
        self.assertEqual(len(data["photos"]), 22)
        self.assertTrue(all(call[1] != "Private-3" for call in client.calls))
        self.assertNotIn("living relative", json.dumps(data))
        self.assertNotIn("Private-3", json.dumps(data))
        self.assertEqual(data["immigration_leads"][0]["us_presence_by"], "1867-03-00")
        self.assertIsNone(data["immigration_leads"][0]["arrival_date"])
        self.assertEqual(data["branch_summary"][0]["earliest_dated_presence_leads"], ["Ancestor-2"])

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
            self.assertTrue(any(event["coords"] and event["date"] == "1867-03-00" for event in events))

    def test_original_downloads_are_recursive_and_deduplicated(self):
        data, _ = self.dataset(research=True)
        with TemporaryDirectory() as directory:
            run = Path(directory)
            with patch("storyboard.download_photo", return_value="images/test.jpg") as download:
                self.assertEqual(timeline(data, run, originals=True), (1, 0))
            self.assertEqual(download.call_args.args[0], "/photo.php/a/a1/file.jpg")
            log = json.loads((run / "photo_downloads.json").read_text())
            self.assertEqual({entry["profile"] for entry in log}, {"Example-1", "Ancestor-2"})
            self.assertTrue(all(entry["file"] == "images/test.jpg" for entry in log))
        self.assertIsNone(original_path({"metadata": {"path": "https://other.example/file.jpg"}}))

    def test_citations_and_thumbnail_path(self):
        bio = '== Biography ==\n<ref name="a" /><ref name="a">Source A</ref>\n== Sources ==\n<references />\n* Source B\n== Notes ==\n* Note'
        self.assertEqual(list(citations(bio)), [("inline_ref", "Source A"), ("sources_section", "Source B")])
        self.assertIsNone(thumbnail_path({"metadata": {"URL_300": "//other.example/test.jpg"}}))

    def test_name_search_filters_private_and_living_results_and_pages(self):
        client = unittest.mock.Mock()
        client.fetch.side_effect = [
            ({"total": 60, "matches": [
                person("Public-1", 1), person("Private-2", 2, Privacy=40),
                person("Living-3", 3, IsLiving=1),
            ]}, {}),
            ({"total": 60, "matches": [person("Public-4", 4)]}, {}),
        ]
        matches, truncated = search_people(client, "de la Cruz, Maria Elena", "France")
        self.assertFalse(truncated)
        self.assertEqual([p["Name"] for p in matches], ["Public-1", "Public-4"])
        first, second = client.fetch.call_args_list
        self.assertEqual(first.kwargs["LastName"], "de la Cruz")
        self.assertEqual(first.kwargs["FirstName"], "Maria")
        self.assertEqual(first.kwargs["BirthLocation"], "France")
        self.assertEqual(second.kwargs["start"], 50)
        with patch("sys.stdin", StringIO("9\n2\n")), patch("sys.stderr", StringIO()) as menu:
            self.assertEqual(select_profile(matches), "Public-4")
            self.assertIn("Enter a number from the list", menu.getvalue())

    def test_wikitree_application_status(self):
        with patch("genealogy.urlopen") as request:
            response = request.return_value.__enter__.return_value
            response.read.return_value = b'[{"status":"Permission denied"}]'
            with self.assertRaises(Unavailable):
                WikiTree(delay=0).fetch("getPerson", "Example-1")
            response.read.return_value = b'[{"status":"0","person":{}}]'
            self.assertEqual(WikiTree(delay=0).fetch("getPerson", "Example-1")[0]["person"], {})


if __name__ == "__main__":
    unittest.main()
