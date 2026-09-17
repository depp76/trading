# MA Cross — 개별 종목 MA20/MA60 골든크로스 백테스트

> 기준일: 2026-09-17 (코드는 2026-08-12 2차 최적화 때 벡터화된 `data_fetcher.run_backtest_strategy`가
> 원형 — `data/backtest.py`를 거쳐 2026-09-17 `strategy/ma_cross/`로 이동)
> 목적: 이 폴더의 코드(`backtest.py`)가 구현하는 전략 규칙과 산출물을 문서화한다. 다른 전략
> 문서(`rebalance.md`, `trend_following.md`)와 달리 이 문서는 먼저 존재하던 코드를 **역으로
> 정리한 것**이라, 규칙의 근거·파라미터 선택 이유는 코드에 남아 있지 않아 기록하지 못했다.
> 관련 파일: `strategy/ma_cross/backtest.py`, `tests/strategy/test_ma_cross.py`,
> `data/market.py::fetch_stock_ma_multi` / `data/indicators.py::_compute_indicators`(입력 데이터)
> 관련 문서: `rebalance.md` 3-4("기존 백테스트 로직 재사용 — 별도 유지"),
> `trend_following.md` 2장(설계 패턴 참고 자산으로 언급)

---

## 목차

1. [배경 및 현재 상태](#1-배경-및-현재-상태)
2. [입력 데이터](#2-입력-데이터)
3. [전략 규칙](#3-전략-규칙)
4. [함수 구성](#4-함수-구성)
5. [테스트](#5-테스트)
6. [알려진 한계 / 다음 단계](#6-알려진-한계--다음-단계)
7. [변경 이력](#7-변경-이력)

---

## 1. 배경 및 현재 상태

- 개별 종목 단위로 "MA20이 MA60을 10% 이상 상향 돌파하면 매수, 목표 수익 또는 추세 이탈 시
  매도"를 과거 데이터에 적용해 트레이드 수·승수·누적 수익률을 구하는 벡터화(numpy) 백테스트.
- 리밸런싱 알고리즘(`rebalance.md`, 종목군 단위 주간 스코어링)과는 별개 자산이며 함수를
  공유하지 않는다 (`rebalance.md` 3-4).
- **현재 UI 연결 없음** — `src/ui/`, `src/threads/`에서 이 패키지를 호출하는 곳이 없다
  (2026-09-17 확인). 호출자는 `tests/strategy/test_ma_cross.py`뿐이다. `roadmap.md` 4-4
  "백테스트 — 전략 비교 UI"가 이 로직을 다시 화면에 올리는 후보 작업이다.
- 패키지 구성은 `strategy/__init__.py`의 규칙(전략별 폴더 + `<name>.md`)을 따르되, 코드는
  `backtest.py` 하나다 — `config.py`/`signals.py` 분리는 6장 참고.

---

## 2. 입력 데이터

`run_backtest_strategy(df)`는 다음 컬럼을 가진 polars DataFrame을 받는다(날짜 오름차순):

| 컬럼 | 용도 |
|------|------|
| `Date` | 매수/매도 일자 기록, `target_year` 필터 |
| `Open` | 매도 체결가 (신호 다음 날 시가) |
| `Close` | 매수 체결가 (신호 당일 종가), +30% 익절 판정 |
| `MA20`, `MA60` | 진입/청산 신호 |

- 단일 종목 경로: `run_backtest_for_stock()` → `data.market.fetch_stock_ma_multi(ticker, market,
  windows=(10, 20, 60), days, target_year)` — KR 종목/지수는 Naver, 그 외는 FDR에서 OHLCV를
  받아 `_compute_indicators()`로 MA/RSI를 붙인다.
- 대량 경로: `run_bulk_backtest_chunk()` → yahooquery `Ticker(...).history()`로 여러 종목을 한
  번에 받아 종목별로 `_compute_indicators()` 후 위 함수를 호출. 60행 미만인 종목은 단일 경로로
  폴백한다. KR 코드는 `.KS`/`.KQ` 접미사로 변환.
- `_compute_indicators()`가 RSI14도 계산하지만 **이 전략은 RSI를 사용하지 않는다** (아래 3장).
  옛 docstring의 "+ RSI"는 잔재라 2026-09-17에 제거.

---

## 3. 전략 규칙

모든 판단은 t일 값으로 하고 체결은 다음과 같이 한다 (look-ahead 없음).

**진입 (매수)**
- 조건: 전일 `MA20 <= MA60 × 1.10` 이고 당일 `MA20 > MA60 × 1.10` — 즉 MA20이 MA60의 110%
  선을 상향 돌파하는 날.
- 체결: 신호 당일 종가(`Close[t]`).
- `target_year`가 주어지면 그 해에 발생한 진입 신호만 사용한다(데이터는 전년 9월부터 받아
  MA 워밍업을 확보 — `fetch_stock_ma_multi` 참고).
- 포지션은 동시에 하나만 — 이전 트레이드의 매도 신호일 이전에 발생한 진입 신호는 무시.

**청산 (매도)** — 진입 다음 날부터 아래 중 먼저 오는 날 `s`에 신호, 체결은 `s+1`일 시가(`Open[s+1]`)
1. 익절: 진입 이후 종가의 누적 최고가가 `매수가 × 1.30` 이상이 되는 첫날.
2. 과열: `MA20 >= MA60 × 1.30`.
3. 추세 이탈(데드크로스): `MA20 < MA60`.
- `s+1`이 데이터 범위를 벗어나면(마지막 날 신호) 그 트레이드는 집계하지 않는다 — 미청산
  포지션은 결과에 포함되지 않는다.

**집계**
- `trade_return = (매도가 − 매수가) / 매수가 × 100`
- `cumulative_return`은 트레이드 수익률의 **단순 합**(복리 아님), `win_count`는 `trade_return > 0`인
  트레이드 수.

---

## 4. 함수 구성

| 함수 | 역할 | 반환 |
|------|------|------|
| `run_backtest_strategy(df, buy_sell_points=False, target_year=None)` | 3장 규칙의 벡터화 구현 | `(total_trades, win_count, cumulative_return)`; `buy_sell_points=True`면 여기에 `buy_dates, buy_prices, sell_dates, sell_prices, bt_buy_date_list` 5개를 덧붙인 8-튜플 |
| `run_backtest_for_stock(ticker, market, days=1095, target_year=None, df=None)` | 데이터 조회 + 트레이드별 상세(보유일, 연환산 수익률) | `{"ticker", "trades": [{buy_date, buy_price, sell_date, sell_price, return_pct, days_held, ann_return}], "error"}` |
| `run_bulk_backtest_chunk(tickers, market, days=1095, target_year=None)` | yahooquery 일괄 조회 후 종목별 `run_backtest_for_stock` | 위 dict의 리스트 |

구현 메모: 청산 조건 2·3은 `np.minimum.accumulate`로 "각 날짜 이후 첫 충족일" 배열을 한 번
만들어 두고, 익절(조건 1)은 진입 이후 종가의 `np.maximum.accumulate` + `searchsorted`로 찾는다.
그래서 진입 신호 개수에만 비례하는 루프 한 개로 끝난다.

---

## 5. 테스트

`tests/strategy/test_ma_cross.py` (11개, 합성 데이터):
- 엣지: `None`/빈 DataFrame/MA 컬럼 누락 → `(0, 0, 0.0)`, 반환 튜플 길이 3/8.
- 신호: 평탄한 가격에 신호 없음, 승수 ≤ 트레이드 수, 매수/매도 리스트 길이 일치,
  `target_year=2099`에 트레이드 없음, 누적 수익률 = 개별 수익률 합.

실데이터 기준 성과 검증은 수행된 기록이 없다(6장).

---

## 6. 알려진 한계 / 다음 단계

- [ ] 파라미터(1.10 진입 배수, 1.30 익절/과열 배수, MA20/MA60 창)가 `backtest.py`에 리터럴로
      박혀 있다 — `config.py`의 `MaCrossConfig` dataclass로 분리 (`rebalance.md` 8-H와 같은 방식).
- [ ] 신호 생성(3장 진입/청산 조건)을 `signals.py`로 분리해 `backtest.py`는 체결·집계만 담당.
- [ ] 수수료·세금 미반영 (리밸런싱 백테스트는 반영함 — `rebalance.md` 7장 참고).
- [ ] 누적 수익률이 단순 합이라 복리·자본 배분을 반영하지 않는다. 에쿼티 커브 방식으로 바꿀지 결정.
- [ ] 미청산 포지션 무시, 슬리피지 없음, 롱 온리.
- [ ] 실데이터 백테스트 결과를 이 문서 5장에 기록.
- [ ] UI 재연결 여부 결정 (`roadmap.md` 4-4).

---

## 7. 변경 이력

- 2026-09-17: 최초 작성 — `strategy/ma_cross.py`를 `strategy/ma_cross/` 패키지(`__init__.py`
  파사드 + `backtest.py`)로 재구성하면서 기존 코드를 분석해 규칙·함수·한계를 정리.
  docstring의 "+ RSI" 표기는 실제 로직에 없어 제거.
