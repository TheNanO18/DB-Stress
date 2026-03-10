"""
CUBRID 부하 테스트 시나리오 — 시나리오별 독립 User 클래스

실행 방법:
    cd cubrid_stress_tool
    locust -f scenarios/locustfile.py --class-picker

--class-picker 옵션을 사용하면 웹 대시보드에서
원하는 시나리오를 체크박스로 선택하여 실행할 수 있습니다.
"""

import random
import threading
import time

from locust import User, task, between, events

from core.config import get_config
from core.db_client import CubridClient
from data.generator import get_data_pool

# ---------------------------------------------------------------------------
# 전역 설정 로드
# ---------------------------------------------------------------------------
_cfg = get_config()

# ---------------------------------------------------------------------------
# 글로벌 Max ID 추적기 (스레드 안전)
# ---------------------------------------------------------------------------
class _MaxIdTracker:
    """INSERT/DELETE에 따라 현재 테이블의 대략적인 최대 ID를 추적합니다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._value = 0

    def set(self, value: int):
        with self._lock:
            self._value = value

    def increment(self):
        with self._lock:
            self._value += 1
            return self._value

    def get(self) -> int:
        with self._lock:
            return max(1, self._value)


_max_id = _MaxIdTracker()

# ---------------------------------------------------------------------------
# 테이블 초기화 (테스트 시작 시 한 번만 실행)
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_cfg.table_name} (
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
"""

_DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {_cfg.table_name}"


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Locust 테스트가 시작될 때 테이블 초기화, 시드 데이터 삽입, 데이터 풀 워밍업."""
    # 데이터 풀 미리 생성 (시드 삽입에서도 사용)
    pool = get_data_pool()
    print(f"[Init] 데이터 풀 준비 완료 (size={pool.size})")

    print("[Init] 테이블 초기화 시작...")
    client = CubridClient()
    if _cfg.recreate_on_start:
        client.execute_no_report(_DROP_TABLE_SQL)
    client.execute_no_report(_CREATE_TABLE_SQL)
    print("[Init] 테이블 준비 완료")

    # 시드 데이터 삽입
    seed_rows = _cfg.seed_rows
    if seed_rows > 0:
        print(f"[Init] 시드 데이터 {seed_rows}건 삽입 시작...")
        insert_sql = (
            f"INSERT INTO {_cfg.table_name} "
            f"(name, email, phone, address, company, text_col, amount) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        for i in range(seed_rows):
            vals = pool.pick_values()
            client.execute_no_report(insert_sql, vals)
            if (i + 1) % 1000 == 0:
                print(f"[Init]   ... {i + 1}/{seed_rows}건 완료")
        print(f"[Init] 시드 데이터 {seed_rows}건 삽입 완료")

    # 현재 MAX(id) 조회하여 추적기 초기화
    try:
        cursor = client._conn.cursor()
        cursor.execute(f"SELECT MAX(id) FROM {_cfg.table_name}")
        row = cursor.fetchone()
        cursor.close()
        current_max = row[0] if row and row[0] else 0
        _max_id.set(current_max)
        print(f"[Init] 현재 MAX(id) = {current_max}")
    except Exception:
        _max_id.set(seed_rows)

    client.close()

    # 실행 중인 시나리오 목록 출력
    runner = environment.runner
    if runner and runner.user_classes:
        names = [cls.__name__ for cls in runner.user_classes]
        print(f"[Init] 활성 시나리오: {', '.join(names)}")


# ===========================================================================
# 공통 Mixin — DB 연결/해제 및 유틸
# ===========================================================================
class _CubridMixin:
    """모든 시나리오 User가 공유하는 DB 연결 로직."""

    def _setup(self):
        self.client = CubridClient()
        self.pool = get_data_pool()
        self.table = _cfg.table_name

    def _teardown(self):
        self.client.close()

    @staticmethod
    def _get_max_id() -> int:
        """현재까지의 대략적인 최대 ID를 반환합니다."""
        return _max_id.get()


# ===========================================================================
# INSERT SQL 헬퍼 (중복 제거)
# ===========================================================================
_INSERT_SQL_TEMPLATE = (
    "INSERT INTO {} (name, email, phone, address, company, text_col, amount) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


# ===========================================================================
# 1. Bulk Insert — 대량 INSERT로 디스크 I/O · 인덱스 부하 유발
# ===========================================================================
class BulkInsertUser(_CubridMixin, User):
    """대량 INSERT로 디스크 I/O와 인덱스 부하를 유발합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def insert_row(self):
        """INSERT — 더미 데이터 1건 삽입."""
        vals = self.pool.pick_values()
        sql = _INSERT_SQL_TEMPLATE.format(self.table)
        self.client.execute("INSERT", "[BulkInsert] insert", sql, vals)
        _max_id.increment()


