"""
DB Monitor User — 부하를 주지 않고 DB/OS 상태만 관찰하는 시나리오

외부 애플리케이션이 부하를 줄 때, 이 시나리오만 선택하면
Locust 대시보드에서 DB 상태 변화를 실시간으로 모니터링할 수 있습니다.
"""

import re
import time

from locust import User, task, between, events

from core.config import get_config
from core.db_client import CubridClient
from core.metrics_store import get_metrics_store
from core.os_monitor import OsMonitor

_cfg = get_config()
_metrics = get_metrics_store()


class DBMonitorUser(User):
    """
    DB에 부하를 주지 않고 상태만 주기적으로 관찰합니다.

    OS 모니터링 자동 감지:
    - database.host가 localhost/127.0.0.1 → psutil(로컬) 사용
    - database.host가 원격 IP/호스트명 → SSH(paramiko) 사용

    관찰 항목:
    - 응답 시간 프로브 (SELECT 1건으로 DB 응답성 측정)
    - 테이블 행 수 변화 (외부 INSERT/DELETE 감지)
    - 활성 트랜잭션 수 (동시 접속 세션 수)
    - Lock 대기 건수 (Lock 경합 감지)
    - CPU 사용률 (로컬: psutil / 원격: SSH)
    - 메모리 사용률 (로컬: psutil / 원격: SSH)
    - 디스크 I/O (로컬: psutil / 원격: SSH)
    """

    # 모니터는 항상 1명만 실행 — 나머지 유저는 부하 시나리오에 투입
    fixed_count = 1

    # 모니터링은 1~2초 간격으로 (부하를 주지 않기 위해 느리게)
    wait_time = between(
        _cfg.raw.get("monitor", {}).get("interval_min", 1.0),
        _cfg.raw.get("monitor", {}).get("interval_max", 2.0),
    )

    def on_start(self):
        self.client = CubridClient()
        self.table = _cfg.table_name
        self._prev_row_count = None
        self._os_monitor = OsMonitor()
        self._multi_container = self._os_monitor.is_multi_container
        _metrics.mode = self._os_monitor.mode
        if self._multi_container:
            _metrics.set_container_labels(self._os_monitor.container_labels)
            print(f"[Monitor] 멀티 컨테이너 모드: {self._os_monitor.container_labels}")
        print(f"[Monitor] OS 모니터링 모드: {self._os_monitor.mode} (available={self._os_monitor.available})")

    def on_stop(self):
        self.client.close()
        self._os_monitor.close()

    @task(3)
    def probe_response_time(self):
        """응답 시간 프로브 — 가장 가벼운 SELECT로 DB 응답성을 측정합니다."""
        sql = f"SELECT 1 FROM {self.table} WHERE ROWNUM <= 1"
        start = time.perf_counter()
        self.client.execute("MONITOR", "[Monitor] response_probe", sql, fetch=True)
        elapsed_ms = (time.perf_counter() - start) * 1000
        _metrics.record("response_time_ms", round(elapsed_ms, 2))

    @task(2)
    def check_row_count(self):
        """행 수 추적 — 테이블의 현재 행 수를 조회합니다."""
        sql = f"SELECT COUNT(*) FROM {self.table}"
        start = time.perf_counter()
        cursor = self.client._conn.cursor()
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
            count = row[0] if row else 0
            elapsed_ms = (time.perf_counter() - start) * 1000

            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] row_count",
                response_time=elapsed_ms,
                response_length=count,
                exception=None,
            )
            _metrics.record("row_count", count)

            if self._prev_row_count is not None and count != self._prev_row_count:
                diff = count - self._prev_row_count
                sign = "+" if diff > 0 else ""
                print(f"[Monitor] 행 수 변화: {self._prev_row_count} → {count} ({sign}{diff})")
            self._prev_row_count = count
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] row_count",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )
        finally:
            cursor.close()

    @task(2)
    def check_active_transactions(self):
        """활성 트랜잭션 — cubrid tranlist로 현재 활성 트랜잭션 수를 조회합니다."""
        start = time.perf_counter()
        try:
            output = self._os_monitor.exec_in_db_container(
                f"cubrid tranlist -s {_cfg.db_name}@localhost"
            )
            # "  1(ACTIVE)", "  2(ACTIVE)" 등의 패턴을 카운트
            count = len(re.findall(r'\d+\s*\(ACTIVE\)', output))
            elapsed_ms = (time.perf_counter() - start) * 1000

            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] active_transactions",
                response_time=elapsed_ms,
                response_length=count,
                exception=None,
            )
            _metrics.record("active_transactions", count)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] active_transactions",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )

    @task(1)
    def check_lock_waiters(self):
        """Lock 대기 — cubrid lockdb로 Lock 대기 중인 트랜잭션 수를 조회합니다."""
        start = time.perf_counter()
        try:
            output = self._os_monitor.exec_in_db_container(
                f"cubrid lockdb {_cfg.db_name}@localhost"
            )
            # lockdb 출력에서 "Blocked_mode" 키워드로 실제 대기 건수 파악
            count = len(re.findall(r'Blocked_mode', output))
            elapsed_ms = (time.perf_counter() - start) * 1000

            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] lock_waiters",
                response_time=elapsed_ms,
                response_length=count,
                exception=None,
            )
            _metrics.record("lock_waiters", count)

            if count > 0:
                print(f"[Monitor] Lock 대기 감지: {count}건")
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] lock_waiters",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )

    # ------------------------------------------------------------------
    # OS 레벨 모니터링 (로컬: psutil / 원격: SSH / Docker 자동 감지)
    # ------------------------------------------------------------------
    @task(2)
    def check_cpu_memory(self):
        """CPU/메모리 사용률 — 멀티 컨테이너는 개별, 단일은 합산 측정."""
        if not self._os_monitor.available:
            return
        start = time.perf_counter()
        try:
            if self._multi_container:
                # 멀티 컨테이너: 한 번의 docker stats로 CPU/MEM 동시 조회
                stats = self._os_monitor.get_container_stats()
                elapsed_ms = (time.perf_counter() - start) * 1000

                for label, vals in stats.items():
                    cpu_val = round(vals["cpu"], 1)
                    mem_val = round(vals["mem"], 1)
                    _metrics.record(f"cpu_{label}", cpu_val)
                    _metrics.record(f"mem_{label}", mem_val)

                    events.request.fire(
                        request_type="MONITOR",
                        name=f"[Monitor] cpu_{label}",
                        response_time=elapsed_ms,
                        response_length=int(cpu_val),
                        exception=None,
                    )
                    events.request.fire(
                        request_type="MONITOR",
                        name=f"[Monitor] mem_{label}",
                        response_time=elapsed_ms,
                        response_length=int(mem_val),
                        exception=None,
                    )

                    if cpu_val > 90:
                        print(f"[Monitor] {label} CPU 과부하: {cpu_val:.1f}%")
                    if mem_val > 90:
                        print(f"[Monitor] {label} 메모리 과부하: {mem_val:.1f}%")
            else:
                # 단일 모드 (psutil / SSH / 단일 Docker)
                cpu_percent = self._os_monitor.get_cpu_percent()
                mem_percent = self._os_monitor.get_memory_percent()
                elapsed_ms = (time.perf_counter() - start) * 1000

                events.request.fire(
                    request_type="MONITOR",
                    name="[Monitor] cpu_percent",
                    response_time=elapsed_ms,
                    response_length=int(cpu_percent),
                    exception=None,
                )
                events.request.fire(
                    request_type="MONITOR",
                    name="[Monitor] memory_percent",
                    response_time=elapsed_ms,
                    response_length=int(mem_percent),
                    exception=None,
                )
                _metrics.record("cpu_percent", round(cpu_percent, 1))
                _metrics.record("memory_percent", round(mem_percent, 1))

                if cpu_percent > 90:
                    print(f"[Monitor] CPU 과부하: {cpu_percent:.1f}%")
                if mem_percent > 90:
                    print(f"[Monitor] 메모리 과부하: {mem_percent:.1f}%")
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] cpu_memory",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )

    @task(1)
    def check_disk_io(self):
        """디스크 I/O — DB 서버의 디스크 읽기/쓰기를 측정합니다."""
        if not self._os_monitor.available:
            return
        start = time.perf_counter()
        try:
            read_kb_s, write_kb_s = self._os_monitor.get_disk_io()
            elapsed_ms = (time.perf_counter() - start) * 1000

            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] disk_write_kb_s",
                response_time=elapsed_ms,
                response_length=write_kb_s,
                exception=None,
            )
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] disk_read_kb_s",
                response_time=elapsed_ms,
                response_length=read_kb_s,
                exception=None,
            )
            _metrics.record("disk_read_kb_s", read_kb_s)
            _metrics.record("disk_write_kb_s", write_kb_s)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] disk_write_kb_s",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )
