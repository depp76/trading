# 추세추종 알고리즘 (Donchian 채널 돌파) — 설계 및 개발 계획

> 기준일: 2026-09-17
> 목적: Nexus Trading 1차 목표(매매 추천 알고리즘 개발 + 백테스트)의 첫 전략 후보로, 리버모어
> 추세추종 아이디어를 Donchian 채널 돌파 규칙으로 구현한다. 개발은 Paperclip이 아니라 이
> 저장소(src) 위에서 **CLI(Claude Code) 기반**으로 진행한다 — `src/strategy/rebalance/rebalance.md`가 리밸런싱
> 알고리즘의 구현 스펙 역할을 해온 것과 같은 방식으로, 이 문서가 추세추종 알고리즘의 스펙
> 역할을 한다. 앞으로 CLI 세션에서 이 알고리즘을 구현/수정할 때는 이 문서의 섹션 번호를
> 코드 주석·커밋 메시지에서 인용하고, 진행 상황을 6장 체크리스트에 반영할 것.
>
> 위치: 이 문서는 `src/strategy/trend_following/trend_following.md` — 전략 코드와 같은 폴더에
> 스펙을 두는 규칙(2026-09-17, `strategy/__init__.py` 참고)에 따라 저장소 루트에서 옮겨 왔고,
> 예전에 이 폴더에 있던 짧은 안내문은 이 문서로 흡수됨.
> 관련 문서: `src/strategy/rebalance/rebalance.md`(주간 포트폴리오 리밸런싱 알고리즘 — 별개 트랙, 서로 코드/문서를
> 공유하지 않음), `roadmap.md`(전체 로드맵)

---

## 목차