# ===========================================================================
# 2. Read Intensive — PK/인덱스 조회로 최대 TPS 측정
# ===========================================================================
class ReadIntensiveUser(_CubridMixin, User):
    """PK 기반 SELECT로 최대 TPS(처리량)를 측정합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def select_by_pk(self):
        """SELECT — PK 기반 단건 조회."""
        pk = random.randint(1, self._get_max_id())
        sql = f"SELECT * FROM {self.table} WHERE id = ?"
        self.client.execute("SELECT", "[ReadIntensive] select_pk", sql, (pk,), fetch=True)


# ===========================================================================
# 3. Lock Contention — 단일 Row 병목으로 Lock 대기 유발
# ===========================================================================
class LockContentionUser(_CubridMixin, User):
    """동일 행(ID 1~10)에 대한 동시 UPDATE로 Lock 경합을 유발합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()
        # Lock 경합 대상 행이 반드시 존재하도록 시드 데이터 삽입
        # (seed_rows로 이미 채워졌으면 추가 데이터가 될 뿐 무해)
        for i in range(10):
            try:
                vals = self.pool.pick_values()
                sql = _INSERT_SQL_TEMPLATE.format(self.table)
                self.client.execute_no_report(sql, vals)
                _max_id.increment()
            except Exception:
                pass

    def on_stop(self):
        self._teardown()

    @task
    def lock_contention(self):
        """LOCK — 동일 행에 대한 동시 UPDATE로 Lock 경합 유발."""
        pk = random.randint(1, 10)
        new_amount = round(random.uniform(1, 100), 2)
        sql = f"UPDATE {self.table} SET amount = ? WHERE id = ?"
        self.client.execute("UPDATE", "[LockContention] update_same_row", sql, (new_amount, pk))


# ===========================================================================
# 4. Heavy Query — 무거운 정렬/조인으로 CPU · 메모리 고갈
# ===========================================================================
class HeavyQueryUser(_CubridMixin, User):
    """풀스캔, 셀프 조인, 정렬 등 무거운 쿼리로 CPU/메모리를 소모합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task(3)
    def heavy_join(self):
        """HEAVY JOIN — 셀프 조인으로 의도적 CPU/메모리 부하."""
        sql = (
            f"SELECT a.id, b.name, a.amount "
            f"FROM {self.table} a, {self.table} b "
            f"WHERE a.amount > b.amount "
            f"AND ROWNUM <= 100"
        )
        self.client.execute("SELECT", "[HeavyQuery] self_join", sql, fetch=True)

    @task(2)
    def select_full_scan(self):
        """FULL SCAN — 인덱스를 타지 않는 LIKE 검색."""
        sql = f"SELECT * FROM {self.table} WHERE text_col LIKE '%테스트%'"
        self.client.execute("SELECT", "[HeavyQuery] full_scan", sql, fetch=True)

    @task(1)
    def heavy_sort(self):
        """HEAVY SORT — 대량 정렬로 메모리/디스크 부하."""
        sql = (
            f"SELECT * FROM {self.table} "
            f"ORDER BY text_col, amount DESC "
            f"LIMIT 1000"
        )
        self.client.execute("SELECT", "[HeavyQuery] heavy_sort", sql, fetch=True)


# ===========================================================================
# 5. Connection Churn — 연결 끊기/맺기 반복으로 Broker/Network 자원 소모
# ===========================================================================
class ConnectionChurnUser(User):
    """DB 연결을 반복적으로 생성/해제하여 Broker와 Network 자원을 소모합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self.table = _cfg.table_name
        self.pool = get_data_pool()

    @task
    def churn_connection(self):
        """CONNECT/DISCONNECT — 연결 생성 → 간단한 쿼리 → 연결 해제."""
        start = time.perf_counter()
        client = None
        try:
            client = CubridClient()
            pk = random.randint(1, _max_id.get())
            sql = f"SELECT id FROM {self.table} WHERE id = ?"
            client.execute("CONNECT", "[ConnChurn] connect_query_close", sql, (pk,), fetch=True)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="CONNECT",
                name="[ConnChurn] connect_query_close",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )
        finally:
            if client:
                client.close()


