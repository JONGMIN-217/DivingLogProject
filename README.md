# Diving Log Project

다이빙 로그, 포인트, 프로파일 그래프, Import/Export, 친구/투어/공유 사진첩, 관리자 운영 기능을 제공하는 FastAPI 기반 웹 애플리케이션입니다.

## 주요 기능

- UDDF, Shearwater DB 등 다이빙 로그 Import
- Import Preview, 중복 감지, 저장 실패 진단
- 전체 로그 split view, 선택 삭제, 유령 로그 정리, Export
- 수심 프로파일 샘플 저장 및 그래프 표시
- 다이브 포인트, GPS 기반 후보/병합/품질 관리
- Open-Meteo Marine, Open-Meteo Weather, KHOA API 연동
- 국가별 통계, 운영 대시보드
- 사용자 설정, 백업/복구, 공유 사진첩

## 로컬 개발

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn main:app --reload
```

기본 주소는 `http://127.0.0.1:8000`입니다.

## 환경변수

운영 환경에서는 `.env` 파일을 배포하지 말고 플랫폼의 비밀 환경변수 기능을 사용하세요.

- `APP_ENV`: `development` 또는 `production`
- `SECRET_KEY`: 운영 필수, 32자 이상의 고정 난수
- `DATABASE_URL`: SQLite 또는 PostgreSQL URL
- `UPLOAD_DIR`: 업로드 파일 저장 경로
- `UPLOAD_STORAGE_BACKEND`: 현재 `local` 지원
- `LOG_DIR`: 서버/API/Import 로그 저장 경로
- `LOG_LEVEL`: 기본 `INFO`
- `TRUSTED_HOSTS`: 운영 도메인 목록
- `KHOA_SERVICE_KEY`: KHOA OceanGrid API 키

## 데이터베이스

개발은 SQLite로 실행할 수 있지만 실제 서비스는 PostgreSQL을 권장합니다.

```bash
alembic upgrade head
alembic check
```

기존 SQLite 데이터는 앱의 관리자 백업 ZIP으로 내보낸 뒤, PostgreSQL 배포 환경에서 복구하는 방식을 권장합니다.

## Docker Compose

PostgreSQL 포함 로컬 운영 유사 환경을 실행할 수 있습니다.

```bash
docker compose up --build
curl http://127.0.0.1:8000/healthz
```

실행 전에 `.env`에 `POSTGRES_PASSWORD`, `SECRET_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`를 설정하세요.

## 배포

Render와 Railway 설정은 저장소의 `render.yaml`, `railway.json`, `Dockerfile`을 기준으로 준비되어 있습니다.
자세한 절차는 [DEPLOYMENT.md](DEPLOYMENT.md)를 확인하세요.

## 보안 및 운영

- 비밀번호는 PBKDF2 해시로 저장합니다.
- 업로드 파일은 확장자, MIME, 이미지 시그니처, 최대 용량을 검사합니다.
- 업로드 파일은 `/uploads/...` 보호 라우트를 통해 권한 확인 후 제공합니다.
- POST/PUT/PATCH/DELETE 요청은 Origin/Referer 기반 CSRF 방어를 적용합니다.
- 서버 로그, 오류 로그, Import 로그, API 로그를 분리해 저장합니다.
- 운영에서는 `ALLOW_REGISTRATION=false`와 고정 `SECRET_KEY`를 사용하세요.

## 라이선스

이 프로젝트는 MIT 라이선스를 따릅니다.
