# 뉴스 수집·아카이브 백엔드 Implementation Plan (플랜 A · 검증판)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans 또는 subagent-driven-development 로 태스크별 실행. 체크박스(`- [ ]`)로 추적.
> **검증판(2026-06-18):** 실제 telethon/google-genai API·repo 컨벤션(ruff select·mypy strict·asyncio_mode=auto·`.env` 키 누출)을 실증해 반영함. 아래 "검증된 제약"을 반드시 지킨다.

**Goal:** 텔레그램 4채널 + FactSet Insight를 주기 수집해 SQLite 원문 아카이브에 적재하고, 토요일 1회 Gemini로 한국어 주간요약을 생성하는 **백엔드 수집 계층**. (시황 탭 UI·이슈 휴리스틱은 플랜 B.)

**Architecture:** `backend/news/` 하위 모듈. `NewsStore`(SQLite, 기존 `Store` 패턴) ← `collector`(Telethon catch-up)·`factset`(HTML scrape)·`summary`(Gemini). AsyncIOScheduler에 5분 폴링 + 토요일 잡. **라이브 스코어러·점수 무수정.**

**Tech Stack:** Python 3.13, telethon 1.44, google-genai 2.8, httpx, pydantic-settings, APScheduler(AsyncIOScheduler), pytest(+asyncio_mode=auto).

## Global Constraints (검증된 제약 — 위반 시 DoD 실패)

- **라이브 무수정** — `backend/scoring.py`·`engine.py`·`schemas.py` 점수 산출 불변. 뉴스는 점수에 반영 안 함(스펙 §1).
- **ruff select = `E,F,I,UP,B,N,SIM,RUF`** (tests 포함, line-length 100). 결과:
  - `# noqa: BLE001`/`ANN001` 등 **미선택 규칙 noqa 금지**(RUF100). `except Exception:` 는 noqa 없이 OK.
  - `getattr(obj, "literal")` **금지**(B009) → `obj.attr` 직접접근(덕타이핑 객체는 `Any` 로 타입).
  - `typing.Callable` 금지(UP035) → **`from collections.abc import Callable`**.
  - import 정렬(I): `__future__` → 표준 → 공백 → `backend.*`(알파벳).
- **mypy strict**(backend만, tests 제외) — 모든 def 타입주석. 무타입 라이브러리는 **overrides 에 추가**(telethon/google). `Any` 반환을 typed 함수에서 직접 `return` 금지(`warn_return_any`) → 지역변수 경유.
- **테스트 `.env` 누출 차단** — repo `.env` 에 `APP_API_ID`/`APP_API_HASH`/`GEMINI_API_KEY` 가 **실제로 존재**. 테스트는 반드시 `Settings(_env_file=None, ...)  # type: ignore[call-arg]` 로 생성(아니면 실키가 새어 "키 없음" 테스트가 깨짐). 패턴 출처: `tests/test_scoring.py:50`.
- **async 테스트** — `asyncio_mode="auto"` 라 `@pytest.mark.asyncio` **불필요**. 그냥 `async def test_...`.
- **시크릿 커밋 금지** — `.env`·`data/telethon.session`·`data/news.db` 는 gitignore(이미 설정). 절대 커밋 안 함.
- **fail-open** — 키/세션/네트워크 부재·예외 시 앱을 죽이지 않고 조용히 스킵(로그만).
- **DoD(커밋 전 매 태스크)** — `uv run pytest -q` + `uv run ruff check` + `uv run ruff format --check` + `uv run mypy backend/` 모두 통과.
- **대상 채널**(고정): `FastStockNews`, `goodnews_honey`, `getfeed`, `jusikbiso`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `pyproject.toml` (수정) | mypy overrides 에 `telethon.*`·`google.*` 추가 (deps telethon·google-genai 는 이미 추가됨) |
| `backend/config.py` (수정) | Settings 뉴스 필드·채널·경로 |
| `backend/news/__init__.py` (생성) | 패키지 마커 |
| `backend/news/models.py` (생성) | `RawNewsItem`·`FactsetArticle`·`WeeklySummary` (frozen dataclass) |
| `backend/news/store.py` (생성) | `NewsStore` — 4테이블 + 전체 CRUD |
| `backend/news/collector.py` (생성) | 메시지→Item(순수) + telethon catch-up(시드 분리) |
| `backend/news/factset.py` (생성) | insight.factset.com 파싱·수집(fail-open) |
| `backend/news/summary.py` (생성) | Gemini 주간 한국어 요약(fail-open) |
| `backend/scheduler.py` (수정) | 5분/토요일 잡 (news_store 명시주입 시에만 등록) |
| `backend/app.py` (수정) | lifespan NewsStore 초기화·주입·`state.news_store` |
| `tests/test_news_*.py` (생성) | 모듈별 단위테스트 |
| `tests/fixtures/factset_listing.html` (생성) | FactSet 파서 픽스처 |

---

### Task 1: Settings·의존성·mypy overrides

**Files:** Modify `pyproject.toml`, `backend/config.py` · Test `tests/test_news_config.py`

**Interfaces (Produces):** `Settings.{app_api_id,app_api_hash,gemini_api_key,gemini_model,news_db_path,telethon_session_path,news_channels}`, `Settings.news_channel_list -> list[str]`.

- [ ] **Step 1: mypy overrides 확장**

`pyproject.toml` 의 mypy overrides module 리스트에 `telethon.*`·`google.*` 추가:
```toml
[[tool.mypy.overrides]]
module = ["pykrx.*", "yfinance.*", "apscheduler.*", "pandas.*", "telethon.*", "google.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: 실패테스트 작성** — `tests/test_news_config.py`

```python
from __future__ import annotations

from backend.config import Settings


def test_news_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.news_channel_list == ["FastStockNews", "goodnews_honey", "getfeed", "jusikbiso"]
    assert s.news_db_path.name == "news.db"
    assert s.app_api_id == ""  # .env 미로드 → 기본 빈값


