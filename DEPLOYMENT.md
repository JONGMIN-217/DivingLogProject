# Render 및 Railway 배포 가이드

## 플랫폼 비교

| 항목 | Render | Railway |
|---|---|---|
| 프로젝트 설정 | `render.yaml`로 웹, PostgreSQL, 디스크를 함께 선언 | `railway.json`은 앱 배포 설정 중심이며 DB와 볼륨은 대시보드에서 연결 |
| PostgreSQL | Blueprint에서 자동 생성 및 `DATABASE_URL` 연결 | PostgreSQL 서비스를 추가하고 참조 변수로 연결 |
| 마이그레이션 | `preDeployCommand`에서 실행 | `preDeployCommand`에서 실행 |
| 업로드 보존 | 유료 웹 서비스의 영구 디스크 필요 | 서비스에 영구 볼륨 연결 필요 |
| 확장 | 디스크 연결 서비스는 단일 인스턴스 | 볼륨 연결 서비스는 복제본 사용 불가 |
| 이 프로젝트의 설정 난이도 | 저장소 연결 후 Blueprint 적용 중심 | 앱, PostgreSQL, 볼륨, 변수 연결을 각각 확인 |

## 추천 플랫폼

**Render를 우선 추천한다.**

이 프로젝트는 PostgreSQL 외에도 사용자 업로드 파일을 위한 영구 디스크가 반드시 필요하다.
Render는 저장소의 `render.yaml` 하나로 웹 서비스, PostgreSQL, 업로드 디스크, 환경변수,
상태 점검, 배포 전 마이그레이션을 함께 관리할 수 있어 설정 누락 가능성이 더 낮다.

Railway도 정상적으로 지원한다. Railway의 빠른 서비스 생성 방식이 더 익숙하거나 기존
Railway 프로젝트가 있다면 `railway.json`을 사용하고 아래 수동 설정을 완료하면 된다.

## 공통 운영 구조

- 데이터베이스: PostgreSQL
- 스키마 변경: Alembic
- 마이그레이션 명령: `alembic upgrade head`
- 앱 실행: Uvicorn 단일 프로세스
- 상태 점검: `GET /healthz`
- 업로드: 영구 디스크 또는 영구 볼륨의 `UPLOAD_DIR`
- 인스턴스 수: 1개

업로드가 로컬 파일시스템에 저장되므로 현재 구성에서 인스턴스를 여러 개 실행하면 안 된다.
수평 확장이 필요하면 업로드 코드를 S3 호환 객체 저장소로 전환해야 한다.

## Render 배포

저장소 루트의 `render.yaml`이 다음 리소스를 생성한다.

- `diving-log`: Singapore 리전의 Python 웹 서비스
- `diving-log-postgres`: 외부 접근을 차단한 PostgreSQL
- `diving-log-uploads`: `/opt/render/project/src/uploads`에 연결되는 1GB 영구 디스크

### 배포 순서

1. GitHub 또는 GitLab에 저장소를 push한다.
2. Render에서 **New > Blueprint**를 선택한다.
3. 저장소를 연결하고 `render.yaml`을 적용한다.
4. Blueprint 입력 단계에서 다음 비밀값을 입력한다.
   - `BOOTSTRAP_ADMIN_USERNAME`
   - `BOOTSTRAP_ADMIN_PASSWORD`: 12자 이상의 강한 비밀번호
5. 첫 배포 로그에서 `alembic upgrade head` 성공을 확인한다.
6. `/healthz`가 `200`을 반환하는지 확인한다.
7. 관리자 로그인 후 `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD`를 제거한다.
8. 커스텀 도메인을 사용하면 `TRUSTED_HOSTS`에 해당 도메인을 추가한다.

`SECRET_KEY`는 Blueprint가 자동 생성한다. 이후 값을 재생성하면 모든 로그인 세션이
무효화되므로 기존 값을 유지한다.

Render 영구 디스크가 연결된 서비스는 단일 인스턴스로만 운영된다. 또한 디스크 연결 배포에는
짧은 중단 시간이 생길 수 있다.

