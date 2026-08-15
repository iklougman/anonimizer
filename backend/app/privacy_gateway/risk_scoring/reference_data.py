from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import rdflib
from openpyxl import load_workbook

from app.privacy_gateway.detectors.base import normalize

DATA_DIR = Path(__file__).resolve().parent / "data"
ORDO_FILENAME = "ORDO_de_4.9.owl"
KRANKENHAUSVERZEICHNIS_FILENAME = "krankenhausverzeichnis.xlsx"

# ORDO's German label set includes 4-5 character entries such as "Kuru", "Fall",
# "Pest", "Gliom" and gene symbols like "renin". Matching "Fall" would fire on
# "Fallnummer" in almost every clinical note and escalate it to HIGH risk, so short
# names are dropped. Matching is whole-word n-gram, never raw substring.
MIN_RARE_DISEASE_NAME_LENGTH = 6

_ORPHANET_CLASS_PREFIX = "http://www.orpha.net/ORDO/Orphanet_"
_KHV_SHEET_PREFIX = "KHV_"
_HOSPITAL_NAME_COLUMNS = ("KH_Name", "Standortname")


@lru_cache(maxsize=None)
def load_rare_disease_names(owl_path: str) -> frozenset[str]:
    """German rare-disease names from an ORDO OWL release.

    ORDO 4.9's German release carries the German name in `rdfs:label` with **no**
    `xml:lang` attribute, and has no `skos:prefLabel` and no `efo:alternative_term`
    at all — so there is nothing to filter on by language and nothing else to read.
    Only `Orphanet_*` class IRIs are considered, which excludes the imported EFO/OBO
    scaffolding classes that also carry German labels.
    """
    graph = rdflib.Graph()
    graph.parse(owl_path, format="xml")
    names: set[str] = set()
    for subject, label in graph.subject_objects(rdflib.RDFS.label):
        if not str(subject).startswith(_ORPHANET_CLASS_PREFIX):
            continue
        normalized = normalize(str(label))
        if len(normalized) < MIN_RARE_DISEASE_NAME_LENGTH:
            continue
        names.add(normalized)
    return frozenset(names)


@lru_cache(maxsize=None)
def load_hospital_names(xlsx_path: str) -> frozenset[str]:
    """Facility names from the Destatis Krankenhausverzeichnis workbook.

    The hospital sheet's title carries a leading space in the published file
    (`" KHV_2024"`) and the year changes annually, so it is located by stripped
    prefix rather than by exact name or index. The header row is likewise found by
    content (`KH_Name`) rather than by row number, because the sheet starts with
    banner rows.
    """
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        sheet = next(
            (ws for ws in workbook.worksheets if ws.title.strip().startswith(_KHV_SHEET_PREFIX)),
            None,
        )
        if sheet is None:
            raise ValueError(
                f"{xlsx_path} has no worksheet whose title starts with "
                f"{_KHV_SHEET_PREFIX!r}; the Destatis layout has changed"
            )

        name_columns: dict[str, int] | None = None
        names: set[str] = set()
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value).strip() for value in row]
            if name_columns is None:
                if _HOSPITAL_NAME_COLUMNS[0] in cells:
                    name_columns = {
                        column: cells.index(column)
                        for column in _HOSPITAL_NAME_COLUMNS
                        if column in cells
                    }
                continue
            for index in name_columns.values():
                if index >= len(cells) or not cells[index]:
                    continue
                normalized = normalize(cells[index])
                if normalized:
                    names.add(normalized)

        if name_columns is None:
            raise ValueError(
                f"{xlsx_path} has no {_HOSPITAL_NAME_COLUMNS[0]!r} header row; "
                "the Destatis layout has changed"
            )
        return frozenset(names)
    finally:
        workbook.close()


@lru_cache(maxsize=1)
def get_rare_disease_names() -> frozenset[str]:
    """Design spec §6: parsed once, held in memory, no per-request I/O."""
    return load_rare_disease_names(str(DATA_DIR / ORDO_FILENAME))


@lru_cache(maxsize=1)
def get_hospital_names() -> frozenset[str]:
    """Design spec §6: parsed once, held in memory, no per-request I/O."""
    return load_hospital_names(str(DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME))