def test_news_channel_list_strips_and_filters() -> None:
    s = Settings(_env_file=None, news_channels=" a , b ,, c ")  # type: ignore[call-arg]
    assert s.news_channel_list == ["a", "b", "c"]
```

- [ ] **Step 3: 실패확인** — `uv run pytest tests/test_news_config.py -q` → FAIL(AttributeError/ValidationError)

- [ ] **Step 4: Settings 필드 추가** — `backend/config.py`, `# ── 테마 ──` 블록 **앞**:

```python
    # ── 뉴스 수집 (시황 탭) ────────────────────────────────────────────
    #: my.telegram.org App api_id / api_hash (시크릿 — .env, 커밋 금지).
    app_api_id: str = ""
    app_api_hash: str = ""
    #: Gemini 주간요약(주 1회·읽기전용). 키 없으면 스킵(fail-open).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    #: 뉴스 아카이브 DB(대시보드 DB와 분리). gitignore.
    news_db_path: Path = DATA_DIR / "news.db"
    #: Telethon 세션 베이스(.session 자동부착). 시크릿 — gitignore.
    telethon_session_path: Path = DATA_DIR / "telethon"
    #: 수집 대상 텔레그램 채널(쉼표구분, @ 없이 username).
    news_channels: str = "FastStockNews,goodnews_honey,getfeed,jusikbiso"
```

`cors_origin_list` property **아래**:
```python
    @property
    def news_channel_list(self) -> list[str]:
        """뉴스 채널 문자열 → 공백·빈값 제거 리스트."""
        return [c.strip() for c in self.news_channels.split(",") if c.strip()]
```

- [ ] **Step 5: 통과확인** — `uv run pytest tests/test_news_config.py -q` → PASS(2)

- [ ] **Step 6: DoD + commit**

```bash
uv run ruff check && uv run mypy backend/
git add pyproject.toml backend/config.py tests/test_news_config.py
git commit -m "feat(news): Settings 뉴스 필드 + mypy overrides(telethon/google)"
```

---

### Task 2: models + NewsStore (전체 CRUD)

**Files:** Create `backend/news/__init__.py`, `backend/news/models.py`, `backend/news/store.py` · Test `tests/test_news_store.py`

**Interfaces (Produces):**
- `RawNewsItem(source:str, channel:str, msg_id:int, ts_utc:datetime, text:str, urls:tuple[str,...])` frozen; `.ts_kst->datetime`, `.dedup_hash->str`.
- `FactsetArticle(url:str, title:str, published_at:datetime, excerpt:str)` frozen.
- `WeeklySummary(week_start:date, kr_markdown:str, model:str, generated_at:datetime)` frozen.
- `NewsStore(db_path:Path)`: `insert_raw(item)->bool`, `recent_raw(since)->list[RawNewsItem]`, `get_cursor(ch)->int`, `set_cursor(ch,last_id)->None`, `upsert_factset(art)->bool`, `recent_factset(since)->list[FactsetArticle]`, `save_weekly(s)->None`, `latest_weekly()->WeeklySummary|None`.

- [ ] **Step 1: 실패테스트** — `tests/test_news_store.py`

```python
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend.news.models import FactsetArticle, RawNewsItem, WeeklySummary
from backend.news.store import NewsStore


def _item(channel: str, msg_id: int, *, text: str = "급락 속보", mins: int = 0) -> RawNewsItem:
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=timezone.utc) + timedelta(minutes=mins)
    return RawNewsItem(source="telegram", channel=channel, msg_id=msg_id, ts_utc=ts,
                       text=text, urls=())


def test_insert_raw_dedup(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    assert store.insert_raw(_item("ch", 1)) is True
    assert store.insert_raw(_item("ch", 1)) is False
    assert store.insert_raw(_item("ch", 2)) is True


def test_cursor_roundtrip(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    assert store.get_cursor("ch") == 0
    store.set_cursor("ch", 42)
    assert store.get_cursor("ch") == 42
    store.set_cursor("ch", 99)
    assert store.get_cursor("ch") == 99


def test_recent_raw_window_and_kst(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    store.insert_raw(_item("ch", 1, mins=0))
    store.insert_raw(_item("ch", 2, mins=120))
    rows = store.recent_raw(datetime(2026, 6, 18, 4, 0, tzinfo=timezone.utc))
    assert [r.msg_id for r in rows] == [2]
    assert rows[0].ts_kst.utcoffset() == timedelta(hours=9)


def test_factset_upsert_and_recent(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    art = FactsetArticle(url="https://x/a", title="T", excerpt="e",
                         published_at=datetime(2026, 6, 16, tzinfo=timezone.utc))
    assert store.upsert_factset(art) is True
    assert store.upsert_factset(art) is False  # 기존 갱신
    got = store.recent_factset(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert len(got) == 1 and got[0].title == "T"


def test_weekly_save_latest(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    assert store.latest_weekly() is None
    store.save_weekly(WeeklySummary(week_start=date(2026, 6, 8), kr_markdown="A",
                                    model="m", generated_at=datetime(2026, 6, 13, tzinfo=timezone.utc)))
    store.save_weekly(WeeklySummary(week_start=date(2026, 6, 15), kr_markdown="B",
                                    model="m", generated_at=datetime(2026, 6, 20, tzinfo=timezone.utc)))
    latest = store.latest_weekly()
    assert latest is not None and latest.kr_markdown == "B"  # week_start 최신
```

- [ ] **Step 2: 실패확인** — `uv run pytest tests/test_news_store.py -q` → FAIL(ModuleNotFoundError)

- [ ] **Step 3: 패키지·models** — `backend/news/__init__.py`:
```python
"""뉴스 수집·아카이브 모듈 (시황 탭 백엔드). 라이브 스코어러와 분리·매매 무영향."""
```

