"""올웨더 매매 슬리브 — 레짐별 진입/청산 신호 모듈(추세추종 코어와 분리).

- mean_reversion: 횡보·고변동(CHOP_VOL) 레짐용 RSI2 과매도 반등.
- (예정) inverse: 하락(DOWN) 레짐용 인버스 ETF.

슬리브는 '결정 공급기'다 — 주문 실행/리스크 가드/체결 재조회는 trader 의 TraderLoop 가 맡는다.
"""