## Railway 배포

`railway.json`은 Dockerfile 빌드, Alembic 사전 배포, 앱 시작, 상태 점검을 설정한다.
PostgreSQL과 볼륨은 Railway 프로젝트 화면에서 추가해야 한다.

### 배포 순서

1. Railway에서 **New Project > Deploy from GitHub repo**로 저장소를 연결한다.
2. 프로젝트에 PostgreSQL 서비스를 추가한다.
3. 앱 서비스 Variables에서 다음 값을 설정한다.

```env
APP_ENV=production
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=32자 이상의 고정 난수
UPLOAD_DIR=/data/uploads
TRUSTED_HOSTS=*.up.railway.app
SESSION_HTTPS_ONLY=true
FORCE_HTTPS=false
ALLOW_REGISTRATION=false
RUN_STARTUP_MAINTENANCE=false
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=12자 이상의 강한 비밀번호
RAILWAY_RUN_UID=0
```

PostgreSQL 서비스 이름이 `Postgres`가 아니면 `DATABASE_URL` 참조의 서비스 이름을 실제
이름에 맞게 변경한다.

4. 앱 서비스에 Volume을 추가하고 mount path를 `/data/uploads`로 지정한다.
5. 앱 서비스의 복제본 수를 1개로 유지한다.
6. 배포 로그에서 `alembic upgrade head` 성공을 확인한다.
7. Railway 도메인을 생성하고 `/healthz`를 확인한다.
8. 관리자 로그인 후 두 `BOOTSTRAP_ADMIN_*` 변수를 제거한다.

Docker 이미지가 비루트 사용자로 실행되므로 Railway 볼륨 권한 호환을 위해
`RAILWAY_RUN_UID=0`이 필요하다. 볼륨 없이 배포하면 업로드 파일은 재배포 시 사라진다.

## 환경변수

| 변수 | 운영 필수 | 설명 |
|---|---|---|
| `APP_ENV` | 예 | `production` |
| `DATABASE_URL` | 예 | PostgreSQL 연결 URL |
| `SECRET_KEY` | 예 | 32자 이상의 고정 난수 |
| `UPLOAD_DIR` | 예 | 영구 디스크 또는 볼륨의 절대 경로 |
| `TRUSTED_HOSTS` | 예 | 쉼표로 구분한 허용 도메인 |
| `SESSION_HTTPS_ONLY` | 예 | 운영에서는 `true` |
| `FORCE_HTTPS` | 권장 | 플랫폼 프록시 구성을 고려해 기본 `false` |
| `ALLOW_REGISTRATION` | 권장 | 공개 가입이 필요하지 않으면 `false` |
| `RUN_STARTUP_MAINTENANCE` | 예 | 운영에서는 `false` |
| `BOOTSTRAP_ADMIN_USERNAME` | 최초만 | 빈 DB의 첫 관리자 ID |
| `BOOTSTRAP_ADMIN_PASSWORD` | 최초만 | 12자 이상의 첫 관리자 비밀번호 |

운영 비밀값은 `.env`나 Git에 저장하지 않고 플랫폼의 비밀 환경변수 기능을 사용한다.

## 자동 마이그레이션

배포 전 각 플랫폼이 다음 명령을 실행한다.

```bash
alembic upgrade head
```

모델을 변경한 뒤에는 로컬에서 새 마이그레이션을 생성하고 내용과 downgrade를 검토한다.

```bash
alembic revision --autogenerate -m "변경 내용"
alembic upgrade head
alembic check
```

운영 모드에서는 앱이 `Base.metadata.create_all()`이나 SQLite용 임시 스키마 수정을 실행하지
않는다. Alembic 마이그레이션 실패 시 새 앱 버전은 시작되지 않는다.

### 기존 SQLite 데이터 이전

