"""
공유 유틸리티 — MaxIdTracker, CubridMixin, SQL 상수

모든 시나리오 User 클래스가 공통으로 사용하는 요소를 모아둡니다.
"""

import threading

from locust import between

from core.config import get_config
from core.db_client import CubridClient
from data.generator import get_data_pool

_cfg = get_config()


# ---------------------------------------------------------------------------
# 글로벌 Max ID 추적기 (스레드 안전)
# ---------------------------------------------------------------------------
class MaxIdTracker:
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


max_id = MaxIdTracker()


# ---------------------------------------------------------------------------
# 테이블 DDL / DML 상수
# ---------------------------------------------------------------------------
TABLE_NAME = _cfg.table_name

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
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

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {TABLE_NAME}"

INSERT_SQL_TEMPLATE = (
    "INSERT INTO {} (name, email, phone, address, company, text_col, amount) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


# ---------------------------------------------------------------------------
# 공통 Mixin — DB 연결/해제 및 유틸
# ---------------------------------------------------------------------------
class CubridMixin:
    """모든 시나리오 User가 공유하는 DB 연결 로직."""

    def _setup(self):
        self.client = CubridClient()
        self.pool = get_data_pool()
        self.table = TABLE_NAME

    def _teardown(self):
        self.client.close()

    @staticmethod
    def _get_max_id() -> int:
        """현재까지의 대략적인 최대 ID를 반환합니다."""
        return max_id.get()


# ---------------------------------------------------------------------------
# 공통 wait_time
# ---------------------------------------------------------------------------
default_wait_time = between(_cfg.wait_min, _cfg.wait_max)
