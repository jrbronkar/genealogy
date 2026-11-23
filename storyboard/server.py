"""Loopback-only browser interface for WikiTree searches."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
from shutil import copyfileobj
from tempfile import SpooledTemporaryFile
from threading import Lock, Thread
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from genealogy import (
    APIError, CSV_FIELDS, WikiTree, add_marriage_records, collect, export, include_starting_children,
    public_deceased, search_people,
)
from storyboard import timeline

RUN_ID = r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z"
MEDIA = r"images/[0-9a-f]{20}\.(?:jpg|png|gif|webp|tif|bmp)"


def write_export_archive(run, output):
    root = run.resolve()
    tables = ("research.json", "storyboard.html", "photo_downloads.json", *(f"{name}.csv" for name in CSV_FIELDS))
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in tables:
            file = root / name
            if file.is_file() and not file.is_symlink():
                archive.write(file, name)
        images = root / "images"
        if images.is_dir() and not images.is_symlink():
            for file in sorted(images.iterdir()):
                name = f"images/{file.name}"
                if re.fullmatch(MEDIA, name) and file.is_file() and not file.is_symlink():
                    archive.write(file, name, compress_type=ZIP_STORED)


class AtlasServer(ThreadingHTTPServer):
    def __init__(self, initial, port=0):
        self.initial = initial.resolve()
        self.root = self.initial.parent.parent
        info = json.loads((self.initial.parent / "research.json").read_text(encoding="utf-8"))
        self.depth = min(int(info.get("depth", 5)), 8)
        self.lock = Lock()
        self.matches = {}
        self.job = {"state": "idle"}
        super().__init__(("127.0.0.1", port), AtlasHandler)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"

    def build(self, key, name):
        def progress(message):
            with self.lock:
                self.job = {"state": "running", "message": message}

        try:
            client = WikiTree(progress=progress)
            biographies = {}
            data = collect(client, (key,), self.depth, progress=progress, atlas_biographies=biographies)
            if not data["profiles"]:
                raise APIError("WikiTree no longer returns an eligible profile for that match.")
            include_starting_children(client, data, progress=progress, atlas_biographies=biographies)
            add_marriage_records(client, data, progress)
            data["query"] = {"name": name, "birth_place": None}
            progress("[Export] Saving the new family tree.")
            run = export(data, self.root)
            timeline(data, run, progress=progress, biographies=biographies)
            final = {"state": "ready", "url": f"/runs/{run.name}/storyboard.html"}
        except (APIError, OSError, ValueError) as error:
            final = {"state": "failed", "message": str(error)}
        with self.lock:
            self.job = final


class AtlasHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_content(self, content, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data, status=200):
        self.send_content(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def valid_host(self):
        return self.headers.get("Host") == self.server.origin[7:]

    def do_GET(self):
        if not self.valid_host():
            return self.send_error(403)
        path = urlsplit(self.path).path
        if path == "/api/progress":
            with self.server.lock:
                result = dict(self.server.job)
            return self.send_json(result)
        if path == "/":
            return self.send_file(self.server.initial)
        if path == "/download":
            return self.send_archive(self.server.initial.parent)
        download = re.fullmatch(rf"/runs/(?P<run>{RUN_ID})/download", path)
        if download:
            return self.send_archive(self.server.root / download["run"])
        if re.fullmatch("/" + MEDIA, path):
            return self.send_file(self.server.initial.parent / path[1:])
        match = re.fullmatch(rf"/runs/(?P<run>{RUN_ID})/(?P<asset>storyboard\.html|{MEDIA})", path)
        if match:
            return self.send_file(self.server.root / match["run"] / match["asset"])
        self.send_error(404)

    def send_file(self, file):
        if not file.is_file() or self.server.root not in file.resolve().parents:
            return self.send_error(404)
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        self.send_content(file.read_bytes(), content_type, 200)

    def send_archive(self, run):
        if run.resolve().parent != self.server.root or run.is_symlink() or not (run / "research.json").is_file():
            return self.send_error(404)
        with SpooledTemporaryFile(max_size=16 * 1024 * 1024) as output:
            try:
                write_export_archive(run, output)
            except OSError:
                return self.send_error(500, "The export could not be archived.")
            size = output.tell()
            output.seek(0)
            slug = run.name if re.fullmatch(RUN_ID, run.name) else "export"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="genealogy-{slug}.zip"')
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                copyfileobj(output, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != self.server.origin:
            return self.send_error(403)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "Send JSON."}, 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                return self.send_error(413)
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                return self.send_json({"error": "Invalid request."}, 400)
            if self.path == "/api/search":
                return self.search(body)
            if self.path == "/api/build":
                return self.start_build(body)
        except (ValueError, UnicodeError, APIError) as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_error(404)

    def search(self, body):
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            return self.send_json({"error": "Enter a person’s name."}, 400)
        name = name.strip()
        matches, truncated = search_people(WikiTree(), name)
        result = []
        for person in matches:
            if not public_deceased(person):
                continue
            key = person["Name"]
            display = " ".join(str(person[field]) for field in (
                "FirstName", "MiddleName", "LastNameAtBirth", "Suffix",
            ) if person.get(field)) or key
            result.append({"key": key, "name": display, "birth": person.get("BirthDate"),
                           "death": person["DeathDate"], "place": person.get("BirthLocation")})
        with self.server.lock:
            self.server.matches = {match["key"]: name for match in result}
        self.send_json({"matches": result, "truncated": truncated})

    def start_build(self, body):
        key = body.get("key")
        with self.server.lock:
            name = self.server.matches.get(key) if isinstance(key, str) else None
            if not name:
                return self.send_json({"error": "Choose a match from the search results."}, 400)
            if self.server.job["state"] == "running":
                return self.send_json({"error": "A tree is already being built."}, 409)
            self.server.job = {"state": "running", "message": "Starting the ancestry search…"}
        Thread(target=self.server.build, args=(key, name), daemon=True).start()
        self.send_json({"state": "running"}, 202)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with AtlasServer(args.page, args.port) as server:
        print(server.origin + "/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
