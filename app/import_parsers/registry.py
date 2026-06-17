from pathlib import Path

from .base import ImportPreview, UnsupportedImportFormat
from .csv_parser import CsvImportParser
from .db_parser import DiveComputerDbImportParser, ShearwaterDbImportParser
from .fit_parser import GarminFitImportParser
from .subsurface_xml_parser import SubsurfaceXmlImportParser
from .uddf_parser import UddfImportParser
from .xml_parser import XmlImportParser


PARSERS = [
    CsvImportParser(),
    SubsurfaceXmlImportParser(),
    UddfImportParser(),
    GarminFitImportParser(),
    ShearwaterDbImportParser(),
    DiveComputerDbImportParser(),
    XmlImportParser(),
]


def parse_import_file(path: Path, original_filename: str) -> ImportPreview:
    for parser in PARSERS:
        if parser.can_parse(path):
            return parser.parse(path, original_filename)

    supported = ", ".join(
        extension
        for parser in PARSERS
        for extension in parser.supported_extensions
    )
    raise UnsupportedImportFormat(f"지원하지 않는 파일 형식입니다. 지원 형식: {supported}")
