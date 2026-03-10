"""
CUBRID 부하 테스트 시나리오 — 시나리오별 독립 User 클래스

실행 방법:
    cd cubrid_stress_tool
    locust -f scenarios/locustfile.py --class-picker

--class-picker 옵션을 사용하면 웹 대시보드에서
원하는 시나리오를 체크박스로 선택하여 실행할 수 있습니다.
"""

import json
import os
import random
import threading
import time

from locust import User, task, between, events

from core.config import get_config
from core.db_client import CubridClient
from core.metrics_store import get_metrics_store
from core.os_monitor import OsMonitor
from data.generator import get_data_pool

# ---------------------------------------------------------------------------
# 전역 설정 로드
# ---------------------------------------------------------------------------
_cfg = get_config()


# ---------------------------------------------------------------------------
# 커스텀 파라미터 — 웹 UI의 "Advanced options"에 DB 접속 필드 추가
# ---------------------------------------------------------------------------
@events.init_command_line_parser.add_listener
def on_init_parser(parser, **kwargs):
    parser.add_argument(
        "--db-host", type=str, default="",
        env_var="CUBRID_HOST",
        help="DB 호스트 (비워두면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--db-port", type=int, default=0,
        env_var="CUBRID_PORT",
        help="DB 포트 (0이면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--db-name", type=str, default="",
        env_var="CUBRID_DB",
        help="DB 이름 (비워두면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--db-user", type=str, default="",
        env_var="CUBRID_USER",
        help="DB 사용자 (비워두면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--db-password", type=str, default="",
        env_var="CUBRID_PASSWORD",
        help="DB 비밀번호 (비워두면 config.yaml 값 사용)",
    )
    # SSH 원격 모니터링 파라미터
    parser.add_argument(
        "--ssh-user", type=str, default="",
        env_var="SSH_USER",
        help="SSH 사용자 (비워두면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--ssh-password", type=str, default="",
        env_var="SSH_PASSWORD",
        help="SSH 비밀번호 (비워두면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--ssh-port", type=int, default=0,
        env_var="SSH_PORT",
        help="SSH 포트 (0이면 config.yaml 값 사용)",
    )
    parser.add_argument(
        "--ssh-key-file", type=str, default="",
        env_var="SSH_KEY_FILE",
        help="SSH 키 파일 경로 (비워두면 config.yaml 값 사용)",
    )


# ---------------------------------------------------------------------------
# 모니터링 대시보드 웹 라우트 등록 (/monitor, /monitor/data)
# ---------------------------------------------------------------------------
_metrics = get_metrics_store()


@events.init.add_listener
def on_init(environment, **kwargs):
    """Locust 웹 서버에 모니터링 대시보드 라우트를 추가합니다."""
    if environment.web_ui is None:
        return

    from flask import Response

    _template_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "templates"
    )

    @environment.web_ui.app.route("/monitor")
    def monitor_page():
        html_path = os.path.join(_template_dir, "monitor.html")
        with open(html_path, "r", encoding="utf-8") as f:
            return Response(f.read(), content_type="text/html; charset=utf-8")

    @environment.web_ui.app.route("/monitor/data")
    def monitor_data():
        data = _metrics.snapshot()
        data["mode"] = _metrics.mode
        return Response(
            json.dumps(data), content_type="application/json"
        )

    # Locust 메인 페이지에 Monitor 링크 배너를 주입하는 미들웨어
    _original_app = environment.web_ui.app

    @_original_app.after_request
    def inject_monitor_link(response):
        if response.content_type and "text/html" in response.content_type:
            inject_html = (
                '<div id="monitor-banner" style="'
                'position:fixed;top:0;left:0;right:0;z-index:9999;'
                'background:#16213e;border-bottom:2px solid #00d4aa;'
                'padding:6px 16px;text-align:center;font-size:13px;'
                'font-family:Segoe UI,sans-serif;">'
                '<a href="/monitor" style="color:#00d4aa;text-decoration:none;font-weight:bold;">'
                '&#128202; CUBRID Stress Monitor Dashboard &rarr;'
                '</a></div>'
                '<script>document.body.style.paddingTop="36px";</script>'
            )
            data = response.get_data(as_text=True)
            if '/monitor' not in data and '<body' in data.lower():
                data = data.replace('</body>', inject_html + '</body>')
                response.set_data(data)
        return response

    print("[Init] 모니터링 대시보드: http://localhost:8089/monitor")


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

    # ---------------------------------------------------------------
    # 웹 UI 파라미터로 DB 접속 정보 동적 오버라이드
    # Advanced options 또는 Host 필드에 입력한 값이 config.yaml 값을 덮어씁니다.
    # 비워두면 config.yaml 기본값을 그대로 사용합니다.
    # ---------------------------------------------------------------
    opts = environment.parsed_options
    overrides = []

    # Advanced options 필드 (--db-host, --db-port, --db-name, --db-user, --db-password)
    if getattr(opts, "db_host", "") and opts.db_host:
        _cfg._data["database"]["host"] = opts.db_host
        overrides.append(f"host={opts.db_host}")
    if getattr(opts, "db_port", 0) and opts.db_port:
        _cfg._data["database"]["port"] = opts.db_port
        overrides.append(f"port={opts.db_port}")
    if getattr(opts, "db_name", "") and opts.db_name:
        _cfg._data["database"]["name"] = opts.db_name
        overrides.append(f"name={opts.db_name}")
    if getattr(opts, "db_user", "") and opts.db_user:
        _cfg._data["database"]["user"] = opts.db_user
        overrides.append(f"user={opts.db_user}")
    if getattr(opts, "db_password", "") and opts.db_password:
        _cfg._data["database"]["password"] = opts.db_password
        overrides.append("password=***")

    # Host 필드 폴백 (Advanced options에 db-host를 안 넣었을 때)
    host_input = (environment.host or "").strip()
    if host_input and "host" not in [o.split("=")[0] for o in overrides]:
        host_input = host_input.replace("http://", "").replace("https://", "").rstrip("/")
        if ":" in host_input:
            parts = host_input.rsplit(":", 1)
            _cfg._data["database"]["host"] = parts[0]
            overrides.append(f"host={parts[0]}")
            try:
                port_val = int(parts[1])
                _cfg._data["database"]["port"] = port_val
                overrides.append(f"port={port_val}")
            except ValueError:
                pass
        else:
            _cfg._data["database"]["host"] = host_input
            overrides.append(f"host={host_input}")

    # SSH 접속 정보 오버라이드
    ssh_overrides = []
    if getattr(opts, "ssh_user", "") and opts.ssh_user:
        _cfg._data.setdefault("ssh", {})["user"] = opts.ssh_user
        ssh_overrides.append(f"user={opts.ssh_user}")
    if getattr(opts, "ssh_password", "") and opts.ssh_password:
        _cfg._data.setdefault("ssh", {})["password"] = opts.ssh_password
        ssh_overrides.append("password=***")
    if getattr(opts, "ssh_port", 0) and opts.ssh_port:
        _cfg._data.setdefault("ssh", {})["port"] = opts.ssh_port
        ssh_overrides.append(f"port={opts.ssh_port}")
    if getattr(opts, "ssh_key_file", "") and opts.ssh_key_file:
        _cfg._data.setdefault("ssh", {})["key_file"] = opts.ssh_key_file
        ssh_overrides.append(f"key_file={opts.ssh_key_file}")

    if ssh_overrides:
        print(f"[Init] SSH 접속 정보 오버라이드: {', '.join(ssh_overrides)}")

    if overrides:
        print(f"[Init] DB 접속 정보 오버라이드: {', '.join(overrides)}")

    if not _cfg.is_local_db:
        if _cfg.ssh_password or _cfg.ssh_key_file:
            print(f"[Init] 원격 DB 감지 → SSH({_cfg.ssh_user}@{_cfg.db_host}:{_cfg.ssh_port})로 OS 모니터링")
        else:
            print(f"[Init] 원격 DB 감지 — SSH 인증 미설정 → OS 모니터링 비활성 (config.yaml 또는 Advanced options에서 SSH 정보를 입력하세요)")
    else:
        print(f"[Init] 로컬 DB 감지 → psutil로 OS 모니터링")

    print(f"[Init] DB 접속: {_cfg.db_host}:{_cfg.db_port}/{_cfg.db_name} (user={_cfg.db_user})")

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


