// Generates src/i18n/ko.json with every Korean string escaped as \uXXXX.
// Run with: node scripts/gen-i18n.mjs
// This keeps Korean text out of source files (project rule) and out of this
// script's committed output — the JSON on disk is pure ASCII escapes.
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// The single source of Korean copy. Authored here (a build script, not app
// source) and emitted as \uXXXX so app .ts/.tsx never contain Hangul.
const ko = {
  "app.title": "추세추종 스코어 대시보드",
  "app.subtitle": "국장·미장 매수 추천도와 테마별 주도주를 한눈에",

  "tab.themes": "테마별 주도주",
  "tab.kr": "국장 KR",
  "tab.us": "미장 US",

  "market.open": "장중",
  "market.closed": "마감",
  "market.kr": "국장",
  "market.us": "미장",
  "market.badge.KR": "KR",
  "market.badge.US": "US",

  "refresh.lastUpdated": "마지막 갱신",
  "refresh.nextIn": "다음 갱신까지",
  "refresh.now": "지금 갱신",
  "refresh.refreshing": "갱신 중…",
  "refresh.waiting": "갱신 대기",
  "refresh.closed": "마감",
  "refresh.nextOpen": "다음 개장 {time}",

  "disclaimer.text":
    "본 대시보드는 투자 자문에 해당하지 않으며, 투자의 판단과 결정은 철저히 개인에게 있습니다.",
  "disclaimer.label": "면책 고지",

  "demo.banner": "샘플 데이터 데모입니다 — 실시간 시세가 아니며, 표시된 종목·점수·가격은 예시입니다.",
  "live.banner": "장중 30분마다 자동 갱신되는 실데이터입니다 · 실시간(틱)은 아닙니다 · 투자 자문 아님.",

  "grade.strong_buy": "적극매수",
  "grade.buy": "매수",
  "grade.hold": "관망",
  "grade.avoid": "회피",
  "grade.sell": "매도요구",
  "grade.unknown": "미분류",

  "buyLane.title": "매수 추천",
  "buyLane.desc": "추세추종 점수 상위 — 매수·적극매수 등급입니다. 클릭하면 상세를 봅니다.",
  "lane.expand": "+{n}개 더 보기",
  "lane.collapse": "접기",

  "sellAlert.lane.title": "매도 요구 경보",
  "sellAlert.lane.desc": "손절 조건이 발동한 종목입니다. 클릭하면 상세를 봅니다.",
  "sellAlert.badge": "매도요구",
  "sellReason.trailing_stop": "트레일링 손절",
  "sellReason.ma200_break": "200일선 이탈",
  "sellReason.unknown": "손절 발동",

  "ranking.title": "랭킹",
  "ranking.search.placeholder": "종목명·코드 검색",
  "ranking.filter.grade": "등급 필터",
  "ranking.filter.all": "전체 등급",
  "ranking.filter.eligibleOnly": "통과 종목만",
  "ranking.empty": "표시할 종목이 없습니다.",
  "ranking.empty.filtered": "조건에 맞는 종목이 없습니다.",
  "ranking.count": "{n}개 종목",

  "col.rank": "순위",
  "col.ticker": "종목",
  "col.price": "현재가",
  "col.changeFromOpen": "장시작대비",
  "col.changePct": "전일대비",
  "col.score": "점수",
  "col.grade": "등급",
  "col.stop": "손절가",
  "col.near52w": "52주 근접",
  "col.turnover": "거래대금",
  "col.sortAsc": "오름차순 정렬",
  "col.sortDesc": "내림차순 정렬",

  "theme.board.title": "테마별 주도주",
  "theme.board.empty": "표시할 테마가 없습니다.",
  "theme.leaders": "주도주",
  "theme.moreCount": "외 {n}종목",
  "theme.sellCount": "매도요구 {n}",

  "drawer.title": "종목 상세",
  "drawer.close": "닫기",
  "drawer.section.quote": "시세",
  "drawer.section.stats": "통계·52주",
  "drawer.section.fundamentals": "펀더멘털",
  "drawer.section.classification": "분류",
  "drawer.section.investorFlow": "투자자별 매매",
  "drawer.section.factors": "팩터 분해",
  "drawer.section.stops": "손절·추세",
  "drawer.section.rationale": "판정 근거",

  "field.price": "현재가",
  "field.openPrice": "시가",
  "field.changeFromOpen": "장시작 대비",
  "field.changePct": "전일 대비",
  "field.volume": "거래량",
  "field.turnover": "거래대금",
  "field.marketCap": "시가총액",
  "field.w52High": "52주 최고",
  "field.w52Low": "52주 최저",
  "field.near52w": "52주 근접도",
  "field.return1y": "1년 수익률",
  "field.per": "PER",
  "field.pbr": "PBR",
  "field.eps": "EPS",
  "field.sector": "섹터",
  "field.industry": "업종",
  "field.score": "점수",
  "field.grade": "등급",
  "field.eligible": "하드필터 통과",
  "field.ma200": "200일선",
  "field.stopPrice": "손절가",
  "field.trailingPeak": "추세 고점",
  "field.aboveMa200": "200일선 위",
  "stop.notSet": "손절 미산정",
  "stop.notSet.hint": "추세 미진입 — 손절가가 산정되지 않았습니다(안전 의미 아님).",

  "unit.eok": "억",
  "unit.jo": "조",
  "unit.won": "원",

  "investor.foreign": "외국인",
  "investor.institution": "기관",
  "investor.individual": "개인",
  "investor.net": "순매수",
  "investor.buy": "매수",
  "investor.sell": "매도",
  "investor.totalTraded": "매수·매도·순매수",
  "investor.asOf": "기준일",
  "investor.unit": "단위: 원(억·조)",
  "investor.notProvided": "—(미제공)",
  "investor.notProvided.us": "미장은 투자자별 매매를 제공하지 않습니다.",

  "factor.near_52w": "52주 근접",
  "factor.pocket_pivot": "포켓피봇",
  "factor.momentum_norm": "모멘텀",
  "factor.turnover_norm": "거래대금",
  "factor.vol_fit": "변동성 적합",
  "factor.raw.momentum": "모멘텀(원시)",
  "factor.raw.volatility": "변동성(원시)",

  "drawer.section.recommendation": "추천 근거",
  "rec.scoreOf": "점수 {score} / 100",
  "recmeaning.strong_buy": "추세·주도력·돌파 신호가 모두 강함",
  "recmeaning.buy": "추세추종 매수 조건 충족",
  "recmeaning.hold": "일부 조건만 충족 — 관망 구간",
  "recmeaning.avoid": "추세추종 매수 조건 미충족",
  "recmeaning.sell": "손절 조건 발동 — 보유 시 청산 권고",
  "rec.sell.trailing":
    "추세 고점 {peak} 대비 트레일링 손절가 {stop} 하회 (현재가 {price}) — 보유 시 청산 권고",
  "rec.sell.ma200": "200일선 {ma200} 이탈 — 추세 훼손, 보유 시 청산 권고",
  "rec.inelig.title": "매수 후보 제외 사유 (하드필터 미달)",
  "rec.inelig.ma200": "200일선 아래 — 장기 하락추세",
  "rec.inelig.momentum": "최근 20일 모멘텀 음수 — 단기 하락",
  "rec.inelig.vol": "변동성이 적정 밴드를 벗어남",
  "rec.inelig.other": "유동성(거래대금) 등 하드필터 미달",
  "rec.checklist": "추세추종 체크리스트",
  "crit.trend": "200일선 위 · 장기추세",
  "crit.trend.pass": "상승추세 유지",
  "crit.trend.fail": "200일선 이탈",
  "crit.leader": "52주 신고가 근접 · 주도력",
  "crit.leader.detail": "고점 {pct} 근접",
  "crit.momentum": "모멘텀 · 최근 20일",
  "crit.pocket": "포켓피봇 · 대량거래 돌파",
  "crit.pocket.pass": "발생",
  "crit.pocket.fail": "없음",
  "crit.vol": "변동성 적합도",

  "value.yes": "예",
  "value.no": "아니오",
  "value.na": "—",
  "value.eligible.yes": "통과",
  "value.eligible.no": "미통과",

  "counts.scanned": "스캔",
  "counts.eligible": "통과",
  "counts.scored": "점수화",
  "counts.failed": "실패",

  "state.loading": "불러오는 중…",
  "state.error.title": "데이터를 불러오지 못했습니다",
  "state.error.hint": "백엔드가 실행 중인지 확인하세요. 잠시 후 자동으로 다시 시도합니다.",
  "state.retry": "다시 시도",
  "state.empty": "데이터가 없습니다.",

  "footer.disclaimer":
    "본 대시보드는 투자 자문에 해당하지 않으며, 투자의 판단과 결정은 철저히 개인에게 있습니다.",
  "footer.method": "추세추종(맛동산/Sperandeo) 방법론 기반 · 30분 자동 갱신",
};

