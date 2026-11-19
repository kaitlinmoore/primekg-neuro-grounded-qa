"""Download PrimeKG and OptimusKG from Harvard Dataverse into NGQ_DATA_DIR.

What it does, per graph:

1. Fetches the Dataverse dataset metadata (``/api/datasets/:persistentId``) and
   saves it verbatim, so the dataset version and the license the dataset itself
   states are on record.
2. Streams each file listed in ``FILES`` from ``/api/access/datafile/<id>``,
   computing sha256 while writing, and checks the md5 that Dataverse publishes.
3. Writes ``meta/manifest.json`` with URL, Dataverse file ID, dataset version,
   size, sha256, md5 check and access date for every file.

Why not the ``optimuskg`` client: it requires Python >= 3.12 and this project
pins 3.11. The client downloads the same Dataverse files by ID, so fetching them
directly gives the same bytes; the md5 check confirms it.

Run (from WSL, repo root)::

    NGQ_DATA_DIR=$HOME/data/ngq uv run --group data python scripts/data/download.py

Re-running skips files whose md5 already matches.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import meta_dir, raw_dir  # noqa: E402

DATAVERSE = "https://dataverse.harvard.edu"

DATASETS = {
    "primekg": "10.7910/DVN/IXA7BM",
    "optimuskg": "10.7910/DVN/IYNGEV",
}

# Files we need, by graph. kg.csv carries node names, types and sources inline,
# so PrimeKG needs no separate node table for the measurement. For OptimusKG the
# unified nodes/edges tables are enough (properties hold names and sources).
FILES = {
    "primekg": ["kg.csv", "kg_grouped_diseases.tab", "README.txt"],
    "optimuskg": ["nodes.parquet", "edges.parquet"],
}

CHUNK = 1 << 20

# Dataverse answers 403 to urllib's default "Python-urllib" User-Agent, so send
# a generic client name (no personal or project details).
HEADERS = {"User-Agent": "dataset-download-client/0.1"}


def _open(url: str, timeout: int):
    """Open a URL with the generic User-Agent."""
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def fetch_json(url: str) -> dict:
    """GET a URL and parse it as JSON."""
    with _open(url, timeout=60) as resp:
        return json.load(resp)


def file_hashes(path: Path) -> tuple[str, str]:
    """Return (sha256, md5) hex digests of a file on disk."""
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def download(url: str, dest: Path) -> None:
    """Stream a URL to ``dest`` through a ``.part`` file, so a failed download never looks complete."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _open(url, timeout=120) as resp, tmp.open("wb") as out:
        while chunk := resp.read(CHUNK):
            out.write(chunk)
    tmp.replace(dest)


def main() -> int:
    meta_dir().mkdir(parents=True, exist_ok=True)
    manifest: dict = {"accessed": dt.date.today().isoformat(), "datasets": {}}
    ok = True
    for graph, doi in DATASETS.items():
        meta_url = f"{DATAVERSE}/api/datasets/:persistentId/?persistentId=doi:{doi}"
        meta = fetch_json(meta_url)
        (meta_dir() / f"dataverse_{graph}.json").write_text(json.dumps(meta, indent=1))
        version = meta["data"]["latestVersion"]
        ds = {
            "doi": doi,
            "metadata_url": meta_url,
            "dataset_version": f"{version['versionNumber']}.{version['versionMinorNumber']}",
            "release_time": version.get("releaseTime"),
            "license": version.get("license"),
            "terms_of_use": version.get("termsOfUse"),
            "terms_of_access": version.get("termsOfAccess"),
            "files": [],
        }
        by_name = {f["dataFile"]["filename"]: f for f in version["files"]}
        raw_dir(graph).mkdir(parents=True, exist_ok=True)
        for name in FILES[graph]:
            entry = by_name[name]
            df = entry["dataFile"]
            url = f"{DATAVERSE}/api/access/datafile/{df['id']}"
            dest = raw_dir(graph) / name
            if df.get("tabularData") and df.get("originalFileName"):
                # Dataverse re-ingests tabular uploads as .tab; its published md5
                # is of the original upload, so fetch that instead.
                url += "?format=original"
                dest = raw_dir(graph) / df["originalFileName"]
            name = dest.name
            expected_md5 = df.get("md5") or (df.get("checksum") or {}).get("value")
            if dest.exists() and file_hashes(dest)[1] == expected_md5:
                print(f"[{graph}] {name}: already present, md5 matches")
            else:
                print(f"[{graph}] {name}: downloading {df.get('filesize')} bytes from {url}")
                download(url, dest)
            sha, md5 = file_hashes(dest)
            match = md5 == expected_md5
            ok &= match
            ds["files"].append(
                {
                    "name": name,
                    "url": url,
                    "dataverse_file_id": df["id"],
                    "size_bytes": dest.stat().st_size,
                    "dataverse_size_bytes": df.get("filesize"),
                    "sha256": sha,
                    "md5": md5,
                    "dataverse_md5": expected_md5,
                    "md5_matches": match,
                    "restricted": entry.get("restricted"),
                }
            )
            print(f"[{graph}] {name}: sha256 {sha} md5_matches={match}")
        manifest["datasets"][graph] = ds
    (meta_dir() / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {meta_dir() / 'manifest.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
