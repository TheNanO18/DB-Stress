# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CUBRID 스트레스 테스트 도구 — Python/Locust 기반 CUBRID 데이터베이스 부하 테스트 프레임워크. jaydebeapi(JDBC 브릿지)로 DB에 접속하고, Chart.js 기반 실시간 모니터링 대시보드를 제공한다.

## 빌드 및 실행

```bash
# 환경 설정
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Unix
pip install -r requirements.txt

# 웹 UI로 실행 (권장)
locust -f scenarios/locustfile.py --class-picker
# → http://localhost:8089  (모니터링 대시보드: /monitor)

# 헤드리스 모드
locust -f scenarios/locustfile.py --headless -u 100 -r 10 -t 60s BulkInsertUser

# 외부 접속 허용
locust -f scenarios/locustfile.py --class-picker --web-host 0.0.0.0
```

별도의 테스트 스위트나 린터는 구성되어 있지 않음.

## 아키텍처

### 모듈 구성

- **`scenarios/locustfile.py`** — 진입점 (모든 컴포넌트를 import; Locust가 이 파일을 인식)
- **`scenarios/_init_hooks.py`** — Locust 이벤트 훅: CLI 인자 등록, `test_start`(테이블 생성, 시드 데이터 삽입, 모니터 초기화), `test_stop`(정리)
- **`scenarios/_shared.py`** — SQL 템플릿, `MaxIdTracker`(스레드 안전 ID 카운터), `CubridMixin`(User 클래스 공통 setup/teardown)
- **`scenarios/_web_routes.py`** — 모니터링 대시보드 Flask 라우트 (`/monitor`, `/monitor/data`, Excel/CSV/PNG 내보내기)
- **`scenarios/stress_users.py`** — 10개 Locust User 클래스: BulkInsert, ReadIntensive, LockContention, HeavyQuery, ConnectionChurn, CrudMix 및 단일 연산 변형
- **`scenarios/monitor_user.py`** — `DBMonitorUser`(fixed_count=1): 부하 없이 DB 응답 시간, 행 수, 활성 트랜잭션, 락 대기자, OS 지표를 수집

### 핵심 서비스 (`core/`)

- **`config.py`** — 싱글턴 `Config`. 우선순위: CLI 인자 > Web UI > .env > config.yaml
- **`db_client.py`** — jaydebeapi를 래핑한 `CubridClient`. 실행 시간/에러를 Locust 이벤트에 자동 보고
- **`os_monitor.py`** — `OsMonitor` 자동 감지: Docker stats → psutil(로컬) → SSH(원격). CUBRID 프로세스명 + DB명으로 필터링
- **`metrics_store.py`** — 스레드 안전 `MetricsStore` 싱글턴. 고정 크기 deque(순환 버퍼, 1초 간격 약 1시간). 테스트 재시작 시 리셋 지원

### 데이터 생성 (`data/`)

- **`generator.py`** — `DataPool` 싱글턴: 테스트 시작 전 Faker(ko_KR 로케일)로 N개 더미 레코드를 미리 생성하여 런타임 오버헤드 방지. `DummyRecord` dataclass(`slots=True`)

### 프론트엔드 (`templates/`)

- **`monitor.html`** — Chart.js 대시보드. 6개 실시간 차트(CPU, 메모리, 디스크 I/O, DB 응답, 행 수, 트랜잭션/락). `/monitor/data`를 2초마다 폴링

## 주요 설계 패턴

- **싱글턴** — Config, DataPool, MetricsStore는 모듈 레벨 전역 인스턴스
- **CubridMixin** — `_setup()`, `_teardown()`, `_get_max_id()`를 제공하며 모든 User 클래스가 상속
- **이벤트 기반 등록** — `_init_hooks.py`에서 `@events` 데코레이터로 import 시점에 자동 등록
- **락 경합 시뮬레이션** — `gevent.get_hub().threadpool`로 실제 OS 스레드를 사용하여 CUBRID 락이 실제로 유지되도록 함 (greenlet이 아님)

## 설정

`config.yaml` 섹션: `database`, `table`, `data_pool`, `load_test`, `monitor`, `docker`, `ssh`, `scenario_weights`. `.env` 파일의 환경 변수로 DB 설정 오버라이드 가능. CLI 인자(`--db-host`, `--db-port` 등)가 최우선.

## 테스트 생명주기

1. **test_start**: 메트릭 리셋 → CLI/UI에서 설정 오버라이드 → 로컬/원격 DB 감지 → DataPool 초기화 → OsMonitor 초기화 → 테이블 재생성 → 시드 데이터 삽입 → MaxIdTracker 초기화
2. **실행 중**: 각 User가 `@task` 메서드를 반복 실행, CubridClient가 Locust에 보고; DBMonitorUser가 1-2초마다 지표 수집
3. **test_stop**: MaxIdTracker 리셋, 리소스는 즉시 재시작 가능하도록 유지
