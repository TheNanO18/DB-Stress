# CUBRID Stress Test Tool

CUBRID 데이터베이스에 실제 운영 환경과 유사한 부하를 발생시키는 스트레스 테스트 도구입니다.
Locust 프레임워크 기반으로 동작하며, 웹 대시보드에서 실시간 TPS·응답시간·에러율을 모니터링할 수 있습니다.

---

## 목차

- [주요 기능](#주요-기능)
- [시나리오 프로필](#시나리오-프로필)
- [프로젝트 구조](#프로젝트-구조)
- [사전 준비](#사전-준비)
- [실행 방법](#실행-방법)
- [모니터링 대시보드](#모니터링-대시보드)
- [DB 접속 정보 오버라이드](#db-접속-정보-오버라이드)
- [원격 OS 모니터링 (SSH)](#원격-os-모니터링-ssh)
- [Docker 컨테이너 모니터링](#docker-컨테이너-모니터링)
- [설정 커스텀 (config.yaml)](#설정-커스텀-configyaml)
- [코드 커스텀 가이드](#코드-커스텀-가이드)
  - [테이블 스키마 변경](#1-테이블-스키마-변경)
  - [더미 데이터 변경](#2-더미-데이터-변경)
  - [테스트 시나리오 추가](#3-테스트-시나리오-추가)
  - [새 설정 항목 추가](#4-새-설정-항목-추가)
- [파일별 역할 요약](#파일별-역할-요약)

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 시나리오 선택 실행 | 웹 대시보드에서 체크박스로 원하는 시나리오만 골라서 실행 |
| CRUD 부하 | INSERT, SELECT(PK), UPDATE, DELETE 균등 실행 |
| 대량 INSERT | 디스크 I/O와 인덱스 부하 유발 |
| 풀스캔 / Heavy Join | 셀프 조인, 대량 정렬로 CPU/메모리 고갈 |
| Lock 경합 | 동일 행(ID 1~10)에 대한 동시 UPDATE로 Lock 경합 유발 |
| Connection Churn | 연결 생성/해제 반복으로 Broker·Network 자원 소모 |
| 더미 데이터 풀 | Faker로 한국어 더미 데이터 10,000건을 사전 생성하여 Python CPU 병목 방지 |
| 실시간 모니터링 | Locust 웹 대시보드(http://localhost:8089)에서 TPS, P95/P99, 에러율 확인 |
| 모니터링 대시보드 | Chart.js 기반 실시간 차트 — CPU, 메모리, Disk I/O, 응답시간, 행 수, 트랜잭션/Lock |
| CUBRID 프로세스 모니터링 | 서버 전체가 아닌 **특정 DB 인스턴스의 CUBRID 프로세스만** CPU/메모리 측정 |
| 원격 OS 모니터링 | SSH를 통해 원격 DB 서버의 CUBRID 프로세스 메트릭 수집 |
| Docker 지원 | Docker 컨테이너 단위 CPU/메모리 모니터링 (`docker stats`) |
| 테스트 재시작 | Stop 후 새 테스트 시 메트릭·상태 자동 초기화 — 이전 데이터 오염 없이 깨끗하게 재시작 |
| 웹 UI 동적 설정 | DB 접속 정보, SSH 정보, 태스크 대기 시간을 Locust 웹 UI의 Advanced options에서 동적 변경 |

---

## 시나리오 프로필

`--class-picker` 옵션으로 실행하면 웹 UI에서 아래 시나리오를 **체크박스로 선택**할 수 있습니다.

### 부하 유형별

| 시나리오 | 클래스명 | 목적 | 주요 쿼리 |
|----------|----------|------|-----------|
| 대량 INSERT | `BulkInsertUser` | 디스크 I/O, 인덱스 부하 | INSERT만 반복 실행 |
| 읽기 집중 (PK) | `ReadIntensiveUser` | 최대 TPS 측정 | SELECT by PK만 반복 실행 |
| Lock 경합 | `LockContentionUser` | Lock 대기, 동시성 충돌 | ID 1~10 동일 행 UPDATE |
| Heavy 쿼리 | `HeavyQueryUser` | CPU·메모리 고갈 | 셀프 조인 + 풀스캔 + 대량 정렬 |
| Connection Churn | `ConnectionChurnUser` | Broker·Network 부하 | 매 요청마다 연결 생성/해제 |

### CRUD 종합 / 개별

| 시나리오 | 클래스명 | 목적 | 주요 쿼리 |
|----------|----------|------|-----------|
| CRUD 종합 | `CrudMixUser` | 균등 CRUD 부하 | INSERT/SELECT/UPDATE/DELETE 혼합 실행 |
| Create 단독 | `CreateOnlyUser` | INSERT 단독 성능 측정 | INSERT만 반복 실행 |
| Read 단독 | `ReadOnlyUser` | SELECT 단독 성능 측정 | PK 기반 SELECT만 반복 실행 |
| Update 단독 | `UpdateOnlyUser` | UPDATE 단독 성능 측정 | 랜덤 행 UPDATE만 반복 실행 |
| Delete 단독 | `DeleteOnlyUser` | DELETE 단독 성능 측정 | 랜덤 행 DELETE만 반복 실행 |

### 모니터링 (외부 부하 관찰)

| 시나리오 | 클래스명 | 목적 | 관찰 항목 |
|----------|----------|------|-----------|
| DB 모니터 | `DBMonitorUser` | 외부 부하 상태 관찰 | CPU, 메모리, Disk I/O, 응답시간, 행 수, 트랜잭션/Lock |

`DBMonitorUser`는 **DB에 부하를 주지 않고 상태만 관찰**합니다. 외부 애플리케이션(Java, JDBC 등)이 부하를 줄 때 이 시나리오만 선택하면 Locust 대시보드에서 DB 상태 변화를 실시간으로 확인할 수 있습니다.

**대시보드에서 확인할 수 있는 지표:**

| 지표 | Name | 의미 |
|------|------|------|
| 응답 시간 프로브 | `[Monitor] response_probe` | DB 응답 지연 (ms) — 외부 부하가 심하면 이 값이 올라감 |
| 테이블 행 수 | `[Monitor] row_count` | Content Size에 현재 행 수 표시, 변화 시 콘솔 출력 |
| 활성 트랜잭션 | `[Monitor] active_transactions` | Content Size에 현재 세션 수 표시 |
| Lock 대기 | `[Monitor] lock_waiters` | Content Size에 Lock 대기 건수, 감지 시 콘솔 출력 |
| CPU 사용률 | `[Monitor] cpu_percent` | **CUBRID 프로세스만의** CPU 사용률(%), 90% 초과 시 콘솔 경고 |
| 메모리 사용률 | `[Monitor] memory_percent` | **CUBRID 프로세스만의** 메모리 사용률(%), 90% 초과 시 콘솔 경고 |
| 디스크 쓰기 | `[Monitor] disk_write_kb_s` | 디스크 쓰기 속도(KB/s) |
| 디스크 읽기 | `[Monitor] disk_read_kb_s` | 디스크 읽기 속도(KB/s) |

> CPU/메모리는 서버 전체가 아닌 **`database.name`에 해당하는 CUBRID 프로세스만** 측정합니다. 여러 DB 인스턴스가 실행 중이어도 대상 DB의 프로세스만 필터링됩니다.

**사용 예시:**

```bash
# 모니터링만 단독 실행 (외부 부하 관찰 전용)
locust -f scenarios/locustfile.py --class-picker
# → 웹 UI에서 DBMonitorUser만 체크 → 사용자 1명으로 START

# 부하 + 모니터링 동시 실행
# → BulkInsertUser + DBMonitorUser 동시 체크
```

> 여러 시나리오를 동시에 체크하면 **복합 부하 테스트**도 가능합니다.

---

## 프로젝트 구조

```
cubrid_stress_tool/
├── config.yaml              # 전체 설정 파일 (DB 접속, 부하, SSH, Docker 설정)
├── requirements.txt         # Python 의존성 패키지
├── README.md
├── core/
│   ├── __init__.py
│   ├── config.py            # config.yaml 파싱 및 싱글턴 Config 클래스
│   ├── db_client.py         # CUBRID 연결 + SQL 실행 + Locust 이벤트 리포팅
│   ├── metrics_store.py     # 스레드 안전 시계열 메트릭 저장소
│   └── os_monitor.py        # Docker / 로컬(psutil) / 원격(SSH) OS 모니터링
├── data/
│   ├── __init__.py
│   └── generator.py         # Faker 기반 더미 데이터 풀 생성기
├── templates/
│   └── monitor.html         # Chart.js 실시간 모니터링 대시보드
└── scenarios/
    ├── __init__.py
    ├── locustfile.py        # Locust 진입점 (import만 모아둠)
    ├── _shared.py           # 공통 유틸 (MaxIdTracker, CubridMixin, SQL 상수)
    ├── _init_hooks.py       # CLI 파라미터 등록 + 테스트 시작 초기화
    ├── _web_routes.py       # 모니터링 대시보드 웹 라우트
    ├── stress_users.py      # 부하 시나리오 User 클래스 10개
    └── monitor_user.py      # DB 모니터링 전용 User 클래스
```

---

## 사전 준비

### 1. CUBRID 서버 실행

CUBRID 서버가 실행 중이어야 합니다. 기본 설정은 `localhost:33000`의 `hadb` 데이터베이스를 사용합니다.

### 2. 가상환경 생성 및 의존성 설치

```bash
# 가상환경 생성
python -m venv venv

# 가상환경 활성화 (Windows)
venv\Scripts\activate

# 가상환경 활성화 (Linux / Mac)
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

> 테스트 종료 후 가상환경을 빠져나오려면 `deactivate` 명령을 실행합니다.

설치되는 패키지:

| 패키지 | 용도 |
|--------|------|
| `locust==2.32.2` | 부하 테스트 엔진 및 웹 대시보드 |
| `Faker==33.1.0` | 한국어 더미 데이터 생성 |
| `PyYAML==6.0.2` | config.yaml 파싱 |
| `CUBRID-Python` | CUBRID 공식 파이썬 드라이버 |
| `psutil` | 로컬 OS 모니터링 (CPU, 메모리, Disk I/O) |
| `paramiko==3.5.0` | SSH 원격 OS 모니터링 |

---

## 실행 방법

### 시나리오 선택 실행 (권장)

```bash
cd cubrid_stress_tool
locust -f scenarios/locustfile.py --class-picker
```

브라우저에서 **http://localhost:8089** 접속 후:

1. **User classes** — 실행할 시나리오를 체크박스로 선택
2. **Number of users (peak concurrency)** — 동시 가상 사용자 수 입력
3. **Ramp up (users started/second)** — 초당 생성할 사용자 수 입력
4. **Host** — DB 호스트 입력 (예: `192.168.100.41:33000`), 비워두면 config.yaml 값 사용
5. **Advanced options** — DB 접속 정보, SSH 정보 등을 동적으로 변경 가능
6. **START** 클릭

### 전체 시나리오 실행

```bash
locust -f scenarios/locustfile.py
```

> `--class-picker` 없이 실행하면 모든 시나리오가 **동시에** 실행됩니다.

### Headless 모드 (CLI 전용)

```bash
# 전체 시나리오 실행
locust -f scenarios/locustfile.py --headless -u 100 -r 10 -t 60s

# 특정 시나리오만 실행 (클래스명 지정)
locust -f scenarios/locustfile.py --headless -u 100 -r 10 -t 60s BulkInsertUser
locust -f scenarios/locustfile.py --headless -u 50 -r 5 -t 120s LockContentionUser HeavyQueryUser

# SSH 정보를 CLI에서 전달
locust -f scenarios/locustfile.py --class-picker --ssh-user root --ssh-password mypass
```

| 옵션 | 설명 |
|------|------|
| `--headless` | 웹 대시보드 없이 CLI에서 실행 |
| `--class-picker` | 웹 UI에 시나리오 선택 체크박스 표시 |
| `-u 100` | 동시 사용자 100명 |
| `-r 10` | 초당 10명씩 생성 |
| `-t 60s` | 60초 후 자동 종료 |
| `--db-host` | DB 호스트 오버라이드 |
| `--db-port` | DB 포트 오버라이드 |
| `--ssh-user` | SSH 사용자 오버라이드 |
| `--ssh-password` | SSH 비밀번호 오버라이드 |

### 실행 흐름

```
실행 시작 (START 클릭)
  → 이전 테스트 메트릭 데이터 초기화 (재시작 시 차트 오염 방지)
  → 웹 UI / CLI 파라미터로 DB·SSH 접속 정보 오버라이드 (있는 경우)
  → 테이블 초기화 (recreate_on_start: true이면 DROP 후 CREATE)
  → 시드 데이터 삽입 (seed_rows 건)
  → 더미 데이터 풀 사전 생성
  → 웹 UI에서 시나리오 선택 (--class-picker 사용 시)
  → 가상 사용자 스폰 시작
  → 선택된 시나리오의 쿼리만 반복 실행
  → 웹 대시보드 + 모니터링 대시보드에서 실시간 결과 확인

테스트 중지 (STOP 클릭)
  → 모든 가상 사용자의 on_stop() 호출 (DB 연결 해제, SSH 종료 등)
  → 글로벌 상태 정리 (MaxIdTracker 리셋)
  → 콘솔에 "[Stop] 테스트 종료 완료" 출력
  → 새 테스트 즉시 시작 가능 (START 재클릭)
```

> Stop 후 새 테스트를 시작하면 메트릭 저장소가 자동으로 초기화되어 이전 데이터가 차트에 섞이지 않습니다.

---

## 모니터링 대시보드

Locust 메인 페이지 상단의 **CUBRID Stress Monitor Dashboard** 링크를 클릭하거나, 직접 **http://localhost:8089/monitor** 에 접속하면 실시간 차트 대시보드를 확인할 수 있습니다.

**6개 실시간 차트:**

| 차트 | 설명 |
|------|------|
| CPU Usage | CUBRID 프로세스의 CPU 사용률 (%) |
| Memory Usage | CUBRID 프로세스의 메모리 사용률 (%) |
| Disk I/O | 디스크 읽기/쓰기 속도 (KB/s) |
| DB Response Probe | DB 응답 시간 (ms) |
| Table Row Count | 테이블의 현재 행 수 |
| Transactions / Locks | 활성 트랜잭션 수 / Lock 대기 건수 |

> 대시보드는 2초 간격으로 자동 갱신됩니다. `DBMonitorUser` 시나리오가 실행 중이어야 데이터가 수집됩니다.

### OS 모니터링 자동 감지

| DB 위치 | 모니터링 방식 | 필요 패키지 |
|---------|-------------|------------|
| 로컬 (localhost) | `psutil`로 직접 측정 | `psutil` |
| 원격 서버 | SSH로 `ps` 명령어 실행 | `paramiko` |
| Docker 컨테이너 | `docker stats` 명령어 | Docker CLI |

---

## DB 접속 정보 오버라이드

DB 접속 정보는 세 가지 방법으로 설정할 수 있으며, 우선순위는 다음과 같습니다:

1. **웹 UI Advanced options** (최우선) — `--db-host`, `--db-port`, `--db-name`, `--db-user`, `--db-password`
2. **웹 UI Host 필드** — `192.168.100.41:33000` 형식으로 호스트:포트 입력
3. **config.yaml** (기본값) — `database` 섹션

| Advanced options 필드 | 설명 | 환경변수 |
|----------------------|------|---------|
| `--db-host` | DB 호스트 | `CUBRID_HOST` |
| `--db-port` | DB 포트 | `CUBRID_PORT` |
| `--db-name` | DB 이름 | `CUBRID_DB` |
| `--db-user` | DB 사용자 | `CUBRID_USER` |
| `--db-password` | DB 비밀번호 | `CUBRID_PASSWORD` |
| `--wait-min` | 태스크 간 최소 대기 시간(초) | — |
| `--wait-max` | 태스크 간 최대 대기 시간(초) | — |

> `--wait-min`/`--wait-max`를 비워두면(0) config.yaml의 기본값(`0.1`/`1.0`)이 사용됩니다. 값을 입력하면 모든 시나리오의 태스크 대기 시간이 해당 값으로 변경됩니다.

---

## 원격 OS 모니터링 (SSH)

DB가 원격 서버에 있을 때, SSH를 통해 해당 서버의 CUBRID 프로세스 CPU/메모리를 측정합니다.

### 설정 방법

**방법 1: config.yaml에서 설정**

```yaml
ssh:
  port: 22
  user: "root"
  password: "your_password"    # 비밀번호 인증
  key_file: ""                  # 또는 키 파일 인증: "~/.ssh/id_rsa"
```

**방법 2: 웹 UI Advanced options에서 설정**

| 필드 | 설명 | 환경변수 |
|------|------|---------|
| `--ssh-user` | SSH 사용자 | `SSH_USER` |
| `--ssh-password` | SSH 비밀번호 | `SSH_PASSWORD` |
| `--ssh-port` | SSH 포트 | `SSH_PORT` |
| `--ssh-key-file` | SSH 키 파일 경로 | `SSH_KEY_FILE` |

> 웹 UI에서 입력한 값이 config.yaml 값을 덮어씁니다. `password`와 `key_file` 중 하나만 설정하면 됩니다.

### 측정 방식

SSH로 원격 서버에서 `ps -eo %cpu,args` / `ps -eo %mem,args` 명령어를 실행하여 `database.name`(예: `hadb`)에 해당하는 CUBRID 프로세스만 필터링하여 합산합니다.

```bash
# 서버에서 직접 확인 (검증용)
ps -eo %cpu,%mem,args | grep cub_ | grep hadb
```

> 여러 CUBRID 인스턴스가 동일 서버에서 실행 중이어도, config.yaml의 `database.name`에 해당하는 프로세스만 측정됩니다.

### SSH 미설정 시

원격 DB인데 SSH가 설정되지 않으면 OS 모니터링(CPU/메모리/Disk)은 비활성화됩니다. DB 응답시간, 트랜잭션 수, 행 수는 DB 직접 쿼리이므로 정상 수집됩니다.

---

## Docker 컨테이너 모니터링

CUBRID가 Docker 컨테이너로 실행 중일 때 `docker stats`로 컨테이너 단위 CPU/메모리를 측정합니다.

```yaml
docker:
  enabled: true
  container_name: "cubrid-db"    # 실제 컨테이너 이름으로 변경
```

> Docker 모드가 활성화되면 SSH/psutil 대신 `docker stats` 명령어를 사용합니다. CUBRID 전용 컨테이너라면 컨테이너 메트릭 ≈ CUBRID 메트릭입니다.

---

## 설정 커스텀 (config.yaml)

대부분의 경우 **config.yaml만 수정하면 충분**합니다.

### DB 접속 정보

```yaml
database:
  host: "localhost"       # CUBRID 서버 주소
  port: 33000             # 포트 번호
  name: "hadb"            # 데이터베이스 이름
  user: ""                # 사용자
  password: ""            # 비밀번호 (없으면 빈 문자열)
```

### 테이블 설정

```yaml
table:
  name: "concert"             # 테스트용 테이블 이름
  recreate_on_start: true     # true: 매 테스트 시작 시 테이블 DROP 후 재생성
  seed_rows: 3000             # 테스트 시작 시 자동 삽입할 시드 데이터 건수
```

### 더미 데이터 풀

```yaml
data_pool:
  size: 10000           # 사전 생성할 더미 레코드 수 (클수록 다양한 데이터)
  locale: "ko_KR"       # Faker 로케일 (en_US, ja_JP 등으로 변경 가능)
```

### 부하 테스트 설정

```yaml
load_test:
  users: 100            # 동시 가상 사용자 수
  spawn_rate: 10        # 초당 생성할 사용자 수
  run_time: 0           # 테스트 지속 시간(초). 0 = 수동 중지까지 계속
  wait_min: 0.1         # 태스크 간 최소 대기 시간(초)
  wait_max: 1.0         # 태스크 간 최대 대기 시간(초)
```

### 모니터링 설정

```yaml
monitor:
  interval_min: 1.0     # 모니터링 조회 최소 간격(초)
  interval_max: 2.0     # 모니터링 조회 최대 간격(초)
```

### Docker 설정

```yaml
docker:
  enabled: false              # true: docker stats로 컨테이너 모니터링
  container_name: "cubrid-db" # 대상 Docker 컨테이너 이름
```

### SSH 원격 모니터링 설정

```yaml
ssh:
  port: 22                    # SSH 포트
  user: "root"                # SSH 사용자
  password: ""                # SSH 비밀번호 (key_file과 택 1)
  key_file: ""                # SSH 키 파일 경로 (password와 택 1)
```

---

## 코드 커스텀 가이드

테이블 구조 변경, 새 시나리오 추가 등 config.yaml만으로 부족한 경우 아래를 참고합니다.

### 1. 테이블 스키마 변경

`scenarios/_shared.py`의 `CREATE_TABLE_SQL`을 수정합니다.

**현재 스키마:**

```sql
CREATE TABLE IF NOT EXISTS concert (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100),
    email       VARCHAR(200),
    phone       VARCHAR(50),
    address     VARCHAR(500),
    company     VARCHAR(200),
    text_col    VARCHAR(2000),
    amount      NUMERIC(12,2),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

**예시 — `age` 컬럼 추가:**

```sql
CREATE TABLE IF NOT EXISTS concert (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100),
    email       VARCHAR(200),
    phone       VARCHAR(50),
    address     VARCHAR(500),
    company     VARCHAR(200),
    text_col    VARCHAR(2000),
    amount      NUMERIC(12,2),
    age         INT,                                    -- 추가
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

> 스키마를 변경하면 반드시 **더미 데이터**와 **INSERT 쿼리**도 함께 수정해야 합니다. (아래 2번, 3번 참고)

### 2. 더미 데이터 변경

`data/generator.py`의 `DummyRecord` 데이터클래스와 `_generate()` 메서드를 수정합니다.

**예시 — `age` 필드 추가:**

```python
@dataclass(slots=True)
class DummyRecord:
    name: str
    email: str
    phone: str
    address: str
    company: str
    text: str
    amount: float
    age: int              # 추가

# _generate() 메서드 내부에서:
record = DummyRecord(
    name=fake.name(),
    email=fake.email(),
    phone=fake.phone_number(),
    address=fake.address().replace("\n", " "),
    company=fake.company(),
    text=fake.text(max_nb_chars=random.randint(50, 500)),
    amount=round(random.uniform(1000, 9_999_999), 2),
    age=random.randint(20, 70),       # 추가
)
```

`pick_values()` 메서드도 새 필드를 포함하도록 수정합니다:

```python
def pick_values(self) -> tuple:
    r = self.pick()
    return (r.name, r.email, r.phone, r.address, r.company, r.text, r.amount, r.age)
```

### 3. 테스트 시나리오 추가

새 시나리오를 추가하려면 `scenarios/stress_users.py`에 새 User 클래스를 추가하고, `scenarios/locustfile.py`에서 import합니다.

**예시 — 나이 범위 검색 시나리오:**

```python
# scenarios/stress_users.py에 추가
class AgeRangeSearchUser(CubridMixin, User):
    """나이 범위 검색 시나리오."""

    wait_time = default_wait_time

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def select_by_age(self):
        """SELECT — 나이 범위 조회."""
        min_age = random.randint(20, 40)
        max_age = min_age + 10
        sql = f"SELECT * FROM {self.table} WHERE age BETWEEN ? AND ?"
        self.client.execute("SELECT", "select_by_age", sql, (min_age, max_age), fetch=True)
```

```python
# scenarios/locustfile.py에 import 추가
from scenarios.stress_users import AgeRangeSearchUser  # noqa: F401
```

> 새 User 클래스를 `locustfile.py`에서 import하면 `--class-picker` UI에 자동으로 체크박스가 추가됩니다.

### 4. 새 설정 항목 추가

1. `config.yaml`에 새 섹션/항목 추가
2. `core/config.py`의 `Config` 클래스에 `@property` 메서드 추가

**예시 — 로그 레벨 설정 추가:**

```yaml
# config.yaml
logging:
  level: "DEBUG"
```

```python
# core/config.py
@property
def log_level(self) -> str:
    return self._data.get("logging", {}).get("level", "INFO")
```

---

## 파일별 역할 요약

| 파일 | 역할 | 수정 시점 |
|------|------|-----------|
| `config.yaml` | DB 접속, 부하, SSH, Docker 설정 | 설정값만 바꿀 때 |
| `scenarios/locustfile.py` | Locust 진입점 (import만 모아둠) | 새 시나리오 import 추가 시 |
| `scenarios/_shared.py` | MaxIdTracker, CubridMixin, 테이블 DDL/DML 상수 | 스키마·INSERT 쿼리 변경 시 |
| `scenarios/_init_hooks.py` | CLI 파라미터 등록, 테스트 시작 초기화, 테스트 종료 정리 | 초기화/종료 로직 변경 시 |
| `scenarios/_web_routes.py` | 모니터링 대시보드 웹 라우트 (/monitor) | 대시보드 API 변경 시 |
| `scenarios/stress_users.py` | 부하 시나리오 User 클래스 10개 | 시나리오 추가·수정 시 |
| `scenarios/monitor_user.py` | DBMonitorUser (OS/DB 메트릭 수집) | 모니터링 항목 변경 시 |
| `data/generator.py` | 더미 데이터 구조 및 생성 로직 | 테이블 컬럼 변경 시 함께 수정 |
| `core/config.py` | config.yaml 파싱, 설정 접근자 | 새 설정 항목 추가 시 |
| `core/db_client.py` | CUBRID 연결, SQL 실행, Locust 리포팅 | SQL 실행 방식 변경 시 |
| `core/os_monitor.py` | Docker/로컬/원격 OS 모니터링 | 모니터링 방식 변경 시 |
| `core/metrics_store.py` | 스레드 안전 시계열 메트릭 저장소 (reset 지원) | 새 메트릭 추가 시 |
| `templates/monitor.html` | Chart.js 실시간 모니터링 대시보드 | 차트 UI 변경 시 |

> **핵심 원칙**: 테이블 스키마를 변경하면 `_shared.py`(DDL + INSERT 템플릿) → `generator.py`(더미 데이터) → `config.yaml` 세 파일을 함께 수정합니다.
> 새 시나리오를 추가하려면 `stress_users.py`에 User 클래스를 추가하고 `locustfile.py`에서 import하면 `--class-picker` UI에 자동 반영됩니다.
