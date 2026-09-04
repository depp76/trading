# Trading Algorithm — 주간 포트폴리오 재구성 알고리즘 & 개발 방법론

> 기준일: 2026-09-04 (최초 작성 2026-08-28)
> 목적: (1) 주간(금요일 기준) 단위로 Trading Universe 종목을 스코어링하고 포트폴리오를
> 리밸런싱하는 알고리즘의 설계·구현 현황, (2) 이 알고리즘과 향후 매매/포트폴리오 추천 관련
> 기능을 **멀티 에이전트로 개발하는 방법론**을 한 문서에서 계속 누적해 나가는 통합 문서.
> 이전에는 개발 방법론을 `multi_agent_guide.md`에 별도로 뒀으나, 이후 매매·포트폴리오
> 추천 알고리즘과 SW 개발 방법론을 모두 이 문서 하나에서 다루기로 함(12장 참고 —
> `multi_agent_guide.md`는 이 문서에 흡수되어 삭제됨).
> 관련 파일: `data/rebalance/`(신호/백테스트 로직, 2026-09-04 11-4 1단계로 패키지화됨),
> `ui/auto_trading_tab.py`(수동 트리거 UI),
> `ROADMAP.md`(전체 로드맵 — 이 문서는 ROADMAP의 하위 작업 문서)

---

## 목차

