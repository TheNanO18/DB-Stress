"""
부하 시나리오 User 클래스 모음 (10개)

각 클래스는 독립적인 Locust User로, --class-picker 옵션을 통해
웹 대시보드에서 원하는 시나리오를 선택할 수 있습니다.
"""

import random
import time

from locust import User, task, events

from core.config import get_config
from core.db_client import CubridClient
from data.generator import get_data_pool

from ._shared import (
    CubridMixin,
    max_id,
    INSERT_SQL_TEMPLATE,
    default_wait_time,
)

_cfg = get_config()


# ===========================================================================
# 1. Bulk Insert — 대량 INSERT로 디스크 I/O · 인덱스 부하 유발
# ===========================================================================
class BulkInsertUser(CubridMixin, User):
    """대량 INSERT로 디스크 I/O와 인덱스 부하를 유발합니다."""

    wait_time = default_wait_time

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def insert_row(self):
        """INSERT — 더미 데이터 1건 삽입."""
        vals = self.pool.pick_values()
        sql = INSERT_SQL_TEMPLATE.format(self.table)
        self.client.execute("INSERT", "[BulkInsert] insert", sql, vals)
        max_id.increment()


# ===========================================================================
# 2. Read Intensive — PK/인덱스 조회로 최대 TPS 측정
# ===========================================================================
class ReadIntensiveUser(CubridMixin, User):
    """PK 기반 SELECT로 최대 TPS(처리량)를 측정합니다."""

    wait_time = default_wait_time

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
class LockContentionUser(CubridMixin, User):
    """동일 행(ID 1~10)에 대한 동시 UPDATE로 Lock 경합을 유발합니다."""

    wait_time = default_wait_time

    def on_start(self):
        self._setup()
        for i in range(10):
            try:
                vals = self.pool.pick_values()
                sql = INSERT_SQL_TEMPLATE.format(self.table)
                self.client.execute_no_report(sql, vals)
                max_id.increment()
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
class HeavyQueryUser(CubridMixin, User):
    """풀스캔, 셀프 조인, 정렬 등 무거운 쿼리로 CPU/메모리를 소모합니다."""

    wait_time = default_wait_time

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

    wait_time = default_wait_time

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
            pk = random.randint(1, max_id.get())
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
class CrudMixUser(CubridMixin, User):
    """INSERT, SELECT, UPDATE, DELETE를 균등하게 실행하는 종합 CRUD 테스트."""

    wait_time = default_wait_time

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task(3)
    def insert_row(self):
        """INSERT — 더미 데이터 1건 삽입."""
        vals = self.pool.pick_values()
        sql = INSERT_SQL_TEMPLATE.format(self.table)
        self.client.execute("INSERT", "[CrudMix] insert", sql, vals)
        max_id.increment()

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
class CreateOnlyUser(CubridMixin, User):
    """INSERT만 단독 실행하여 쓰기 성능을 측정합니다."""

    wait_time = default_wait_time

    def on_start(self):
        self._setup()

    def on_stop(self):
        self._teardown()

    @task
    def insert_row(self):
        """INSERT — 더미 데이터 1건 삽입."""
        vals = self.pool.pick_values()
        sql = INSERT_SQL_TEMPLATE.format(self.table)
        self.client.execute("INSERT", "[CreateOnly] insert", sql, vals)
        max_id.increment()


# ===========================================================================
# 8. Read Only — SELECT 단독 테스트
# ===========================================================================
class ReadOnlyUser(CubridMixin, User):
    """SELECT만 단독 실행하여 읽기 성능을 측정합니다."""

    wait_time = default_wait_time

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
class UpdateOnlyUser(CubridMixin, User):
    """UPDATE만 단독 실행하여 갱신 성능을 측정합니다."""

    wait_time = default_wait_time

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
class DeleteOnlyUser(CubridMixin, User):
    """DELETE만 단독 실행하여 삭제 성능을 측정합니다."""

    wait_time = default_wait_time

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
