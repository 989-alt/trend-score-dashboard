"""scheduler 모듈 테스트 — refresh_now(동기 산출·저장) · build_scheduler(잡 등록).

검증은 외부 API 없이 ``SampleProvider`` (sample 모드 기본설정) + 임시 SQLite ``Store``
로만 수행한다. 스케줄러는 트리거 등록만 검사하고 실제 실행(.start())은 하지 않는다.

핵심 불변식:
- ``refresh_now`` 가 스냅샷을 저장(store.load_snapshot 으로 확인)하고 동일 객체를 반환.
- ``refresh_now`` 의 ``generated_at`` 이 timezone-aware (Asia/Seoul).
- ``build_scheduler`` 가 prep(Cron) · intraday(Interval) 잡을 시장별로 등록한다.
- intraday 잡은 폐장·휴장 시 산출을 건너뛴다(저장 없음).
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from backend import market_hours
from backend import scheduler as sched
from backend.config import Settings
from backend.schemas import Market
from backend.store import Store

_SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture
def settings() -> Settings:
    """sample 모드 설정(키 불필요·결정론). ``.env`` 의 live 모드가 새지 않도록 명시 고정."""
    return Settings(data_mode="sample")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """임시 SQLite Store (테스트마다 격리)."""
    return Store(tmp_path / "scheduler_test.db")


# ── refresh_now ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("market", ["KR", "US"])
def test_refresh_now_saves_and_returns_snapshot(
    market: Market, store: Store, settings: Settings
) -> None:
    """refresh_now — 스냅샷을 저장하고 반환한다(반환값 == 저장값)."""
    snap = sched.refresh_now(market, store, settings)

    assert snap.market == market
    assert snap.entries  # SampleProvider 는 1개 이상 산출

    loaded = store.load_snapshot(market)
    assert loaded is not None
    assert loaded == snap  # 저장본과 반환본 일치


def test_refresh_now_generated_at_is_seoul_aware(store: Store, settings: Settings) -> None:
    """refresh_now — generated_at 이 timezone-aware (Asia/Seoul)."""
    snap = sched.refresh_now("KR", store, settings)

    assert snap.generated_at.tzinfo is not None
    assert snap.generated_at.utcoffset() == ZoneInfo("Asia/Seoul").utcoffset(
        snap.generated_at.replace(tzinfo=None)
    )


# ── build_scheduler: 잡 등록 ─────────────────────────────────────────────────


def test_build_scheduler_returns_unstarted(store: Store, settings: Settings) -> None:
    """build_scheduler — AsyncIOScheduler 를 미시작 상태로 반환."""
    scheduler = sched.build_scheduler(store, settings)

    assert isinstance(scheduler, AsyncIOScheduler)
    assert scheduler.running is False


def test_build_scheduler_registers_all_jobs(store: Store, settings: Settings) -> None:
    """build_scheduler — KR/US 각각 prep + intraday 잡(총 4개) 등록."""
    scheduler = sched.build_scheduler(store, settings)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"prep-KR", "prep-US", "intraday-KR", "intraday-US"}


@pytest.mark.parametrize("market", ["KR", "US"])
def test_build_scheduler_intraday_interval_trigger(
    market: Market, store: Store, settings: Settings
) -> None:
    """intraday 잡 — refresh_interval_min 주기의 IntervalTrigger."""
    scheduler = sched.build_scheduler(store, settings)

    job = scheduler.get_job(f"intraday-{market}")
    assert job is not None
    assert isinstance(job.trigger, IntervalTrigger)
    expected_seconds = settings.refresh_interval_min * 60
    assert job.trigger.interval.total_seconds() == expected_seconds


@pytest.mark.parametrize(
    ("market", "hour", "minute"),
    [("KR", "8", "30"), ("US", "22", "0")],
)
def test_build_scheduler_prep_cron_trigger(
    market: Market, hour: str, minute: str, store: Store, settings: Settings
) -> None:
    """prep 잡 — KR 08:30 / US 22:00 KST 의 CronTrigger."""
    scheduler = sched.build_scheduler(store, settings)

    job = scheduler.get_job(f"prep-{market}")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == hour
    assert fields["minute"] == minute
    assert str(job.trigger.timezone) == "Asia/Seoul"


# ── intraday 게이트: 장중에만 산출 ───────────────────────────────────────────


def test_intraday_skips_when_market_closed(
    store: Store, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """intraday 본체 — 폐장 시 산출·저장을 건너뛴다."""
    monkeypatch.setattr(market_hours, "is_market_open", lambda *_: False)

    sched._run_intraday("KR", store, settings)

    assert store.load_snapshot("KR") is None


def test_intraday_scores_when_market_open(
    store: Store, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """intraday 본체 — 장중이면 산출해 스냅샷을 저장한다."""
    monkeypatch.setattr(market_hours, "is_market_open", lambda *_: True)

    sched._run_intraday("KR", store, settings)

    snap = store.load_snapshot("KR")
    assert snap is not None
    assert snap.market == "KR"