1. 기존 앱에서 사진 포함 관리자 전체 ZIP 백업을 생성한다.
2. 새 PostgreSQL 배포에서 Alembic 마이그레이션이 완료됐는지 확인한다.
3. 초기 관리자로 로그인한다.
4. 백업/복구 페이지에서 관리자 전체 복구를 실행한다.
5. 로그, 포인트, 프로파일 샘플, 사진, 친구, 투어 수를 비교한다.
6. 다이빙 번호 재정렬을 한 번 실행한다.

기존 SQLite DB에 Alembic을 직접 적용하지 않는다. 현재 초기 마이그레이션은 새 PostgreSQL
데이터베이스 생성 기준이다.

## 업로드 파일

- Render: `/opt/render/project/src/uploads`
- Railway: `/data/uploads`
- 사진과 Import 원본, 복구 임시 파일이 같은 `UPLOAD_DIR` 아래에 저장된다.
- 업로드 파일은 정적 공개하지 않고 로그인 및 소유자/참여자 권한을 확인해 제공한다.
- 플랫폼 볼륨 스냅샷 외에 앱의 사진 포함 ZIP 백업을 정기적으로 보관한다.
- 다중 인스턴스, CDN, 대용량 사진이 필요해지면 S3 호환 저장소로 이전한다.

## 배포 체크리스트

### 배포 전

- [ ] `git status`가 깨끗하다.
- [ ] `pip install -r requirements.txt`가 성공한다.
- [ ] `alembic upgrade head`와 `alembic check`가 성공한다.
- [ ] `SECRET_KEY`가 32자 이상이며 Git에 포함되지 않았다.
- [ ] PostgreSQL `DATABASE_URL`이 앱 서비스에 연결됐다.
- [ ] 영구 디스크 또는 볼륨이 `UPLOAD_DIR`에 연결됐다.
- [ ] `TRUSTED_HOSTS`가 실제 서비스 도메인을 포함한다.
- [ ] 초기 관리자 비밀번호가 12자 이상이다.
- [ ] 앱 인스턴스 또는 복제본 수가 1개다.

### 배포 후

- [ ] `/healthz`가 `200`과 `database: postgresql`을 반환한다.
- [ ] 관리자 로그인이 된다.
- [ ] `BOOTSTRAP_ADMIN_*` 환경변수를 제거했다.
- [ ] 로그 추가, 수정, 삭제가 정상 동작한다.
- [ ] 사진 업로드 후 재배포해도 파일이 유지된다.
- [ ] UDDF와 Shearwater DB Import 미리보기가 열린다.
- [ ] 수심 프로파일 API와 그래프가 동작한다.
- [ ] 백업 ZIP 다운로드를 별도 위치에 보관했다.
- [ ] PostgreSQL 및 볼륨 백업 정책을 활성화했다.

## 로컬 배포 검증

새 임시 SQLite DB로 마이그레이션을 확인할 수 있다.

```bash
DATABASE_URL=sqlite:////tmp/divinglog_deploy_test.db alembic upgrade head
DATABASE_URL=sqlite:////tmp/divinglog_deploy_test.db alembic check
```

Alembic 도입 전 생성된 기존 SQLite DB는 최초 `alembic upgrade head`에서 baseline 처리된다.
과거 SQLite 보정 과정에서 추가된 인덱스와 외래 키의 차이 때문에 해당 기존 DB에서는 최초
`alembic check`가 차이를 보고할 수 있다. 운영 PostgreSQL은 빈 DB에 초기 마이그레이션을
적용한 뒤 사용한다.

Docker 실행:

```bash
docker build -t diving-log .
docker run --rm -p 8000:8000 --env-file .env -v diving-uploads:/data/uploads diving-log
curl http://127.0.0.1:8000/healthz
```

## 공식 문서

- Render Blueprint: https://render.com/docs/blueprint-spec
- Render Persistent Disk: https://render.com/docs/disks
- Railway Config as Code: https://docs.railway.com/config-as-code/reference
- Railway Volumes: https://docs.railway.com/volumes/reference
- Railway PostgreSQL: https://docs.railway.com/databases/postgresql
