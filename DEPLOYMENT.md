# 배포 점검 및 운영 가이드

## 필수 환경변수

운영 환경에서는 `.env` 파일을 이미지나 Git 저장소에 포함하지 말고 배포 플랫폼의 비밀
환경변수 기능을 사용한다.

```env
APP_ENV=production
SECRET_KEY=32자 이상의 고정 난수
DATABASE_URL=postgresql+psycopg://사용자:비밀번호@호스트:5432/데이터베이스
UPLOAD_DIR=/data/uploads
TRUSTED_HOSTS=diving.example.com
SESSION_HTTPS_ONLY=true
FORCE_HTTPS=true
ALLOW_REGISTRATION=false
RUN_STARTUP_MAINTENANCE=false
```

빈 데이터베이스의 첫 관리자가 필요하면 첫 배포에만 아래 값을 추가한다. 관리자 생성 후 두
환경변수를 제거하고 다시 배포한다.

```env
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=12자 이상의 강한 비밀번호
```

## SECRET_KEY

- 운영 환경에서 누락되거나 32자 미만이면 앱이 시작되지 않는다.
- 배포마다 키를 바꾸면 기존 로그인 세션이 모두 무효화된다.
- 소스 코드, Docker 이미지, 로그에 값을 기록하지 않는다.

## 데이터베이스

### SQLite 한계

- 동시에 쓰는 요청이 많으면 `database is locked` 오류가 발생할 수 있다.
- 로컬 파일이므로 다중 인스턴스 배포에 사용할 수 없다.
- 많은 호스팅 플랫폼의 기본 파일시스템은 재배포 시 초기화된다.
- 단일 프로세스·소규모 개인 운영 외에는 PostgreSQL을 권장한다.

SQLite를 계속 사용한다면 DB 파일과 `UPLOAD_DIR`을 모두 영구 볼륨에 배치하고 앱 인스턴스를
하나만 실행한다.

`divinglog.db`와 로컬 `venv`는 현재 버전부터 Git 추적 대상에서 제외한다. DB 파일은 과거
커밋 이력에 남아 있으므로 저장소를 외부에 공개했다면 `git filter-repo` 등으로 이력을
정리하고 노출된 계정 비밀번호를 변경해야 한다.

### PostgreSQL 전환

1. 기존 서비스의 관리자 백업에서 사진 포함 ZIP 전체 백업을 받는다.
2. PostgreSQL 데이터베이스를 생성하고 `DATABASE_URL`을 설정한다.
3. 빈 DB로 앱을 한 번 시작해 테이블을 생성한다.
4. 초기 관리자 계정으로 로그인한다.
5. 백업/복구 화면에서 관리자 전체 복구를 실행한다.
6. 로그 수, 사진, 프로파일 샘플, 다이브 번호를 검증한다.

현재 프로젝트는 Alembic 마이그레이션 이력이 없다. 운영 스키마 변경이 시작되면
`Base.metadata.create_all()` 대신 Alembic 버전 마이그레이션을 도입해야 한다.

## 업로드 저장소

- `UPLOAD_DIR`은 재배포 후에도 유지되는 영구 볼륨이어야 한다.
- 여러 앱 인스턴스를 실행하면 S3 호환 객체 저장소 같은 공유 저장소로 전환해야 한다.
- 사진은 확장자, MIME 형식, 파일 시그니처, 크기를 검사한다.
- 업로드 파일은 정적 디렉터리로 공개하지 않고 로그인 및 참여자 권한을 확인한 뒤 제공한다.
- Import 및 복구 파일은 최대 크기를 제한하며 ZIP 경로 이동과 압축 폭탄을 검사한다.

## 보안 운영

- HTTPS를 종료하는 프록시에서 전달 헤더를 신뢰하도록 실행 명령에 `--proxy-headers`를 사용한다.
- `TRUSTED_HOSTS`에는 실제 서비스 도메인만 입력한다.
- 회원가입이 필요하지 않으면 `ALLOW_REGISTRATION=false`를 유지한다.
- 로그인 실패 횟수 제한은 아직 없다. 공개 서비스에서는 프록시 또는 애플리케이션 수준의
  속도 제한을 추가해야 한다.
- CSRF 방어는 SameSite 쿠키와 Origin/Referer 검사에 기반한다. 외부 API 클라이언트를
  추가할 때는 별도 토큰 인증을 사용한다.
- 인라인 스크립트가 많아 엄격한 Content-Security-Policy는 아직 적용하지 않았다.

## 실행과 상태 확인

```bash
docker build -t diving-log .
docker run --rm -p 8000:8000 --env-file .env -v diving-uploads:/data/uploads diving-log
curl http://127.0.0.1:8000/healthz
```

정상 응답 예:

```json
{"status":"ok","database":"postgresql"}
```