// Escape every non-ASCII char to \uXXXX so the JSON file is pure ASCII.
function escapeNonAscii(str) {
  let out = "";
  for (const ch of str) {
    const code = ch.codePointAt(0);
    if (code > 0x7f) {
      // Handle BMP + astral (astral splits into surrogate pair via the unit).
      if (code > 0xffff) {
        const hi = 0xd800 + ((code - 0x10000) >> 10);
        const lo = 0xdc00 + ((code - 0x10000) & 0x3ff);
        out += "\\u" + hi.toString(16).padStart(4, "0");
        out += "\\u" + lo.toString(16).padStart(4, "0");
      } else {
        out += "\\u" + code.toString(16).padStart(4, "0");
      }
    } else {
      out += ch;
    }
  }
  return out;
}

const lines = ['{'];
const keys = Object.keys(ko);
keys.forEach((key, i) => {
  const val = escapeNonAscii(ko[key]);
  const comma = i === keys.length - 1 ? "" : ",";
  lines.push(`  ${JSON.stringify(key)}: "${val}"${comma}`);
});
lines.push("}");
lines.push("");

const outPath = resolve(__dirname, "..", "src", "i18n", "ko.json");
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, lines.join("\n"), "utf8");
console.log("wrote", outPath, `(${keys.length} keys)`);
