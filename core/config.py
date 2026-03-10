"""
config.yaml 파싱 및 설정 관리 클래스
"""

import os
import yaml


_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config.yaml"
)


class Config:
    """YAML 설정 파일을 로드하고 각 섹션에 대한 접근자를 제공합니다."""

    def __init__(self, path: str = _DEFAULT_CONFIG_PATH):
        with open(path, "r", encoding="utf-8") as f:
            self._data: dict = yaml.safe_load(f)

    # --- 원본 딕셔너리 접근 ---
    @property
    def raw(self) -> dict:
        return self._data

    # --- database 섹션 ---
    @property
    def db_host(self) -> str:
        return self._data["database"]["host"]

    @property
    def db_port(self) -> int:
        return self._data["database"]["port"]

    @property
    def db_name(self) -> str:
        return self._data["database"]["name"]

    @property
    def db_user(self) -> str:
        return self._data["database"]["user"]

    @property
    def db_password(self) -> str:
        return self._data["database"]["password"]

    @property
    def db_connection_string(self) -> str:
        """CUBRID 연결 문자열: CUBRID:<host>:<port>:<dbname>:::"""
        return f"CUBRID:{self.db_host}:{self.db_port}:{self.db_name}:::"

    @property
    def jdbc_driver_path(self) -> str:
        """CUBRID JDBC 드라이버 JAR 파일 경로."""
        return self._data["database"].get(
            "jdbc_driver", os.path.join(os.environ.get("CUBRID", ""), "jdbc", "cubrid_jdbc.jar")
        )

    # --- table 섹션 ---
    @property
    def table_name(self) -> str:
        return self._data["table"]["name"]

    @property
    def recreate_on_start(self) -> bool:
        return self._data["table"].get("recreate_on_start", True)

    @property
    def seed_rows(self) -> int:
        return self._data["table"].get("seed_rows", 0)

    # --- data_pool 섹션 ---
    @property
    def pool_size(self) -> int:
        return self._data["data_pool"]["size"]

    @property
    def pool_locale(self) -> str:
        return self._data["data_pool"]["locale"]

    # --- load_test 섹션 ---
    @property
    def users(self) -> int:
        return self._data["load_test"]["users"]

    @property
    def spawn_rate(self) -> int:
        return self._data["load_test"]["spawn_rate"]

    @property
    def run_time(self) -> int:
        return self._data["load_test"]["run_time"]

    @property
    def wait_min(self) -> float:
        return self._data["load_test"]["wait_min"]

    @property
    def wait_max(self) -> float:
        return self._data["load_test"]["wait_max"]

    # --- docker 섹션 ---
    @property
    def docker_enabled(self) -> bool:
        return self._data.get("docker", {}).get("enabled", False)

    @property
    def docker_container_name(self) -> str:
        """하위 호환: 단일 컨테이너 이름 (첫 번째 컨테이너)."""
        containers = self.docker_containers
        if containers:
            return containers[0]["name"]
        return self._data.get("docker", {}).get("container_name", "")

    @property
    def docker_containers(self) -> list:
        """Docker 컨테이너 목록. [{name: str, label: str}, ...]"""
        docker_cfg = self._data.get("docker", {})
        containers = docker_cfg.get("containers", [])
        if containers:
            return containers
        # 하위 호환: container_name 단일 값
        name = docker_cfg.get("container_name", "")
        if name:
            return [{"name": name, "label": name}]
        return []

    # --- ssh 섹션 (원격 OS 모니터링) ---
    @property
    def ssh_port(self) -> int:
        return self._data.get("ssh", {}).get("port", 22)

    @property
    def ssh_user(self) -> str:
        return self._data.get("ssh", {}).get("user", "root")

    @property
    def ssh_password(self) -> str:
        return self._data.get("ssh", {}).get("password", "")

    @property
    def ssh_key_file(self) -> str:
        return self._data.get("ssh", {}).get("key_file", "")

    @property
    def is_local_db(self) -> bool:
        """DB 서버가 로컬인지 판별합니다."""
        host = self.db_host.strip().lower()
        return host in ("localhost", "127.0.0.1", "::1", "")

    # --- scenario_weights 섹션 ---
    @property
    def scenario_weights(self) -> dict:
        return self._data.get("scenario_weights", {})


# 모듈 레벨 싱글턴 (필요 시)
_instance: Config | None = None


def get_config(path: str = _DEFAULT_CONFIG_PATH) -> Config:
    """싱글턴 Config 인스턴스를 반환합니다."""
    global _instance
    if _instance is None:
        _instance = Config(path)
    return _instance
