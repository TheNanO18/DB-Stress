"""
Locust 이벤트 훅 — CLI 파라미터 등록 + 테스트 시작 시 초기화

import 시점에 @events 데코레이터가 자동으로 리스너를 등록합니다.
"""

from locust import events

from core.config import get_config
from core.db_client import CubridClient
from data.generator import get_data_pool

from core.metrics_store import get_metrics_store

from ._shared import (
    max_id,
    CREATE_TABLE_SQL,
    DROP_TABLE_SQL,
    INSERT_SQL_TEMPLATE,
)

_cfg = get_config()
_metrics = get_metrics_store()


# ---------------------------------------------------------------------------
# 커스텀 파라미터 — 웹 UI의 "Advanced options"에 DB/SSH 접속 필드 추가
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
# 테스트 시작 시 초기화 (테이블 생성, 시드 삽입, 설정 오버라이드)
# ---------------------------------------------------------------------------
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Locust 테스트가 시작될 때 테이블 초기화, 시드 데이터 삽입, 데이터 풀 워밍업."""

    # 이전 테스트의 메트릭 데이터 초기화 (재시작 시 차트 오염 방지)
    _metrics.reset()
    max_id.set(0)
    print("[Init] 메트릭 저장소 초기화 완료")

    # ---------------------------------------------------------------
    # 웹 UI 파라미터로 DB 접속 정보 동적 오버라이드
    # ---------------------------------------------------------------
    opts = environment.parsed_options
    overrides = []

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
        client.execute_no_report(DROP_TABLE_SQL)
    client.execute_no_report(CREATE_TABLE_SQL)
    print("[Init] 테이블 준비 완료")

    # 시드 데이터 삽입
    seed_rows = _cfg.seed_rows
    if seed_rows > 0:
        print(f"[Init] 시드 데이터 {seed_rows}건 삽입 시작...")
        insert_sql = INSERT_SQL_TEMPLATE.format(_cfg.table_name)
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
        max_id.set(current_max)
        print(f"[Init] 현재 MAX(id) = {current_max}")
    except Exception:
        max_id.set(seed_rows)

    client.close()

    # 실행 중인 시나리오 목록 출력
    runner = environment.runner
    if runner and runner.user_classes:
        names = [cls.__name__ for cls in runner.user_classes]
        print(f"[Init] 활성 시나리오: {', '.join(names)}")


# ---------------------------------------------------------------------------
# 테스트 종료 시 정리
# ---------------------------------------------------------------------------
@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Locust 테스트가 종료될 때 글로벌 상태를 정리합니다."""
    print("[Stop] 테스트 종료 — 리소스 정리 중...")
    max_id.set(0)
    print("[Stop] 테스트 종료 완료. 새 테스트를 시작할 수 있습니다.")
