"""Download the reference datasets the risk scorer needs — at build time only.

ADR-0001 and design spec §6: `app/privacy_gateway/` must never be network-capable.
This script lives outside that package (and outside the `app` root package the
import-linter contracts cover) and runs during `docker build` and CI setup, never
at request time. The files it writes are gitignored: large, stale-prone, and
re-fetchable.

Sources:
  * Orphanet Rare Disease Ontology, German release 4.9 (~51 MB, CC BY 4.0).
  * Verzeichnis der Krankenhäuser und Vorsorge- oder Rehabilitationseinrichtungen
    in Deutschland, Statistisches Bundesamt (~2.4 MB, free use with attribution).
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

DATA_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "privacy_gateway"
    / "risk_scoring"
    / "data"
)

ORDO_URL = "https://www.orphadata.com/data/ontologies/ordo/last_version/ORDO_de_4.9.owl"
ORDO_FILENAME = "ORDO_de_4.9.owl"
ORDO_MIN_BYTES = 40_000_000

KRANKENHAUSVERZEICHNIS_URL = (
    "https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Gesundheit/Krankenhauser/"
    "Publikationen/Downloads-Krankenhaeuser/krankenhausverzeichnis-3500100247005.xlsx"
    "?__blob=publicationFile&v=6"
)
KRANKENHAUSVERZEICHNIS_FILENAME = "krankenhausverzeichnis.xlsx"
KRANKENHAUSVERZEICHNIS_MIN_BYTES = 1_000_000

USER_AGENT = "chatgpt-proxy-reference-data-fetch/1.0"
CHUNK_BYTES = 1 << 20


def download(url: str, target: Path, min_bytes: int) -> None:
    if target.exists() and target.stat().st_size >= min_bytes:
        print(f"{target.name}: already present ({target.stat().st_size} bytes), skipping")
        return

    print(f"{target.name}: downloading from {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = target.parent / (target.name + ".part")
    with urllib.request.urlopen(request, timeout=300) as response:
        with partial.open("wb") as handle:
            while chunk := response.read(CHUNK_BYTES):
                handle.write(chunk)

    size = partial.stat().st_size
    if size < min_bytes:
        # A truncated download, an HTML error page, or a moved release must fail the
        # build loudly rather than leave a file the startup parser silently accepts.
        partial.unlink()
        raise SystemExit(
            f"{target.name}: downloaded only {size} bytes, expected at least "
            f"{min_bytes}; the upstream URL has probably moved — check {url}"
        )
    partial.replace(target)
    print(f"{target.name}: wrote {size} bytes")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    download(ORDO_URL, DATA_DIR / ORDO_FILENAME, ORDO_MIN_BYTES)
    download(
        KRANKENHAUSVERZEICHNIS_URL,
        DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME,
        KRANKENHAUSVERZEICHNIS_MIN_BYTES,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