`backend/news/models.py`:
```python
"""뉴스 수집 데이터 모델 (순수·불변). 저장/표시 계약의 단일 출처."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class RawNewsItem:
    """수집된 원문 1건(텔레그램 메시지 등). 가공 전 원시 데이터."""

    source: str
    channel: str
    msg_id: int
    ts_utc: datetime
    text: str
    urls: tuple[str, ...]

    @property
    def ts_kst(self) -> datetime:
        """KST 표시용 시각."""
        return self.ts_utc.astimezone(_KST)

    @property
    def dedup_hash(self) -> str:
        """교차채널 동일뉴스 탐지용 해시(채널·id·본문). SHA-256 hex."""
        raw = f"{self.channel}|{self.msg_id}|{self.text}".encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class FactsetArticle:
    """FactSet Insight 글 1건."""

    url: str
    title: str
    published_at: datetime
    excerpt: str


@dataclass(frozen=True)
class WeeklySummary:
    """Gemini 주간 한국어 요약 1건."""

    week_start: date
    kr_markdown: str
    model: str
    generated_at: datetime
```

- [ ] **Step 4: NewsStore** — `backend/news/store.py`:
```python
"""뉴스 아카이브 영속 (SQLite). 기존 backend/store.py 패턴(연결+Lock, 멱등 스키마).

테이블: news_raw(원문)·news_state(폴링 커서)·news_factset·weekly_summary.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from backend.news.models import FactsetArticle, RawNewsItem, WeeklySummary

_NEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_raw (
    source     TEXT NOT NULL,
    channel    TEXT NOT NULL,
    msg_id     INTEGER NOT NULL,
    ts_utc     TEXT NOT NULL,
    text       TEXT NOT NULL,
    urls       TEXT NOT NULL,
    dedup_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, channel, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_news_raw_ts ON news_raw (ts_utc);

CREATE TABLE IF NOT EXISTS news_state (
    channel      TEXT PRIMARY KEY,
    last_seen_id INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_factset (
    url          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    published_at TEXT NOT NULL,
    excerpt      TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_summary (
    week_start   TEXT PRIMARY KEY,
    kr_markdown  TEXT NOT NULL,
    model        TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class NewsStore:
    """뉴스 아카이브 SQLite 계층. 스레드 안전(check_same_thread=False + Lock)."""

    def __init__(self, db_path: Path) -> None:
        """``db_path`` 에 연결하고 4테이블을 생성한다(없으면)."""
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_NEWS_SCHEMA)
        self._conn.commit()

    # ── 원문(raw) ──────────────────────────────────────────────────────
    def insert_raw(self, item: RawNewsItem) -> bool:
        """원문 1건 저장. 신규면 True, (source,channel,msg_id) 중복이면 False."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO news_raw "
                "(source, channel, msg_id, ts_utc, text, urls, dedup_hash, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item.source, item.channel, item.msg_id, item.ts_utc.isoformat(),
                 item.text, "\n".join(item.urls), item.dedup_hash, _now_iso()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def recent_raw(self, since: datetime) -> list[RawNewsItem]:
        """``since``(포함) 이후 원문을 시각 오름차순으로."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, channel, msg_id, ts_utc, text, urls FROM news_raw "
                "WHERE ts_utc >= ? ORDER BY ts_utc ASC",
                (since.isoformat(),),
            ).fetchall()
        out: list[RawNewsItem] = []
        for source, channel, msg_id, ts_utc, text, urls in rows:
            out.append(RawNewsItem(
                source=source, channel=channel, msg_id=int(msg_id),
                ts_utc=datetime.fromisoformat(ts_utc), text=text,
                urls=tuple(u for u in urls.split("\n") if u)))
        return out

    # ── 폴링 커서 ──────────────────────────────────────────────────────
    def get_cursor(self, channel: str) -> int:
        """``channel`` 의 마지막 본 msg_id. 없으면 0."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seen_id FROM news_state WHERE channel = ?", (channel,)
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def set_cursor(self, channel: str, last_id: int) -> None:
        """``channel`` 커서를 ``last_id`` 로 갱신(upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO news_state (channel, last_seen_id, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(channel) DO UPDATE SET "
                "last_seen_id = excluded.last_seen_id, updated_at = excluded.updated_at",
                (channel, last_id, _now_iso()),
            )
            self._conn.commit()

    # ── FactSet ────────────────────────────────────────────────────────
    def upsert_factset(self, art: FactsetArticle) -> bool:
        """FactSet 글 저장(url upsert). 신규면 True, 기존 갱신이면 False."""
        with self._lock:
            existed = self._conn.execute(
                "SELECT 1 FROM news_factset WHERE url = ?", (art.url,)
            ).fetchone() is not None
            self._conn.execute(
                "INSERT INTO news_factset (url, title, published_at, excerpt, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
                "title = excluded.title, excerpt = excluded.excerpt",
                (art.url, art.title, art.published_at.isoformat(), art.excerpt, _now_iso()),
            )
            self._conn.commit()
            return not existed

    def recent_factset(self, since: datetime) -> list[FactsetArticle]:
        """``since`` 이후 발행 FactSet 글(발행 내림차순)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT url, title, published_at, excerpt FROM news_factset "
                "WHERE published_at >= ? ORDER BY published_at DESC",
                (since.isoformat(),),
            ).fetchall()
        return [FactsetArticle(url=u, title=t, published_at=datetime.fromisoformat(p),
                               excerpt=e) for u, t, p, e in rows]

    # ── 주간 요약 ──────────────────────────────────────────────────────
    def save_weekly(self, summary: WeeklySummary) -> None:
        """주간 요약 저장(week_start upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO weekly_summary (week_start, kr_markdown, model, generated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(week_start) DO UPDATE SET "
                "kr_markdown = excluded.kr_markdown, model = excluded.model, "
                "generated_at = excluded.generated_at",
                (summary.week_start.isoformat(), summary.kr_markdown, summary.model,
                 summary.generated_at.isoformat()),
            )
            self._conn.commit()

    def latest_weekly(self) -> WeeklySummary | None:
        """가장 최근(week_start 기준) 주간 요약. 없으면 None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT week_start, kr_markdown, model, generated_at FROM weekly_summary "
                "ORDER BY week_start DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return WeeklySummary(week_start=date.fromisoformat(row[0]), kr_markdown=row[1],
                             model=row[2], generated_at=datetime.fromisoformat(row[3]))


__all__ = ["NewsStore"]
```

