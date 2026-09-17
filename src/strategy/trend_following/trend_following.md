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
- 이 서브패키지의 실제 구현(위 파일들의 로직 작성)은 **CLI(Claude Code) 세션에서 진행** —
  이 문서는 그 세션이 참고할 스펙까지만 정리한다.
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
- **아직 확인되지 않은 것**: 실데이터(KOSPI/KOSDAQ/미국주식) 기준 성과 — CLI 세션에서
  `get_historical_data()`로 재검증 필요.
- 산출 지표: 총수익률, CAGR, 연변동성, Sharpe, MDD, 트레이드 수, 승률, 평균 트레이드
  수익률, 시장노출비중.
- Risk gate 통과 여부: Sharpe ≥ 1.5, MDD ≤ 15% (1장 참고).

---

## 6. 결정 사항 / 다음 단계

- [x] 전략 후보 확정 — 리버모어 추세추종 → Donchian 채널 돌파로 구체화
- [x] 개발 트랙 확정 — Paperclip 대신 CLI(Claude Code) 기반, 기존 `src` 코드베이스 위에서
      진행 (paperclipai는 코드 저장 위치가 아님)
- [x] 파이프라인 프로토타입 검증 — 합성 데이터, look-ahead 없음 확인 완료 (클라우드 세션)
- [ ] `entry_n`/`exit_n` 등 파라미터 초기값 확정 (실데이터 백테스트로 탐색)
- [ ] `strategy/trend_following/` 서브패키지 실제 구현 (CLI 세션에서 진행 — `__init__.py` 스캐폴드만 존재,
      `config.py`/`signals.py`/`backtest.py`는 4장 구성안대로 신규 작성)
- [ ] 실데이터 기준 백테스트 실행 및 결과를 본 문서 5장에 기록
- [ ] 수수료/슬리피지 반영 여부 결정
- [ ] 리버모어 피라미딩(분할 추가 매수) 반영 여부 — v2 후보
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
