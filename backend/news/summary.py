"""Gemini 주간 한국어 요약 — 사람이 읽는 뷰(읽기전용·매매 무영향·fail-open).

프롬프트는 *사실 요약·출처 보존*에 한정한다(예측·매매조언 금지 — 면책과 일관).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

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


def build_prompt(
    factset: list[FactsetArticle], telegram: list[RawNewsItem], week_start: date
) -> str:
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


def summarize_week(
    store: NewsStore,
    settings: Settings,
    *,
    now: datetime | None = None,
    gemini: Callable[[str], str] | None = None,
) -> WeeklySummary | None:
    """지난 7일 수집물을 Gemini로 한국어 요약·저장. 키없음/실패면 None(fail-open)."""
    if gemini is None and not settings.gemini_api_key:
        _log.info("news: GEMINI_API_KEY 없음 → 주간요약 스킵(fail-open)")
        return None
    moment = now or datetime.now(tz=UTC)
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
    summary = WeeklySummary(
        week_start=week_start,
        kr_markdown=text,
        model=settings.gemini_model,
        generated_at=moment,
    )
    store.save_weekly(summary)
    return summary


__all__ = ["build_prompt", "summarize_week"]
