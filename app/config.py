import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def load_dotenv(path: Path = ENV_FILE):
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        os.environ.setdefault(key, value)


def resolve_path(value: str | None, default: str) -> Path:
    path = Path(value or default)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def sqlite_url_for(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default

    return value if value > 0 else default


load_dotenv()


class Settings:
    secret_key: str = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
    database_url: str = os.getenv(
        "DATABASE_URL",
        sqlite_url_for(BASE_DIR / "divinglog.db"),
    )
    upload_dir: Path = resolve_path(os.getenv("UPLOAD_DIR"), "uploads")
    static_dir: Path = resolve_path(os.getenv("STATIC_DIR"), "static")
    templates_dir: Path = resolve_path(os.getenv("TEMPLATES_DIR"), "templates")
    max_import_file_size_mb: int = env_int("MAX_IMPORT_FILE_SIZE_MB", 200)


settings = Settings()
