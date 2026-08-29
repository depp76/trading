# 포트폴리오 관리 시스템 코드 분석 및 개선 검토 보고서

> **문서명**: `agy_analysis.md`  
> **분석 기준일**: 2026-08-29  
> **대상 시스템**: PyQt6 기반 주식 포트폴리오 관리 및 퀀트 주간 리밸런싱 데스크톱 애플리케이션  
> **핵심 파일**: `main.py`, `data_fetcher.py`, `trade_db.py`, `gemini_helper.py`, `ui/`, `threads/`, `tests/`

---

## 1. 개요 및 아키텍처 현황

본 프로젝트는 한국(KOSPI/KOSDAQ) 및 미국(NASDAQ/S&P500) 주식 포트폴리오를 추적하고, 주간 팩터 스코어링을 통한 자산 리밸런싱 및 백테스팅을 지원하는 단일 사용자 데스크톱 애플리케이션입니다.

최근 Phase 0~5 모듈화 리팩토링을 통해 거대했던 UI 계층이 탭별로 잘 분리되었으며, Polars와 NumPy 기반의 고속 벡터화 연산 파이프라인이 구축되어 있습니다.

### 디렉터리 및 모듈 구조

```text
Portfolio Management/
├── main.py                     # [409줄] 앱 엔트리포인트, MainWindow, 전역 스타일/단축키/타이머
├── data_fetcher.py             # [3,190줄] 외부 시세 수집, 지표 계산, 백테스팅, 주간 리밸런싱 알고리즘
├── trade_db.py                 # [361줄] SQLite WAL 모드 기반 매매 기록 영속화
├── gemini_helper.py            # [246줄] Gemini API 기반 AI 포트폴리오 진단 및 자연어 필터
├── ui/                         # UI 컴포넌트 모듈 (PyQt6)
│   ├── universe_tab.py         # Trading Universe 탭 (워치리스트, AI 필터, MA 차트 연동)
│   ├── history_tab.py          # Trading History 탭 (매매 내역, 포지션 P/L, 계좌 예수금)
│   ├── assets_tab.py           # Total Assets 탭 (주간 자산 스냅샷, 환율/지수 연동, matplotlib)
│   ├── auto_trading_tab.py     # Auto Trading 탭 (주간 팩터 스코어링 신호, 워크포워드 백테스트)
│   ├── dialogs.py              # 7종 다이얼로그 (MA 차트, 매수/매도 편집, 백테스트 결과 등)
│   └── widgets.py              # StockTable, GroupedHeaderView, Excel 스타일 필터 팝업
├── threads/                    # 비동기 백그라운드 워커 (QThread)
│   ├── fetch_threads.py        # 시세/지수 수집, 자동 백업, 백테스트 스레드
│   └── realtime.py             # 1분 주기 실시간 시세 갱신 스레드
└── tests/                      # pytest 기반 단위 테스트 (76개 테스트 통과)
```

---

## 2. 모듈별 정밀 분석

### 2.1 UI 계층 (`main.py`, `ui/`)
- **장점**:
  - `main.py`가 6,490줄에서 **409줄(-94%)**로 축소되어 창 오케스트레이션과 라이프사이클에만 집중.
  - 각 탭(`UniverseTab`, `TradingHistoryTab`, `TradingRecordTab`, `AutoTradingTab`)이 `MainWindow`를 직접 참조하지 않고 **PyQt 시그널/슬롯**을 통해 느슨하게 결합됨.
  - 대량 테이블 갱신 시 `setUpdatesEnabled(False)` 패턴과 `Malgun Gothic Semilight` 폰트 일원화가 잘 준수됨.
- **개선점**:
  - 각 `ui/*.py` 모듈에서 `_get_create_font()`와 같은 함수 내부 지연 임포트(`import main as _m`) 패턴을 사용하고 있어, 공통 UI 유틸리티(`ui/common.py`)로 추출할 필요가 있음.

### 2.2 데이터 수집 및 연산 엔진 (`data_fetcher.py`)
- **장점**:
  - Naver Mobile API, Yahoo Finance, Kiwoom REST, KRX OpenAPI 등 다양한 데이터 소스를 안정적으로 통합.
  - `_compute_indicators`, `run_backtest_strategy`, `_score_and_rank` 등 핵심 지표 계산이 Polars 표현식과 NumPy 벡터화로 구현되어 연산 속도가 뛰어남.
  - 모듈 레벨의 LRU 캐시(`_HIST_CACHE`, `_USD_KRW_CACHE`)와 캐시 히트율 모니터링 적용.
