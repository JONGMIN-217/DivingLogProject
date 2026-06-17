from pathlib import Path
from xml.etree import ElementTree

from .base import ImportParser, ImportPreview


class XmlImportParser(ImportParser):
    parser_name = "XML"
    supported_extensions = (".xml",)

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        messages: list[str] = []
        tree = ElementTree.parse(path)
        root = tree.getroot()

        candidates = [
            element for element in root.iter()
            if len(list(element)) > 0 and _local_name(element.tag).lower() in {"dive", "log", "sample"}
        ]

        if not candidates:
            candidates = [
                element for element in root
                if len(list(element)) > 0
            ]

        rows: list[dict[str, str]] = []
        columns: list[str] = []

        for element in candidates:
            row = _flatten_element(element)
            if not row:
                continue

            rows.append(row)
            for column in row:
                if column not in columns:
                    columns.append(column)

        if not rows:
            messages.append("미리보기로 표시할 다이빙 항목을 찾지 못했습니다.")

        return ImportPreview(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            columns=columns,
            rows=rows,
            messages=messages,
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flatten_element(element) -> dict[str, str]:
    row: dict[str, str] = {}

    for key, value in element.attrib.items():
        row[_local_name(key)] = value.strip()

    for child in list(element):
        name = _local_name(child.tag)
        text = (child.text or "").strip()

        if text:
            row[name] = text

        for key, value in child.attrib.items():
            row[f"{name}.{_local_name(key)}"] = value.strip()

    return row