- [ ] **Step 5: 통과확인** — `uv run pytest tests/test_news_store.py -q` → PASS(5)

- [ ] **Step 6: DoD + commit**
```bash
uv run ruff check && uv run mypy backend/
git add backend/news/__init__.py backend/news/models.py backend/news/store.py tests/test_news_store.py
git commit -m "feat(news): RawNewsItem/FactsetArticle/WeeklySummary + NewsStore(4테이블 CRUD)"
```

---

### Task 3: collector (순수 + telethon 어댑터·시드 분리)

**Files:** Create `backend/news/collector.py` · Test `tests/test_news_collector.py`

**Interfaces (Produces):**
- `extract_urls(text:str)->tuple[str,...]`, `message_to_item(channel:str, msg:Any)->RawNewsItem|None`, `store_items(store, items)->int`.
- `async collect_channel(store, client:Any, channel:str)->int` — 커서 0이면 최신 시드(SEED_LIMIT), 아니면 catch-up(min_id+reverse).
- `async collect_once(store, settings, *, client:Any|None=None)->dict[str,int]` — 미주입이면 세션·키로 telethon 접속(fail-open).

> **검증된 telethon API:** `get_messages(entity, limit=None, *, min_id=0, reverse=False, ...)`. **first-run(cursor=0)에 min_id=0+reverse=True 면 과거부터 역주입**되는 함정 → cursor==0 은 `limit=SEED_LIMIT`(최신 N)로 시드하고 커서를 최신 id로 올린다.

- [ ] **Step 1: 실패테스트** — `tests/test_news_collector.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.config import Settings
from backend.news.collector import (
    collect_channel, collect_once, extract_urls, message_to_item, store_items,
)
from backend.news.store import NewsStore


@dataclass
class FakeMsg:
    id: int
    date: datetime
    message: str


class FakeClient:
    """telethon get_messages 흉내. reverse=False=최신우선(시드), reverse=True=오래된우선(catch-up)."""

    def __init__(self, data: dict[str, list[FakeMsg]]) -> None:
        self._data = data

    async def get_messages(self, channel: str, limit: int | None = None, *,
                           min_id: int = 0, reverse: bool = False) -> list[FakeMsg]:
        msgs = [m for m in self._data.get(channel, []) if m.id > min_id]
        msgs.sort(key=lambda m: m.id, reverse=not reverse)
        return msgs[:limit] if limit is not None else msgs


def _msgs(n: int) -> list[FakeMsg]:
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=timezone.utc)
    return [FakeMsg(id=i, date=ts, message=f"뉴스{i}") for i in range(1, n + 1)]


def test_extract_urls() -> None:
    assert extract_urls("속보 https://a.com/x 그리고 http://b.kr 끝") == (
        "https://a.com/x", "http://b.kr")
    assert extract_urls("링크 없음") == ()


def test_message_to_item_maps_and_skips_empty() -> None:
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=timezone.utc)
    item = message_to_item("getfeed", FakeMsg(id=7, date=ts, message="삼성전자 급락 https://x.io"))
    assert item is not None
    assert (item.channel, item.msg_id, item.source) == ("getfeed", 7, "telegram")
    assert item.urls == ("https://x.io",)
    assert message_to_item("getfeed", FakeMsg(id=8, date=ts, message="   ")) is None


def test_store_items_counts_new(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=timezone.utc)
    items = [message_to_item("c", FakeMsg(id=i, date=ts, message=f"뉴스{i}")) for i in (1, 2)]
    items = [i for i in items if i is not None]
    assert store_items(store, items) == 2
    assert store_items(store, items) == 0


async def test_collect_channel_seed_then_catchup(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    client = FakeClient({"c": _msgs(3)})
    assert await collect_channel(store, client, "c") == 3  # 시드(최신 3)
    assert store.get_cursor("c") == 3
    assert await collect_channel(store, client, "c") == 0  # 새 메시지 없음
    client._data["c"] = _msgs(5)                            # 4,5 추가
    assert await collect_channel(store, client, "c") == 2   # catch-up 4,5
    assert store.get_cursor("c") == 5


async def test_collect_once_no_keys_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert await collect_once(store, s) == {}


async def test_collect_once_injected_client(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None, news_channels="a,b")  # type: ignore[call-arg]
    client = FakeClient({"a": _msgs(2), "b": _msgs(1)})
    assert await collect_once(store, s, client=client) == {"a": 2, "b": 1}
```

- [ ] **Step 2: 실패확인** — `uv run pytest tests/test_news_collector.py -q` → FAIL

