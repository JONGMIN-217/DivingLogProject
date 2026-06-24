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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_database_url(value: str) -> str:
    # Some platforms still provide the deprecated postgres:// scheme.
    if value.startswith("postgres://"):
        return f"postgresql+psycopg://{value.removeprefix('postgres://')}"
    if value.startswith("postgresql://"):
        return f"postgresql+psycopg://{value.removeprefix('postgresql://')}"
    return value


load_dotenv()


class Settings:
    environment: str = os.getenv("APP_ENV", "development").strip().lower()
    is_production: bool = environment == "production"
    secret_key: str = os.getenv("SECRET_KEY", "").strip()
    database_url: str = normalize_database_url(
        os.getenv(
            "DATABASE_URL",
            sqlite_url_for(BASE_DIR / "divinglog.db"),
        )
    )
    upload_dir: Path = resolve_path(os.getenv("UPLOAD_DIR"), "uploads")
    static_dir: Path = resolve_path(os.getenv("STATIC_DIR"), "static")
    templates_dir: Path = resolve_path(os.getenv("TEMPLATES_DIR"), "templates")
    max_import_file_size_mb: int = env_int("MAX_IMPORT_FILE_SIZE_MB", 200)
    max_image_upload_size_mb: int = env_int("MAX_IMAGE_UPLOAD_SIZE_MB", 5)
    max_backup_upload_size_mb: int = env_int("MAX_BACKUP_UPLOAD_SIZE_MB", 500)
    session_https_only: bool = env_bool("SESSION_HTTPS_ONLY", is_production)
    session_max_age_seconds: int = env_int("SESSION_MAX_AGE_SECONDS", 60 * 60 * 24 * 14)
    force_https: bool = env_bool("FORCE_HTTPS", False)
    allow_registration: bool = env_bool("ALLOW_REGISTRATION", not is_production)
    bootstrap_admin_username: str = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    bootstrap_admin_password: str = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    trusted_hosts: list[str] = env_list(
        "TRUSTED_HOSTS",
        "localhost,127.0.0.1" if not is_production else "",
    )
    run_startup_maintenance: bool = env_bool("RUN_STARTUP_MAINTENANCE", not is_production)

    def validate(self):
        if not self.secret_key:
            if self.is_production:
                raise RuntimeError("운영 환경에서는 SECRET_KEY를 반드시 설정해야 합니다.")
            self.secret_key = secrets.token_urlsafe(48)

        if self.is_production and len(self.secret_key) < 32:
            raise RuntimeError("운영 환경의 SECRET_KEY는 32자 이상이어야 합니다.")

        if self.is_production and not self.trusted_hosts:
            raise RuntimeError("운영 환경에서는 TRUSTED_HOSTS를 반드시 설정해야 합니다.")

        if bool(self.bootstrap_admin_username) != bool(self.bootstrap_admin_password):
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_USERNAME과 BOOTSTRAP_ADMIN_PASSWORD는 함께 설정해야 합니다."
            )

        if self.bootstrap_admin_password and len(self.bootstrap_admin_password) < 12:
            raise RuntimeError("초기 관리자 비밀번호는 12자 이상이어야 합니다.")


settings = Settings()
settings.validate()