1. [배경 및 목표](#1-배경-및-목표)
2. [활용 가능한 기존 코드 자산](#2-활용-가능한-기존-코드-자산)
3. [후보 접근법 및 채택 현황](#3-후보-접근법-및-채택-현황)
4. [현재 구현 상태](#4-현재-구현-상태)
5. [설계 시 공통 고려사항 — 반영 현황](#5-설계-시-공통-고려사항--반영-현황)
6. [앱 통합 방안 — 구현 결과](#6-앱-통합-방안--구현-결과)
7. [검증 계획 — 진행 현황](#7-검증-계획--진행-현황)
8. [다음 단계 (미착수 개발 항목)](#8-다음-단계-미착수-개발-항목)
9. [결정 사항 / 진행 상황](#9-결정-사항--진행-상황)
10. [기존 매매 이력 분석 (베이스라인)](#10-기존-매매-이력-분석-베이스라인)
11. [소스 코드 구성안](#11-소스-코드-구성안)
12. [멀티 에이전트 개발 방법론](#12-멀티-에이전트-개발-방법론)
13. [변경 이력](#13-변경-이력)

---

## 1. 배경 및 목표

- 매주(금요일) 단위로 Trading Universe(KOSPI/KOSDAQ, 추후 NASDAQ100/S&P500)를 스캔해
  보유/매수/매도 후보를 기계적으로 산출하는 알고리즘을 만들고자 함.
- `TradingRecordTab`이 이미 금요일 기준으로 자산 스냅샷을 기록하고 있어(`_friday_dates()`),
  이 알고리즘의 실행 주기와 자연스럽게 맞아떨어짐.
- 신호 생성과 실제 매매 실행은 분리한다 — "이번 주 기준 매수/매도 후보 리스트"를 산출하는
  것까지가 현재 범위이며, 자동 주문 실행 여부는 별도 결정 사항으로 둔다. (4장에서 보듯
  실제 구현도 이 원칙을 그대로 따르고 있음 — `AutoTradingTab`은 신호만 계산하고 주문은
  절대 넣지 않는다.)
- 투자자문이 아닌 개인용 알고리즘 개발 문서 — 실제 자금 투입 전 반드시 백테스트/페이퍼
  트레이딩으로 검증할 것 (7장 참고).

---

## 2. 활용 가능한 기존 코드 자산

`data/` 패키지에 이미 구현되어 재사용 가능한 함수/지표 (3-1 모듈화 이후 기준 경로):

| 자산 | 위치 | 비고 |
|------|------|------|
| MA10/20/60 이격도, 52주 고저 대비, 기간별 수익률(3/5/10/20/60/120일) | `data/indicators.py`, `data/market.py` | Universe 탭에 이미 표시 중, `_compute_indicators()`가 4장 팩터 계산의 기반 |
| `run_backtest_strategy()` | `data/backtest.py` | MA 이격도/데드크로스 매도 조건, +30% 익절 조건이 벡터화되어 검증 완료된, 종목별 개별 백테스트 로직 (리밸런싱과는 별개의 기존 자산) |
| `compute_weekly_rebalance_signals()` / `run_rebalance_backtest()` | `data/rebalance.py` | **4장의 실제 구현 결과물** — 이 문서의 최초 작성 시점엔 미구현이었으나 이후 완성됨 |
| `fetch_stock_ma_multi()` | `data/market.py` | 개별 종목 MA10/20/60 시계열 |
| `fetch_investor_trend()` / `_fetch_index_investor_trend()` | `data/collectors/kiwoom.py`, `data/collectors/naver.py` | 기관/외국인 순매수 동향 — 아직 리밸런싱 팩터로는 미사용 (8장 참고) |
| `fetch_vkospi_history()` / `fetch_vkospi()` | `data/collectors/krx.py` | 시장 변동성 게이지 — 아직 리밸런싱 로직에 미통합 (8장 "레짐 오버레이") |
| `fetch_quarterly_financials()` | `data/collectors/naver.py` | 분기 재무 데이터 — 현재 팩터는 PER만 사용, 추가 밸류 팩터 후보로 미사용 |
| pykrx `get_market_sector_classifications()` | 미사용 (ROADMAP 4-5) | 섹터 집중도 제약에 필요 (8장 참고) |
| `gemini_helper.portfolio_diagnosis()` | `gemini_helper.py` | 산출된 신호에 대한 자연어 해설/리스크 코멘트 생성에 재사용 가능 (미통합) |

---

## 3. 후보 접근법 및 채택 현황

### 3-1. 팩터 스코어링 + 순위 리밸런싱 — ✅ 채택 및 구현 완료

전 종목에 대해 이미 계산되는 지표를 z-score로 정규화해 합산 점수를 매기고, 시장별 상위
N개 종목만 보유. 순위가 크게 밀려난 종목은 매도 후보, 새로 상위권에 진입한 종목은 매수
후보로 분류. **4장에 실제 구현 상세를 정리.**

- 장점: 기존 컬럼을 그대로 재활용, 팩터 추가/제거가 쉬움
- 핵심 설계 지점: 팩터별 가중치(모멘텀 vs 밸류 vs 퀄리티), 정규화 방식(z-score vs 랭크 백분위)
  → v1은 7개 팩터 동일가중, z-score 방식으로 확정 구현됨

### 3-2. 모멘텀 로테이션 — 부분 흡수됨

20~60일 수익률(`ret_20d`/`ret_60d`) 상위 K개 종목을 보유하는 아이디어는 3-1의 팩터 중
2개(모멘텀 팩터)로 흡수되었고, "순위가 일정 폭 이상 밀렸을 때만 교체"하는 밴드 버퍼
아이디어도 3-1 구현의 `band_multiplier=1.5`로 그대로 반영됨. 별도 접근법으로 독립 구현하지
않기로 함.

### 3-3. 평균회귀 + 리스크 오버레이 — 미구현 (8장 다음 단계)

Div20/Div50(이격도)이 낮은(과매도) 종목을 매수 후보로 삼되, VKOSPI를 시장 전체 리스크
게이지로 사용해 VKOSPI 급등 주간에는 신규 매수 비중을 축소하는 아이디어. **팩터 자체(과매도
반영)는 3-1의 ma20/ma50 momentum 팩터가 방향은 반대지만 유사 정보를 담고 있어 우선순위가
낮아졌지만, VKOSPI 리스크 게이지는 3-1에 없는 시장 국면(regime) 정보라 별도로 추가할
가치가 있음** → 8장 "VKOSPI 레짐 오버레이" 항목으로 이관.

### 3-4. 기존 백테스트 로직 재사용 — 별도 유지

`run_backtest_strategy()`(개별 종목 단위 백테스트)는 3-1 리밸런싱 알고리즘과는 별개로
Universe 탭 등에서 계속 사용 중. 리밸런싱 알고리즘의 워크포워드 백테스트(`run_rebalance_backtest`)는
이 로직을 재사용하지 않고 별도로 새로 구현됨(포트폴리오 단위 시뮬레이션이 필요해 개별
종목 백테스트와 성격이 다름).

---

## 4. 현재 구현 상태

> `data/rebalance/`(구 `data/rebalance.py`, 22.7KB, 12개 함수 — 2026-09-04 패키지화, 11-2)와
> `ui/auto_trading_tab.py`(14.6KB, `AutoTradingTab`)로
> 구현 완료. UI의 클래스 docstring이 "trading.md 3-1", "trading.md 3-5", "trading.md 6" 등
> 이 문서의 섹션 번호를 직접 인용하고 있음 — **이 문서가 실제로 구현 스펙 역할을 하고
> 있다는 뜻이므로, 앞으로도 이 문서를 갱신할 때는 실제 구현이 참조할 수 있게 섹션 번호를
> 안정적으로 유지할 것.**

### 4-1. 팩터 (7개, 동일가중)

| 팩터 | 방향 | 설명 |
|------|------|------|
| `value_per` | 낮을수록 좋음 | PER (0 이하는 결측 처리 — 적자기업이 밸류에서 유리해지는 왜곡 방지) |
| `ma20_momentum` / `ma50_momentum` | 높을수록 좋음 | MA20/50 이격도 |
| `ma20_slope_1w` | 높을수록 좋음 | MA20의 최근 1주 변화율 (추세 가속도) |
| `high52w_proximity` | 높을수록 좋음 | 52주 고점 대비 근접도 |
| `ret_20d` / `ret_60d` | 높을수록 좋음 | 20/60일 수익률 |

최소 3개 팩터 이상 유효값이 있어야 후보에 포함(`_REBALANCE_MIN_FACTORS = 3`). 팩터별 z-score를
구해 방향을 부호로 통일한 뒤 평균해 종합 점수 산출.

### 4-2. 분류 규칙

- 시장별 상위 N종목만 매수 후보: `top_n_by_market = {"KOSPI": 10, "KOSDAQ": 10}` (설계 당시
  우려했던 "KOSDAQ 변동성이 통합 top-N을 잠식하는 문제"를 시장별 순위로 해결)
- 회전율 완화 버퍼: `band_multiplier = 1.5` → 보유 종목은 시장 내 순위가 `N × 1.5`(예: 15위)
  아래로 밀려야 매도 후보가 됨. 신규 매수는 여전히 상위 N위 이내만.
- 랭크에서 아예 빠진(팩터 부족 등) 보유 종목은 자동으로 매도 후보 처리.

### 4-3. 워크포워드 백테스트

- 과거 매주 금요일마다 그 시점까지의 데이터만으로 신호를 재계산(look-ahead bias 방지),
  포트폴리오를 시뮬레이션.
- 매수 수수료 0.015%, 매도 수수료 0.015% + 거래세 0.18%를 실제로 차감 — 설계 문서(7장)의
  "회전율 관리" 우려를 실제 비용으로 정량화.
- 포지션 사이징은 **동일가중**(현금을 매수 후보 수로 나눠 배분) — 7장에서 언급한
  변동성 역가중 등 대안은 아직 미구현(8장).
- 산출 지표: 총수익률, 벤치마크(KS11) 대비 수익률, CAGR, MDD, 승률, 총 비용/비용 드래그(%).

### 4-4. UI 통합

- `AutoTradingTab`: "이번 주 신호 계산" 버튼 → 현재 Universe 데이터와 보유 종목을 기반으로
  즉시 계산(실시간 구독이 아니라 클릭 시 1회성 계산 — 이 탭 특성상 적절한 설계).
- 워크포워드 백테스트는 1~5년 lookback 선택 후 백그라운드 스레드(`RebalanceBacktestThread`)로
  실행, 결과를 `BacktestResultDialog`로 표시.
- 신호 계산과 백테스트가 **동일한 `_score_and_rank`/`_classify_buy_sell_hold` 함수를 공유** —
  알고리즘을 튜닝하면 실시간 신호와 백테스트가 항상 같은 로직을 쓰게 되어 있음(둘이
  따로 놀 위험을 원천 차단한 설계).

### 4-5. 테스트 커버리지

`tests/test_backtest.py`에 `TestRebalanceFactorExtraction`, `TestRebalanceTransactionCosts`
클래스로 팩터 추출과 거래비용 계산이 테스트되고 있음. 워크포워드 시뮬레이션 전체 흐름이나
`classify`/`signals` 단계에 대한 전용 테스트는 아직 얕은 편 — 11장의 모듈 분리 제안과 함께
테스트도 모듈 단위로 보강할 필요.

### 4-6. 아직 이 문서에 반영되지 않은 사실

`ROADMAP.md`에는 이 Auto Trading 기능(3-1 구현)이 별도 항목으로 등재되어 있지 않음 —
`data/rebalance.py`가 이 문서(trading.md)만 참조하고 ROADMAP.md는 참조하지 않기 때문. 두
문서의 역할이 갈라지는 지점이므로, ROADMAP.md 쪽에도 "완료" 항목으로 한 줄 추가하는 것을
권장(8장 항목으로 등재).

---

## 5. 설계 시 공통 고려사항 — 반영 현황

- **섹터/시장 집중도 제약**: 시장(KOSPI/KOSDAQ) 단위 집중도는 4-2의 시장별 top-N으로 반영됨.
  **섹터 단위 집중도는 아직 미반영** — pykrx 섹터 분류(ROADMAP 4-5)와 연계 필요 (8장).
- **회전율 관리**: `band_multiplier`로 반영됨(4-2). 실제 수수료/세금까지 백테스트에 반영해
  회전율의 비용을 정량화함(4-3) — 설계 단계보다 한 걸음 더 나간 상태.
- **생존편향**: 여전히 미해결 — 현재 KOSPI/KOSDAQ 리스트만 사용, 상장폐지/편입·편출 이력이
  과거 백테스트에 반영되지 않음. 워크포워드 백테스트 결과를 해석할 때 이 점을 감안할 것.
- **포지션 사이징**: 동일가중으로 확정 구현됨(4-3). 변동성 역가중 등은 8장으로 이관.
- **과최적화 경계**: 워크포워드 방식 자체가 아웃오브샘플에 가까운 검증이라 어느 정도
  완화되지만, 팩터 7개 동일가중이 "v1 기본값"이라는 점(코드 docstring에 명시)을 볼 때
  향후 팩터 가중치를 튜닝할 때는 반드시 별도 기간으로 검증할 것.

---

## 6. 앱 통합 방안 — 구현 결과

설계 당시 (a) 수동 트리거 / (b) 자동 트리거 두 갈래를 고민했는데, **(a) 수동 트리거로
확정 구현됨** — `AutoTradingTab`의 "이번 주 신호 계산" 버튼. 금요일 자동 감지 후 알림을
띄우는 (b)안은 미구현 상태로 남아 있음(8장 후보 항목은 아니며, 필요성이 재확인되면 별도
로드맵 항목으로 추가).

신호 생성과 실제 주문 실행의 분리 원칙은 코드 docstring에 명시적으로 재확인됨
("Signal generation only — this tab never places orders").

---

## 7. 검증 계획 — 진행 현황

1. **워크포워드 백테스트 — 구현 완료.** `run_rebalance_backtest()`가 look-ahead bias 없이
   과거 시점 데이터만으로 신호를 재계산하며 시뮬레이션. 다만 **아직 실행 결과 수치(총수익률/
   CAGR/MDD/승률 등)를 이 문서에 기록한 적은 없음** — 실행 후 9장 체크리스트와 함께 결과를
   여기 추가할 것.
2. **페이퍼 트레이딩 — 미착수.** 실제 주문 없이 매주 신호만 기록해 관찰하는 단계 — 8장 항목.
3. **소액 실운용 — 미착수.** 위 두 단계 통과 후에만 검토.

---

## 8. 다음 단계 (미착수 개발 항목)

12장의 멀티 에이전트 작업 분배 예시가 이 목록을 그대로 사용한다 — 각 항목이 서로 다른
파일을 건드리도록 의도적으로 골랐다(11장의 구조 개편 이후 기준). 8-1에서 각 항목을
"바로 구현에 들어갈 수 있는 수준"까지 구체화했다 — 지금까지는 이름만 있었던 지점.

| # | 항목 | 근거 | 예상 작업 파일 |
|---|------|------|----------------|
| A | 섹터 집중도 제약 | 5장 "섹터/시장 집중도", ROADMAP 4-5 | `data/rebalance/sector.py`(신규) |
| B | VKOSPI 레짐 오버레이 | 3-3에서 이관 — 시장 변동성 급등 시 매수 축소 | `data/rebalance/regime.py`(신규) |
| C | 포지션 사이징 개선 (동일가중 → 변동성 역가중 옵션) | 5장 "포지션 사이징" | `data/rebalance/sizing.py`(신규) |
| D | 페이퍼 트레이딩 로그 | 7장 검증계획 2단계 | `trade_db.py`(신규 테이블), `ui/auto_trading_tab.py` |
| E | ROADMAP.md에 Auto Trading 완료 항목 등재 | 4-6 | `ROADMAP.md` |
| F | 밸류 팩터 확장 (PER 외 PBR/ROE/배당수익률) | 2장 `fetch_quarterly_financials` 미사용 | `data/rebalance/factors.py` |
| G | 백테스트 실행 결과를 7장에 기록 | 7장 | `trading.md` (문서만) |
| H | ✅ 전략 파라미터 통합 관리 (`RebalanceConfig`) — 완료(2026-09-04) | 8-2 — A/B/C가 늘리는 파라미터를 한 곳에서 관리 | `data/rebalance/config.py`(신규, **A/B/C보다 선행**) |
| I | `compute_weekly_rebalance_signals` 오케스트레이터 분리 | 8-2 — sector/regime 훅을 `classify.py` 안 건드리고 연결 | `data/rebalance/signals.py`(신규, `classify.py`에서 이관, **A/B보다 선행**) |

A/B/F는 신규 팩터·모듈 추가라 서로 파일이 겹치지 않아 동시 진행에 가장 적합하고, C는
신규 파일(`sizing.py`)로 분리했으므로 A/B와도 병행 가능해졌다. D/E/G는 언제든 독립적으로
진행 가능. **H/I는 A/B/C가 공유하는 기반이라 먼저 끝나 있어야 한다** — 순서는 11-4/12-5 참고.

### 8-1. 각 항목의 구체적 스펙

**A. 섹터 집중도 제약**
- 데이터: pykrx `get_market_sector_classifications(date, market)` → `{ticker: sector}` (WICS
  업종분류). 섹터 분류는 자주 바뀌지 않으므로 `data/cache.py` 패턴대로 일 단위 캐시.
- 함수: `apply_sector_cap(ranked, sector_map, max_per_sector) -> list` — 상위 랭크부터 순서대로
  담되, 이미 담긴 종목 수가 `max_per_sector`(예: `ceil(top_n * 0.3)`, 즉 상위 10종목이면 섹터당
  최대 3종목)에 도달한 섹터는 건너뛰고 다음 순위 종목으로 대체(greedy 선택).
- 연결 지점: `classify.py`가 만든 `buy_candidates`를 **후처리**만 한다 — `classify.py` 자체는
  섹터를 몰라도 됨 (8-2 원칙).

**B. VKOSPI 레짐 오버레이**
- 데이터: `fetch_vkospi_history()`.
- 정의: 현재 VKOSPI의 최근 60일 평균/표준편차 대비 z-score → `normal`(z<1) /
  `elevated`(1≤z<2) / `crisis`(z≥2) 3단계.
- 효과: `elevated`면 신규 매수 후보 수를 50%로 축소, `crisis`면 신규 매수 0(매도는 그대로
  허용 — 리스크 축소는 항상 가능해야 함).
- 함수: `compute_market_regime(vkospi_series) -> {"regime": str, "buy_scale": float}`.
- 연결 지점: A와 마찬가지로 `buy_candidates` 리스트 길이를 `buy_scale`만큼 자르는 후처리.

**C. 포지션 사이징 개선**
- 방식: 역변동성 가중 — 종목별 최근 20일 일별수익률 표준편차 σ_i 계산 →
  `weight_i = (1/σ_i) / Σ(1/σ_j)`. 특정 종목 쏠림 방지를 위해 종목당 최대 비중 캡(예 15%)을
  두고, 캡을 넘는 초과분은 나머지 종목에 비례 재분배.
- 함수: `compute_position_weights(candidates, price_history, method, max_weight) -> dict[ticker, weight]`.
  `method="equal"`(현재 기본값)과 `"inverse_vol"`을 옵션으로 유지 — 백테스트로 비교 가능하게.
- 연결 지점: `walkforward.py`의 `alloc = cash / len(buy_list)` 한 줄을
  `alloc = cash * weights[t]`로 교체하는 정도의 국소 수정.

**D. 페이퍼 트레이딩 로그**
- `trade_db.py`에 `rebalance_signals` 테이블 추가: `as_of_date, ticker, action(buy/sell/hold),
  score, rank, created_at` — 기존 `upsert_trades` 배치 패턴을 그대로 재사용.
- UI: "이번 주 신호 기록" 버튼 → 매주 1회 upsert. 몇 주 쌓이면 "지난주 매수 후보가 실제로
  올랐는지" 추적 리포트를 추가할 수 있는 기반이 됨(이번 항목의 범위 밖, 향후 항목).

**F. 밸류 팩터 확장**
- `fetch_quarterly_financials()`가 이미 있으므로 PBR·ROE·배당수익률을
  `_REBALANCE_FACTORS` 딕셔너리에 항목만 추가하면 됨 — 팩터가 plug-in 구조(이름→추출함수)라
  구조 변경 없이 확장 가능한 게 현재 설계의 장점.

### 8-2. 파라미터 관리 문제 — 왜 H/I가 선행되어야 하는가

지금 `top_n_by_market={"KOSPI":10,"KOSDAQ":10}`와 `band_multiplier=1.5`가 **두 곳에 중복
정의**되어 있다 — `data/rebalance.py`의 함수 기본값과 `ui/auto_trading_tab.py`의
`AutoTradingTab` 클래스 상수. 지금은 파라미터가 2개뿐이라 버틸 만하지만, A(섹터 cap
비율)·B(VKOSPI 임계값 2개)·C(역변동성 max_weight) 세 항목이 들어오면 파라미터가 최소
6~7개로 늘고, "어느 조합이 백테스트 성과가 좋았는지" 추적할 방법이 없어진다.

**제안**: `data/rebalance/config.py`에 `RebalanceConfig` dataclass 하나로 전략 파라미터를
전부 모은다.

```python
@dataclass
class RebalanceConfig:
    top_n_by_market: dict = field(default_factory=lambda: {"KOSPI": 10, "KOSDAQ": 10})
    band_multiplier: float = 1.5
    max_per_sector: int = 3          # 8-A
    vkospi_elevated_z: float = 1.0   # 8-B
    vkospi_crisis_z: float = 2.0     # 8-B
    sizing_method: str = "equal"     # 8-C: "equal" | "inverse_vol"
    max_position_weight: float = 0.15  # 8-C
```

UI·실시간 신호 계산·워크포워드 백테스트가 전부 이 하나의 `RebalanceConfig` 인스턴스를
주고받게 되면, ① 파라미터 중복이 없어지고 ② 여러 config를 만들어 백테스트를 비교하는
방식(그리드서치의 기초)이 자연스럽게 열린다. `compute_weekly_rebalance_signals` 함수는
현재 `classify.py`에 있지만, sector/regime 후처리 훅을 걸려면 "팩터→분류→섹터cap→레짐조정"을
조합하는 별도의 오케스트레이터가 필요해서 `data/rebalance/signals.py`로 분리하는 것을
같이 제안한다(11-2 갱신).

---

## 9. 결정 사항 / 진행 상황

- [x] 접근법 선택 — **3-1 팩터 스코어링 + 순위 리밸런싱** 채택 (3장)
- [x] 팩터/신호 구성 확정 — 7개 팩터, 동일가중 z-score, v1 기본값으로 확정, 튜닝은 백테스트
      기반으로 추후 (4-1)
- [x] 앱 통합 방식 확정 — (a) 수동 트리거, `AutoTradingTab` (6장)
- [x] 워크포워드 백테스트 구현 — `run_rebalance_backtest()` (7장)
- [x] `data/rebalance.py` → `data/rebalance/` 패키지 분리 + 리밸런싱 테스트 이동 (11-4 1~2단계, 2026-09-04)
- [x] 전략 파라미터 통합 관리 `RebalanceConfig` 신설 (8-H / 11-4 3단계, 2026-09-04)
- [ ] 백테스트 실행 결과 기록 — 아직 이 문서에 수치 미기록 (8-G)
- [ ] 페이퍼 트레이딩 시작일 / 결과 (8-D)
- [ ] 섹터 집중도 제약 (8-A)
- [ ] VKOSPI 레짐 오버레이 (8-B)
- [ ] 포지션 사이징 개선 (8-C)
- [ ] ROADMAP.md 동기화 (8-E)
- [ ] 밸류 팩터 확장 (8-F)

---

## 10. 기존 매매 이력 분석 (베이스라인)

> `portfolio.db`의 `trades` 테이블(총 88건, 2026-08-31 기준)을 분석한 결과. 신규 알고리즘의
> 성과를 비교할 **베이스라인(수동 매매 실적)**으로 활용.

### 10-1. 전체 요약

| 지표 | 값 |
|------|-----|
| 전체 거래 | 88건 (청산 85건 / 보유 중 3건) |
| 총 실현손익 | +285,392,432원 |
| 총 매수원가(청산분) | 1,281,355,800원 |
| 가중평균 수익률 | **+22.3%** |
| 승률 | 63.5% (54승 31패) |
| 평균 익절 / 평균 손절 | +6,819,160원(+46.2%) / -2,672,329원(-10.6%) |
| Profit Factor | 4.45 (총이익 ÷ 총손실) |
| 평균 보유기간(중앙값) | 53.5일 (41일) |

승률 대비 손익비가 매우 좋은 구조 — 이기는 폭(+46%)이 지는 폭(-11%)의 4배 이상.

### 10-2. 종목 집중도 — 반도체 2종목이 수익의 92%

| 종목 | 거래수 | 실현손익 | 평균수익률 | 승률 |
|------|-------|---------|-----------|------|
| 삼성전자 | 17 | +165,554,210 | +50.7% | 76.5% |
| SK하이닉스 | 10 | +96,625,025 | +63.4% | 80.0% |
| 그 외 33종목 합계 | 58 | +23,213,197 | - | - |

삼성전자·SK하이닉스 두 종목이 전체 거래의 32%(27/85건)에 불과하지만 총 실현손익의
**91.9%**를 차지 — 향후 알고리즘의 성과를 비교할 때 "삼성전자/SK하이닉스 제외 시 성과"도
별도로 확인해볼 가치가 있음(4-1의 팩터 스코어링이 이 정도 종목 집중을 만들어내는지, 아니면
더 분산되는지가 중요한 비교 포인트).

최근 SK하이닉스 매매(2026-06-26 매수 2,664,000원 → 07-28 매도 1,583,000원, **-40.7%**)는
직전 급등 이후 고점 매수 사례 — 알고리즘이 모멘텀 팩터만으로 이런 추격매수를 걸러내지
못한다면 8-B(레짐 오버레이)가 특히 중요해짐.

### 10-3. 분기별 실현손익 추이

| 분기 | 거래수 | 실현손익 | 승률 |
|------|-------|---------|------|
| 2025 Q1 | 2 | +16,241 | 50% |
| 2025 Q2 | 15 | +6,984,356 | 53% |
| 2025 Q3 | 9 | +723,060 | 67% |
| 2025 Q4 | 11 | +837,989 | 45% |
| 2026 Q1 | 24 | +103,505,300 | **88%** |
| 2026 Q2 | 14 | +63,878,389 | 50% |
| 2026 Q3 (~08월) | 10 | +109,447,097 | 60% |

### 10-4. 보유기간 분포 (청산 85건)

0~3일 2건 · 4~7일 8건 · 8~14일 12건 · 15~30일 12건 · 31~60일 22건 · 61~90일 9건 ·
91~180일 20건 — 단타보다는 수주~수개월 스윙 위주. 4장의 "주간" 리밸런싱 주기와 실제
매매 습관(스윙)이 어느 정도 맞아떨어짐 — 최소 보유기간 제약까지는 필요 없어 보임.

### 10-5. 현재 보유 중 (미실현, 2026-08-31 기준)

삼성전자만 3회 분할매수로 보유 중: 07-31(85주 @247,000) · 08-05(120주 @247,000) ·
08-14(185주 @271,500) — 총 390주, 매수원가 100,877,360원. 현재 보유 종목도 삼성전자
단일 종목.

### 10-6. 데이터 품질 메모 — 해결됨

`sell_date` 컬럼의 0-패딩 불일치는 이후 `fix: normalize buy/sell date input to zero-padded
YYYY-MM-DD` 커밋으로 해결됨(입력 검증 로드맵 항목 반영).

---

## 11. 소스 코드 구성안

> 3장~4장의 알고리즘을 계속 확장하면서, 동시에 12장처럼 여러 에이전트에게 병렬로 일을
> 맡기려면 **지금의 파일 구조 자체가 병목**이 된다. 이 장은 "무엇을 만들지"가 아니라
> "어디에 나눠 담을지"에 대한 구성안이다.

### 11-1. 이전 구조 (2026-09-04 실측, 11-4 1~2단계 실행 전 기준 — 이력 보존용)

```
data/
  __init__.py          # 하위 모듈 재노출(facade)
  cache.py             # 전역 캐시/세션 상수
  indicators.py        # 지표 계산
  market.py            # 시세/상장목록/환율
  backtest.py          # run_backtest_strategy (개별 종목 백테스트, 3-1과 무관)
  rebalance.py          # 팩터/스코어링/분류/워크포워드백테스트 전부 (22.7KB, 12개 함수) ⚠
  collectors/
    kiwoom.py, krx.py, naver.py, yahoo.py
ui/
  auto_trading_tab.py   # AutoTradingTab (14.6KB)
  assets_tab.py, dialogs.py, history_tab.py, universe_tab.py, widgets.py, common.py
threads/
  fetch_threads.py, realtime.py
tests/
  test_backtest.py      # 리밸런싱 팩터/거래비용 테스트도 여기 포함 ⚠
  test_data_fetcher.py, test_trade_db.py, test_assets_calc.py, test_ui_common.py
```

⚠ 표시한 두 파일이 문제 지점이었다 — `data/rebalance.py`는 "실시간 신호 계산"과 "과거
워크포워드 시뮬레이션"이라는 서로 다른 책임이 한 파일에 섞여 있고 계속 커지는 중이었으며,
`tests/test_backtest.py`도 개별종목 백테스트와 리밸런싱 테스트가 섞여 있었다. 이대로
"섹터 제약은 A가, 레짐 오버레이는 B가"라고 시키면 **둘 다 `rebalance.py`를 열게 되어
100% 충돌**하는 구조였다. **2026-09-04 11-4 1~2단계 실행으로 아래 11-2 구조로 전환
완료** — 이 절은 전환 전 상태의 기록으로만 남긴다.

### 11-2. `data/rebalance.py` → `data/rebalance/` 패키지화 — ✅ 완료 (2026-09-04, 11-4 1단계)

이미 `data/collectors/`가 같은 패턴(여러 소스별 파일 + `__init__.py` facade)으로 잘 쓰이고
있으므로 동일한 방식을 그대로 따른다.

```
data/rebalance/
  __init__.py     # 기존 data.rebalance의 공개 심볼을 그대로 재노출 → data/__init__.py는 무수정
  config.py (신규, 8-H)  # RebalanceConfig dataclass — 모든 전략 파라미터의 단일 소스
  factors.py       # _REBALANCE_FACTORS, _extract_live_candidates, _score_and_rank
  classify.py       # _classify_buy_sell_hold, _DEFAULT_TOP_N_BY_MARKET — 순수 랭킹 기반 분류만, sector/regime 모름
  signals.py (신규, 8-I) # compute_weekly_rebalance_signals — factors→classify→sector cap→regime scale을 조합하는 오케스트레이터 (classify.py에서 이관)
  sector.py (신규, 8-A)  # 섹터 집중도 제약 — signals.py가 호출하는 후처리 함수
  regime.py (신규, 8-B)   # VKOSPI 레짐 오버레이 — signals.py가 호출하는 후처리 함수
  sizing.py (신규, 8-C)    # 포지션 사이징 (동일가중/역변동성) — walkforward.py가 호출
  walkforward.py     # _compute_historical_factor_series, _factor_snapshot_*, _build_snapshot_lookup, _run_walkforward_simulation
  backtest.py          # _summarize_backtest, run_rebalance_backtest
```

- `__init__.py`에서 기존 이름을 그대로 재노출하면 `data/__init__.py`의
  `from data.rebalance import (...)` 구문을 한 글자도 안 고쳐도 되어, 이 리팩토링 자체는
  다른 코드에 영향이 없는 안전한 선행 작업이 된다(먼저 해두고 나서 8장 항목들을 배분하는
  순서를 권장).
- **`classify.py`는 sector/regime을 몰라야 한다.** 원래는 `compute_weekly_rebalance_signals`가
  `classify.py`에 있었는데, sector cap/regime scale을 붙이려면 결국 누군가 이 함수를
  고쳐야 한다 — 그러면 A(섹터)와 B(레짐)가 같은 파일(`classify.py`)에서 만나 충돌한다.
  그래서 이 함수를 `signals.py`로 옮기고 "팩터 계산 → 순위분류 → 섹터cap 후처리 → 레짐
  후처리" 순서로 조합만 담당하게 하면, A와 B는 각자의 신규 파일(`sector.py`/`regime.py`)만
  건드리고 `signals.py`에는 **한 줄씩 자기 훅 호출만 추가**하면 된다 — 그래도 같은 파일을
  건드리긴 하지만, "함수 로직 수정"이 아니라 "호출 한 줄 추가"라 git이 자동 병합하기 쉬운
  형태의 충돌로 바뀐다(12-5에서 실제 지시서에 이 점을 명시).
- `config.py`는 A/B/C 모두가 파라미터를 꺼내 쓰는 공용 기반이므로, 8-H로 **가장 먼저**
  만들어 두는 것을 권장(11-4).
- `sector.py`/`regime.py`/`sizing.py`는 신규 파일이라 배분 즉시 다른 에이전트와 충돌 없이
  시작 가능.

`data/rebalance/__init__.py`는 원래 `data/rebalance.py`가 모듈 최상위에 노출하던 이름을
전부(공개 함수뿐 아니라 `data/__init__.py`가 쓰지 않는 `_DEFAULT_TOP_N_BY_MARKET`,
`_build_snapshot_lookup`, `_factor_snapshot_at`까지) 그대로 재노출한다 — `data/__init__.py`,
`data_fetcher.py`(→`data`를 통해 재노출), `ui/auto_trading_tab.py`, `tests/`가 전부
`data_fetcher`를 거쳐서만 참조하고 있어 **이 전환에서 위 파일들은 단 한 줄도 수정하지
않았다**(11-2 원안의 "안전한 선행 작업" 주장을 실제로 확인). `pytest tests/`(85건) 전부
통과 및 `ui.auto_trading_tab`/`threads.fetch_threads` import 재확인 완료.

### 11-3. `ui/`, `tests/` 제안 — 테스트 이동은 완료, UI 분리는 아직 보류

- `ui/auto_trading_tab.py`는 아직 14.6KB로 급히 나눌 필요는 없음 — 다만 8-A/8-B/8-D로 표시
  UI가 늘어나면 `ui/auto_trading_widgets.py`(테이블/다이얼로그)와
  `ui/auto_trading_tab.py`(컨트롤러)로 분리를 재검토.
- `tests/rebalance/test_factors.py`(`TestRebalanceFactorExtraction`), `test_walkforward.py`
  (`TestRebalanceTransactionCosts`)로 이동 완료(2026-09-04, 11-4 2단계) — `test_backtest.py`에는
  원래부터 있던 개별종목 백테스트 테스트(`TestBacktestEdgeCases`/`TestBacktestSignal`)만 남음.
  `test_classify.py`/`test_backtest.py`(리밸런싱용)는 아직 대응하는 테스트가 없어 미생성 —
  8-A~8-C 구현 시 해당 모듈 테스트를 `tests/rebalance/`에 새로 추가할 것.

### 11-4. 마이그레이션 순서

1. [x] (선행, 리스크 낮음) `data/rebalance.py` → `data/rebalance/` 패키지 분리, 기존 심볼
   재노출만 하고 로직은 그대로 복붙 — 기능 변화 없음, 테스트로 회귀 확인. **완료(2026-09-04)**
2. [x] `tests/test_backtest.py`의 리밸런싱 테스트를 `tests/rebalance/`로 이동. **완료(2026-09-04)**
3. [x] (8-H) `config.py` 신설 — 기존 함수 기본값(`top_n_by_market`, `band_multiplier`)을
   `RebalanceConfig` 기본값으로 옮기고, `ui/auto_trading_tab.py`의 중복 클래스 상수를
   `RebalanceConfig()` 참조로 교체. **완료(2026-09-04)** — `data/rebalance/config.py`
   신설(8-2 스펙대로 `max_per_sector`/`vkospi_elevated_z`/`vkospi_crisis_z`/
   `sizing_method`/`max_position_weight`까지 A/B/C 대비 필드 선반영, 아직 미사용).
   `classify.py`의 `_DEFAULT_TOP_N_BY_MARKET`, `signals.py`/`backtest.py`의
   `band_multiplier` 기본값이 전부 `RebalanceConfig()`를 참조하도록 교체.
   `ui/auto_trading_tab.py`의 `TOP_N_BY_MARKET`/`BAND_MULTIPLIER` 클래스 상수도
   `RebalanceConfig()` 인스턴스에서 파생하도록 교체 — 8-2가 지적한 이중 정의 해소.
   `data/rebalance/__init__.py` → `data/__init__.py` → `data_fetcher.py` 3단 facade에
   `RebalanceConfig`를 추가 재노출, `pytest tests/` 85건 통과로 회귀 확인.
4. [x] (8-I) `compute_weekly_rebalance_signals`을 `classify.py` → `signals.py`로 이동, 아직은
   섹터/레짐 훅 없이 순수 이동만(회귀 없음을 테스트로 확인). **11-4 1단계(패키지 분리) 실행
   시 처음부터 `signals.py`에 배치해 함께 완료됨** — `classify.py`에는 `compute_weekly_rebalance_signals`가
   있었던 적이 없어 별도 이동 작업이 불필요했음.
5. 1~4가 master에 merge된 뒤에야 8-A/8-B/8-C/8-F/8-D/8-E/8-G를 여러 에이전트에 병렬
   배분(12장) — 이 시점부터 `data/rebalance/` 안의 파일들이 실제로 서로 겹치지 않는다.

---

## 12. 멀티 에이전트 개발 방법론

> 이 장은 기존 `multi_agent_guide.md`를 이 문서로 통합한 것이다 — 앞으로 매매/포트폴리오
> 추천 알고리즘 관련 개발은 전부 이 문서 안에서 계획하고 기록한다. 11장의 소스 구조와
> 8장의 다음 단계 목록을 그대로 재료로 써서, "역할을 어떻게 나누고 동시에 어떻게 지시할지"를
> 구체적으로 다룬다.

### 12-1. 배경 — 지금까지 이 프로젝트에서 실제로 있었던 일

| 에이전트 | 실행 위치 | 실행 권한 | 실제로 한 일 |
|---------|----------|-----------|------------------------------|
| Claude (이 세션) | 클라우드 샌드박스 + 파일 브릿지 | 쉘/git 직접 실행 불가 — 파일 읽기·쓰기와 커맨드 안내만 가능 | 문서화(ROADMAP.md/trading.md), 단기 로드맵 코드 구현, git 설정 안내, 매매이력 분석(10장) |
| Antigravity CLI (Sonnet 모델) | 사용자 PC 로컬 쉘 | 파일 시스템·git·pytest 전권 | 3-2 테스트 인프라, 3-1 모듈화(`ui/`/`threads/`/`data/` 분리), **이 문서의 3-1 알고리즘 실제 구현(`data/rebalance.py`, `ui/auto_trading_tab.py`)**, 이후 지속적인 성능/버그 개선 |
| 사용자 | PC | 최종 결정권자 | 제안 검토·승인, git 커맨드 실행, 병합 결정 |

지금까지는 **"한 번에 한 에이전트만 저장소를 만지고 번갈아 작업"**하는 방식이었고, 이
방식이 성립하려면 한쪽이 시작하기 전에 항상 최신 상태를 확인해야 했다(git이 없으면 아예
불가능 — 실제로 git 도입 전엔 "고쳤다는데 반영이 안 됨" 혼란이 있었다). 아래부터는 이를
한 단계 발전시켜 **진짜 동시에 서로 다른 작업을 지시하는 방법**을 다룬다.

### 12-2. 핵심 원칙

- **git 없이는 멀티 에이전트를 하지 않는다.** git이 "지금 상태가 어느 에이전트가 만든
  것인지"를 구분하는 유일한 방법이다.
- **파일 단위가 아니라 브랜치/모듈 단위로 나눈다.** 11장에서 `data/rebalance/`를 미리
  패키지화해 두는 이유가 바로 이것 — 모듈 경계가 곧 작업 분할 경계가 되게 만든다.
- **큰 작업은 별도 브랜치 + 단계별 원자적 커밋.**
- **병합 전 검증(테스트)을 강제한다.**
- **원치 않는 결과는 삭제 대신 revert 커밋으로 남긴다.**
- **커밋 메시지에 작업 주체(에이전트/모델)를 남긴다.**
- **이 문서 자체를 에이전트 간 공유 상태판으로 쓴다** — 실제로 `ui/auto_trading_tab.py`가
  이 문서의 섹션 번호를 코드 docstring에 인용하고 있을 정도로 이미 그렇게 쓰이고 있다.

### 12-3. 파일시스템 격리 — `git worktree`

같은 폴더를 두 에이전트가 동시에 건드리면 한쪽이 저장하는 순간 다른 쪽이 읽던 내용과
어긋난다. **진짜 동시 작업의 전제 조건은 각 에이전트가 서로 다른 물리적 폴더에서 일하는
것**이다.

```powershell
cd "D:\Source Code\Portfolio Management"

git worktree add ../PM-agentA -b feat/rebalance-sector-cap      # Agent A (8-A)
git worktree add ../PM-agentB -b feat/rebalance-regime-overlay  # Agent B (8-B)
```

작업이 끝나면:

```powershell
cd "D:\Source Code\Portfolio Management"
git worktree remove ../PM-agentA
git worktree remove ../PM-agentB
```

> 이 세션(Claude)은 사용자 PC에서 쉘 명령을 직접 실행하지 못하므로, worktree 생성 자체는
> 사용자가 위 명령을 실행해야 한다. 이후 `PM-agentB` 폴더를 Claude 데스크톱 앱에서 추가로
> 연결하면 이 세션도 그 폴더를 대상으로 파일 브릿지 작업을 할 수 있다.

### 12-4. 작업 지시서 템플릿

```
[작업 지시서]
목표: <한 문장>
작업 폴더/브랜치: <예: D:\Source Code\PM-agentA, 브랜치 feat/rebalance-sector-cap>
허용 범위(이 파일/디렉토리만 수정): <예: data/rebalance/sector.py(신규), tests/rebalance/test_sector.py>
금지 범위(절대 건드리지 말 것 — 다른 에이전트가 동시에 작업 중): <예: data/rebalance/classify.py, ui/auto_trading_tab.py>
완료 기준: <예: pytest 전부 통과, trading.md 8장 해당 항목에 근거 남길 것>
완료 후 보고 형식: <예: 커밋 해시, 변경 파일 목록, 테스트 결과>
```

### 12-5. 실전 예시 — 8장 다음 단계를 동시에 배분

8장 표를 그대로 이용한다. **선행 조건: 11-4의 1~4단계(패키지화, `config.py`, `signals.py`
분리)가 master에 병합되어 있어야 아래 배분이 성립한다** — H/I 없이 A/B를 먼저 시키면
결국 `classify.py`에서 둘 다 충돌한다.

- **Agent A (Antigravity CLI, `PM-agentA`, `feat/rebalance-sector-cap`)** — 8-A 섹터 집중도
  제약. 허용 범위: `data/rebalance/sector.py`(신규), `tests/rebalance/test_sector.py`,
  `data/rebalance/signals.py`(섹터cap 호출 **한 줄 추가만**, 기존 로직 수정 금지). 금지
  범위: `data/rebalance/classify.py`, `ui/`. 참고 자료: 2장의 pykrx
  `get_market_sector_classifications()`, 8-1-A 스펙. 완료 기준: 섹터별 최대 비중 제약 함수
  + pytest.
- **Agent B (Antigravity CLI 두 번째 인스턴스 또는 사용자가 시차를 두고 실행,
  `PM-agentB`, `feat/rebalance-regime-overlay`)** — 8-B VKOSPI 레짐 오버레이. 허용 범위:
  `data/rebalance/regime.py`(신규), `tests/rebalance/test_regime.py`,
  `data/rebalance/signals.py`(레짐 호출 **한 줄 추가만**). 금지 범위:
  `data/rebalance/classify.py`, `ui/`. 참고 자료: `data/collectors/krx.py`의
  `fetch_vkospi_history()`, 8-1-B 스펙.
- **Agent C (순차, A/B 병합 후 진행)** — 8-C 포지션 사이징 개선. 허용 범위:
  `data/rebalance/sizing.py`(신규), `data/rebalance/walkforward.py`(alloc 계산 한 줄 교체).
  A/B와 파일이 거의 겹치지 않지만, `signals.py`를 함께 건드릴 수 있어 A/B 병합 이후 시작
  권장.
- **Agent D (언제든 독립적으로)** — 8-D 페이퍼 트레이딩 로그. 허용 범위: `trade_db.py`(신규
  테이블), `ui/auto_trading_tab.py`. A/B/C와 파일이 거의 무관해 아무 때나 진행 가능.

A와 B가 `signals.py`를 동시에 건드리는 것(각자 호출 한 줄)은 12-2의 "파일 단위가 아니라
모듈 단위로 나눈다" 원칙에 대한 유일한 예외다 — 8-2에서 설명한 대로 "로직 수정"이 아니라
"호출 추가"이기 때문에 허용하되, 병합 시 `signals.py`의 diff만은 반드시 사람이 눈으로
확인한다(12-6의 자동 diff 확인만으로 충분하지 않은 유일한 파일).

### 12-6. 병합 프로토콜 — 작업은 병렬, 통합은 직렬

```powershell
cd "D:\Source Code\Portfolio Management"
git checkout master
git merge feat/rebalance-sector-cap        # A 먼저
# pytest 실행 — 통과 확인
git merge feat/rebalance-regime-overlay    # A 반영된 master 기준으로 B
# pytest 다시 실행
```

두 번째 병합 전에는 `git diff master...feat/rebalance-regime-overlay`로 A의 변경과 실제
충돌 여부를 마지막으로 확인한다.

### 12-7. 공용 파일(trading.md, ROADMAP.md, requirements.txt 등) 다루는 법

- 이 문서(trading.md)처럼 "현재 상태"를 기록하는 문서는 각 에이전트가 완료한 뒤, 병합이
  끝난 시점에 한 번만 갱신 — 8장/9장처럼 번호가 붙은 항목 단위로 기록해 두면 "이번엔
  8-A만 체크 표시"처럼 부분 갱신이 쉬워진다.
- 정말 동시에 여러 명이 이 문서에 쓸 일이 생기면, 서로 다른 장(예: 8장은 A가, 10장은 B가)에만
  쓰게 하면 텍스트 충돌이 나더라도 git이 대부분 자동으로 풀어준다.
- `requirements.txt`처럼 항목 추가형 파일은 서로 다른 패키지를 추가하는 한 병합 시 문제
  없음.

### 12-8. 도구별 특성 비교

| | Claude (이 세션) | Antigravity CLI |
|---|---|---|
| 실행 위치 | 클라우드 샌드박스 | 사용자 PC 로컬 |
| 쉘/git 직접 실행 | 불가 | 가능 |
| 동시 작업(worktree) 투입 시 역할 | 별도 연결된 worktree 폴더에서 파일 단위 구현, 브랜치/커밋/테스트는 사용자가 대행 | worktree 생성부터 커밋·테스트·병합까지 스스로 실행 가능 |
| 강점 | 계획 수립, 문서화, 코드 리뷰, 소규모 변경 | 멀티 파일 리팩토링, pytest 실행·반복 수정 루프 |
| 리스크 | 실행을 못 하니 "적용됐다고 착각"하기 쉬움 | 확인 없이 범위를 넓게 바꿀 수 있어 범위 통제 필요 (`--mode=accept-edits`, `toolPermission`, `permissions.allow/deny`로 조절) |

### 12-9. 체크리스트

**시작 전**: `git status` 확인 · 필요하면 worktree 분리(12-3) · 8장 표에서 파일이 겹치지
않는 항목만 동시 배분 · 12-4 템플릿으로 지시서 작성

**작업 중**: 단계별 원자적 커밋 유도 · 범위 확장 감지 시 즉시 개입 · 금지 범위 침범 조짐
보이면 중단

**병합 전**: 테스트 실행·기록 · `git diff`로 계획 대비 범위 확인 · 12-6대로 순차 병합 ·
원치 않는 결과는 revert 커밋 · 커밋 메시지에 작업 주체 남기기 · 이 문서(8·9장) 갱신

---

## 13. 변경 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-08-28 | 최초 작성 — 배경, 기존 코드 자산 정리, 후보 접근법 4가지, 공통 고려사항, 앱 통합 방안, 검증 계획 정리 |
| 2026-08-31 | `portfolio.db` 실제 매매 이력(88건) 분석 결과를 "기존 매매 이력 분석(베이스라인)" 장으로 추가 |
| 2026-09-04 | Antigravity CLI가 실제 구현한 3-1 팩터 스코어링 알고리즘(`data/rebalance.py`, `ui/auto_trading_tab.py`)을 확인해 3~7장을 "구현 결과" 기준으로 갱신, 4장(현재 구현 상태)·8장(다음 단계)·9장(결정사항 체크) 신설/갱신 |
| 2026-09-04 | 11장 "소스 코드 구성안"(`data/rebalance.py` → `data/rebalance/` 패키지화 제안) 신설, 12장 "멀티 에이전트 개발 방법론" 신설 — 기존 `multi_agent_guide.md`를 이 문서로 통합하고 8장 항목을 예시로 한 구체적 동시 작업 배분안 추가. 이후 매매/포트폴리오 알고리즘과 SW 개발 방법론은 모두 이 문서 하나에서 관리(사용자 요청) — `multi_agent_guide.md`는 삭제 |
| 2026-09-04 | 8장 항목(A~G)을 8-1에서 실제 구현 가능한 수준(공식·파라미터·연결 지점)까지 구체화, 8-2에 파라미터 중복 문제를 근거로 `config.py`(8-H)/`signals.py`(8-I) 선행 작업 추가 — 11장 구조안과 12장 배분 예시를 이 선행 작업 반영해 갱신(사용자 요청: "리밸런싱 알고리즘을 더 구체적으로 구현할 필요, 이를 고려한 파일 구조 개선 필요성 검토") |
| 2026-09-04 | 11-4 마이그레이션 1~2단계 실행(사용자 요청) — `data/rebalance.py`(22.7KB, 12개 함수)를 `data/rebalance/{factors,classify,signals,walkforward,backtest}.py` + `__init__.py`(재노출)로 패키지화, `tests/test_backtest.py`의 리밸런싱 테스트 2개 클래스를 `tests/rebalance/{test_factors,test_walkforward}.py`로 이동. 로직 변경 없음 — `data/__init__.py`/`data_fetcher.py`/`ui/auto_trading_tab.py`/`tests/`는 전부 `data_fetcher` facade를 거쳐서만 참조하고 있어 무수정으로 통과, `pytest tests/` 85건 전부 통과로 회귀 확인. 3~5단계(`config.py`/`signals.py`로 `compute_weekly_rebalance_signals` 이관/8-A~8-C 병렬 배분)는 아직 미착수 |
| 2026-09-04 | 11-4 마이그레이션 3단계(8-H) 실행(사용자 요청: "나머지 구현" → 범위 확인 후 3단계만 진행) — `data/rebalance/config.py`에 `RebalanceConfig` dataclass 신설(8-2 스펙: `top_n_by_market`/`band_multiplier` 외 8-A/B/C용 필드 5개 선반영, 아직 미사용). `classify.py`의 `_DEFAULT_TOP_N_BY_MARKET`, `signals.py`/`backtest.py`의 `band_multiplier` 기본값을 `RebalanceConfig()` 참조로 교체하고, `ui/auto_trading_tab.py`의 `TOP_N_BY_MARKET`/`BAND_MULTIPLIER` 클래스 상수(8-2가 지적한 중복 정의)도 동일 인스턴스에서 파생하도록 교체. `data/rebalance/__init__.py`→`data/__init__.py`→`data_fetcher.py` 3단 facade에 `RebalanceConfig` 재노출 추가. `pytest tests/` 85건 통과로 회귀 확인. 8-I는 11-4 1단계 때 `compute_weekly_rebalance_signals`를 처음부터 `signals.py`에 배치해 이미 완료 상태였음을 확인. 4~5단계(8-A~8-C 등 병렬 배분)는 사용자가 범위를 3단계로 한정해 미착수 |
