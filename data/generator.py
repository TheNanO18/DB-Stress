"""
Faker 기반 한국어 더미 데이터 풀 생성기

테스트 시작 전에 data_pool.size만큼의 레코드를 메모리에 미리 생성(Pre-generation)하여
파이썬 CPU 병목을 방지합니다.
"""

import random
from dataclasses import dataclass

from faker import Faker

from core.config import get_config


@dataclass(slots=True)
class DummyRecord:
    """stress_test 테이블에 삽입할 한 건의 더미 레코드."""
    name: str
    email: str
    phone: str
    address: str
    company: str
    text: str          # 가변 길이 텍스트 (DB 부하 증가용)
    amount: float


class DataPool:
    """
    Faker로 미리 생성된 더미 레코드 풀.
    random 접근을 통해 각 Locust 유저가 고루 사용합니다.
    """

    def __init__(self):
        cfg = get_config()
        self._faker = Faker(cfg.pool_locale)
        self._pool: list[DummyRecord] = []
        self._size = cfg.pool_size
        self._generate()

    def _generate(self):
        """풀을 한 번에 생성합니다."""
        fake = self._faker
        pool = []
        for _ in range(self._size):
            record = DummyRecord(
                name=fake.name(),
                email=fake.email(),
                phone=fake.phone_number(),
                address=fake.address().replace("\n", " "),
                company=fake.company(),
                text=fake.text(max_nb_chars=random.randint(50, 500)),
                amount=round(random.uniform(1000, 9_999_999), 2),
            )
            pool.append(record)
        self._pool = pool
        print(f"[DataPool] {len(self._pool)}건의 더미 레코드 생성 완료")

    def pick(self) -> DummyRecord:
        """랜덤하게 하나의 레코드를 반환합니다."""
        return random.choice(self._pool)

    def pick_values(self) -> tuple:
        """INSERT SQL 바인드용 튜플을 반환합니다."""
        r = self.pick()
        return (r.name, r.email, r.phone, r.address, r.company, r.text, r.amount)

    @property
    def size(self) -> int:
        return len(self._pool)


# 모듈 레벨 싱글턴
_pool_instance: DataPool | None = None


def get_data_pool() -> DataPool:
    """싱글턴 DataPool 인스턴스를 반환합니다."""
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = DataPool()
    return _pool_instance
