"""
OS 레벨 모니터링 — Docker / 로컬(psutil) / 원격(SSH) 자동 감지

우선순위:
1. docker.enabled=true → docker stats 로 컨테이너 단위 CPU% 측정
2. database.host가 localhost → psutil로 호스트 전체 메트릭
3. database.host가 원격 → SSH로 원격 서버 메트릭
"""

import re
import subprocess
import time

from core.config import get_config

# psutil — 로컬 모니터링용 (선택적)
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# paramiko — 원격 모니터링용 (선택적)
try:
    import paramiko
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False


class OsMonitor:
    """
    Docker / 로컬 / 원격을 자동 감지하여 OS 메트릭을 수집하는 통합 모니터.

    사용법:
        monitor = OsMonitor()
        # 단일 모드
        cpu = monitor.get_cpu_percent()
        mem = monitor.get_memory_percent()
        # 멀티 컨테이너 모드
        stats = monitor.get_container_stats()
        # → {"Master": {"cpu": 12.3, "mem": 5.1}, "Slave": {"cpu": 3.2, "mem": 4.0}, ...}
        monitor.close()
    """

    def __init__(self):
        cfg = get_config()
        self._is_local = cfg.is_local_db
        self._ssh_client = None
        self._available = False
        self._mode = "none"
        self._db_name = cfg.db_name  # 특정 DB 인스턴스 필터링용

        # Docker 설정
        self._docker_enabled = cfg.docker_enabled
        self._docker_containers = cfg.docker_containers  # [{name, label}, ...]
        self._multi_container = len(self._docker_containers) > 1
        self._prev_block_io = None       # Docker BlockIO 누적값 (read_kb, write_kb)
        self._prev_block_io_time = 0.0   # 마지막 BlockIO 조회 시각

        print(f"[OsMonitor] DB host='{cfg.db_host}', is_local={self._is_local}, "
              f"docker={self._docker_enabled}, containers={len(self._docker_containers)}, "
              f"psutil={_HAS_PSUTIL}, paramiko={_HAS_PARAMIKO}")

        if self._docker_enabled and self._docker_containers:
            labels = [c["label"] for c in self._docker_containers]
            if self._is_local:
                self._init_docker_local()
            else:
                if _HAS_PARAMIKO:
                    self._connect_ssh(cfg)
                    if self._ssh_client:
                        self._mode = f"docker(SSH:{cfg.db_host}:{','.join(labels)})"
                        print(f"[OsMonitor] 원격 Docker 모드 — SSH로 컨테이너 {labels} 모니터링")
                else:
                    print(f"[OsMonitor] 원격 Docker 모드이나 paramiko 미설치 — OS 모니터링 비활성")
        elif self._is_local:
            if _HAS_PSUTIL:
                self._available = True
                self._mode = "local(psutil)"
                print(f"[OsMonitor] 로컬 모드 — psutil로 OS 메트릭 수집")
            else:
                print(f"[OsMonitor] 로컬 모드이나 psutil 미설치 — OS 모니터링 비활성")
        else:
            # 원격 모드: SSH로만 OS 메트릭 수집 가능
            if _HAS_PARAMIKO:
                self._connect_ssh(cfg)
            if not self._available:
                print(f"[OsMonitor] 원격 DB({cfg.db_host})의 OS 모니터링에는 SSH 접속이 필요합니다")
                print(f"[OsMonitor] config.yaml의 ssh 섹션에 password 또는 key_file을 설정하세요")
                print(f"[OsMonitor] OS 모니터링 비활성 (DB 응답시간·트랜잭션·행 수는 정상 수집됩니다)")

    def _init_docker_local(self):
        """로컬 Docker 컨테이너 모니터링을 초기화합니다."""
        try:
            # 첫 번째 컨테이너로 Docker 접근 가능 여부 확인
            first = self._docker_containers[0]["name"]
            result = subprocess.run(
                f"docker ps --filter name={first} --format '{{{{.Names}}}}'",
                shell=True, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                self._available = True
                labels = [c["label"] for c in self._docker_containers]
                self._mode = f"docker(local:{','.join(labels)})"
                print(f"[OsMonitor] 로컬 Docker 모드 — 컨테이너 {labels} 모니터링")
            else:
                print(f"[OsMonitor] 컨테이너 '{first}'를 찾을 수 없습니다")
                if _HAS_PSUTIL:
                    self._available = True
                    self._docker_enabled = False
                    self._mode = "local(psutil)"
                    print(f"[OsMonitor] psutil 폴백으로 호스트 전체 메트릭 수집")
        except FileNotFoundError:
            print(f"[OsMonitor] docker 명령어를 찾을 수 없습니다 — Docker가 설치되어 있는지 확인하세요")
            if _HAS_PSUTIL:
                self._available = True
                self._docker_enabled = False
                self._mode = "local(psutil)"
                print(f"[OsMonitor] psutil 폴백으로 호스트 전체 메트릭 수집")
        except Exception as e:
            print(f"[OsMonitor] Docker 초기화 실패: {e}")
            if _HAS_PSUTIL:
                self._available = True
                self._docker_enabled = False
                self._mode = "local(psutil)"

    def _connect_ssh(self, cfg):
        """SSH 연결을 수립합니다."""
        try:
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": cfg.db_host,
                "port": cfg.ssh_port,
                "username": cfg.ssh_user,
                "timeout": 10,
            }

            if cfg.ssh_key_file:
                connect_kwargs["key_filename"] = cfg.ssh_key_file
            elif cfg.ssh_password:
                connect_kwargs["password"] = cfg.ssh_password
            else:
                print("[OsMonitor] SSH 인증 정보 없음 — config.yaml의 ssh.password 또는 ssh.key_file을 설정하세요")
                self._ssh_client = None
                return

            self._ssh_client.connect(**connect_kwargs)
            self._available = True
            if not self._docker_enabled:
                self._mode = f"remote(SSH:{cfg.db_host})"
                print(f"[OsMonitor] 원격 모드 — SSH({cfg.db_host}:{cfg.ssh_port})로 OS 메트릭 수집")
        except Exception as e:
            print(f"[OsMonitor] SSH 연결 실패: {e}")
            print(f"[OsMonitor] config.yaml의 ssh 섹션을 확인하세요")
            self._ssh_client = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_multi_container(self) -> bool:
        """멀티 컨테이너 모드인지 여부."""
        return self._docker_enabled and self._multi_container

    @property
    def container_labels(self) -> list:
        """설정된 컨테이너 라벨 목록."""
        return [c["label"] for c in self._docker_containers]

    # ------------------------------------------------------------------
    # 멀티 컨테이너 통합 조회 (Docker 전용)
    # ------------------------------------------------------------------
    def get_container_stats(self) -> dict:
        """
        모든 Docker 컨테이너의 CPU/메모리를 한 번에 조회합니다.

        Returns:
            {label: {"cpu": float, "mem": float}, ...}
            예: {"Master": {"cpu": 12.3, "mem": 5.1}, "Slave": {"cpu": 3.2, "mem": 4.0}}
        """
        if not self._available or not self._docker_enabled:
            return {}

        try:
            # docker stats --no-stream 으로 전체 컨테이너 CPU/MEM을 한 번에 조회
            cmd = "docker stats --no-stream --format '{{.Name}}\\t{{.CPUPerc}}\\t{{.MemPerc}}'"
            if self._is_local:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10,
                )
                output = result.stdout.strip()
            else:
                output = self._exec_ssh(cmd).strip()

            # 출력 파싱: 각 줄 → "container_name\tCPU%\tMEM%"
            stats_map = {}
            for line in output.split('\n'):
                line = line.strip().strip("'\"")
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) >= 3:
                    container_name = parts[0]
                    cpu_str = parts[1].strip().strip("'\"")
                    mem_str = parts[2].strip().strip("'\"")

                    cpu_match = re.search(r'([\d.]+)%', cpu_str)
                    mem_match = re.search(r'([\d.]+)%', mem_str)

                    cpu_val = float(cpu_match.group(1)) if cpu_match else 0.0
                    mem_val = float(mem_match.group(1)) if mem_match else 0.0

                    stats_map[container_name] = {"cpu": cpu_val, "mem": mem_val}

            # 설정된 컨테이너 이름(접두사)으로 매칭하여 라벨 부여
            result_dict = {}
            for container_cfg in self._docker_containers:
                prefix = container_cfg["name"]
                label = container_cfg["label"]
                for full_name, vals in stats_map.items():
                    if full_name.startswith(prefix):
                        result_dict[label] = vals
                        break
                else:
                    result_dict[label] = {"cpu": 0.0, "mem": 0.0}

            return result_dict
        except Exception as e:
            print(f"[OsMonitor] Docker stats 조회 실패: {e}")
            return {c["label"]: {"cpu": 0.0, "mem": 0.0} for c in self._docker_containers}

    # ------------------------------------------------------------------
    # CPU (단일 모드용)
    # ------------------------------------------------------------------
    def get_cpu_percent(self) -> float:
        """CUBRID 프로세스의 CPU 사용률(%)을 반환합니다."""
        if not self._available:
            return 0.0
        if self._docker_enabled:
            # 단일 컨테이너 모드일 때만 사용
            stats = self.get_container_stats()
            if stats:
                return list(stats.values())[0].get("cpu", 0.0)
            return 0.0
        if self._is_local:
            return self._local_cubrid_cpu()
        return self._ssh_cpu_percent()

    def _local_cubrid_cpu(self) -> float:
        """psutil로 로컬 CUBRID 프로세스의 CPU 사용률을 측정합니다."""
        try:
            db = self._db_name.lower()
            total = 0.0
            for proc in psutil.process_iter(['name', 'cmdline', 'cpu_percent']):
                name = (proc.info['name'] or '').lower()
                if not name.startswith('cub_'):
                    continue
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()
                if db in cmdline:
                    total += proc.info['cpu_percent'] or 0.0
            return round(total, 1)
        except Exception:
            return 0.0

    def _ssh_cpu_percent(self) -> float:
        """SSH로 원격 서버의 특정 CUBRID DB 프로세스 CPU 사용률을 조회합니다."""
        try:
            db = self._db_name
            output = self._exec_ssh(
                f"ps -eo %cpu,args --no-headers | grep 'cub_' | grep '{db}' | awk '{{sum+=$1}} END {{printf \"%.1f\", sum}}'"
            )
            val = output.strip()
            if val:
                return float(val)
            return 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Memory (단일 모드용)
    # ------------------------------------------------------------------
    def get_memory_percent(self) -> float:
        """CUBRID 프로세스의 메모리 사용률(%)을 반환합니다."""
        if not self._available:
            return 0.0
        if self._docker_enabled:
            stats = self.get_container_stats()
            if stats:
                return list(stats.values())[0].get("mem", 0.0)
            return 0.0
        if self._is_local:
            return self._local_cubrid_memory()
        return self._ssh_memory_percent()

    def _local_cubrid_memory(self) -> float:
        """psutil로 로컬 CUBRID 프로세스의 메모리 사용률을 측정합니다."""
        try:
            db = self._db_name.lower()
            total = 0.0
            for proc in psutil.process_iter(['name', 'cmdline', 'memory_percent']):
                name = (proc.info['name'] or '').lower()
                if not name.startswith('cub_'):
                    continue
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()
                if db in cmdline:
                    total += proc.info['memory_percent'] or 0.0
            return round(total, 1)
        except Exception:
            return 0.0

    def _ssh_memory_percent(self) -> float:
        """SSH로 원격 서버의 특정 CUBRID DB 프로세스 메모리 사용률(%)을 조회합니다."""
        try:
            db = self._db_name
            output = self._exec_ssh(
                f"ps -eo %mem,args --no-headers | grep 'cub_' | grep '{db}' | awk '{{sum+=$1}} END {{printf \"%.1f\", sum}}'"
            )
            val = output.strip()
            if val:
                return float(val)
            return 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------
    def get_disk_io(self) -> tuple:
        """(read_kb_s, write_kb_s) 튜플을 반환합니다."""
        if not self._available:
            return (0, 0)
        if self._docker_enabled:
            return self._docker_disk_io()
        if self._is_local:
            return self._local_disk_io()
        if self._ssh_client:
            return self._ssh_disk_io()
        return (0, 0)

    def _docker_disk_io(self) -> tuple:
        """Docker 컨테이너의 BlockIO로 디스크 I/O rate(KB/s)를 계산합니다."""
        try:
            cmd = "docker stats --no-stream --format '{{.Name}}\\t{{.BlockIO}}'"
            if self._is_local:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10,
                )
                output = result.stdout.strip()
            else:
                output = self._exec_ssh(cmd).strip()

            total_read_kb = 0.0
            total_write_kb = 0.0
            for line in output.split('\n'):
                line = line.strip().strip("'\"")
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) < 2:
                    continue
                container_name = parts[0].strip("'\"")
                matched = any(
                    container_name.startswith(c["name"])
                    for c in self._docker_containers
                )
                if not matched:
                    continue
                bio = parts[1].strip().strip("'\"")
                bio_parts = bio.split('/')
                if len(bio_parts) == 2:
                    total_read_kb += self._parse_block_io_kb(bio_parts[0])
                    total_write_kb += self._parse_block_io_kb(bio_parts[1])

            now = time.time()
            if self._prev_block_io is not None:
                dt = now - self._prev_block_io_time
                if dt > 0:
                    read_kb_s = int((total_read_kb - self._prev_block_io[0]) / dt)
                    write_kb_s = int((total_write_kb - self._prev_block_io[1]) / dt)
                else:
                    read_kb_s, write_kb_s = 0, 0
            else:
                read_kb_s, write_kb_s = 0, 0

            self._prev_block_io = (total_read_kb, total_write_kb)
            self._prev_block_io_time = now
            return (max(0, read_kb_s), max(0, write_kb_s))
        except Exception as e:
            print(f"[OsMonitor] Docker disk I/O 조회 실패: {e}")
            return (0, 0)

    @staticmethod
    def _parse_block_io_kb(s: str) -> float:
        """Docker stats BlockIO 문자열 (예: '1.23MB')을 KB로 변환합니다."""
        s = s.strip().strip("'\"")
        m = re.match(r'([\d.]+)\s*(\w+)', s)
        if not m:
            return 0.0
        val = float(m.group(1))
        unit = m.group(2).upper()
        if unit == 'B':
            return val / 1024
        if unit in ('KB', 'KIB'):
            return val
        if unit in ('MB', 'MIB'):
            return val * 1024
        if unit in ('GB', 'GIB'):
            return val * 1024 * 1024
        return val

    def _local_disk_io(self) -> tuple:
        """psutil로 로컬 디스크 I/O를 측정합니다."""
        io1 = psutil.disk_io_counters()
        time.sleep(0.1)
        io2 = psutil.disk_io_counters()
        read_kb_s = int((io2.read_bytes - io1.read_bytes) / 1024 * 10)
        write_kb_s = int((io2.write_bytes - io1.write_bytes) / 1024 * 10)
        return (read_kb_s, write_kb_s)

    def _ssh_disk_io(self) -> tuple:
        """SSH로 원격 서버 디스크 I/O를 측정합니다."""
        try:
            output = self._exec_ssh("iostat -dk 1 2 2>/dev/null | tail -n +7")
            total_read = 0
            total_write = 0
            for line in output.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 4 and not parts[0].startswith('Device'):
                    try:
                        total_read += int(float(parts[2]))
                        total_write += int(float(parts[3]))
                    except (ValueError, IndexError):
                        pass
            return (total_read, total_write)
        except Exception:
            return (0, 0)

    # ------------------------------------------------------------------
    # SSH 실행 헬퍼
    # ------------------------------------------------------------------
    def _exec_ssh(self, command: str) -> str:
        """SSH로 명령어를 실행하고 stdout을 반환합니다."""
        if not self._ssh_client:
            return ""
        _, stdout, _ = self._ssh_client.exec_command(command, timeout=10)
        return stdout.read().decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # DB 컨테이너 명령 실행 (cubrid tranlist 등)
    # ------------------------------------------------------------------
    def _resolve_container_id(self, prefix: str) -> str:
        """Docker 컨테이너 이름 접두사로 컨테이너 ID를 조회합니다."""
        find_cmd = f"docker ps -q --filter name={prefix}"
        if self._is_local:
            result = subprocess.run(
                find_cmd, shell=True, capture_output=True, text=True, timeout=5,
            )
            output = result.stdout.strip()
        elif self._ssh_client:
            output = self._exec_ssh(find_cmd).strip()
        else:
            return ""
        # 첫 번째 컨테이너 ID 반환
        return output.split('\n')[0].strip() if output else ""

    def exec_in_db_container(self, cmd: str) -> str:
        """DB 서버 컨텍스트에서 명령어를 실행합니다 (Docker/SSH/로컬 자동 감지)."""
        try:
            if self._docker_enabled and self._docker_containers:
                prefix = self._docker_containers[0]["name"]
                container_id = self._resolve_container_id(prefix)
                if not container_id:
                    print(f"[OsMonitor] 컨테이너 '{prefix}'를 찾을 수 없습니다")
                    return ""
                full_cmd = f'docker exec {container_id} bash -l -c "{cmd}"'
            else:
                full_cmd = cmd

            if self._is_local:
                result = subprocess.run(
                    full_cmd, shell=True, capture_output=True, text=True, timeout=15,
                )
                return result.stdout
            elif self._ssh_client:
                return self._exec_ssh(full_cmd)
            return ""
        except Exception as e:
            print(f"[OsMonitor] 명령 실행 실패: {e}")
            return ""

    # ------------------------------------------------------------------
    # 정리
    # ------------------------------------------------------------------
    def close(self):
        """SSH 연결을 종료합니다."""
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception:
                pass
            self._ssh_client = None
            self._available = False