# ===========================================================================
# 6. CRUD Mix — INSERT / SELECT / UPDATE / DELETE 종합 테스트
# ===========================================================================
class CrudMixUser(_CubridMixin, User):
    """INSERT, SELECT, UPDATE, DELETE를 균등하게 실행하는 종합 CRUD 테스트."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task(3)
    def insert_row(self):
        """INSERT — 더미 데이터 1건 삽입."""
        vals = self.pool.pick_values()
        sql = _INSERT_SQL_TEMPLATE.format(self.table)
        self.client.execute("INSERT", "[CrudMix] insert", sql, vals)
        _max_id.increment()

    @task(3)
    def select_by_pk(self):
        """SELECT — PK 기반 단건 조회."""
        pk = random.randint(1, self._get_max_id())
        sql = f"SELECT * FROM {self.table} WHERE id = ?"
        self.client.execute("SELECT", "[CrudMix] select", sql, (pk,), fetch=True)

    @task(3)
    def update_row(self):
        """UPDATE — 랜덤 행의 amount 갱신."""
        pk = random.randint(1, self._get_max_id())
        new_amount = round(random.uniform(1000, 9_999_999), 2)
        sql = f"UPDATE {self.table} SET amount = ? WHERE id = ?"
        self.client.execute("UPDATE", "[CrudMix] update", sql, (new_amount, pk))

    @task(1)
    def delete_row(self):
        """DELETE — 랜덤 행 삭제."""
        pk = random.randint(1, self._get_max_id())
        sql = f"DELETE FROM {self.table} WHERE id = ?"
        self.client.execute("DELETE", "[CrudMix] delete", sql, (pk,))


# ===========================================================================
# 7. Create Only — INSERT 단독 테스트
# ===========================================================================
class CreateOnlyUser(_CubridMixin, User):
    """INSERT만 단독 실행하여 쓰기 성능을 측정합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def insert_row(self):
        """INSERT — 더미 데이터 1건 삽입."""
        vals = self.pool.pick_values()
        sql = _INSERT_SQL_TEMPLATE.format(self.table)
        self.client.execute("INSERT", "[CreateOnly] insert", sql, vals)
        _max_id.increment()


# ===========================================================================
# 8. Read Only — SELECT 단독 테스트
# ===========================================================================
class ReadOnlyUser(_CubridMixin, User):
    """SELECT만 단독 실행하여 읽기 성능을 측정합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def select_by_pk(self):
        """SELECT — PK 기반 단건 조회."""
        pk = random.randint(1, self._get_max_id())
        sql = f"SELECT * FROM {self.table} WHERE id = ?"
        self.client.execute("SELECT", "[ReadOnly] select", sql, (pk,), fetch=True)


# ===========================================================================
# 9. Update Only — UPDATE 단독 테스트
# ===========================================================================
class UpdateOnlyUser(_CubridMixin, User):
    """UPDATE만 단독 실행하여 갱신 성능을 측정합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def update_row(self):
        """UPDATE — 랜덤 행의 amount 갱신."""
        pk = random.randint(1, self._get_max_id())
        new_amount = round(random.uniform(1000, 9_999_999), 2)
        sql = f"UPDATE {self.table} SET amount = ? WHERE id = ?"
        self.client.execute("UPDATE", "[UpdateOnly] update", sql, (new_amount, pk))


# ===========================================================================
# 10. Delete Only — DELETE 단독 테스트
# ===========================================================================
class DeleteOnlyUser(_CubridMixin, User):
    """DELETE만 단독 실행하여 삭제 성능을 측정합니다."""

    wait_time = between(_cfg.wait_min, _cfg.wait_max)

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def delete_row(self):
        """DELETE — 랜덤 행 삭제."""
        pk = random.randint(1, self._get_max_id())
        sql = f"DELETE FROM {self.table} WHERE id = ?"
        self.client.execute("DELETE", "[DeleteOnly] delete", sql, (pk,))
