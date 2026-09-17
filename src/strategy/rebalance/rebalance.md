# Rebalance Strategy — 주간 포트폴리오 재구성 알고리즘

> 기준일: 2026-09-17 (최초 작성 2026-08-28, 2026-09-17 `trading.md`에서 분리)
> 목적: 주간(금요일 기준) 단위로 Trading Universe 종목을 스코어링하고 포트폴리오를
> 리밸런싱하는 알고리즘의 설계·구현 현황과 다음 단계를 기록하는 스펙 문서. 코드는 이 폴더
> (`src/strategy/rebalance/`)에 있고, 코드 주석·커밋 메시지는 이 문서의 절 번호를 인용한다.
> 문서 분리(2026-09-17): 원래 저장소 루트 `trading.md`의 1~9장·11장·13장을 그대로 옮겼다.
> 절 번호는 코드/문서의 기존 인용("3-1", "8-H", "11-2" 등)이 그대로 맞도록 **원본 번호를
> 유지**했으므로 10장·12장이 비어 있다 — 10장(기존 매매 이력 베이스라인)과 12장(멀티 에이전트
> 개발 방법론)은 특정 전략에 종속되지 않는 내용이라 `trading.md`에 남아 있고, 이 문서 안의
> "trading.md 10장/12장/12-x" 참조는 그쪽을 가리킨다.
> 관련 파일: `strategy/rebalance/`(신호/백테스트 로직), `ui/auto_trading_tab.py`(수동 트리거 UI),
> `tests/strategy/rebalance/`(테스트)
> 관련 문서: `trading.md`(개발 방법론·베이스라인), `roadmap.md`(전체 로드맵),
> `trend_following.md`(추세추종 — 별개 트랙, 코드/문서 미공유)

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
11. [소스 코드 구성안](#11-소스-코드-구성안)
13. [변경 이력](#13-변경-이력)

(10장·12장은 `trading.md` 참고 — 위 헤더 설명)

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
| `run_backtest_strategy()` | `strategy/ma_cross.py` | MA 이격도/데드크로스 매도 조건, +30% 익절 조건이 벡터화되어 검증 완료된, 종목별 개별 백테스트 로직 (리밸런싱과는 별개의 기존 자산) |
| `compute_weekly_rebalance_signals()` / `run_rebalance_backtest()` | `data/rebalance.py` | **4장의 실제 구현 결과물** — 이 문서의 최초 작성 시점엔 미구현이었으나 이후 완성됨 |
| `fetch_stock_ma_multi()` | `data/market.py` | 개별 종목 MA10/20/60 시계열 |
| `fetch_investor_trend()` / `_fetch_index_investor_trend()` | `data/collectors/kis.py`, `data/collectors/naver.py` | 기관/외국인 순매수 동향 — 아직 리밸런싱 팩터로는 미사용 (8장 참고) |
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

> `strategy/rebalance/`(구 `data/rebalance.py`, 22.7KB, 12개 함수 — 2026-09-04 패키지화, 11-2)와
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

trading.md 12장의 멀티 에이전트 작업 분배 예시가 이 목록을 그대로 사용한다 — 각 항목이 서로 다른
파일을 건드리도록 의도적으로 골랐다(11장의 구조 개편 이후 기준). 8-1에서 각 항목을
"바로 구현에 들어갈 수 있는 수준"까지 구체화했다 — 지금까지는 이름만 있었던 지점.

| # | 항목 | 근거 | 예상 작업 파일 |
|---|------|------|----------------|
| A | 섹터 집중도 제약 | 5장 "섹터/시장 집중도", ROADMAP 4-5 | `strategy/rebalance/sector.py`(신규) |
| B | VKOSPI 레짐 오버레이 | 3-3에서 이관 — 시장 변동성 급등 시 매수 축소 | `strategy/rebalance/regime.py`(신규) |
| C | 포지션 사이징 개선 (동일가중 → 변동성 역가중 옵션) | 5장 "포지션 사이징" | `strategy/rebalance/sizing.py`(신규) |
| D | 페이퍼 트레이딩 로그 | 7장 검증계획 2단계 | `trade_db.py`(신규 테이블), `ui/auto_trading_tab.py` |
| E | ROADMAP.md에 Auto Trading 완료 항목 등재 | 4-6 | `ROADMAP.md` |
| F | 밸류 팩터 확장 (PER 외 PBR/ROE/배당수익률) | 2장 `fetch_quarterly_financials` 미사용 | `strategy/rebalance/factors.py` |
| G | 백테스트 실행 결과를 7장에 기록 | 7장 | `trading.md` (문서만) |
| H | ✅ 전략 파라미터 통합 관리 (`RebalanceConfig`) — 완료(2026-09-04) | 8-2 — A/B/C가 늘리는 파라미터를 한 곳에서 관리 | `strategy/rebalance/config.py`(신규, **A/B/C보다 선행**) |
| I | `compute_weekly_rebalance_signals` 오케스트레이터 분리 | 8-2 — sector/regime 훅을 `classify.py` 안 건드리고 연결 | `strategy/rebalance/signals.py`(신규, `classify.py`에서 이관, **A/B보다 선행**) |

A/B/F는 신규 팩터·모듈 추가라 서로 파일이 겹치지 않아 동시 진행에 가장 적합하고, C는
신규 파일(`sizing.py`)로 분리했으므로 A/B와도 병행 가능해졌다. D/E/G는 언제든 독립적으로
진행 가능. **H/I는 A/B/C가 공유하는 기반이라 먼저 끝나 있어야 한다** — 순서는 11-4 / trading.md 12-5 참고.

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

**제안**: `strategy/rebalance/config.py`에 `RebalanceConfig` dataclass 하나로 전략 파라미터를
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
조합하는 별도의 오케스트레이터가 필요해서 `strategy/rebalance/signals.py`로 분리하는 것을
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
- [x] `src/strategy/` 최상위 폴더로 재구성 — `data/`는 순수 데이터 계층만, 전략 로직(`rebalance/`,
      `trend_following/`, 개별종목 백테스트 `ma_cross.py`)은 `strategy/`로 이동 (11-5, 2026-09-17 완료)

---

## 11. 소스 코드 구성안

> 3장~4장의 알고리즘을 계속 확장하면서, 동시에 trading.md 12장처럼 여러 에이전트에게 병렬로 일을
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
    kis.py, krx.py, naver.py, yahoo.py
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

> 이 절의 경로는 2026-09-04 당시 기준. 패키지는 2026-09-17 11-5에 따라 `strategy/rebalance/`로
> 이동했고(내부 구성은 동일), 테스트는 `tests/strategy/rebalance/`에 있다.

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
   배분(trading.md 12장) — 이 시점부터 `data/rebalance/` 안의 파일들이 실제로 서로 겹치지 않는다.

### 11-5. `src/strategy/` 최상위 전략 폴더로 재구성 — ✅ 완료 (2026-09-17)

> **실행 결과 (2026-09-17, CLI 세션)**: 아래 계획의 1~7단계 완료. `data/rebalance/` →
> `strategy/rebalance/`(사용자가 폴더 이동, 내부 `data.rebalance.*` 절대 import 6개 파일을
> `strategy.rebalance.*`로 갱신), `data/backtest.py` → `strategy/ma_cross.py`,
> `strategy/__init__.py`·`strategy/trend_following/__init__.py`(스캐폴드) 신설. `data/__init__.py`와
> `data_fetcher.py`에서 전략 심볼 재노출을 제거해 `data/`가 `strategy/`를 import하지 않도록 했고
> (순환 방지), 호출자 `ui/auto_trading_tab.py`·`threads/fetch_threads.py`는 `strategy.rebalance`를
> 직접 import. 테스트는 `tests/strategy/rebalance/`·`tests/strategy/test_ma_cross.py`로 이동,
> `tests/conftest.py`로 `src/` sys.path 설정 일원화. 8단계(archive 백업)는 git 관리 전환으로 불필요.
> 검증: pytest 119/119, ruff 0건.

> 별도 트랙인 리버모어 추세추종 알고리즘(`trend_following.md`) 작업을 시작하면서, `data/`
> 폴더 하나에 성격이 다른 두 가지가 섞여 있다는 점이 드러났다 — `market.py`/`indicators.py`/
> `cache.py`/`collectors/`처럼 순수 데이터 접근 계층과, `rebalance/`처럼 실제 매매 전략
> 로직이 같은 폴더 밑에 있다. `data`라는 이름이 전략 코드까지는 설명하지 못하므로,
> **전략 관련 코드는 전부 `src/strategy/`로 옮기고 `data/`는 순수 데이터 계층만 남긴다.**

**대상 (전략 로직이므로 이동)**
- `data/rebalance/` → `strategy/rebalance/` (7개 파일, 동작 중인 코드 + 테스트 있음)
- `data/backtest.py`(개별 종목 MA20/60 골든크로스 전략, `run_backtest_strategy` 등) →
  `strategy/`(신규 파일명 미정 — 예: `ma_cross.py`. 3-4에서 다룬 것처럼 리밸런싱과는 별개
  자산이지만 이것도 "매매 전략 로직"이므로 포함)
- `data/trend_following/` → `strategy/trend_following/` (아직 코드 없이 `trend_following.md`
  문서만 있는 상태라 이동 비용이 가장 낮음 — 처음부터 이 경로에 구현 시작 권장)

**남는 것 (순수 데이터 계층, `data/`에 유지)**
- `market.py`, `indicators.py`, `cache.py`, `collectors/`, `__init__.py`(facade)

**마이그레이션 순서 (제안)**
1. `src/strategy/__init__.py` 신설
2. `data/trend_following/` → `strategy/trend_following/` (코드 없음, 가장 먼저·가장 안전하게 실행 가능)
3. `data/rebalance/` → `strategy/rebalance/` — `git mv`로 히스토리 보존, 내부 상대 import는
   변경 없음(패키지 내부끼리의 참조라 경로만 이동)
4. `data/backtest.py` → `strategy/ma_cross.py`(가칭, 이름은 CLI 세션에서 확정)
5. import 갱신: `data/__init__.py`, `data_fetcher.py`, `ui/auto_trading_tab.py`,
   `run_backtest_strategy`를 호출하는 UI 코드, `tests/rebalance/` → `tests/strategy/rebalance/`,
   `tests/test_backtest.py`의 개별종목 백테스트 테스트도 위치 재검토
6. 이 문서(`trading.md`) 안의 `data/rebalance/...` 경로 표기 전부 `strategy/rebalance/...`로 갱신
   (11-2, 11-4, 8장 표 등 다수 인용 — 일괄 치환 후 재검토 필요)
7. `pytest tests/` 전체 통과 확인 (기존 85건 기준선 유지)
8. CLAUDE.md의 archive 백업 관례대로, 변경 전 `archive/backup_<timestamp>/`에 원본 백업

**주의**: 이 재구성은 동작 중인 코드·테스트·문서 경로를 광범위하게 건드리므로 CLI(Claude
Code) 세션에서 실행하고 회귀 테스트로 확인할 것. 파일 이동/삭제 도구가 없는 원격 세션에서
실행하면 새 경로에 복사본만 늘고 기존 파일이 남아 두 벌이 되는 문제가 있어, 실제 이동은
여기서 수행하지 않았다.

---

## 13. 변경 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-08-28 | 최초 작성 — 배경, 기존 코드 자산 정리, 후보 접근법 4가지, 공통 고려사항, 앱 통합 방안, 검증 계획 정리 |
| 2026-08-31 | `portfolio.db` 실제 매매 이력(88건) 분석 결과를 "기존 매매 이력 분석(베이스라인)" 장으로 추가 |
| 2026-09-04 | Antigravity CLI가 실제 구현한 3-1 팩터 스코어링 알고리즘(`data/rebalance.py`, `ui/auto_trading_tab.py`)을 확인해 3~7장을 "구현 결과" 기준으로 갱신, 4장(현재 구현 상태)·8장(다음 단계)·9장(결정사항 체크) 신설/갱신 |
| 2026-09-04 | 11장 "소스 코드 구성안"(`data/rebalance.py` → `data/rebalance/` 패키지화 제안) 신설, trading.md 12장 "멀티 에이전트 개발 방법론" 신설 — 기존 `multi_agent_guide.md`를 이 문서로 통합하고 8장 항목을 예시로 한 구체적 동시 작업 배분안 추가. 이후 매매/포트폴리오 알고리즘과 SW 개발 방법론은 모두 이 문서 하나에서 관리(사용자 요청) — `multi_agent_guide.md`는 삭제 |
| 2026-09-04 | 8장 항목(A~G)을 8-1에서 실제 구현 가능한 수준(공식·파라미터·연결 지점)까지 구체화, 8-2에 파라미터 중복 문제를 근거로 `config.py`(8-H)/`signals.py`(8-I) 선행 작업 추가 — 11장 구조안과 trading.md 12장 배분 예시를 이 선행 작업 반영해 갱신(사용자 요청: "리밸런싱 알고리즘을 더 구체적으로 구현할 필요, 이를 고려한 파일 구조 개선 필요성 검토") |
| 2026-09-04 | trading.md 12-9 "Claude Code 자체의 멀티 에이전트 기능" 신설 — Subagent/`--worktree`/Agent teams/Agent view/Dynamic workflows 5가지 공식 기능을 정리하고, 8장 A~D 예시를 Claude Code 서브에이전트(`isolation: worktree`)로 구현하는 방법을 trading.md 12-3/12-4의 수동 절차에 대한 대안으로 추가(사용자 요청: "claude code 내에서 multi agent로 구현하는 방법") |
| 2026-09-04 | 11-4 마이그레이션 1~2단계 실행(사용자 요청) — `data/rebalance.py`(22.7KB, 12개 함수)를 `data/rebalance/{factors,classify,signals,walkforward,backtest}.py` + `__init__.py`(재노출)로 패키지화, `tests/test_backtest.py`의 리밸런싱 테스트 2개 클래스를 `tests/rebalance/{test_factors,test_walkforward}.py`로 이동. 로직 변경 없음 — `data/__init__.py`/`data_fetcher.py`/`ui/auto_trading_tab.py`/`tests/`는 전부 `data_fetcher` facade를 거쳐서만 참조하고 있어 무수정으로 통과, `pytest tests/` 85건 전부 통과로 회귀 확인. 3~5단계(`config.py`/`signals.py`로 `compute_weekly_rebalance_signals` 이관/8-A~8-C 병렬 배분)는 아직 미착수 |
| 2026-09-04 | 11-4 마이그레이션 3단계(8-H) 실행(사용자 요청: "나머지 구현" → 범위 확인 후 3단계만 진행) — `data/rebalance/config.py`에 `RebalanceConfig` dataclass 신설(8-2 스펙: `top_n_by_market`/`band_multiplier` 외 8-A/B/C용 필드 5개 선반영, 아직 미사용). `classify.py`의 `_DEFAULT_TOP_N_BY_MARKET`, `signals.py`/`backtest.py`의 `band_multiplier` 기본값을 `RebalanceConfig()` 참조로 교체하고, `ui/auto_trading_tab.py`의 `TOP_N_BY_MARKET`/`BAND_MULTIPLIER` 클래스 상수(8-2가 지적한 중복 정의)도 동일 인스턴스에서 파생하도록 교체. `data/rebalance/__init__.py`→`data/__init__.py`→`data_fetcher.py` 3단 facade에 `RebalanceConfig` 재노출 추가. `pytest tests/` 85건 통과로 회귀 확인. 8-I는 11-4 1단계 때 `compute_weekly_rebalance_signals`를 처음부터 `signals.py`에 배치해 이미 완료 상태였음을 확인. 4~5단계(8-A~8-C 등 병렬 배분)는 사용자가 범위를 3단계로 한정해 미착수 |
| 2026-09-04 | 11-4 1~3단계 완료 기록과 trading.md 12-9(Claude Code 멀티 에이전트 기능) 신설이 서로 다른 세션에서 동시에 편집되어 충돌 — 12-9는 유지하고 11-4 완료 기록(위 두 항목)을 그 위에 재적용해 병합(사용자 요청: "두 변경 병합"). 문서 동시 편집 시 trading.md 12-2("git 없이는 멀티 에이전트를 하지 않는다")의 실제 사례로, 향후 12-9에 이 케이스를 교훈으로 보강할 가치가 있음 |
| 2026-09-09 | trading.md 12-11 "실전 사례 1"로 위 문서 충돌 사건을 정식 케이스 스터디화(원인·교훈 정리, 12-7에 "코드 변경과 문서 갱신을 같은 커밋으로 묶는다" 규칙 추가), trading.md 12-12 "실전 사례 2" 신설 — 11-4 1~4단계가 모두 완료되어 12-5의 선행 조건이 충족된 지금 시점 기준으로, 8-A/8-B를 실제로 동시 실행하는 전체 절차(worktree 생성 → 각 에이전트에게 그대로 전달할 프롬프트 전문 → 병합 → 문서 갱신)를 실제 `data/rebalance/config.py`/`signals.py`/`classify.py` 코드에 맞춰 구체화(사용자 요청: "multi agent를 이용한 코딩의 구체적인 케이스 및 구현 방법을 만들어서 trading.md에 예시로 추가") |
| 2026-09-17 | 11-5 실행(사용자가 `data/rebalance/`를 `strategy/rebalance/`로 이동한 뒤 CLI 세션에서 마무리): `strategy/__init__.py` 신설, 이동된 패키지의 절대 import를 `strategy.rebalance.*`로 갱신, `data/backtest.py` → `strategy/ma_cross.py`, `data/__init__.py`·`data_fetcher.py`에서 전략 재노출 제거(`data/`는 `strategy/`를 import하지 않음), `ui/auto_trading_tab.py`·`threads/fetch_threads.py`가 `strategy.rebalance` 직접 import, `tests/rebalance/` → `tests/strategy/rebalance/`, `tests/test_backtest.py` → `tests/strategy/test_ma_cross.py`, `tests/conftest.py` 신설. 이 문서의 현행 경로 표기(8장 표, trading.md 12장 프롬프트 예시 등)를 `strategy/rebalance/`·`tests/strategy/rebalance/`로 일괄 갱신 (11-2/11-4/변경 이력의 과거 기록은 유지). 9장 체크리스트 11-5 항목 완료 처리. |
| 2026-09-17 | 문서 분리: 저장소 루트 `trading.md`의 1~9장·11장·13장(변경 이력)을 이 파일(`src/strategy/rebalance/rebalance.md`)로 이동(사용자 요청, 절 번호 유지). 10장(베이스라인)·12장(개발 방법론)은 `trading.md`에 잔류. 코드 주석·UI 문자열·다른 문서의 `trading.md N-x` 인용을 `rebalance.md N-x`로 갱신. |
