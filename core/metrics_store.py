"""
모니터링 메트릭 저장소 — 실시간 차트용 시계열 데이터를 보관합니다.
"""

import threading
import time
from collections import deque


class MetricsStore:
    """스레드 안전한 시계열 메트릭 저장소. 동적 키를 지원합니다."""

    def __init__(self, max_points: int = 300):
        self._lock = threading.Lock()
        self._max = max_points
        self.mode = "none"  # OsMonitor 모드 (UI 표시용)
        self._container_labels = []  # Docker 컨테이너 라벨 목록
        self._data = {
            "timestamps": deque(maxlen=max_points),
            "cpu_percent": deque(maxlen=max_points),
            "memory_percent": deque(maxlen=max_points),
            "disk_read_kb_s": deque(maxlen=max_points),
            "disk_write_kb_s": deque(maxlen=max_points),
            "row_count": deque(maxlen=max_points),
            "active_transactions": deque(maxlen=max_points),
            "lock_waiters": deque(maxlen=max_points),
            "response_time_ms": deque(maxlen=max_points),
        }

    def set_container_labels(self, labels: list):
        """Docker 멀티 컨테이너 라벨을 설정하고 대응하는 키를 초기화합니다."""
        with self._lock:
            self._container_labels = labels
            for label in labels:
                cpu_key = f"cpu_{label}"
                mem_key = f"mem_{label}"
                if cpu_key not in self._data:
                    self._data[cpu_key] = deque(maxlen=self._max)
                    # 기존 타임스탬프 수만큼 0으로 채움
                    for _ in range(len(self._data["timestamps"])):
                        self._data[cpu_key].append(0)
                if mem_key not in self._data:
                    self._data[mem_key] = deque(maxlen=self._max)
                    for _ in range(len(self._data["timestamps"])):
                        self._data[mem_key].append(0)

    def record(self, metric: str, value):
        """단일 메트릭 값을 현재 시각과 함께 기록합니다."""
        with self._lock:
            # 동적 키 자동 생성
            if metric not in self._data:
                self._data[metric] = deque(maxlen=self._max)
                for _ in range(len(self._data["timestamps"])):
                    self._data[metric].append(0)

            now = time.time()
            # 같은 초에 이미 타임스탬프가 있으면 해당 포인트에 값만 업데이트
            if self._data["timestamps"] and abs(self._data["timestamps"][-1] - now) < 0.5:
                self._data[metric][-1] = value
            else:
                self._data["timestamps"].append(now)
                for key in self._data:
                    if key == "timestamps":
                        continue
                    if key == metric:
                        self._data[key].append(value)
                    else:
                        # 이전 값 유지 (마지막 값 복사)
                        prev = self._data[key][-1] if self._data[key] else 0
                        self._data[key].append(prev)

    def reset(self):
        """모든 시계열 데이터를 초기화합니다. 테스트 재시작 시 호출합니다."""
        with self._lock:
            for key in self._data:
                self._data[key].clear()
            self._container_labels = []
            self.mode = "none"

    def snapshot(self) -> dict:
        """현재까지의 모든 데이터를 JSON 직렬화 가능한 dict로 반환합니다."""
        with self._lock:
            result = {key: list(vals) for key, vals in self._data.items()}
            result["container_labels"] = self._container_labels
            return result


# 글로벌 싱글턴
_store = MetricsStore()


def get_metrics_store() -> MetricsStore:
    return _store