- [ ] **Step 3: 구현** — `backend/news/collector.py`:
```python
"""텔레그램 수집기 — 메시지→RawNewsItem(순수) + catch-up 폴링(telethon 어댑터).

순수 함수는 네트워크 없이 테스트한다. cursor==0(첫 폴링)은 최신 SEED_LIMIT 건만 시드해
과거 전체 역주입을 피하고, 이후는 min_id 초과분만 catch-up 한다(누락 0, 텔레그램 히스토리 보존).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.news.models import RawNewsItem
from backend.news.store import NewsStore

_log = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://[^\s]+")

#: 첫 폴링(커서 없음) 때 시드할 최신 메시지 수.
SEED_LIMIT = 30
#: catch-up 1회 최대 건수(폭주 방지; 초과분은 다음 폴링이 흡수).
BATCH_LIMIT = 200


def extract_urls(text: str) -> tuple[str, ...]:
    """본문에서 http(s) URL 을 등장순으로 추출."""
    return tuple(str(u) for u in _URL_RE.findall(text))


def message_to_item(channel: str, msg: Any) -> RawNewsItem | None:
    """telethon Message(또는 .id/.date/.message 페이크) → RawNewsItem. 본문 없으면 None."""
    text = str(msg.message or "").strip()
    if not text:
        return None
    return RawNewsItem(source="telegram", channel=channel, msg_id=int(msg.id),
                       ts_utc=msg.date, text=text, urls=extract_urls(text))


def store_items(store: NewsStore, items: list[RawNewsItem]) -> int:
    """``items`` 저장 후 신규 건수 반환(중복 제외)."""
    return sum(1 for it in items if store.insert_raw(it))


async def collect_channel(store: NewsStore, client: Any, channel: str) -> int:
    """한 채널 수집: 첫 폴링이면 최신 시드, 아니면 catch-up. 신규 건수 반환·커서 전진."""
    min_id = store.get_cursor(channel)
    if min_id == 0:
        messages: list[Any] = await client.get_messages(channel, limit=SEED_LIMIT)
    else:
        messages = await client.get_messages(channel, min_id=min_id, reverse=True,
                                              limit=BATCH_LIMIT)
    items: list[RawNewsItem] = []
    max_id = min_id
    for msg in messages:
        item = message_to_item(channel, msg)
        if item is not None:
            items.append(item)
        if int(msg.id) > max_id:
            max_id = int(msg.id)
    new = store_items(store, items)
    if max_id > min_id:
        store.set_cursor(channel, max_id)
    return new


async def collect_once(store: NewsStore, settings: Settings, *,
                       client: Any | None = None) -> dict[str, int]:
    """전체 채널 폴링. 채널→신규건수. ``client`` 미주입이면 세션·키로 telethon 접속(fail-open)."""
    if client is not None:
        return {ch: await collect_channel(store, client, ch)
                for ch in settings.news_channel_list}

    if not settings.app_api_id or not settings.app_api_hash:
        _log.info("news: APP_API_ID/HASH 없음 → 수집 스킵(fail-open)")
        return {}
    session_file = Path(f"{settings.telethon_session_path}.session")
    if not session_file.exists():
        _log.info("news: telethon 세션 없음(%s) → 수집 스킵", session_file)
        return {}

    from telethon import TelegramClient

    tg: Any = TelegramClient(str(settings.telethon_session_path),
                             int(settings.app_api_id), settings.app_api_hash)
    await tg.connect()
    try:
        if not await tg.is_user_authorized():
            _log.warning("news: 세션 미인증 → scripts/telegram_login.py 재실행 필요")
            return {}
        result: dict[str, int] = {}
        for ch in settings.news_channel_list:
            try:
                result[ch] = await collect_channel(store, tg, ch)
            except Exception as exc:
                _log.warning("news: 채널 %s 수집 실패: %s", ch, exc)
                result[ch] = 0
        return result
    finally:
        await tg.disconnect()


__all__ = ["collect_channel", "collect_once", "extract_urls", "message_to_item", "store_items"]
```

- [ ] **Step 4: 통과확인** — `uv run pytest tests/test_news_collector.py -q` → PASS(6)

- [ ] **Step 5: DoD + commit**
```bash
uv run ruff check && uv run mypy backend/
git add backend/news/collector.py tests/test_news_collector.py
git commit -m "feat(news): telethon catch-up 수집기(시드 분리·채널격리·fail-open)"
```

---

### Task 4: FactSet 파싱·수집

**Files:** Create `backend/news/factset.py`, `tests/fixtures/factset_listing.html` · Test `tests/test_news_factset.py`

**Interfaces (Produces):** `parse_listing(html:str, base_url=...)->list[FactsetArticle]`, `collect_factset(store, settings, *, http_get:Callable[[str],str]|None=None)->int`.

- [ ] **Step 1: 픽스처** — `tests/fixtures/factset_listing.html`:
```html
<html><body>
<div class="insights">
  <article class="card">
    <a class="card-title" href="/articles/sp-500-earnings-2026">S&amp;P 500 Earnings Insight</a>
    <time datetime="2026-06-16T12:00:00Z">June 16, 2026</time>
    <p class="excerpt">Earnings growth estimates for Q2.</p>
  </article>
  <article class="card">
    <a class="card-title" href="https://insight.factset.com/articles/cpi-forecast">CPI Forecast Note</a>
    <time datetime="2026-06-14T09:30:00Z">June 14, 2026</time>
    <p class="excerpt">Inflation trend update.</p>
  </article>
</div>
</body></html>
```

- [ ] **Step 2: 실패테스트** — `tests/test_news_factset.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.config import Settings
from backend.news.factset import collect_factset, parse_listing
from backend.news.store import NewsStore

_FIXTURE = Path(__file__).parent / "fixtures" / "factset_listing.html"


def test_parse_listing_extracts_articles() -> None:
    arts = parse_listing(_FIXTURE.read_text(encoding="utf-8"))
    assert len(arts) == 2
    first = arts[0]
    assert first.title == "S&P 500 Earnings Insight"
    assert first.url == "https://insight.factset.com/articles/sp-500-earnings-2026"
    assert first.published_at == datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    assert "Earnings growth" in first.excerpt


def test_collect_factset_stores(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    html = _FIXTURE.read_text(encoding="utf-8")
    assert collect_factset(store, s, http_get=lambda _u: html) == 2
    assert collect_factset(store, s, http_get=lambda _u: html) == 0
    assert len(store.recent_factset(datetime(2026, 6, 1, tzinfo=timezone.utc))) == 2


def test_collect_factset_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]

    def boom(_u: str) -> str:
        raise RuntimeError("network down")

    assert collect_factset(store, s, http_get=boom) == 0
```

