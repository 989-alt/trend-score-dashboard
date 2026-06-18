from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from backend.news.issues import StockMeta, build_issues, clean_text, load_severity
from backend.news.models import RawNewsItem

_NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
_SEV = {"급락": Decimal("0.7"), "서킷브레이커": Decimal("1.0")}


def _item(channel: str, msg_id: int, text: str) -> RawNewsItem:
    return RawNewsItem(
        source="telegram", channel=channel, msg_id=msg_id, ts_utc=_NOW, text=text, urls=()
    )


def test_clean_text_strips_noise() -> None:
    out = clean_text("✅ 삼성전자 신고가 https://x.io/a #pokara61 📝 핵심적 본문 요약")
    assert "https" not in out
    assert "#pokara61" not in out
    assert "✅" not in out
    assert "삼성전자" in out and "신고가" in out


def test_build_issues_clusters_by_stock_and_severity() -> None:
    names = {"삼성전자"}
    items = [
        _item("getfeed", 1, "삼성전자 목표주가 상향"),
        _item("getfeed", 2, "삼성전자 신고가 경신"),
        _item("goodnews_honey", 3, "삼성전자 실적 호조"),
        _item("jusikbiso", 4, "코스피 급락 서킷브레이커 발동"),
        _item("getfeed", 5, "오늘 점심 메뉴 추천"),  # 엔티티 없음 → 제외
    ]
    issues = build_issues(items, names, _SEV, now=_NOW, top_n=10)
    assert [i.key for i in issues] == ["삼성전자", "서킷브레이커"]
    top = issues[0]
    assert top.count == 3
    assert top.channels == ("getfeed", "goodnews_honey")
    assert top.urgency == Decimal("8.5")  # 2*2채널 + 1*3건 + 1.5*1최신 + 0심각도
    sev = issues[1]
    assert sev.severity == Decimal("1.0")  # 서킷브레이커
    assert sev.urgency == Decimal("6.5")  # 2*1 + 1*1 + 1.5 + 2*1.0


def test_build_issues_deterministic() -> None:
    names = {"삼성전자"}
    items = [_item("getfeed", i, "삼성전자 뉴스") for i in range(1, 4)]
    a = build_issues(items, names, _SEV, now=_NOW)
    b = build_issues(items, names, _SEV, now=_NOW)
    assert [(i.key, i.urgency) for i in a] == [(i.key, i.urgency) for i in b]


def test_build_issues_top_n() -> None:
    names = {f"종목{i}" for i in range(20)}
    items = [_item("getfeed", i, f"종목{i} 급등") for i in range(20)]
    assert len(build_issues(items, names, _SEV, now=_NOW, top_n=10)) == 10


def test_load_severity_real_file() -> None:
    from backend.config import DATA_DIR

    sev = load_severity(DATA_DIR / "news_severity_lexicon.yml")
    assert sev["서킷브레이커"] == Decimal("1.0")
    assert all(isinstance(v, Decimal) for v in sev.values())


# ── 통합: 언급 급등(spike) + 점수연결 ────────────────────────────────────────


def test_spike_uses_baseline_window() -> None:
    names = {"삼성전자"}
    recent = [_item("getfeed", i, "삼성전자 신고가") for i in range(1, 4)]  # 최근 3건
    baseline = [_item("getfeed", 9, "삼성전자 종가")]  # 과거 1건
    issues = build_issues(recent, names, _SEV, now=_NOW, baseline_items=baseline)
    s = next(i for i in issues if i.key == "삼성전자")
    assert s.spike == Decimal("1.50")  # 3 / (1 + 1)


def test_spike_zero_without_baseline() -> None:
    names = {"삼성전자"}
    items = [_item("getfeed", i, "삼성전자 신고가") for i in range(1, 4)]
    # baseline 미제공 → spike 표시 신호 없음(back-compat: urgency·정렬 불변).
    assert build_issues(items, names, _SEV, now=_NOW)[0].spike == Decimal("0")


def test_stock_meta_attaches_score_only_for_stock_keys() -> None:
    names = {"삼성전자"}
    meta = {
        "삼성전자": StockMeta(
            ticker="005930", score=Decimal("82.5"), grade="strong_buy", market="KR"
        )
    }
    items = [
        _item("getfeed", 1, "삼성전자 신고가"),
        _item("jusikbiso", 2, "코스피 서킷브레이커 발동"),
    ]
    issues = build_issues(items, names, _SEV, now=_NOW, baseline_items=[], stock_meta=meta)
    stock = next(i for i in issues if i.key == "삼성전자")
    assert stock.ticker == "005930"
    assert stock.score == Decimal("82.5")
    assert stock.grade == "strong_buy"
    assert stock.market == "KR"
    sev = next(i for i in issues if i.key == "서킷브레이커")
    assert sev.ticker is None and sev.score is None  # 심각도어 이슈엔 점수 없음
