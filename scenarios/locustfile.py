"""
CUBRID 부하 테스트 시나리오 — Locust 진입점

실행 방법:
    cd cubrid_stress_tool

    # 로컬 전용 (본인 PC에서만 접속)
    locust -f scenarios/locustfile.py --class-picker

    # 외부 접속 허용 (같은 네트워크의 다른 PC·모바일에서 접속 가능)
    locust -f scenarios/locustfile.py --class-picker --web-host 0.0.0.0

--class-picker 옵션을 사용하면 웹 대시보드에서
원하는 시나리오를 체크박스로 선택하여 실행할 수 있습니다.

이 파일은 진입점 역할만 합니다.
실제 로직은 아래 모듈에 분리되어 있습니다:
  - _shared.py       : 공통 유틸 (MaxIdTracker, CubridMixin, SQL 상수)
  - _init_hooks.py   : CLI 파라미터 등록 + 테스트 시작 초기화
  - _web_routes.py   : 모니터링 대시보드 웹 라우트
  - stress_users.py  : 부하 시나리오 User 클래스 10개
  - monitor_user.py  : DB 모니터링 전용 User 클래스
"""

# 이벤트 훅 등록 (import 시점에 @events 데코레이터 자동 실행)
from scenarios._init_hooks import *   # noqa: F401,F403
from scenarios._web_routes import *   # noqa: F401,F403

# Locust User 클래스 노출 (Locust가 이 네임스페이스에서 User 서브클래스를 탐색)
from scenarios.stress_users import (  # noqa: F401
    BulkInsertUser,
    ReadIntensiveUser,
    LockContentionUser,
    HeavyQueryUser,
    ConnectionChurnUser,
    CrudMixUser,
    CreateOnlyUser,
    ReadOnlyUser,
    UpdateOnlyUser,
    DeleteOnlyUser,
)
from scenarios.monitor_user import DBMonitorUser  # noqa: F401