- [ ] **Step 3: 실패확인** — `uv run pytest tests/test_news_factset.py -q` → FAIL

- [ ] **Step 4: 구현** — `backend/news/factset.py`:
```python
"""FactSet Insight 수집 — 목록 HTML 파싱(순수) + fetch·저장(fail-open).

공식 RSS 없음 → 목록 페이지를 관대하게 파싱(anchor[href*=articles/] + time[datetime] + p.excerpt).
실제 마크업 변경에 대비해 실패는 fail-open(0). 파서 보정은 Step 6(실페이지) 참조.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from backend.config import Settings
from backend.news.models import FactsetArticle
from backend.news.store import NewsStore

_log = logging.getLogger(__name__)
_LISTING_URL = "https://insight.factset.com"


class _ListingParser(HTMLParser):
    """``<a href*=articles/>`` 제목 + 직후 ``<time datetime>`` + ``<p class=excerpt>`` 수집."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base = base_url
        self.articles: list[FactsetArticle] = []
        self._href: str | None = None
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_excerpt = False
        self._excerpt_parts: list[str] = []
        self._pending: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        href = a.get("href") or ""
        if tag == "a" and "articles/" in href:
            self._href = urljoin(self._base + "/", href)
            self._in_title = True
            self._title_parts = []
        elif tag == "time" and a.get("datetime") and self._pending.get("url"):
            self._pending["dt"] = a.get("datetime") or ""
        elif tag == "p" and "excerpt" in (a.get("class") or "") and self._pending.get("url"):
            self._in_excerpt = True
            self._excerpt_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_excerpt:
            self._excerpt_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
            title = "".join(self._title_parts).strip()
            if self._href and title:
                self._pending = {"url": self._href, "title": title}
        elif tag == "p" and self._in_excerpt:
            self._in_excerpt = False
            self._pending["excerpt"] = "".join(self._excerpt_parts).strip()
            self._flush()

    def _flush(self) -> None:
        p = self._pending
        if not p.get("url") or not p.get("dt"):
            self._pending = {}
            return
        try:
            dt = datetime.fromisoformat(p["dt"].replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            self._pending = {}
            return
        self.articles.append(FactsetArticle(url=p["url"], title=p["title"],
                                            published_at=dt, excerpt=p.get("excerpt", "")))
        self._pending = {}


def parse_listing(html: str, base_url: str = _LISTING_URL) -> list[FactsetArticle]:
    """목록 HTML → FactsetArticle 리스트(관대한 추출)."""
    parser = _ListingParser(base_url)
    parser.feed(html)
    return parser.articles


def _httpx_get(url: str) -> str:
    """기본 HTTP GET(httpx). 데스크톱 UA."""
    import httpx

    resp = httpx.get(url, timeout=20.0, follow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0 (news-archive)"})
    resp.raise_for_status()
    return resp.text


def collect_factset(store: NewsStore, settings: Settings, *,
                    http_get: Callable[[str], str] | None = None) -> int:
    """목록 fetch→파싱→저장. 신규 건수. 예외는 fail-open(0)."""
    get = http_get or _httpx_get
    try:
        arts = parse_listing(get(_LISTING_URL))
    except Exception as exc:
        _log.warning("news: FactSet 수집 실패: %s", exc)
        return 0
    return sum(1 for a in arts if store.upsert_factset(a))


__all__ = ["collect_factset", "parse_listing"]
```

- [ ] **Step 5: 통과확인** — `uv run pytest tests/test_news_factset.py -q` → PASS(3)

- [ ] **Step 6: 실페이지 보정(수동·네트워크)**
```bash
uv run python -c "from backend.news.factset import parse_listing, _httpx_get; print(len(parse_listing(_httpx_get('https://insight.factset.com'))))"
```
Expected: ≥1. **0이면** 실제 마크업이 픽스처와 다른 것 → 실제 페이지를 저장해 `_ListingParser` 의 anchor/time/excerpt 조건과 픽스처·테스트를 보정한다(여전히 fail-open이라 앱 안전).

- [ ] **Step 7: DoD + commit**
```bash
uv run ruff check && uv run mypy backend/
git add backend/news/factset.py tests/test_news_factset.py tests/fixtures/factset_listing.html
git commit -m "feat(news): FactSet Insight 목록 파싱·수집(fail-open)"
```

---

### Task 5: Gemini 주간 한국어 요약

**Files:** Create `backend/news/summary.py` · Test `tests/test_news_summary.py`

**Interfaces (Produces):** `build_prompt(factset, telegram, week_start:date)->str`, `summarize_week(store, settings, *, now:datetime|None=None, gemini:Callable[[str],str]|None=None)->WeeklySummary|None`.

> **검증된 Gemini API(google-genai 2.8):** `genai.Client(api_key=...).models.generate_content(model=..., contents=...)` → `.text`. 테스트는 `gemini` 콜러블을 주입해 SDK 미사용.

- [ ] **Step 1: 실패테스트** — `tests/test_news_summary.py`:
```python
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from backend.config import Settings
from backend.news.models import FactsetArticle, RawNewsItem
from backend.news.store import NewsStore
from backend.news.summary import build_prompt, summarize_week


def _factset() -> FactsetArticle:
    return FactsetArticle(url="https://insight.factset.com/a", title="CPI Note",
                          published_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
                          excerpt="inflation")


def test_build_prompt_includes_sources() -> None:
    tg = [RawNewsItem(source="telegram", channel="getfeed", msg_id=1,
                      ts_utc=datetime(2026, 6, 16, tzinfo=timezone.utc), text="삼성전자 급락", urls=())]
    prompt = build_prompt([_factset()], tg, date(2026, 6, 15))
    assert "CPI Note" in prompt
    assert "삼성전자 급락" in prompt
    assert "한국어" in prompt


def test_summarize_week_saves(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    store.upsert_factset(_factset())
    s = Settings(_env_file=None, gemini_api_key="x", gemini_model="gemini-test")  # type: ignore[call-arg]
    out = summarize_week(store, s, now=datetime(2026, 6, 18, tzinfo=timezone.utc),
                         gemini=lambda _p: "## 이번 주 시황\n- 요약")
    assert out is not None and out.kr_markdown.startswith("## 이번 주 시황")
    latest = store.latest_weekly()
    assert latest is not None and latest.model == "gemini-test"


def test_summarize_week_no_key_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert summarize_week(store, s, now=datetime(2026, 6, 18, tzinfo=timezone.utc)) is None


def test_summarize_week_error_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None, gemini_api_key="x")  # type: ignore[call-arg]

    def boom(_p: str) -> str:
        raise RuntimeError("api down")

    assert summarize_week(store, s, now=datetime(2026, 6, 18, tzinfo=timezone.utc),
                          gemini=boom) is None
```

