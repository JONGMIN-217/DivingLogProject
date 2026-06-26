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

### Start Command

플랫폼에서 직접 시작 명령을 입력해야 하는 경우 다음 값을 사용합니다.

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

프록시 뒤에서 실행하는 Render/Railway 환경에서는 저장소의 `Procfile`, `render.yaml`,
`railway.json`처럼 다음 옵션을 함께 사용하는 구성을 권장합니다.

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
```

### Render 배포

1. 저장소를 GitHub 또는 GitLab에 push합니다.
2. Render에서 **New > Blueprint**를 선택하고 저장소의 `render.yaml`을 적용합니다.
3. Blueprint 입력 단계에서 `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD`를 설정합니다.
4. Render가 PostgreSQL, 웹 서비스, 업로드 디스크를 생성합니다.
5. 배포 전 `alembic upgrade head`가 실행되고, 성공하면 앱이 시작됩니다.
6. `/healthz`가 `200`을 반환하는지 확인합니다.
7. 첫 관리자 로그인 후 `BOOTSTRAP_ADMIN_*` 환경변수를 제거합니다.

### Railway 배포

1. Railway에서 GitHub 저장소를 연결합니다.
2. PostgreSQL 서비스를 추가합니다.
3. 앱 서비스에 Volume을 추가하고 mount path를 `/data/uploads`로 지정합니다.
4. 앱 서비스 환경변수에 `DATABASE_URL`, `SECRET_KEY`, `UPLOAD_DIR`, `TRUSTED_HOSTS` 등을 설정합니다.
5. `railway.json`의 `preDeployCommand`가 `alembic upgrade head`를 실행합니다.
6. 배포 후 `/healthz`와 관리자 로그인을 확인합니다.

### 운영 환경변수

필수 또는 권장 환경변수는 다음과 같습니다.

- `APP_ENV=production`
- `DATABASE_URL`: PostgreSQL 연결 URL
- `SECRET_KEY`: 32자 이상의 고정 난수
- `UPLOAD_DIR`: Render 디스크 또는 Railway Volume 경로
- `UPLOAD_STORAGE_BACKEND=local`
- `LOG_DIR`: 운영 로그 저장 경로
- `LOG_LEVEL=INFO`
- `TRUSTED_HOSTS`: 실제 서비스 도메인
- `SESSION_HTTPS_ONLY=true`
- `ALLOW_REGISTRATION=false`
- `RUN_STARTUP_MAINTENANCE=false`
- `BOOTSTRAP_ADMIN_USERNAME`: 최초 관리자 생성 시에만 사용
- `BOOTSTRAP_ADMIN_PASSWORD`: 최초 관리자 생성 시에만 사용
- `KHOA_SERVICE_KEY`: KHOA API를 사용할 때 설정

### 초기 DB 생성 및 마이그레이션

배포 환경에서는 앱 시작 전에 다음 명령을 실행해야 합니다.

```bash
alembic upgrade head
```

Render와 Railway 설정 파일에는 이 명령이 배포 전 단계로 포함되어 있습니다. 새 모델 변경을 배포하기 전에는 로컬에서 다음을 확인합니다.

```bash
alembic upgrade head
alembic check
```

## 보안 및 운영

- 비밀번호는 PBKDF2 해시로 저장합니다.
- 업로드 파일은 확장자, MIME, 이미지 시그니처, 최대 용량을 검사합니다.
- 업로드 파일은 `UPLOAD_DIR` 아래 `photos/logs`, `photos/trips`, `photos/albums`, `imports`, `backups`로 분리 저장합니다.
- 업로드 파일은 `/uploads/...` 보호 라우트를 통해 권한 확인 후 제공합니다.
- POST/PUT/PATCH/DELETE 요청은 Origin/Referer 기반 CSRF 방어를 적용합니다.
- 서버 로그, 오류 로그, Import 로그, API 로그를 분리해 저장합니다.
- 운영에서는 `ALLOW_REGISTRATION=false`와 고정 `SECRET_KEY`를 사용하세요.

## 라이선스

이 프로젝트는 MIT 라이선스를 따릅니다.
