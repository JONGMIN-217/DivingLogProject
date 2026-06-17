from pathlib import Path
from xml.etree import ElementTree

from .base import ImportDive, ImportParser, ImportPreview, preview_from_dives
from .utils import parse_date_value, parse_float_value, parse_int_value


class SubsurfaceXmlImportParser(ImportParser):
    parser_name = "Subsurface XML"
    supported_extensions = (".xml",)

    def can_parse(self, path: Path) -> bool:
        if not super().can_parse(path):
            return False

        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            return False

        names = {_local_name(element.tag).lower() for element in root.iter()}
        return "divesites" in names or "divesite" in names or root.attrib.get("program") == "subsurface"

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        root = ElementTree.parse(path).getroot()
        dive_sites = _dive_sites(root)
        dives: list[ImportDive] = []

        for element in root.iter():
            if _local_name(element.tag).lower() != "dive":
                continue

            site_id = element.attrib.get("divesiteid") or element.attrib.get("site")
            site = dive_sites.get(site_id or "", {})
            latitude, longitude = _parse_gps(site.get("gps", ""))
            dive_date = parse_date_value(element.attrib.get("date") or element.attrib.get("datetime"))
            duration = element.attrib.get("duration") or element.attrib.get("divetime")

            dives.append(
                ImportDive(
                    dive_date=dive_date,
                    max_depth=parse_float_value(element.attrib.get("maxdepth")),
                    avg_depth=parse_float_value(element.attrib.get("meandepth") or element.attrib.get("avgdepth")),
                    dive_time=parse_int_value(duration),
                    water_temp=parse_float_value(element.attrib.get("watertemp") or element.attrib.get("temperature")),
                    note=site.get("name"),
                    latitude=latitude,
                    longitude=longitude,
                    source_label=site.get("name"),
                    raw={key: value for key, value in element.attrib.items()},
                )
            )

        return preview_from_dives(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            dives=dives,
        )


def _dive_sites(root) -> dict[str, dict[str, str]]:
    sites: dict[str, dict[str, str]] = {}
    for element in root.iter():
        if _local_name(element.tag).lower() not in {"site", "divesite"}:
            continue

        site_id = element.attrib.get("uuid") or element.attrib.get("id")
        if site_id:
            sites[site_id] = {
                "name": element.attrib.get("name", ""),
                "gps": element.attrib.get("gps", ""),
            }
    return sites


def _parse_gps(value: str) -> tuple[float | None, float | None]:
    parts = value.replace(",", " ").split()
    if len(parts) < 2:
        return None, None

    return parse_float_value(parts[0]), parse_float_value(parts[1])


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
