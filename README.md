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
| DB 모니터 | `DBMonitorUser` | 외부 부하 상태 관찰 | 응답 시간 프로브, 행 수 변화, 활성 트랜잭션 수, Lock 대기 건수 |

`DBMonitorUser`는 **DB에 부하를 주지 않고 상태만 관찰**합니다. 외부 애플리케이션(Java, JDBC 등)이 부하를 줄 때 이 시나리오만 선택하면 Locust 대시보드에서 DB 상태 변화를 실시간으로 확인할 수 있습니다.

**대시보드에서 확인할 수 있는 지표:**

| 지표 | Name | 의미 |
|------|------|------|
| 응답 시간 프로브 | `[Monitor] response_probe` | DB 응답 지연 (ms) — 외부 부하가 심하면 이 값이 올라감 |
| 테이블 행 수 | `[Monitor] row_count` | Content Size에 현재 행 수 표시, 변화 시 콘솔 출력 |
| 활성 트랜잭션 | `[Monitor] active_transactions` | Content Size에 현재 세션 수 표시 |
| Lock 대기 | `[Monitor] lock_waiters` | Content Size에 Lock 대기 건수, 감지 시 콘솔 출력 |
| CPU 사용률 | `[Monitor] cpu_percent` | Content Size에 CPU 사용률(%) 표시, 90% 초과 시 콘솔 경고 |
| 메모리 사용률 | `[Monitor] memory_percent` | Content Size에 메모리 사용률(%) 표시, 90% 초과 시 콘솔 경고 |
| 디스크 쓰기 | `[Monitor] disk_write_kb_s` | Content Size에 디스크 쓰기 속도(KB/s) 표시 |
| 디스크 읽기 | `[Monitor] disk_read_kb_s` | Content Size에 디스크 읽기 속도(KB/s) 표시 |

> CPU, 메모리, 디스크 I/O 지표는 `psutil` 패키지가 필요하며, **DB 서버와 같은 머신에서 실행할 때** 의미가 있습니다. 원격 실행 시 해당 태스크는 자동 스킵됩니다.

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
├── config.yaml              # 전체 설정 파일 (DB 접속, 부하 설정)
├── requirements.txt         # Python 의존성 패키지
├── README.md
├── core/
│   ├── __init__.py
│   ├── config.py            # config.yaml 파싱 및 싱글턴 Config 클래스
│   └── db_client.py         # CUBRID 연결 + SQL 실행 + Locust 이벤트 리포팅
├── data/
│   ├── __init__.py
│   └── generator.py         # Faker 기반 더미 데이터 풀 생성기
└── scenarios/
    ├── __init__.py
    └── locustfile.py        # 6개 시나리오별 User 클래스 정의
```

---

## 사전 준비

### 1. CUBRID 서버 실행

CUBRID 서버가 실행 중이어야 합니다. 기본 설정은 `localhost:33000`의 `demodb` 데이터베이스를 사용합니다.

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
4. **Host** — 비워두면 됩니다 (DB 연결은 config.yaml에서 설정)
5. **Advanced options** — 펼치면 **Run time** 설정 가능 (예: `60s`, `5m`, `1h`)
6. **START** 클릭

### 전체 시나리오 실행

```bash
locust -f scenarios/locustfile.py
```

> `--class-picker` 없이 실행하면 6개 시나리오가 **모두 동시에** 실행됩니다.

### Headless 모드 (CLI 전용)

```bash
# 전체 시나리오 실행
locust -f scenarios/locustfile.py --headless -u 100 -r 10 -t 60s

# 특정 시나리오만 실행 (클래스명 지정)
locust -f scenarios/locustfile.py --headless -u 100 -r 10 -t 60s BulkInsertUser
locust -f scenarios/locustfile.py --headless -u 50 -r 5 -t 120s LockContentionUser HeavyQueryUser
```

| 옵션 | 설명 |
|------|------|
| `--headless` | 웹 대시보드 없이 CLI에서 실행 |
| `--class-picker` | 웹 UI에 시나리오 선택 체크박스 표시 |
| `-u 100` | 동시 사용자 100명 |
| `-r 10` | 초당 10명씩 생성 |
| `-t 60s` | 60초 후 자동 종료 |

### 실행 흐름

```
실행 시작
  → 테이블 초기화 (recreate_on_start: true이면 DROP 후 CREATE)
  → 더미 데이터 풀 10,000건 사전 생성
  → 웹 UI에서 시나리오 선택 (--class-picker 사용 시)
  → 가상 사용자 스폰 시작
  → 선택된 시나리오의 쿼리만 반복 실행
  → 웹 대시보드에서 실시간 결과 확인
  → 수동 중지 또는 run_time 경과 시 종료
```

---

## 설정 커스텀 (config.yaml)

대부분의 경우 **config.yaml만 수정하면 충분**합니다.

### DB 접속 정보

```yaml
database:
  host: "localhost"       # CUBRID 서버 주소
  port: 33000             # 포트 번호
  name: "demodb"          # 데이터베이스 이름
  user: "dba"             # 사용자
  password: ""            # 비밀번호 (없으면 빈 문자열)
```

### 테이블 설정

```yaml
table:
  name: "stress_test"         # 테스트용 테이블 이름
  recreate_on_start: true     # true: 매 테스트 시작 시 테이블 DROP 후 재생성
                              # false: 기존 테이블 유지 (데이터 누적)
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

---

## 코드 커스텀 가이드

테이블 구조 변경, 새 시나리오 추가 등 config.yaml만으로 부족한 경우 아래를 참고합니다.

### 1. 테이블 스키마 변경

`scenarios/locustfile.py`의 `_CREATE_TABLE_SQL`을 수정합니다.

**현재 스키마:**

```sql
CREATE TABLE IF NOT EXISTS stress_test (
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
CREATE TABLE IF NOT EXISTS stress_test (
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

새 시나리오를 추가하려면 `scenarios/locustfile.py`에 새 User 클래스를 추가합니다.

**예시 — 나이 범위 검색 시나리오:**

```python
class AgeRangeSearchUser(_CubridMixin, User):
    """나이 범위 검색 시나리오."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

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

> 새 User 클래스를 추가하면 `--class-picker` UI에 자동으로 체크박스가 추가됩니다.

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
| `config.yaml` | DB 접속, 부하 설정 | 설정값만 바꿀 때 |
| `scenarios/locustfile.py` | 테이블 DDL, 6개 시나리오 User 클래스 | 스키마·쿼리·시나리오 변경 시 |
| `data/generator.py` | 더미 데이터 구조 및 생성 로직 | 테이블 컬럼 변경 시 함께 수정 |
| `core/config.py` | config.yaml 파싱, 설정 접근자 | 새 설정 항목 추가 시 |
| `core/db_client.py` | CUBRID 연결, SQL 실행, Locust 리포팅 | SQL 실행 방식 변경 시 |

> **핵심 원칙**: 테이블 스키마를 변경하면 `locustfile.py`(DDL + 쿼리) → `generator.py`(더미 데이터) → `config.yaml` 세 파일을 함께 수정합니다.
> 새 시나리오를 추가하려면 `locustfile.py`에 User 클래스를 추가하면 `--class-picker` UI에 자동 반영됩니다.