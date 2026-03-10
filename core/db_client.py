"""
CUBRID 커스텀 클라이언트 — Locust와 연동하여 요청 성공/실패/응답 시간을 기록합니다.
jaydebeapi (JDBC) 를 통해 CUBRID에 접속합니다.
"""

import time
import traceback

import jaydebeapi
from locust import events

from core.config import get_config


class CubridClient:
    """
    CUBRID 연결을 관리하고, 쿼리 실행 결과를 Locust 이벤트로 보고합니다.
    각 Locust User 인스턴스마다 하나의 CubridClient를 생성합니다.
    """

    def __init__(self):
        cfg = get_config()
        jdbc_url = f"jdbc:cubrid:{cfg.db_host}:{cfg.db_port}:{cfg.db_name}:::"
        self._conn = jaydebeapi.connect(
            "cubrid.jdbc.driver.CUBRIDDriver",
            jdbc_url,
            [cfg.db_user, cfg.db_password],
            cfg.jdbc_driver_path,
        )
        self._conn.jconn.setAutoCommit(True)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _report(self, request_type: str, name: str, start: float,
                exception: Exception | None = None, row_count: int = 0):
        """Locust 이벤트 시스템에 결과를 보고합니다."""
        elapsed_ms = (time.perf_counter() - start) * 1000
        if exception:
            events.request.fire(
                request_type=request_type,
                name=name,
                response_time=elapsed_ms,
                response_length=0,
                exception=exception,
            )
        else:
            events.request.fire(
                request_type=request_type,
                name=name,
                response_time=elapsed_ms,
                response_length=row_count,
                exception=None,
            )

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def execute(self, request_type: str, name: str, sql: str,
                params: tuple | None = None, fetch: bool = False):
        """
        SQL을 실행하고 결과를 Locust에 보고합니다.

        Args:
            request_type: Locust 대시보드에 표시될 요청 유형 (INSERT, SELECT 등)
            name: 요청 이름 (시나리오 식별자)
            sql: 실행할 SQL 문
            params: 바인드 변수 튜플
            fetch: True이면 fetchall 결과를 반환
        Returns:
            fetch=True일 때 결과 행 리스트, 아니면 None
        """
        cursor = self._conn.cursor()
        start = time.perf_counter()
        try:
            cursor.execute(sql, params or ())
            rows = None
            if fetch:
                rows = cursor.fetchall()
                self._report(request_type, name, start, row_count=len(rows))
            else:
                self._report(request_type, name, start, row_count=cursor.rowcount)
            return rows
        except Exception as e:
            self._report(request_type, name, start, exception=e)
            traceback.print_exc()
            return None
        finally:
            cursor.close()

    def execute_no_report(self, sql: str, params: tuple | None = None):
        """Locust에 보고하지 않고 SQL을 실행합니다 (테이블 생성 등 초기화용)."""
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params or ())
        finally:
            cursor.close()

    def close(self):
        """DB 연결을 종료합니다."""
        if self._conn:
            self._conn.close()
            self._conn = None