# ===========================================================================
# 11. DB Monitor — 외부 부하 관찰용 (부하를 주지 않고 DB 상태만 모니터링)
# ===========================================================================
class DBMonitorUser(User):
    """
    DB에 부하를 주지 않고 상태만 주기적으로 관찰합니다.

    외부 애플리케이션이 JDBC로 부하를 줄 때, 이 시나리오만 선택하면
    Locust 대시보드에서 DB 상태 변화를 실시간으로 모니터링할 수 있습니다.

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
        _metrics.mode = self._os_monitor.mode
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
        """활성 트랜잭션 — 현재 활성 트랜잭션(세션) 수를 조회합니다."""
        sql = "SELECT COUNT(*) FROM db_tran_lock"
        start = time.perf_counter()
        cursor = self.client._conn.cursor()
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
            count = row[0] if row else 0
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
        finally:
            cursor.close()

    @task(1)
    def check_lock_waiters(self):
        """Lock 대기 — Lock 대기 중인 트랜잭션 수를 조회합니다."""
        sql = "SELECT COUNT(*) FROM db_tran_lock WHERE is_blocked = 1"
        start = time.perf_counter()
        cursor = self.client._conn.cursor()
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
            count = row[0] if row else 0
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
        finally:
            cursor.close()

    # ------------------------------------------------------------------
    # OS 레벨 모니터링 (로컬: psutil / 원격: SSH / Docker 자동 감지)
    # ------------------------------------------------------------------
    @task(2)
    def check_cpu_usage(self):
        """CPU 사용률 — DB 서버의 CPU 사용률을 측정합니다."""
        if not self._os_monitor.available:
            return
        start = time.perf_counter()
        try:
            cpu_percent = self._os_monitor.get_cpu_percent()
            elapsed_ms = (time.perf_counter() - start) * 1000

            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] cpu_percent",
                response_time=elapsed_ms,
                response_length=int(cpu_percent),
                exception=None,
            )
            _metrics.record("cpu_percent", round(cpu_percent, 1))

            if cpu_percent > 90:
                print(f"[Monitor] CPU 과부하: {cpu_percent:.1f}%")
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] cpu_percent",
                response_time=elapsed_ms,
                response_length=0,
                exception=e,
            )

    @task(2)
    def check_memory_usage(self):
        """메모리 사용률 — DB 서버의 메모리 사용률을 측정합니다."""
        if not self._os_monitor.available:
            return
        start = time.perf_counter()
        try:
            mem_percent = self._os_monitor.get_memory_percent()
            elapsed_ms = (time.perf_counter() - start) * 1000

            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] memory_percent",
                response_time=elapsed_ms,
                response_length=int(mem_percent),
                exception=None,
            )
            _metrics.record("memory_percent", round(mem_percent, 1))

            if mem_percent > 90:
                print(f"[Monitor] 메모리 과부하: {mem_percent:.1f}%")
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="MONITOR",
                name="[Monitor] memory_percent",
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