- [ ] **Step 2: 실패확인** — `uv run pytest tests/test_news_summary.py -q` → FAIL

- [ ] **Step 3: 구현** — `backend/news/summary.py`:
```python
"""Gemini 주간 한국어 요약 — 사람이 읽는 뷰(읽기전용·매매 무영향·fail-open).

프롬프트는 *사실 요약·출처 보존*에 한정한다(예측·매매조언 금지 — 면책과 일관).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from backend.config import Settings
from backend.news.models import FactsetArticle, RawNewsItem, WeeklySummary
from backend.news.store import NewsStore

_log = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))

_GUIDE = (
    "당신은 한국 투자자를 위한 시황 요약가입니다. 아래 지난 7일 뉴스(텔레그램·FactSet)를 "
    "한국어 마크다운으로 요약하세요. 규칙: ① 사실만, 추측·예측·매매조언 금지 ② 핵심 이슈를 "
    "굵은 제목으로 묶고 출처 보존 ③ 분량은 한 화면. 투자 자문이 아닙니다.\n\n"
)


def build_prompt(factset: list[FactsetArticle], telegram: list[RawNewsItem],
                 week_start: date) -> str:
    """수집물 → Gemini 프롬프트(순수)."""
    lines = [_GUIDE, f"[주 시작: {week_start.isoformat()}]", "", "## FactSet (글로벌 매크로)"]
    for a in factset:
        lines.append(f"- {a.title} ({a.published_at.date().isoformat()}) — {a.excerpt} [{a.url}]")
    lines.append("")
    lines.append("## 텔레그램 속보(국장)")
    for t in telegram:
        lines.append(f"- [{t.channel}] {t.ts_kst.strftime('%m-%d %H:%M')} {t.text[:200]}")
    return "\n".join(lines)


def _default_gemini(settings: Settings) -> Callable[[str], str]:
    """google-genai 호출 클로저(prompt→텍스트)."""
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.gemini_model

    def _call(prompt: str) -> str:
        resp = client.models.generate_content(model=model, contents=prompt)
        text: str = resp.text or ""
        return text

    return _call


def summarize_week(store: NewsStore, settings: Settings, *, now: datetime | None = None,
                   gemini: Callable[[str], str] | None = None) -> WeeklySummary | None:
    """지난 7일 수집물을 Gemini로 한국어 요약·저장. 키없음/실패면 None(fail-open)."""
    if gemini is None and not settings.gemini_api_key:
        _log.info("news: GEMINI_API_KEY 없음 → 주간요약 스킵(fail-open)")
        return None
    moment = now or datetime.now(tz=timezone.utc)
    since = moment - timedelta(days=7)
    week_start = moment.astimezone(_KST).date() - timedelta(days=6)
    prompt = build_prompt(store.recent_factset(since), store.recent_raw(since), week_start)
    call = gemini or _default_gemini(settings)
    try:
        text = call(prompt)
    except Exception as exc:
        _log.warning("news: Gemini 주간요약 실패: %s", exc)
        return None
    if not text.strip():
        return None
    summary = WeeklySummary(week_start=week_start, kr_markdown=text,
                            model=settings.gemini_model, generated_at=moment)
    store.save_weekly(summary)
    return summary


__all__ = ["build_prompt", "summarize_week"]
```

- [ ] **Step 4: 통과확인** — `uv run pytest tests/test_news_summary.py -q` → PASS(4)

- [ ] **Step 5: DoD + commit**
```bash
uv run ruff check && uv run mypy backend/
git add backend/news/summary.py tests/test_news_summary.py
git commit -m "feat(news): Gemini 주간 한국어 요약(fail-open·사실요약 한정)"
```

---

### Task 6: 스케줄러·앱 배선

**Files:** Modify `backend/scheduler.py`, `backend/app.py` · Test `tests/test_news_scheduler.py`

**Interfaces (Produces):** `build_scheduler(store, settings, news_store:NewsStore|None=None)` 가 **news_store 주입 + 키 있을 때만** `news-poll`(5분)·`news-weekly`(토 08:00 KST) 등록. `app.state.news_store`.

> **회귀 차단(검증):** 기존 `build_scheduler(store, settings)` 2-인자 호출(기존 테스트들)은 repo `.env` 의 APP_API_ID 를 주워도 **news_store 미주입이라 뉴스 잡을 안 만든다**(실데이터 쓰기·잡 추가 회귀 방지).