- **개선점**:
  - **단일 파일 거대화 (3,190줄)**: 수집기(Collector), 기술적 지표(Indicators), 퀀트 백테스트(Backtest), 리밸런싱 알고리즘(Rebalance)이 한 파일에 혼재되어 있어 모듈 분리가 시급함.

### 2.3 데이터베이스 계층 (`trade_db.py`)
- **장점**:
  - SQLite WAL(Write-Ahead Logging) 모드를 적용하여 읽기/쓰기 동시성 확보.
  - `upsert_trades()` 일괄 트랜잭션으로 대량 쓰기 성능 최적화.
  - 레거시 JSON 파일(`custom_history.json`, `trade_overrides.json`) 자동 멱등(Idempotent) 마이그레이션 지원.
- **개선점**:
  - `AutoBackupThread`에서 `shutil.copy2`로 단순 파일 복사를 수행 중이므로, WAL 모드에서의 일관성을 위해 `sqlite3.Connection.backup()` 표준 API로 전환 필요.

### 2.4 AI 도우미 계층 (`gemini_helper.py`)
- **장점**:
  - `google-genai` SDK 기반 싱글톤 클라이언트 관리.
  - 포트폴리오 진단(`portfolio_diagnosis`) 및 자연어 필터(`nl_to_filter`, JSON 모드) 기능이 안정적으로 연결됨.
- **개선점**:
  - ROADMAP 3-4A 항목인 **"AI 종목 리포트 3줄 요약"** 기능이 아직 미구현 상태.

### 2.5 테스트 인프라 (`tests/`)
- **장점**:
  - `TempDBMixin`을 통해 실제 사용자 DB와 분리된 완전 격리 환경에서 SQLite CRUD 테스트.
  - 4개 테스트 파일(76개 테스트) 100% 통과.
- **개선점**:
  - 최근 추가된 `compute_weekly_rebalance_signals` 및 `run_rebalance_backtest`에 대한 정식 pytest 케이스 추가 필요.

---

## 3. 핵심 개선 사항 및 권장 방안 (Top Recommendations)

### 🏗️ 1. 아키텍처: `data_fetcher.py` 서브패키지화 (우선순위: ⭐⭐⭐)
단일 파일에 집중된 3,190줄의 코드를 도메인별 패키지로 분리합니다:

```text
data/
├── __init__.py           # 기존 data_fetcher.py의 공개 API를 re-export하여 하위 호환성 유지
├── collectors/           # 외부 API 수집기
│   ├── naver.py          # 네이버 실시간 시세, PER, 재무제표, 수급
│   ├── yahoo.py          # yf_quote_batch, Crumb 세션, 미국 시세
│   ├── kiwoom.py         # 키움 REST 토큰, 예수금, 투자자 동향
│   ├── krx.py            # VKOSPI, 국채/지수 데이터
│   └── listing.py        # 시장별 상장 종목 리스팅 (FDR 폴백 포함)
├── indicators.py         # Polars 기반 이동평균, RSI, 이격도, 52주 고저
├── rebalance.py          # 팩터 스코어링, z-score 정규화, 주간 신호 분류
├── backtest.py           # 단일 종목 백테스트 & 워크포워드 포트폴리오 시뮬레이션
└── cache.py              # _HIST_CACHE LRU, 환율 캐시 등
```

### 🧩 2. 공통 UI 유틸리티 모듈 추출 (우선순위: ⭐⭐)
- `ui/common.py` 또는 `ui/base.py`를 신설하여 `create_font`, `_fmt_num_edit`, `_MARKET_ORDER`, `_HIST_KEYS`, 입력 유효성 검사 함수(`_validate_date_str`, `_validate_positive_number`)를 배치.
- 모든 UI 모듈의 내부 지연 임포트(`import main as _m`)를 직접 정적 임포트로 교체하여 런타임 오버헤드 및 코드 가독성 개선.

