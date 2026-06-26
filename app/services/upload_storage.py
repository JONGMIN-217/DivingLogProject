import hashlib
from pathlib import Path
from typing import Iterable

from fastapi import UploadFile


IMAGE_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
}


def format_file_size(size_bytes: int):
    size_mb = size_bytes / (1024 * 1024)
    if size_mb.is_integer():
        return f"{int(size_mb)}MB"
    return f"{size_mb:.1f}MB"


def image_signature_matches(suffix: str, header: bytes):
    if suffix == ".webp":
        return len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    signatures = IMAGE_SIGNATURES.get(suffix)
    if not signatures:
        return True
    return header.startswith(signatures)


class LocalUploadStorage:
    def __init__(self, upload_root: Path):
        self.upload_root = upload_root.resolve()
        self.upload_root.mkdir(parents=True, exist_ok=True)

    def resolve_stored_path(self, image_path: str):
        relative_path = image_path.removeprefix("uploads/")
        candidate = (self.upload_root / relative_path).resolve()
        if candidate != self.upload_root and self.upload_root not in candidate.parents:
            return None
        return candidate

    def validate(
        self,
        upload_file: UploadFile,
        allowed_extensions: set[str],
        allowed_content_types: set[str],
        max_size: int,
    ):
        original_filename = Path(upload_file.filename or "").name
        suffix = Path(original_filename).suffix.lower()
        if suffix not in allowed_extensions:
            return None, "허용되지 않는 파일 형식입니다."

        if upload_file.content_type and upload_file.content_type not in allowed_content_types:
            return None, "허용되지 않는 파일 형식입니다."

        upload_size = getattr(upload_file, "size", None)
        if upload_size is not None and upload_size > max_size:
            return None, f"파일 크기는 {format_file_size(max_size)} 이하만 허용됩니다."

        if suffix in IMAGE_SIGNATURES or suffix == ".webp":
            position = upload_file.file.tell()
            header = upload_file.file.read(16)
            upload_file.file.seek(position)
            if not image_signature_matches(suffix, header):
                return None, "파일 확장자와 실제 이미지 형식이 일치하지 않습니다."

        return suffix, None

    def save(self, upload_file: UploadFile, destination: Path, max_size: int):
        destination.parent.mkdir(parents=True, exist_ok=True)
        bytes_written = 0
        with destination.open("wb") as buffer:
            for chunk in self._chunks(upload_file):
                bytes_written += len(chunk)
                if bytes_written > max_size:
                    destination.unlink(missing_ok=True)
                    return f"파일 크기는 {format_file_size(max_size)} 이하만 허용됩니다."
                buffer.write(chunk)
        return None

    def file_sha256(self, path: Path):
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _chunks(upload_file: UploadFile) -> Iterable[bytes]:
        while True:
            chunk = upload_file.file.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
