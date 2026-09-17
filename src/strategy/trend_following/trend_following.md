# src/strategy/trend_following/ — Donchian 채널 돌파 추세추종

> 전체 설계/의사결정 이력은 저장소 루트의 `trend_following.md`를 참고할 것 — 이 문서는
> 이 서브패키지 코드를 읽는 사람을 위한 짧은 안내만 담는다 (`strategy/rebalance/`가
> `rebalance.md`를 스펙으로 참조하는 것과 같은 관계).
>
> 위치는 `data/trend_following/`이 아니라 `src/strategy/trend_following/`이다 — 전략 로직은
> `data/`(순수 데이터 접근 계층)와 분리해 `strategy/` 밑에 모으기로 결정함
> (2026-09-17, `rebalance.md` 11-5). `data/rebalance/`도 같은 날 `strategy/rebalance/`로 이동 완료.

## 상태

아직 코드 미구현 — `__init__.py`(docstring만)로 패키지 스캐폴드만 있고, 실제 구현은
CLI(Claude Code) 세션에서 진행한다. 구현 시 아래 파일 구성을 따를 것.

## 전략 요약

- Donchian 채널 돌파: 최근 `entry_n`일 최고가 상향 돌파 시 매수, 최근 `exit_n`일 최저가
  하향 돌파 시 매도.
- 롱 온리, 단일 포지션, 피라미딩 없음 (v1 기준 — 리버모어 피라미딩은 v2 후보).
- 데이터: 새 fetch 로직을 만들지 않고 `data.market.get_historical_data()`를 그대로 재사용.
- No-lookahead 원칙: 신호는 t일 종가로 확정, 포지션은 t+1일부터 적용
  (`position.shift(1) * daily_return`).

## 파일 구성 (예정)

```
src/strategy/trend_following/
  __init__.py     # 공개 심볼 재노출 (strategy/rebalance/__init__.py 패턴)
  config.py       # TrendFollowingConfig — entry_n, exit_n, risk gate 값(sharpe_min, mdd_max)
  signals.py      # donchian_signal() — 진입/청산 신호 생성
  backtest.py      # run_backtest() — 벡터화 백테스트 + Sharpe/MDD/CAGR/승률 산출
```

대응하는 테스트는 `tests/strategy/trend_following/`에 둔다 (`tests/rebalance/`와 동일 컨벤션),
look-ahead 없음 검증을 포함할 것. 데이터는 `data.market.get_historical_data()`를 상위
패키지 참조로 그대로 가져다 쓴다(데이터 계층은 `data/`에 남아있음).

## 리스크 게이트

Sharpe ratio ≥ 1.5, 최대낙폭(MDD) ≤ 15% (기준은 `trend_following.md` 1장 참고).