1. [배경 및 목표](#1-배경-및-목표)
2. [활용 가능한 기존 코드 자산](#2-활용-가능한-기존-코드-자산)
3. [전략 정의 (v1)](#3-전략-정의-v1)
4. [소스 코드 구성안](#4-소스-코드-구성안)
5. [검증(백테스트) 계획](#5-검증백테스트-계획)
6. [결정 사항 / 다음 단계](#6-결정-사항--다음-단계)
7. [변경 이력](#7-변경-이력)

---

## 1. 배경 및 목표

- Nexus Trading 프로젝트의 1차 목표는 매매 추천 알고리즘을 개발하고 이를 기반으로
  백테스트하여 결과를 도출하는 것 (페이퍼 트레이딩 범위 — 실계좌 연동은 범위 밖).
- 첫 전략 후보는 제시 리버모어의 추세추종: "신고점 돌파 시 진입, 추세가 꺾이면(신저점
  이탈) 청산"이라는 원형을 Donchian 채널 돌파 규칙으로 구체화한다.
- 리스크 게이트: Sharpe ratio ≥ 1.5, 최대낙폭(MDD) ≤ 15%.
- 대상 시장/자산군: 주식·외환·선물/옵션이 최종 범위이나, v1은 주식 단일 종목 백테스트부터
  시작한다.
- `strategy/rebalance/`(`strategy/rebalance/rebalance.md`)와는 별개 트랙이다 — 리밸런싱은 "여러 종목을 매주
  스코어링해 포트폴리오를 재구성"하는 종목군 단위 알고리즘이고, 이 문서의 추세추종은
  "한 종목의 진입/청산 타이밍"을 다루는 개별 종목 단위 알고리즘이라 설계 접근 자체가
  다르다. 두 트랙은 코드도 문서도 공유하지 않는다.

---

## 2. 활용 가능한 기존 코드 자산

| 자산 | 위치 | 비고 |
|------|------|------|
| `get_historical_data(ticker, start_date)` | `data/history.py` (`data/market.py`에서도 재노출) | KOSPI/KOSDAQ(KIS·Naver·KRX)·해외(Yahoo) OHLCV를 Polars DataFrame으로 반환. 이 알고리즘의 데이터 계층은 새로 만들 필요 없이 이 함수를 그대로 재사용한다 (프로토타입에서 쓴 yfinance 기반 `fetch_ohlcv`는 폐기). |
| `_compute_indicators()` | `data/indicators.py` | MA/RSI14 계산 — Donchian 채널 자체는 High/Low의 rolling max/min이라 별도 지표이지만, 추후 필터(RSI 등)를 추가할 때 재사용 가능. |
| `run_backtest_strategy()` | `strategy/ma_cross/backtest.py` (구 `data/backtest.py`, 스펙 `ma_cross.md`) | 개별 종목 단위, 벡터화(numpy), no-lookahead 원칙이 이미 적용된 기존 백테스트 로직 — 이 알고리즘과 "종목 단위 백테스트"라는 성격이 가장 가깝다. 다만 전략 로직(MA20/60 골든크로스)은 다르므로 함수를 공유하지는 않고, 설계 패턴(벡터화 방식, 지표 재사용 방식)만 참고한다. |
| `strategy/rebalance/` 패키지 구조 | `strategy/rebalance/{config,factors,classify,signals,walkforward,backtest}.py` | 서브패키지 분리 컨벤션(각 책임을 별도 파일로 분리 + `__init__.py` facade)의 선례 — 4장의 구성안이 이 패턴을 따른다. |

---

## 3. 전략 정의 (v1)

- **Donchian 채널 돌파**: 최근 `entry_n`일 최고가를 상향 돌파하면 매수, 최근 `exit_n`일
  최저가를 하향 돌파하면 매도.
- 롱 온리, 단일 포지션, 피라미딩 없음. 리버모어 특유의 "추세 지속 시 분할 추가 매수"는
  v2 후보로 6장에 이관.
- 파라미터 `entry_n`/`exit_n`의 초기값은 아직 미확정 — 클라우드 세션 프로토타입에서는
  20/10을 임시로 사용했으나, 실데이터 기준 백테스트로 탐색해서 확정할 것 (6장 체크리스트).

**구현에서 확정한 세부 규칙 (2026-09-17, `signals.py`)**
- 채널은 **당일을 제외한** 직전 `entry_n`일 `High`의 최댓값(`upper`) / 직전 `exit_n`일 `Low`의
  최솟값(`lower`)이다 (`shift(1)` 후 rolling). 당일을 포함하면 종가가 자기 자신의 고가를 넘을 수
  없어 돌파가 성립하지 않는다.
- 돌파 판정은 t일 **종가** 기준: `Close > upper` → 진입 신호, `Close < lower` → 청산 신호.
  워밍업 구간(직전 n일이 채워지지 않은 행)에서는 신호를 내지 않는다.
- `position`은 t일 종가 시점의 상태(0/1). flat이면 진입 신호에, long이면 청산 신호에만
  반응하므로 피라미딩·숏이 생기지 않는다. 같은 날 두 신호가 동시에 나면 현재 상태가 결정한다.
- 수수료/슬리피지는 `TrendFollowingConfig.fee_rate`/`slippage_rate`(편도, 거래대금 비율)로
  준비만 해두고 기본값 0이다 — 반영 여부는 6장의 미결정 항목.

**v2 오버레이 (2026-09-17 구현, 모두 기본값 0 = 꺼짐이라 기본 설정은 v1과 동일)**
- **레짐 필터** `regime_ma_n`: 종가가 `SMA(regime_ma_n)` 위일 때만 신규 진입. 청산에는 영향 없음.
  워밍업 구간(SMA 미계산)은 진입 불가.
- **ATR 손절** `stop_atr_mult`, `atr_n`, `stop_mode`: ATR은 true range(High−Low, |High−전일종가|,
  |Low−전일종가| 중 최댓값)의 `atr_n`일 단순이동평균. 손절선은 `anchor − stop_atr_mult × ATR`이고
  `trailing`(샹들리에)은 anchor = 진입 후 최고 종가로 매일 갱신(위로만 이동), `fixed`는 anchor =
  진입 종가로 고정. **t일에 적용되는 손절선은 t−1일 종가 시점에 확정**되며(`stop` 컬럼) 종가가
  그 아래로 마감하면 청산(`exit_reason="stop"`; 채널 이탈이 같은 날 겹치면 `"channel"`).
- **변동성 타깃 사이징** `vol_target_pct`, `vol_n`, `max_weight`: 진입일에 직전 `vol_n`일 일간수익률
  표준편차 × √252로 실현변동성을 구하고 `weight = min(max_weight, vol_target / vol)`을 트레이드
  기간 내내 고정. 변동성 추정치가 아직 없으면(워밍업) `min(1, max_weight)`. 사이징이 꺼져 있어도
  `max_weight`는 상한으로 작동한다. 백테스트는 `weight.shift(1) × 일간수익률`로 수익을, 비중 변화량 ×
  편도 비용으로 거래비용을 계산한다.
- 산출 추가: 트레이드별 `weight`, `price_return_pct`(진입→청산 종가 가격수익률), `exit_reason`;
  요약에 `avg_weight`, `n_channel_exits`, `n_stop_exits`, `v2`(적용된 파라미터).

---

## 4. 소스 코드 구성안

`strategy/rebalance/`가 쓴 것과 같은 서브패키지 컨벤션을 따르되, 위치는 `data/` 밑이 아니라
**`src/strategy/`** 최상위 폴더 밑이다 (2026-09-17 결정 — `strategy/rebalance/rebalance.md` 11-5 참고).
`data/`는 순수 데이터 접근 계층(시세/지표/캐시)만 남기고, 매매 전략 로직은 전부
`strategy/`로 분리하기로 했다 — `data/rebalance/`도 같은 결정에 따라 `strategy/rebalance/`로
이동 완료(2026-09-17, `strategy/rebalance/rebalance.md` 11-5).
`trend_following/`은 아직 코드가 없어 이동 비용이 가장 낮으므로, 처음부터 새 경로에
구현을 시작한다.

```
src/strategy/trend_following/
  __init__.py     # 공개 심볼 재노출 (strategy/rebalance/__init__.py 패턴)
  config.py       # TrendFollowingConfig — entry_n, exit_n, risk gate 값(sharpe_min, mdd_max) 등 파라미터 단일 소스
  signals.py      # donchian_signal() — 진입/청산 신호 생성 (no-lookahead)
  backtest.py     # run_backtest() — position.shift(1) 방식 벡터화 백테스트 + Sharpe/MDD/CAGR/승률 산출
tests/strategy/trend_following/
  test_signals.py       # look-ahead 없음 검증 포함
  test_backtest.py
```

- 데이터 계층은 신규 파일 없이 `data.market.get_historical_data()`를 그대로 호출
  (패키지 위치가 바뀌었으니 import 경로는 `from data.market import get_historical_data` 형태로
  상위 패키지를 명시적으로 참조).
- **구현 완료 (2026-09-17, CLI 세션)** — 위 구성 그대로 `config.py`(`TrendFollowingConfig`),
  `signals.py`(`donchian_signal`), `backtest.py`(`run_backtest`, `run_backtest_for_ticker`),
  `__init__.py` 파사드, 테스트 18개(`tests/strategy/trend_following/test_signals.py`·
  `test_backtest.py`, no-lookahead 검증 포함). 데이터는 `data.history.get_historical_data()`를
  그대로 사용한다.
- **v2 오버레이 (2026-09-17)** — 같은 세 파일 안에 구현(3장 v2 항목). `signals.py`가 `atr`,
  `regime_ma`, `regime_ok`, `weight`, `stop`, `exit_reason` 컬럼을 추가하고, `backtest.py`는
  `weight` 기반으로 수익·비용을 계산한다. 테스트 `test_v2.py` 15개(기본값의 v1 동일성, 레짐 차단,
  trailing/fixed 손절과 사유, 사이징 비중·고정·워밍업 폴백, 비용 비례, v2 전체 no-lookahead).
- **UI 연결 (2026-09-17)** — 메인 윈도우 6번째 탭 "Trend Following"(`ui/trend_following_tab.py`,
  `TrendFollowingTab`): 티커 입력(Trading Universe 종목 콤보에서 선택 가능), 시작일, `entry_n`/
  `exit_n`, 편도 수수료·슬리피지(%)를 받아 `threads.fetch_threads.TrendFollowingBacktestThread`로
  `run_backtest_for_ticker()`를 백그라운드 실행하고, 5장 지표 요약 행(리스크 게이트 PASS/FAIL 색상)과
  트레이드 표(청산 사유·비중·가격수익률 포함)를 표시한다. 세 번째 입력 행이 v2 오버레이(레짐 MA,
  손절 ATR 배수·모드, 변동성 타깃, 최대 비중)이며 기본값은 모두 off다. "Chart" 버튼은
  `ui/dialogs/trend_following_chart.py`(`TrendFollowingChartDialog`)로 종가+채널+레짐 MA+손절선+
  진입/청산 마커, 전략 vs 매수&보유 에쿼티 곡선을 띄운다.
  주문 실행 기능은 없다(연구용). 헤드리스 테스트 `tests/test_trend_following_tab.py` 7개.
- `run_backtest(df, config)` 반환값: `summary`(5장 지표 + `passes_risk_gate`), `trades`
  (진입/청산일, 보유일, 트레이드 수익률 — 미청산 트레이드는 `exit_date=None`), `equity_curve`,
  `signals`(신호 프레임 + `daily_return`/`strategy_return`/`equity`).
- (정리 완료) 결정 이전에 `src/data/trend_following/`에 있던 문서는 삭제됐고, 저장소 루트의
  `trend_following.md`도 2026-09-17 이 파일로 옮겨져 스펙은 이 문서 하나만 유지한다.

---

## 5. 검증(백테스트) 계획

- No-lookahead 원칙: 신호는 t일 종가로 확정되고, 포지션은 t+1일부터 적용
  (`position.shift(1) * daily_return`).
- **이미 확인된 것** (2026-09-15~17, 클라우드 세션 프로토타입 — `paperclipai/prototype/`에
  있었으나 해당 폴더는 이후 정리됨, 코드는 사용자에게 zip으로 전달됨):
  - Donchian 신호 + 벡터화 백테스트 엔진의 파이프라인 배관 자체는 정상 동작 확인 (합성
    더미 데이터 기준).
  - `test_no_lookahead.py`로 미래 데이터를 잘라내도 과거 시점의 포지션 판단이 바뀌지
    않음을 확인 (PASS).
  - **주의**: 이 검증은 실데이터 없이 합성 데이터로만 이루어진 것이라 Sharpe/MDD 수치
    자체는 무의미하다 — "배관이 새지 않는지"만 확인된 상태.
- **실데이터 예비 결과 (2026-09-17, CLI 세션)** — `get_historical_data(ticker, "2021-01-01")`,
  약 5.7년(1,400행), 무비용과 편도 0.1% 두 가지, 파라미터 3조합. 이 표는 in-sample 단일
  구간이라 **탐색용**이며 확정 근거가 아니다(6장 IS/OOS 항목 참고).

  | 종목 | 매수&보유 | entry/exit | 총수익 % | CAGR % | Sharpe | MDD % | 트레이드 | 승률 % | 노출 % |
  |------|-----------|-----------|---------|--------|--------|-------|---------|-------|-------|
  | 005930 삼성전자 | +208 | 20/10 | +231 | +23.4 | 0.92 | 26.8 | 18 | 67 | 39 |
  | 005930 | | 55/20 | +119 | +14.7 | 0.66 | 32.4 | 11 | 36 | 29 |
  | 005930 | | 100/50 | +304 | +27.7 | 0.96 | 32.7 | 3 | 67 | 47 |
  | 000660 SK하이닉스 | +1,302 | 20/10 | +651 | +42.4 | 1.19 | 29.7 | 21 | 55 | 42 |
  | 000660 | | 55/20 | +675 | +43.2 | 1.18 | 36.8 | 9 | 78 | 39 |
  | 000660 | | 100/50 | +665 | +42.9 | 1.08 | 46.9 | 4 | 50 | 50 |
  | 035420 NAVER | −32 | 20/10 | −36 | −7.6 | −0.17 | 51.4 | 19 | 39 | 32 |
  | 035420 | | 55/20 | −52 | −12.2 | −0.52 | 56.9 | 10 | 20 | 23 |
  | 035420 | | 100/50 | −27 | −5.3 | −0.30 | 31.6 | 4 | 25 | 17 |
  | AAPL | +165 | 20/10 | +96 | +12.5 | 0.84 | 17.9 | 21 | 55 | 46 |
  | AAPL | | 55/20 | +52 | +7.6 | 0.57 | 21.3 | 11 | 70 | 41 |
  | AAPL | | 100/50 | +55 | +7.9 | 0.54 | 22.6 | 6 | 80 | 50 |
  | ^GSPC S&P500 | +104 | 20/10 | +35 | +5.4 | 0.61 | 9.1 | 28 | 50 | 52 |
  | ^GSPC | | 55/20 | +9 | +1.5 | 0.22 | 22.6 | 16 | 56 | 53 |
  | ^GSPC | | 100/50 | +32 | +4.9 | 0.55 | 13.8 | 7 | 67 | 60 |
  | ^KS11 KOSPI | +128 | 20/10 | +93 | +12.2 | 0.80 | 28.2 | 19 | 68 | 36 |
  | ^KS11 | | 55/20 | +63 | +8.9 | 0.56 | 20.5 | 10 | 40 | 33 |
  | ^KS11 | | 100/50 | +92 | +12.1 | 0.65 | 33.9 | 4 | 25 | 42 |

  관찰:
  - **리스크 게이트(Sharpe ≥ 1.5, MDD ≤ 15%)를 통과한 조합이 없다.** 가장 좋은 Sharpe는
    000660 20/10의 1.19, MDD가 15% 아래인 경우는 ^GSPC 20/10(9.1%)·100/50(13.8%)뿐인데 그때는
    Sharpe가 0.6 미만이다. 규칙 그대로는 단일 종목 롱온리로 게이트를 넘기 어렵다.
  - 추세가 강한 종목(000660)에서는 매수&보유의 절반 수준 수익을 노출 40%로 냈고, 횡보·하락
    종목(035420)에서는 매수&보유보다 더 잃었다 — 채널 돌파가 잦은 가짜 신호를 낸다.
  - 시도한 3조합 중 20/10이 대체로 Sharpe가 가장 높고 트레이드 수가 많다. 100/50은
    트레이드가 3~7회뿐이라 통계적 의미가 약하다.
  - 편도 0.1% 비용은 5.7년 총수익을 1~7%p 낮추는 정도(표는 무비용 값, 비용 반영 값은
    `run_backtest`로 재현 가능)라 결론을 바꾸지 않는다.
  - 다음 탐색 방향(v2 후보): 손절/ATR 기반 청산, 변동성 타깃 포지션 사이징, 시장 레짐
    필터(예: 200일선 위에서만 진입), 여러 종목 분산 — MDD를 낮추지 않으면 게이트 통과가
    어렵다. → 아래 v2 결과.

- **v2 오버레이 예비 결과 (2026-09-17)** — 같은 6종목·2021-01-01~, Donchian 20/10 고정, 무비용.
  6종목 평균:

  | 변형 | 평균 CAGR % | 평균 Sharpe | 평균 MDD % | 최대 MDD % | 게이트 통과 |
  |------|------------|------------|-----------|-----------|-----------|
  | v1 (20/10) | +14.7 | 0.70 | 27.2 | 51.4 | 0/6 |
  | 레짐 MA200 | +13.6 | 0.64 | 28.5 | 52.7 | 0/6 |
  | 손절 3×ATR14 (trailing) | +14.2 | 0.72 | 26.6 | 47.6 | 0/6 |
  | 손절 2×ATR14 (trailing) | +12.8 | 0.71 | 23.7 | 40.5 | 0/6 |
  | 변동성 타깃 20% | +9.5 | 0.73 | 18.3 | 28.8 | 0/6 |
  | 변동성 타깃 15% | +7.4 | 0.72 | 14.2 | 22.3 | 0/6 |
  | 레짐200 + 손절3 + 타깃20% | +7.8 | 0.73 | 16.0 | 25.1 | 0/6 |
  | 레짐200 + 손절3 + 타깃15% | +6.1 | 0.73 | 12.5 | 19.4 | 0/6 |

  종목별 상세(전체 8변형 × 6종목 표)는 `run_backtest`로 재현 가능하며, 대표값만 적으면 삼성전자
  레짐200+손절3+타깃15%는 CAGR +10.5%, Sharpe 1.18, MDD 9.0%(v1: +23.4%, 0.92, 26.8%),
  SK하이닉스 같은 조합은 +10.3%, 1.11, 10.9%(v1: +42.4%, 1.19, 29.7%)다.

  관찰:
  - **여전히 게이트 통과 조합은 없다.** v2는 MDD를 크게 낮추지만(평균 27→12.5%, 최대 51→19%)
    Sharpe는 0.7 안팎에서 거의 움직이지 않는다 — 세 오버레이 모두 수익과 위험을 같은 비율로
    줄이는 성격이라 위험조정수익은 개선되지 않는다.
  - 변동성 타깃 사이징이 MDD 감소의 대부분을 만든다(단독으로 27→14%). 손절은 MDD를 소폭 낮추고
    (3×: −0.6%p, 2×: −3.5%p) 트레이드 수를 늘리며, 레짐 MA200 단독은 오히려 평균이 나빠진다
    (진입 지연으로 수익 구간을 놓치고 MDD는 그대로).
  - MDD ≤ 15%는 타깃 15% 이하 사이징으로 개별 종목에서도 대부분 달성된다(삼성전자 9.0%,
    SK하이닉스 10.9%, AAPL 9.4%). 병목은 Sharpe ≥ 1.5다.
  - 따라서 다음 단계는 단일 종목 규칙 추가가 아니라 **여러 종목 분산 포트폴리오**(상관이 낮은
    종목군에 동시 적용해 변동성을 낮추는 방향)와 IS/OOS 검증이다. 단일 종목 롱온리로는 이 게이트를
    맞추기 어렵다는 것이 v1·v2 결과의 공통 결론이다.
- 산출 지표: 총수익률, CAGR, 연변동성, Sharpe, MDD, 트레이드 수, 승률, 평균 트레이드
  수익률, 시장노출비중.
- Risk gate 통과 여부: Sharpe ≥ 1.5, MDD ≤ 15% (1장 참고).

---

## 6. 결정 사항 / 다음 단계

- [x] 전략 후보 확정 — 리버모어 추세추종 → Donchian 채널 돌파로 구체화
- [x] 개발 트랙 확정 — Paperclip 대신 CLI(Claude Code) 기반, 기존 `src` 코드베이스 위에서
      진행 (paperclipai는 코드 저장 위치가 아님)
- [x] 파이프라인 프로토타입 검증 — 합성 데이터, look-ahead 없음 확인 완료 (클라우드 세션)
- [ ] `entry_n`/`exit_n` 등 파라미터 초기값 확정 — 5장 예비 결과에서는 20/10이 우세하나
      IS/OOS 분리 전이라 미확정. 코드 기본값은 20/10.
- [x] `strategy/trend_following/` 서브패키지 실제 구현 (2026-09-17, CLI 세션 — 4장 참고)
- [x] 앱 UI 연결 — "Trend Following" 탭 + 차트 다이얼로그 (2026-09-17, 4장 참고)
- [x] 실데이터 기준 백테스트 실행 및 결과를 본 문서 5장에 기록 (2026-09-17 예비 결과 —
      게이트 미통과, v2 방향 도출)
- [ ] 수수료/슬리피지 반영 여부 결정 — `fee_rate`/`slippage_rate` 파라미터는 구현됨(기본 0),
      5장 결과상 결론에 영향 없음
- [x] v2 오버레이 구현 — ATR 손절(trailing/fixed), 변동성 타깃 사이징, 레짐 MA 필터 (2026-09-17,
      3장·5장 참고). 게이트는 여전히 미통과(Sharpe 병목).
- [ ] 여러 종목 분산 포트폴리오 백테스트 (v3 후보 — 5장 v2 관찰 참고)
- [ ] 리버모어 피라미딩(분할 추가 매수) 반영 여부 — 보류
- [ ] In-sample / Out-of-sample 분리 검증 방식 결정
- [ ] paperclipai 재활용 여부 — 알고리즘 개발 프로세스가 안정화된 이후 재검토 (당분간 미사용)
- [x] 소스 위치를 `data/trend_following/`이 아닌 `src/strategy/trend_following/`로 확정
      (2026-09-17, `strategy/rebalance/rebalance.md` 11-5) — `data/rebalance/`·`data/backtest.py` 등 기존 전략 코드도
      같은 날 `strategy/`로 이동 완료

---

## 7. 변경 이력

- 2026-09-17: 최초 작성 (Cowork 세션) — 리버모어 추세추종을 Donchian 채널 돌파로 구체화하고,
  CLI 기반 개발을 위한 스펙 문서로 정리.
- 2026-09-17: 소스 위치를 `data/trend_following/` → `src/strategy/trend_following/`로 변경
  (4장, 6장) — `strategy/rebalance/rebalance.md` 11-5에 전체 마이그레이션 계획 수립.
- 2026-09-17: `strategy/rebalance/rebalance.md` 11-5 실행 완료에 맞춰 경로 표기 갱신 — `strategy/rebalance/`,
  `strategy/ma_cross.py`, `strategy/trend_following/__init__.py` 스캐폴드 생성(코드는 아직 없음).
- 2026-09-17: 문서를 저장소 루트에서 `src/strategy/trend_following/trend_following.md`로 이동하고,
  이 폴더에 있던 짧은 안내문(`trend_following.md`, 42줄)을 흡수해 단일 스펙 문서로 통합.
- 2026-09-17: 서브패키지 구현(`config.py`/`signals.py`/`backtest.py`/`__init__.py`, 테스트 18개)과
  실데이터 예비 백테스트(6종목 × 3조합 × 2비용) 수행. 3장에 구현 세부 규칙, 4장에 API, 5장에
  결과 표와 관찰, 6장 체크리스트 갱신. 리스크 게이트 통과 조합 없음 → v2 방향(손절·사이징·
  레짐 필터) 기록.
- 2026-09-17: UI 연결 — `ui/trend_following_tab.py`(탭), `ui/dialogs/trend_following_chart.py`(차트),
  `threads/fetch_threads.py`의 `TrendFollowingBacktestThread`; 4장·6장 갱신.
- 2026-09-17: v2 오버레이(레짐 MA 필터, ATR 손절, 변동성 타깃 사이징) 구현 및 UI 입력 행 추가,
  실데이터 8변형 비교를 5장에 기록. 결론: MDD는 12.5%까지 내려가나 Sharpe 0.7대로 게이트 미통과 →
  다음은 다종목 분산.