- [ ] **Step 1: 실패테스트** — `tests/test_news_scheduler.py`:
```python
from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config import Settings
from backend.news.store import NewsStore
from backend.scheduler import build_scheduler
from backend.store import Store


def _ids(scheduler: AsyncIOScheduler) -> set[str]:
    return {j.id for j in scheduler.get_jobs()}


def test_news_jobs_registered_with_store_and_keys(tmp_path: Path) -> None:
    store = Store(tmp_path / "dash.db")
    ns = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None, app_api_id="123", app_api_hash="h")  # type: ignore[call-arg]
    ids = _ids(build_scheduler(store, s, ns))
    assert "news-poll" in ids
    assert "news-weekly" in ids


def test_news_jobs_absent_without_store(tmp_path: Path) -> None:
    store = Store(tmp_path / "dash.db")
    s = Settings(_env_file=None, app_api_id="123", app_api_hash="h")  # type: ignore[call-arg]
    assert "news-poll" not in _ids(build_scheduler(store, s))  # news_store 미주입


def test_news_jobs_absent_without_keys(tmp_path: Path) -> None:
    store = Store(tmp_path / "dash.db")
    ns = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "news-poll" not in _ids(build_scheduler(store, s, ns))
```

- [ ] **Step 2: 실패확인** — `uv run pytest tests/test_news_scheduler.py -q` → FAIL

- [ ] **Step 3: scheduler.py 수정** — 상단 import 에 추가(`import asyncio` 는 표준 그룹, backend.news.* 는 backend 그룹 알파벳순):
```python
import asyncio
```
```python
from backend.news.collector import collect_once
from backend.news.factset import collect_factset
from backend.news.store import NewsStore
from backend.news.summary import summarize_week
```
`build_scheduler` 정의 **앞**에 잡 본체:
```python
async def _run_news_poll(news_store: NewsStore, settings: Settings) -> None:
    """5분 폴링 — 텔레그램 채널 catch-up(fail-open 은 collect_once 내부)."""
    await collect_once(news_store, settings)


async def _run_news_weekly(news_store: NewsStore, settings: Settings) -> None:
    """토요일 — FactSet 수집 후 Gemini 주간요약(둘 다 동기→to_thread)."""
    await asyncio.to_thread(collect_factset, news_store, settings)
    await asyncio.to_thread(summarize_week, news_store, settings)
```
`build_scheduler` 시그니처:
```python
def build_scheduler(
    store: Store, settings: Settings, news_store: NewsStore | None = None
) -> AsyncIOScheduler:
```
기존 잡 루프 **뒤**, `return scheduler` **앞**:
```python
    # ── 뉴스 잡 (news_store 주입 + 키 있을 때만) ───────────────────────
    if news_store is not None and settings.app_api_id and settings.app_api_hash:
        scheduler.add_job(
            _run_news_poll,
            IntervalTrigger(minutes=5, timezone=_SEOUL),
            args=(news_store, settings),
            id="news-poll",
        )
        scheduler.add_job(
            _run_news_weekly,
            CronTrigger(day_of_week="sat", hour=8, minute=0, timezone=_SEOUL),
            args=(news_store, settings),
            id="news-weekly",
        )
```

- [ ] **Step 4: 통과확인** — `uv run pytest tests/test_news_scheduler.py -q` → PASS(3)

- [ ] **Step 5: app.py 배선** — import(backend.engine 와 backend.schemas 사이, 알파벳):
```python
from backend.news.store import NewsStore
```
lifespan 의 `store = Store(cfg.db_path)` **다음 줄**:
```python
        news_store = NewsStore(cfg.news_db_path)
```
`scheduler: AsyncIOScheduler = sched.build_scheduler(store, cfg)` 를 교체:
```python
        scheduler: AsyncIOScheduler = sched.build_scheduler(store, cfg, news_store)
```
`application.state.store = store` **아래**:
```python
        application.state.news_store = news_store
```

- [ ] **Step 6: 전체 DoD + 부팅 스모크**
```bash
uv run pytest -q
uv run ruff check && uv run ruff format --check && uv run mypy backend/
uv run python -c "from backend.app import create_app; create_app(); print('app ok')"
```
Expected: 전체 PASS(기존 회귀 0), ruff/mypy clean, `app ok`.

- [ ] **Step 7: commit**
```bash
git add backend/scheduler.py backend/app.py tests/test_news_scheduler.py
git commit -m "feat(news): 스케줄러 5분/토요일 잡(news_store 게이트) + 앱 lifespan 배선"
```

---

### Task 7: 실연결 스모크(수동) → 플랜 B 입력

세션·키가 있는 로컬에서 실제 4채널 수집을 1회 돌려 아카이브에 쌓이는지 확인하고, **실제 메시지 형태**(길이·말머리·종목표기·URL 패턴)를 관찰한다 — 이게 플랜 B 이슈 휴리스틱·심각도 사전의 입력.

- [ ] **Step 1: 1회 수집 실행**
```bash
uv run python -c "import asyncio; from backend.config import get_settings; from backend.news.store import NewsStore; from backend.news.collector import collect_once; s=get_settings(); print(asyncio.run(collect_once(NewsStore(s.news_db_path), s)))"
```
Expected: `{'FastStockNews': N, ...}` (N≥0, 첫 실행은 시드 ~30/채널).

- [ ] **Step 2: 아카이브 확인**
```bash
uv run python -c "import sqlite3; from backend.config import get_settings; c=sqlite3.connect(str(get_settings().news_db_path)); print('rows:', c.execute('SELECT COUNT(*) FROM news_raw').fetchone()[0]); [print(r) for r in c.execute('SELECT channel, substr(text,1,60) FROM news_raw ORDER BY ts_utc DESC LIMIT 8')]"
```
Expected: rows>0 + 최근 메시지 8건 미리보기. **이 출력으로 플랜 B 를 구체화한다.**

- [ ] **Step 3: 관찰 기록** — 메시지 형태 특징(말머리·종목표기·반복 패턴·노이즈)을 플랜 B 작성 시 심각도 사전·클러스터 키에 반영.

---

## 완료 후 (플랜 B)

`news.db` 실데이터 확보 후 작성·구현: `backend/news/issues.py`(종목명·심각도 사전 기반 이슈 클러스터·긴급도 Top10) + `/api/news/issues`·`/api/news/weekly`(면책 meta) + 프런트 "시황" 탭(Top10 사이드바·상세·주간 패널·면책 배지) + 점수 무영향 회귀.