### 🛡️ 3. SQLite 백업 안전성 강화 (우선순위: ⭐⭐)
- `AutoBackupThread`에서 단순 `shutil.copy2` 대신 `sqlite3.Connection.backup()`을 사용하도록 수정.
- WAL 모드 실행 중에 실시간 쓰기가 발생하더라도 손상 없는 원자적(Atomic) 핫 백업 보장.

### ⚙️ 4. 환경 변수 기반 설정 일원화 (우선순위: ⭐⭐)
- `_get_kiwoom_keys()`의 하드코딩된 외부 절대 경로(`D:\Source Code\Kiwoom MCP\...`)를 `.env`의 `KIWOOM_KEY_PATH` 또는 `KIWOOM_APP_KEY`/`KIWOOM_SECRET_KEY`로 대체.

### 🎯 5. 리밸런싱 알고리즘 및 퀀트 전략 고도화 (우선순위: ⭐⭐⭐)
1. **섹터/업종 집중도 상한 제약 (Sector Constraint)**:
   - `pykrx` 업종 분류를 연동하여 특정 섹터에 Top 20 랭킹이 과도하게 쏠리지 않도록 "단일 섹터 최대 N종목" 필터링 적용.
2. **포지션 사이징 개선 (Inverse Volatility)**:
   - 현재의 단순 균등 분할(Equal-Weight) 외에, 20일 변동성의 역수를 적용하여 변동성이 큰 종목의 비중을 낮추는 변동성 역가중 옵션 추가.
3. **AI 종목 리포트 3줄 요약 (ROADMAP 3-4A)**:
   - Universe 탭 종목 우클릭 메뉴 → Gemini API 기반 최근 주가 흐름/지표/수급 요약 팝업 구현.

### 🧪 6. 리밸런싱/백테스트 단위 테스트 보강 (우선순위: ⭐⭐)
- `tests/test_rebalance.py`를 추가하여:
  - 팩터 결측치 처리(3개 미만 시 제외)
  - 랭킹 정합성 및 매도 밴드(Top N=20, 1.5배수=30위) 경계값 검증
  - 합성 시계열을 이용한 워크포워드 시뮬레이션의 자산 곡선 및 거래 내역 무결성 검증

---

## 4. 단계별 실행 로드맵 (Action Plan)

| 단계 | 작업 목표 | 세부 실행 내용 | 비고 |
|:---:|---|---|---|
| **Phase 1** | **안정성 및 공통화** | 1. `ui/common.py` 신설 및 UI 모듈 지연 임포트 제거<br>2. `trade_db.py` / `fetch_threads.py`에 SQLite Online Backup API 적용<br>3. 키움 API 키 경로 `.env` 파라미터화 | 저위험 / 높은 안정성 개선 |
| **Phase 2** | **데이터 엔진 모듈화** | 1. `data_fetcher.py`를 `collectors/`, `indicators.py`, `rebalance.py`, `backtest.py`로 분리<br>2. `data_fetcher.py`는 Re-export Facade로 유지해 기존 호출부 100% 호환 보장 | 유지보수성 및 확장성 대폭 향상 |
| **Phase 3** | **신규 기능 및 퀀트 고도화** | 1. Universe 탭 종목 우클릭 "AI 종목 리포트 요약" 구현 (ROADMAP 3-4A)<br>2. 리밸런싱 알고리즘에 섹터 분산 제약 룰 추가<br>3. 변동성 역가중 포지션 사이징 옵션 추가 | 투자 의사결정 지원 강화 |
| **Phase 4** | **테스트 커버리지 확장** | 1. `tests/test_rebalance.py` 작성 (합성 데이터 기반 검증)<br>2. 전체 테스트 100개+ 달성 및 회귀 방지 | 시스템 신뢰성 완성 |

---

## 5. 결론

본 시스템은 체계적인 모듈화와 고속 연산 파이프라인이 이미 잘 구축되어 있는 완성도 높은 애플리케이션입니다. 

향후 **`data_fetcher.py`의 도메인별 서브모듈화**와 **공통 UI 유틸리티 분리**를 선행한 후, **섹터 제약 리밸런싱** 및 **AI 종목 리포트** 기능을 단계적으로 도입하면 더욱 견고하고 강력한 포트폴리오 관리 플랫폼으로 발전할 수 있습니다.
