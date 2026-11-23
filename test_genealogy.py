import csv
from html.parser import HTMLParser
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from genealogy import WikiTree, Unavailable, citations, collect, export, main, migration_lead, search_people, select_profile
from storyboard.places import lookup_place
from storyboard.server import write_export_archive
from storyboard import original_path, timeline, thumbnail_path


class MemoryClient:
    def __init__(self, people):
        self.people = people
        self.calls = []

    def fetch(self, action, key=None, **params):
        self.calls.append((action, key, params))
        origin = {"action": action, "retrieved_at": "2026-01-01T00:00:00Z"}
        if action == "getPerson":
            return {"person": self.people[key]}, origin
        if action == "getRelatives":
            return {"items": []}, origin
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
            self.assertIn("WikiTree contributors", page)
            self.assertIn("https://www.naturalearthdata.com/about/terms-of-use/", page)
            self.assertIn("U.S. Census Bureau 2025 Gazetteer", page)
            self.assertIn('id="people-toggle"', page)

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
        direct = "/photo.php/a/a1/small.jpg"
        row = {"metadata": {"URL_300": direct}}
        self.assertEqual(original_path(row), direct)
        self.assertEqual(thumbnail_path(row), direct)
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

    def test_cli_prints_progress_and_opens_storyboard_unless_disabled(self):
        client = MemoryClient({"Example-1": person("Example-1", 1, bio="Biographical evidence.")})
        with TemporaryDirectory() as directory, patch("genealogy.WikiTree", return_value=client):
            args = ["--seed", "Example-1", "--depth", "0", "--storyboard", "--out", directory]
            with patch("sys.stderr", StringIO()) as stderr, patch("sys.stdout", StringIO()) as stdout:
                with patch("genealogy.open_storyboard", return_value=True) as launch:
                    self.assertEqual(main(args), 0)
                    page = Path(stdout.getvalue().strip()) / "storyboard.html"
                    launch.assert_called_once_with(page.resolve())
                    self.assertTrue(page.is_file())
                    self.assertIn("Biographical evidence.", page.read_text())
                    self.assertNotIn("biography_wikitext", (page.parent / "research.json").read_text())
                    self.assertIn("[Ancestry] Found", stderr.getvalue())
                    self.assertIn("[Photos] Fetching attachment metadata", stderr.getvalue())
                    self.assertIn("[Storyboard] Opening", stderr.getvalue())
                    launch.reset_mock()
                    self.assertEqual(main([*args, "--no-open"]), 0)
                    launch.assert_not_called()

    def test_name_search_preserves_the_original_query(self):
        client = MemoryClient({"Example-1": person("Example-1", 1)})
        with TemporaryDirectory() as directory, patch("genealogy.WikiTree", return_value=client):
            with patch("genealogy.search_people", return_value=([client.people["Example-1"]], False)):
                with patch("sys.stdin", StringIO("1\n")), patch("sys.stderr", StringIO()), patch("sys.stdout", StringIO()) as stdout:
                    args = ["--name", "Example, Ada Jane", "--birth-place", "Ohio", "--depth", "0", "--out", directory, "--no-open"]
                    self.assertEqual(main(args), 0)
                    run = Path(stdout.getvalue().strip())
            expected = {"name": "Example, Ada Jane", "birth_place": "Ohio"}
            self.assertEqual(json.loads((run / "research.json").read_text())["query"], expected)
            raw_tree = (run / "storyboard.html").read_text().split('id="tree">')[1].split("</script>")[0]
            self.assertEqual(json.loads(raw_tree)["query"], expected)

    def test_place_lookup_prefers_a_qualified_city_and_labels_fallbacks(self):
        pasadena, precision = lookup_place("Pasadena, Los Angeles, California, United States")
        self.assertEqual(precision, "city")
        self.assertAlmostEqual(pasadena[0], -118.14, places=2)
        self.assertEqual(lookup_place("Oklahoma, United States")[1], "state")
        self.assertEqual(lookup_place("Paris, France")[1], "city")
        self.assertGreater(lookup_place("Paris, France")[0][0], 0)
        self.assertLess(lookup_place("Paris, Texas")[0][0], 0)
        self.assertEqual(lookup_place("France")[1], "country")
        self.assertEqual(lookup_place("Unknown place"), (None, None))
        self.assertEqual(lookup_place("Concord, Chester, Province of Pennsylvania")[1], "state")
        self.assertEqual(
            lookup_place("Concord Township, Chester, Province of Pennsylvania, British Colonial America"),
            lookup_place("Concord Township, Chester, Pennsylvania"),
        )

    def test_township_lookup_requires_the_correct_county(self):
        chester, precision = lookup_place("East Fallowfield Township, Chester, Pennsylvania, United States")
        self.assertEqual(precision, "township")
        self.assertAlmostEqual(chester[0], -75.8133, places=3)
        wayne, precision = lookup_place("Wayne Township, Muskingum, Ohio, United States")
        self.assertEqual(precision, "township")
        self.assertAlmostEqual(wayne[0], -81.9409, places=3)
        self.assertEqual(lookup_place("Wayne Township, Ohio")[1], "state")
        self.assertEqual(lookup_place("Wayne Township, Franklin, Ohio")[1], "state")

    def test_export_archive_contains_only_current_run_artifacts(self):
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            run = parent / "export"
            run.mkdir()
            (run / "research.json").write_text("{}")
            (run / "storyboard.html").write_text("<html></html>")
            (run / "profiles.csv").write_text("Name\n")
            (run / "photo_downloads.json").write_text("[]")
            (run / "scratch.txt").write_text("Excluded")
            (parent / "outside.jpg").write_bytes(b"Excluded")
            images = run / "images"
            images.mkdir()
            name = "a" * 20 + ".jpg"
            (images / name).write_bytes(b"Cached image")
            (images / ("b" * 20 + ".jpg")).symlink_to(parent / "outside.jpg")
            output = BytesIO()
            write_export_archive(run, output)
            with ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "research.json", "storyboard.html", "profiles.csv", "photo_downloads.json", "images/" + name,
                })
                self.assertEqual(archive.read("images/" + name), b"Cached image")
                self.assertIsNone(archive.testzip())

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
