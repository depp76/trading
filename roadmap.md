# Portfolio Management — 발전 로드맵

> 기준일: 2026-09-17 (최초 작성 2026-08-21, 실제 코드 상태 재조사 후 갱신)  
> 현재 상태: PyQt6 단일 사용자 데스크톱 앱 (한국/미국 주식 포트폴리오 추적) — git 관리(46 커밋), pytest 119/119 통과, ruff(F) 0건  
> 핵심 파일: `src/main.py` (313줄), `src/data_fetcher.py` (파사드) → `src/data/` 패키지 (cache·indicators·market·collectors, 순수 데이터 계층), `src/strategy/` (rebalance·ma_cross·trend_following, 전략 계층 — 2026-09-17 분리), `src/ui/` (약 6,300줄), `src/threads/`, `src/trade_db.py`, `src/gemini_helper.py`

---

## 목차

1. [현황 요약](#1-현황-요약)
2. [단기 개선 (1~4주)](#2-단기-개선-14주)
3. [중기 개선 (1~3개월)](#3-중기-개선-13개월)
4. [장기 개선 (3개월+)](#4-장기-개선-3개월)
5. [우선순위 매트릭스](#5-우선순위-매트릭스)
6. [코드 분석 기반 개선 항목 (2026-09-17)](#6-코드-분석-기반-개선-항목-2026-09-17)
7. [UI 개선 항목 (2026-09-19)](#7-ui-개선-항목-2026-09-19)

---

## 1. 현황 요약

### 현재 기능

| 탭 | 기능 |
|----|------|
| **Trading Universe** | KOSPI / KOSDAQ / NASDAQ 100 / S&P 500 종목 워치리스트, 실시간 시세, MA 지표, 필터 팝업 |
| **Trading History** | 거래 기록 수동 입력, SQLite 영속화 (`portfolio.db`), 오버라이드 지원 |
| **Total Assets** | 날짜별 자산 합계 테이블 + matplotlib 그래프, 환율·KOSPI 연동 |
| **Auto Trading** | 주간 팩터 스코어링 리밸런싱 신호(매수/매도/보유 후보) + 워크포워드 백테스트 (거래비용 반영) |

### 기술 스택

- **UI**: PyQt6 + matplotlib (QtAgg 백엔드)
- **데이터**: pykrx / FinanceDataReader / yfinance / yahooquery / Naver / KIS(한국투자증권) REST·WebSocket
- **연산**: polars (내부) → pandas (외부 라이브러리 경계)
- **DB**: SQLite (WAL 모드), `trade_db.py`
- **AI**: `google-genai` (`gemini_helper.py`로 포트폴리오 진단·종목 리포트에 실사용 중; 자연어 필터는 2026-09-19 삭제), `google-cloud-aiplatform` (requirements에 포함, 미사용)

### 소스 코드 구조 (2026-09-19 갱신)

> **구조가 바뀔 때마다(폴더/파일 신설·이동·삭제) 이 섹션을 최신 상태로 갱신할 것.** 3-1의
> "현재 파일 구조"는 Phase 0~5 리팩토링 당시의 역사적 기록이라 갱신 대상이 아니고, 지금
> 시점의 실제 트리를 반영하는 곳은 이 섹션 하나로 유지한다.

```
src/
├── main.py                # PyQt6 진입점 + MainWindow, 공용 헬퍼
├── paths.py                # BASE_DIR 등 경로 상수 (6-1d, cwd 무관)
├── data_fetcher.py          # data/·strategy/ 재노출 파사드 (하위 호환용)
├── trade_db.py               # SQLite 거래 이력 (portfolio.db, WAL)
├── gemini_helper.py            # Gemini 기반 포트폴리오 진단 / 종목 리포트
├── data/                        # 순수 데이터 접근 계층
│   ├── cache.py                   # 전역 캐시/세션 상수
│   ├── indicators.py               # RSI/MA 등 지표
│   ├── market.py                    # 시세 집계
│   ├── history.py                    # 과거 시세 조회
│   ├── listing.py                     # 종목 리스팅
│   ├── frames.py                       # 데이터프레임 변환 유틸
│   ├── fx.py                            # 환율
│   └── collectors/                       # kis.py / krx.py / naver.py / yahoo.py
├── strategy/                    # 매매 전략 로직 계층 (2026-09-17 data/에서 분리)
│   ├── rebalance/                 # 팩터 스코어링 + 워크포워드 백테스트 (+ rebalance.md 스펙)
│   ├── ma_cross/                    # 개별종목 MA20/60 골든크로스 전략 (+ ma_cross.md)
│   └── trend_following/               # Donchian 채널 돌파 추세추종 (+ trend_following.md 스펙)
├── ui/
│   ├── common.py / widgets.py           # 공용 폰트·위젯 헬퍼
│   ├── common.py                        # 폰트·검증·JSON I/O·retire_thread·ThreadOwnerMixin
│   ├── universe_tab.py / history_tab.py / assets_tab.py / strategy_tab.py (7-1: Auto
│   │     Trading·Trend Following 서브탭 + 요약 바) / auto_trading_tab.py / trend_following_tab.py
│   ├── history_table.py / history_calc.py # History 탭 셀 팩토리·SectionTable / 순수 계산(summarize_positions 등)
│   └── dialogs/                           # 다이얼로그 11개 (stock_ma.py는 메서드 단위로 재구성)
├── threads/                       # fetch_threads.py, realtime.py
└── tests/                          # test_<모듈>.py 단위 테스트 + strategy/{ma_cross,rebalance,trend_following}/
                                    #   (trend_following/frames.py = 공용 OHLCV 프레임 헬퍼)
```

전략별 상세 스펙·의사결정 이력은 `strategy/<전략명>/<전략명>.md`에 각각 둔다(코드와 같은
폴더) — `rebalance.md`, `ma_cross.md`, `trend_following.md`. 예전에 저장소 루트에 있던
`changelog_optimization.md`/`test_plan.md`는 `docs/history/`로 이관되었다.

### 기존 최적화 내역

- 2026-08-11 1차: LRU 캐시, 배치 upsert, yf_quote_batch 통합, deepcopy 제거 등 7건
- 2026-08-12 2차: Semaphore 병렬화, 벡터화 백테스트, 렌더링 최적화 등 7건
- 2026-08-21 이후: `logging` 인프라 도입(2-1 대부분 완료), `gemini_helper.py` 신설로
  포트폴리오 AI 진단(3-4B)·자연어 필터(3-4C) 기능이 실제로 연결되어 사용 중
- 2026-08-28 3차: 단기 개선 항목(2-1~2-5) 전체 구현 — 잔여 무음 예외 로깅, 캐시 hit/miss
  모니터링, 상태바 진행 피드백, 자동 백업 스케줄러, 거래 입력 실시간 유효성 검사
- 2026-08-29: 3-1 모듈화 Phase 1~3 완료 — `main.py` 6,490줄→3,035줄 (-53%). 스레드 8개 →
  `threads/fetch_threads.py`+`realtime.py`, 위젯·필터 → `ui/widgets.py`, 다이얼로그 7개 →
  `ui/dialogs.py` 분리.
- 2026-08-29 (2차): 3-1 모듈화 Phase 4~5 완료로 전체 완료 — `main.py` 3,035줄→**401줄**
  (누계 6,490줄 대비 **-94%**). `TradingHistoryTab` → `ui/history_tab.py`,
  `TradingRecordTab` → `ui/assets_tab.py`, `MainWindow`에 인라인으로 남아있던 "Trading
  Universe" 탭(워치리스트 UI + 시세 갱신/AI 필터/개별 종목 MA 오케스트레이션 전체)을
  `ui/universe_tab.py`의 `UniverseTab` 클래스로 분리. `MainWindow`는 833줄→247줄로 축소되어
  목표치(~200줄)에 근접. 탭 간 통신은 기존 `TradingHistoryTab.status_message` 패턴을 그대로
  따라 시그널로 연결(`status_text_changed`/`sync_time_changed`/`status_message`/
  `refresh_started`/`auto_lightweight_tick`) — 공유 풋터(상태 레이블·최종 갱신 시각)의 화면
  위치(탭과 무관하게 항상 표시)는 그대로 유지. 검증: `py_compile`, `import main`, 76/76
  pytest, `MainWindow()` 인스턴스화 + 시그널 전파 스모크 테스트 통과.

---

## 2. 단기 개선 (1~4주)

### 2-1. 코드 품질 — 오류 가시성 향상 — ✅ 완료

**현황 (2026-08-28 갱신)**: `main.py`에 파일+콘솔 핸들러를 갖춘 `logging.basicConfig` 설정,
`data_fetcher.py`/`main.py` 모두에 모듈 레벨 `logger`가 있고, 남아있던 무음
`except Exception: pass` (data_fetcher.py 1곳, main.py 9곳)를 전부 `logger.warning`/
`logger.debug(..., exc_info=True)`로 교체 완료. `app.log`에 실제 오류가 기록되고 있음을 확인.

**검증**: `py_compile`/`ast.parse` 통과, 코드 전체에서 로깅 없는 `except Exception: pass` 0건 확인.

---

### 2-2. 성능 — 캐시 효율 모니터링 — ✅ 완료

**구현 내용**: `data_fetcher.py`에 `_HIST_CACHE_HITS`/`_HIST_CACHE_MISSES` 카운터와
`_log_hist_cache_stats()`를 추가. `get_historical_data()`가 캐시 히트/미스 시마다 카운터를
증가시키고, 100회 조회마다(`_HIST_CACHE_LOG_INTERVAL`) 히트율을 INFO 레벨로 `app.log`에 출력.

**검증**: 카운팅·로깅 트리거 로직을 별도 스크립트로 복제해 5회 주기마다 정확히 1회 로깅되고
누적 히트/미스 수가 일치하는지 확인.

**⚠️ 2026-09-17 재조사**: 카운팅은 동작하지만 로그는 실제로 기록되지 않음 — `main.py`의 파일/콘솔
핸들러 레벨이 WARNING이라 INFO 출력이 전부 버려짐(`app.log` 0바이트). 6-1a에서 수정 완료
(파일 핸들러 INFO, root INFO).

---

### 2-3. UX — 상태바 피드백 개선 — ✅ 완료

**구현 내용**: `QMainWindow.statusBar()`를 초기화하고, `UniverseLightweightFetchThread`·
`PositionPriceFetchThread`·`RealtimePriceThread`에 `status_message` 시그널을 추가해
"국내 시세 조회 중… (Naver, N종목)", "Yahoo Finance 응답 대기 중…", "종목 코드 조회 중…" 등을
방출. `TradingHistoryTab`이 이를 다시 `status_message`로 포워딩하고, `MainWindow`가
`_on_thread_status_message()`로 받아 상태바에 표시(5초 유지). 기존 `AllDataFetchThread`의
`market_progress` 시그널도 동일 핸들러로 연결해 `"KOSPI 시세 조회 중… (3/5)"` 형태로 표시.
기존 `self.status_label`(마켓별 카운트 요약)은 그대로 두고, 네이티브 상태바는 순간적인
진행 메시지 전용으로 병행 사용.

---

### 2-4. 안전성 — 자동 백업 스케줄러 — ✅ 완료

**구현 내용**: `main.py`에 `AutoBackupThread(QThread)`를 추가. `MainWindow.__init__` 끝에서
시작되어 `portfolio.db` + `custom_settings.json`을 `archive/auto_<yyyyMMdd_HHmmss>/`에 복사하고,
`archive/auto_*` 폴더가 7개를 초과하면 오래된 것부터 삭제(`backup_*` 수동 백업은 건드리지 않음).
완료/실패 메시지를 상태바(2-3)로도 전달.

**검증**: 프루닝 로직을 별도 스크립트로 복제해 10개 중 최신 7개만 남고 수동 `backup_*` 폴더는
보존되는지 확인.

---

### 2-5. 데이터 신뢰성 — 입력 유효성 검사 강화 — ✅ 완료

**구현 내용**: `_validate_date_str`/`_validate_positive_number`/`_mk_field_validator`/
`_set_field_error` 공용 헬퍼를 추가하고, `BuyEditDialog`·`SellEditDialog`·`TradeEntryDialog`의
날짜/가격/수량 필드에 연결. `textChanged`로 실시간 빨간 테두리+툴팁을 표시하고, 저장 버튼
클릭 시 동일 검증을 재실행해 통과 못하면 저장을 막음. `SellEditDialog`는 매도일이 비어있으면
가격/수량을 선택 입력으로 취급(포지션이 열린 채로 유지)하고, 매도일 입력 시에는 매수일보다
빠르면 경고 다이얼로그를 띄우고 저장을 막도록 구현.

**검증**: 날짜/숫자 검증 함수와 매수일>매도일 교차 검증 로직을 별도 스크립트로 복제해
경계값(빈 문자열, 0, 음수, 잘못된 형식, 동일 날짜 등)을 모두 확인.

---

## 3. 중기 개선 (1~3개월)

### 3-1. 아키텍처 — 파일 분리 (모듈화) — ✅ 완료 (Phase 0~5 전체)

**현황 (2026-08-29 갱신)**: Phase 0~5 전체 완료. `main.py` **6,490줄 → 401줄** (-94%).  
`main.py`에는 공용 헬퍼(`create_font`, `_fmt_num_edit`, 입력 검증 함수 등)와 `MainWindow`
(247줄)만 남음. 탭 3개(Universe/History/Assets)는 모두 `ui/`로 분리 완료.

#### 실제 분리 결과

| 클래스 / 그룹 | 이동 결과 |
|---|---|
| `IndexMaThread`, `StockMaThread`, `SingleStockFetchThread`, `AllDataFetchThread`, `UniverseLightweightFetchThread`, `PositionPriceFetchThread`, `AutoBackupThread` (7개) | ✅ `threads/fetch_threads.py` (420줄) |
| `RealtimePriceThread` | ✅ `threads/realtime.py` (25줄) |
| `FilterPopup`, `FilterableHeader`, `StockTable`, `GroupedHeaderView` | ✅ `ui/widgets.py` (906줄) |
| `IndexMaDialog`, `StockMaDialog`, `BuyEditDialog`, `SellEditDialog`, `TradeEntryDialog`, `StockTradeHistoryDialog`, `TotalAssetsGraphDialog` (7개) | ✅ `ui/dialogs.py` (1,825줄) |
| `TradingHistoryTab` (~1,780줄) | ✅ `ui/history_tab.py` (1,837줄) |
| `TradingRecordTab` (~590줄) | ✅ `ui/assets_tab.py` (630줄) |
| "Trading Universe" 탭 (`MainWindow`에 인라인으로 존재하던 워치리스트 UI + 시세 갱신/AI 필터/개별 종목 MA 오케스트레이션 전체, ~600줄) | ✅ `ui/universe_tab.py`의 `UniverseTab` 클래스 (683줄) |
| `MainWindow` (탭 구성 + 헤더/상태바/전역 타이머/단축키/자동 백업만 남김) | ✅ `main.py` (247줄, 목표 ~200줄에 근접) |

#### 현재 파일 구조

```
portfolio_mgmt/
├── main.py              # 공용 헬퍼 + MainWindow (401줄, MainWindow만 247줄)
├── ui/
│   ├── __init__.py
│   ├── universe_tab.py  # UniverseTab (683줄)  ✅
│   ├── history_tab.py   # TradingHistoryTab (1,837줄)  ✅
│   ├── assets_tab.py    # TradingRecordTab (630줄)  ✅
│   ├── dialogs.py       # 7개 Dialog (1,825줄)  ✅
│   └── widgets.py       # StockTable, GroupedHeaderView 등 (906줄)  ✅
├── threads/
│   ├── __init__.py
│   ├── fetch_threads.py # 7개 Thread (420줄)  ✅
│   └── realtime.py      # RealtimePriceThread (25줄)  ✅
├── data_fetcher.py      # (현행 유지, 2,246줄)
└── trade_db.py          # (현행 유지)
```

#### Phase 4~5 구현 메모

- `TradingHistoryTab`/`TradingRecordTab`/`UniverseTab` 모두 서로를 직접 참조하지 않고
  `MainWindow`가 시그널로만 중개하는 구조였기 때문에(Phase 4 이전부터 결합도 0건 확인),
  각 탭을 독립적으로 이동해도 순환 참조 없이 완료됨.
- `create_font`/`_fmt_num_edit`처럼 `main.py`에 남아있는 공유 헬퍼는, 새로 분리된 모든 `ui/*`
  모듈에서 `def _get_x(): import main as _m; return _m.x` 형태의 지연 임포트 후 동일한 이름의
  래퍼 함수로 감싸 원본 호출부(`create_font(...)`)를 그대로 재사용 — Phase 1~3에서 쓰인
  "메서드마다 지역 재바인딩" 방식 대신 모듈 레벨 forwarding 함수로 통일해 호출부 누락 위험을
  없앰.
- Universe 탭의 공유 풋터(상태 레이블 `status_label`, 최종 갱신 시각 `update_time_label`)는
  탭 전환과 무관하게 항상 보이는 위치(탭 위젯 아래 공용 영역)에 있었으므로, 그 두 `QLabel`은
  `MainWindow`에 그대로 두고 `UniverseTab`은 `status_text_changed`/`sync_time_changed`
  시그널만 emit하도록 설계 — 위젯을 그대로 옮겼다면 Universe 탭이 활성화된 동안에만 보이는
  것으로 화면이 바뀌었을 것.
- `refresh_data()` 호출 시 Trading History 탭도 함께 리로드하던 기존 동작, 60초 자동 타이머의
  경량 갱신 시 Trading History 실시간 시세도 함께 갱신하던 기존 동작은 각각
  `refresh_started`/`auto_lightweight_tick` 시그널로 대체해 그대로 보존.
- `MainWindow.closeEvent()`의 스레드 정리 로직은 `UniverseTab.collect_threads_to_stop()`
  헬퍼로 위임.

**검증**: `py_compile` 전체 통과, `import main` 성공, `python -m pytest tests/ -v` 76/76 통과,
`MainWindow()` 인스턴스화 및 시그널 전파(상태 텍스트/갱신 시각/자동 타이머 경량 갱신 경로)
스모크 테스트 통과. GUI 자체는 헤드리스 환경 특성상 수동 조작 테스트는 못함 — 실제 사용 중
이상 발견 시 `archive/backup_20260829_141942/`(Phase 4 이전 스냅샷)로 대조 가능.

**진행 원칙 (적용됨)**:
1. `archive/backup_<date>/` 백업 후 시작
2. 클래스 1개 이동 → `py_compile` → `python -m pytest tests/ -v` → `import main` / 인스턴스화 확인
3. 한 번에 전체 리팩터링 금지 (Phase 4·5를 각각 별도 커밋으로 분리)
4. 순환 임포트는 지연 임포트(`import main as _m` 함수 내부)로 해결 — `TYPE_CHECKING`은 타입
   힌트 전용이라 런타임에 실제 함수/상수를 가져와야 하는 이 케이스엔 부적합해 채택하지 않음

---

### 3-2. 테스트 인프라 구축 — ✅ 완료

**구현 내용 (2026-08-28)**: `tests/` 디렉터리 신설, `pytest` 설치 및 `requirements.txt`에 추가.

```
tests/
├── test_trade_db.py      # SQLite CRUD, 인덱스, 배치 upsert (TempDBMixin으로 실제 DB 격리)
├── test_data_fetcher.py  # LRU 캐시 hit/miss/eviction, yf_quote_batch (mock 기반)
├── test_backtest.py      # run_backtest_strategy 전략 신호, 엣지 케이스, 누적 수익률 정합성
└── test_assets_calc.py   # 환율 캐시, safe_float, 날짜/양수 검증, 매수일>매도일 교차검증
```

- `pytest` + `unittest.mock` 사용, GUI(PyQt6) 테스트 제외
- `TempDBMixin`이 `_DB_FILE` / `_CUSTOM_JSON` / `_OVERRIDES_JSON` 경로를 임시 파일로 교체해
  레거시 마이그레이션이 실제 프로젝트 JSON을 읽지 못하도록 완전 격리
- 로컬 실행: `python -m pytest tests/ -v`
- **검증**: `76 passed in 3.66s` (100% pass rate)

---

### 3-3. 성능 — pandas/polars 경계 최소화 — ✅ 완료 (재조사 결과 대부분 이미 최적 상태)

**재조사 결과 (2026-08-29)**: 로드맵 작성 당시 우려했던 두 항목은 이미 해소되어 있었음을
코드 확인:

- **히스토리컬 파이프라인**: `get_historical_data()` → `_fetch_historical_uncached()`는
  이미 소스별로 `_to_polars()`를 정확히 1회만 호출(KR은 Naver JSON에서 애초에 `pl.DataFrame`을
  직접 생성, US/지수는 yfinance/yahooquery/FDR pandas 결과를 함수 끝에서 단 한 번만 변환).
  추가로 구성할 것이 없었음.
- **`_compute_indicators()`**: 입력부터 출력까지 전부 polars 표현식(`with_columns`,
  `rolling_mean`, `ewm_mean` 등)만 사용 — 중간에 pandas로 왕복하는 지점이 원래 없었음
  (2026-08-12 2차 최적화에서 이미 벡터화됨, `run_backtest_strategy()`도 numpy 전용으로 pandas
  미사용).

**실제로 발견·수정한 중복 변환 (2건)**: 위 두 함수는 이미 정상이었지만, 이들을 호출하는 두
지점에서 **같은 pandas 데이터를 두 번 polars로 변환**하는 낭비를 발견:

1. `fetch_single_stock()`의 미국/해외 종목 분기 — `fdr.DataReader()` 결과를 현재가 추출용으로
   `_to_polars()`(→`df_p`)한 뒤, `fetch_historical_changes(..., df_pd)`에 **원본 pandas**를
   다시 넘겨 내부에서 또 `_to_polars()`가 호출됨.
2. `fetch_indice_as_stock()` (JP10YT/KR3YT/VKOSPI + 모든 지수, `fetch_major_indices_as_stocks()`가
   매 새로고침·60초 자동 갱신마다 호출) — 채권/VKOSPI 분기는 위와 동일한 이중 변환, 그 외
   지수 분기는 `df_pd_fallback=None`을 넘겨 `fetch_historical_changes` 내부에서
   `get_historical_data()`를 (캐시 히트이긴 하나) 불필요하게 한 번 더 호출.

**수정**: 두 지점 모두 이미 계산해 둔 polars df(`df_p`/`df`)를 그대로
`fetch_historical_changes(..., df_pd=...)`에 전달하도록 변경. `_to_polars()`는 polars 입력에
대해 no-op(즉시 반환)이므로 동일 인자를 재사용하면 두 번째 변환/재조회가 사라짐.

**검증**:
- 동일 pandas 원본을 "그대로 전달"과 "미리 polars 변환 후 전달" 두 경로로
  `fetch_historical_changes()`에 넣어 20개 랜덤 시드에 대해 반환값이 완전히 동일함을 확인
  (동작 보존, 순수 성능 최적화).
- 벤치마크(410행 랜덤 OHLCV, 2,000회 반복): 이중 변환 제거로 호출당 1.235ms → 0.800ms,
  **약 35% 감소**. `fetch_indice_as_stock`은 지수 개수만큼(약 8~10개) 매 새로고침/60초 자동
  갱신마다 호출되므로 체감 가능한 수준의 절감.
- `python -m pytest tests/ -v` 76/76 통과, `import data_fetcher` / `import main` 정상.

**결론**: 로드맵에 적힌 "처음부터 polars로 구성"·"중간 pandas 왕복 제거" 두 방향은 이미
달성되어 있었고, 실제 개선 여지는 두 호출부의 중복 변환 제거뿐이었음 — 이를 수정 완료.

---

### 3-4. AI 기능 — 포트폴리오 인사이트 — 부분 완료 (B·C 완료 / A 미착수)

**현황 (2026-08-28 확인)**: `gemini_helper.py`가 신설되어 `main.py`에서 실제로 호출되고
있음. 아래 A/B/C 중 B, C는 이미 구현·연결 완료, A만 남아 있음.

#### A. 종목 리포트 요약 — 미착수
- Universe 탭에서 종목 우클릭 → "AI 리포트 요약" 메뉴 (컨텍스트 메뉴 자체가 아직 없음)
- Gemini API에 종목 코드 + 최근 OHLCV 전달 → 한국어 3줄 요약 반환
- 결과를 팝업 다이얼로그로 표시

#### B. 포트폴리오 진단 — ✅ 완료
- `gemini_helper.portfolio_diagnosis(open_data, closed_data)`로 구현
- `main.py`에서 호출 (Total Assets/Trading History 쪽 "AI 진단" 흐름), 보유 종목·시장별
  집중도·미실현손익을 Gemini에 전달해 한국어 인사이트 반환

#### C. 자연어 필터 — ✅ 완료
- `gemini_helper.nl_to_filter(nl_query)`로 구현
- `main.py`에서 호출, 자연어 쿼리를 컬럼 인덱스/연산자/값 조건 JSON으로 변환해
  기존 `StockTable` 필터 팝업에 적용

**남은 작업**:
- A(종목 리포트 요약) 컨텍스트 메뉴 및 호출부 구현
- API 비용 실측 (월 호출 수 기준) — 아직 측정 안 됨

---

### 3-5. 데이터 — 실시간 시세 안정성 — ✅ 핵심 완료 (`fetch_kr_market_data` 리스팅 fallback)

**구현 내용 (2026-08-29)**: 실제 장애 경로였던 `fetch_kr_market_data`의 Step 1(종목
리스트 수집)에 fallback을 추가. 기존에는 Naver `sise_market_sum` 스크래핑이 페이지 하나만
타임아웃돼도 예외가 함수 전체를 빠져나가 `[]`를 반환했음(8/23 KOSDAQ 전체 실패 사례).

- `_fetch_kr_listing_naver()` / `_fetch_kr_listing_fdr_fallback()`로 분리: Naver 스크래핑
  실패·빈 결과 시 `fdr.StockListing(market)`(Marcap 포함) 결과로 자동 대체.
- `yf_quote_batch()`에 `max_retries` 파라미터 추가(기본값 3 유지, 호출부 변경 불필요).
- **범위 조정 (재조사 근거)**: 애초 계획한 4단계 체인("Kiwoom REST → Naver → Yahoo Finance
  → pykrx") 중 Kiwoom·pykrx는 이번 구현에서 제외:
  - Kiwoom REST는 계좌 기반 단일 종목 조회 API로, 200개 이상 종목을 매 새로고침마다
    개별 호출하는 것은 비현실적이고(속도·API 부하), 이 저장소 밖의 외부 키 파일에 의존해
    오히려 신뢰성을 낮춤.
  - pykrx는 실제 검증 결과 이 환경에서 `get_market_ohlcv_by_ticker`/`get_market_cap_by_ticker`
    모두 다수 날짜에서 빈 `(0, 0)` DataFrame을 반환(`data.krx.co.kr`의 세션/차단 이슈로
    추정) — 검증되지 않은 소스를 추가하는 대신, 실제로 동작을 확인한 FDR 폴백만 채택.
  - "소스 정보 UI에 표시"는 이 저장소의 기존 관례(다른 fallback 체인도 로그만 남기고 UI
    표시는 없음, `fetch_us_stock_data_bulk`/`fetch_investor_trend` 참고)에 맞춰 로깅으로
    범위를 좁힘.
- **검증**: Naver를 강제 실패시켜도 `fetch_kr_market_data`가 FDR 폴백으로 정상 데이터를
  반환하는지, 양쪽 다 실패 시 예외 없이 `[]`를 반환하는지, `yf_quote_batch`의
  `max_retries`가 실제로 시도 횟수를 제한하는지 스크립트로 확인(모두 통과). FDR 폴백
  리스팅이 시가총액 내림차순으로 정렬됨도 확인.

---

### 3-6. UX — 다크 모드 지원 — 미착수 (구현 후 제외됨)

**현황 (2026-08-29)**: 한 차례 전체 구현(`ui/theme.py`, 67곳 스타일시트 전수 수정,
`QApplication.setPalette()` + `plt.style.use('dark_background')` 연동, 헤더에
"🌙 Dark Mode" 체크박스)했으나, 병합 전 수동 테스트 단계에서 사용자 요청으로 **제외 및
관련 코드 전체 삭제**(`git revert`로 해당 커밋 되돌림, `ui/theme.py` 삭제). 자동
검증(스타일시트 문자열에 테마 색상이 반영되는지, pytest 76/76)은 모두 통과했었고 기능적
결함이 보고된 것은 아니었음 — 재도전 시 이전 구현을 참고할 수 있도록 아래 방향은 유지.

**방향** (재도전 시 참고):

- `QApplication.setPalette()`로 다크/라이트 토글
- matplotlib 그래프도 `plt.style.use('dark_background')` 연동
- `custom_settings.json`에 `"theme": "dark"` 저장
- (이전 구현 노트) main.py/ui/*.py의 대다수 위젯이 생성 시점에 개별
  `setStyleSheet()` 리터럴 문자열을 굽는 구조라, 라이브 재테마보다는 "설정 저장 +
  재시작 안내" 방식이 훨씬 낮은 리스크로 구현 가능함을 확인함

---

## 4. 장기 개선 (3개월+)

### 4-1. 아키텍처 — 웹 앱 전환 검토

**현황**: PyQt6 데스크톱 앱 (단일 머신 의존)  
**방향 옵션**:

| 옵션 | 기술 | 장점 | 단점 |
|------|------|------|------|
| A. 유지 | PyQt6 | 현행 자산 재사용 | 멀티 디바이스 불가 |
| B. 웹 프론트 | FastAPI + React | 모바일 접근 가능 | 재작성 비용 큼 |
| C. 경량 웹 | Streamlit / Dash | Python 자산 재사용 | 커스터마이징 제한 |

> **권장**: 단기~중기는 PyQt6 유지. 장기적으로 FastAPI 백엔드 + React 프론트엔드 검토.

---

### 4-2. 데이터 — 클라우드 동기화

**현황**: `portfolio.db`가 로컬 파일  
**방향**:

- Google Cloud Storage에 `portfolio.db` 주기적 업로드 (기존 `tools/register_secret.py` 인프라 활용)
- 또는 Cloud Firestore/Supabase로 마이그레이션
- 다중 기기 접근, 자동 백업 목적

---

### 4-3. 알림 — 조건부 시세 알림

**현황**: 알림 기능 없음  
**방향**:

- 종목별 알림 조건 설정 (목표가 도달, 손절가 도달, 거래량 급증)
- Windows 알림 (`win10toast` 또는 `plyer`) + 사운드
- 60초 `global_auto_timer` 틱마다 조건 체크

---

### 4-4. 백테스트 — 전략 비교 UI

**현황**: `run_backtest_strategy()` 함수 존재하나 결과를 텍스트로만 표시  
**방향**:

- 백테스트 결과를 matplotlib으로 시각화 (진입·청산 시점 마킹)
- 여러 전략/파라미터 조합 비교 테이블
- 전략 파라미터를 UI에서 조정 가능 (슬라이더/스핀박스)

---

### 4-5. 섹터/업종 분석

**현황**: 종목 단위 데이터만 존재  
**방향**:

- KOSPI/KOSDAQ 업종 분류 데이터 (pykrx `get_market_sector_classifications()`) 연동
- Universe 탭에 섹터별 그룹 필터 추가
- Total Assets 탭에 섹터별 비중 파이차트 추가

---

## 5. 우선순위 매트릭스

> `상태` 컬럼은 2026-09-17 재조사 결과 반영. 완료 항목은 우선순위 재산정 대상에서 제외.
> 6-x 행은 섹션 6(코드 분석 기반 개선 항목) 참고.

| # | 항목 | 영향 | 난이도 | 우선순위 | 상태 |
|---|------|------|--------|----------|------|
| 2-1 | 로깅 도입 | 중 | 낮음 | — | ✅ 완료 |
| 2-2 | 캐시 효율 모니터링 | 낮음 | 낮음 | — | ✅ 완료 |
| 2-3 | 상태바 피드백 | 중 | 낮음 | — | ✅ 완료 |
| 2-4 | 자동 백업 | 높음 | 낮음 | — | ✅ 완료 |
| 2-5 | 입력 유효성 검사 | 중 | 낮음 | — | ✅ 완료 |
| 3-4B/C | AI 진단/자연어 필터 | 높음 | 중간 | — | ✅ 완료 |
| 3-2 | 테스트 인프라 | 높음 | 중간 | — | ✅ 완료 |
| 3-1 | 모듈화 | 높음 | 높음 | — | ✅ 완료 (Phase 0~5) |
| 3-3 | pandas/polars 경계 최소화 | 낮음 | 낮음 | — | ✅ 완료 (재조사 결과 대부분 이미 최적) |
| 3-4A | AI 종목 리포트 | 높음 | 중간 | ⭐⭐⭐ 높음 | 미착수 |
| 3-5 | 시세 fallback | 높음 | 중간 | — | ✅ 핵심 완료 (`fetch_kr_market_data` 리스팅 fallback) |
| 4-3 | 조건부 알림 | 높음 | 중간 | ⭐⭐ 중간 | 미착수 |
| 4-5 | 섹터 분석 | 중 | 중간 | ⭐⭐ 중간 | 미착수 |
| 3-6 | 다크 모드 | 낮음 | 낮음 | ⭐ 낮음 | 미착수 |
| 4-4 | 백테스트 UI | 중 | 높음 | ⭐ 낮음 | 미착수 |
| 4-1 | 웹 전환 | 높음 | 매우 높음 | ⭐ 낮음 | 미착수 |
| 4-2 | 클라우드 동기화 | 중 | 높음 | ⭐ 낮음 | 미착수 |
| 6-1 | 동작 버그 5건 (로깅·타이머 이중화·UI 프리즈·경로·키 생성) | 높음 | 낮음 | — | ✅ 완료 |
| 6-3a | CLAUDE.md / AGENTS.md 현행화 | 높음 | 낮음 | — | ✅ 완료 |
| 6-2a | 파사드 역참조 해소 | 중 | 중간 | — | ✅ 완료 |
| 6-2b~f | 죽은 코드·미사용 import·중복·의존성 정리 | 중 | 낮음 | — | ✅ 완료 (US 시장 경로는 유지) |
| 6-3b | 테스트 공백 보강 | 중 | 낮음 | — | ✅ 완료 |

---

## 6. 코드 분석 기반 개선 항목 (2026-09-17)

> 2026-09-17 `src/` 전체(13,429줄, 36개 파일) 정독 + `pytest` 101/101 확인 + AST 기반 미사용
> import 스캔 결과. 모듈화(3-1)·테스트(3-2)·로깅(2-1) 인프라는 건강하며, 아래는 그 위에서
> 발견된 실제 동작 문제와 기술 부채. 파일 경로는 모두 `src/` 기준.
>
> **상태 (2026-09-17 당일 처리)**: 6-1a~e, 6-2a~f, 6-3a~b 전부 구현 완료 — 상세는 6-5 참고.
> 유일한 예외는 6-2b의 US 시장 경로(재활성화 계획이 확정되지 않아 코드 유지).

### 6-1. 실제 동작에 영향 — 우선 수정

| # | 항목 | 위치 | 문제 | 조치 |
|---|------|------|------|------|
| 6-1a | 캐시 통계 로그 미기록 (2-2 회귀) | `main.py:43-49`, `data/cache.py::_log_hist_cache_stats` | 파일/콘솔 핸들러가 WARNING인데 통계는 INFO로 출력 → `app.log`에 한 번도 기록된 적 없음. 반대로 root 로거는 DEBUG라 yfinance·urllib3 등 서드파티 디버그 레코드가 전부 생성된 뒤 폐기됨 | 파일 핸들러 INFO, root 로거 INFO로 조정 |
| 6-1b | Trading History 실시간 갱신 타이머 이중화 | `ui/history_tab.py:98-102` + `main.py`의 `auto_lightweight_tick` 연결 | 탭 자체 60초 타이머가 무조건 시작되고, MainWindow 글로벌 타이머도 `auto_lightweight_tick` 시그널로 같은 `_start_realtime_price_update`를 호출. "Auto Update" 체크를 꺼도 KIS WebSocket/REST 폴링이 계속됨 (`isRunning` 가드로 중복 실행만 막힘) | 탭 자체 타이머 제거, 글로벌 타이머 경로로 일원화 |
| 6-1c | UI 스레드 동기 네트워크 호출 | `ui/history_tab.py::_fetch_account_deposit`, `_on_cell_double_clicked` (col 2, `fetch_single_stock`) | KIS REST 예수금 조회와 티커 편집 시 종목 조회가 메인 스레드에서 실행되어 응답 지연 시 창이 멈춤 (2026-09-13 3차 Gemini 이관과 같은 유형의 잔여분) | QThread로 이관 |
| 6-1d | 상태 파일 경로가 cwd 상대 | `universe_cache.json`, `custom_settings.json`, `trading_record.json`, `kis_token_cache.json`, `vkospi_cache.json`, `app.log`, `archive/` | `trade_db.py`만 `_BASE_DIR` 절대경로. 다른 폴더에서 실행하면 DB만 맞고 나머지는 빈 파일로 새로 생성됨 (2026-08-29 6차 회귀와 같은 계열). CLAUDE.md의 "루트에서 실행" 제약의 원인 | `src/paths.py`에 `BASE_DIR`과 파일 경로 상수 집약 |
| 6-1e | 거래 추가 시 orig_key 생성 중복 | `ui/history_tab.py::_show_add_trade_dialog` | `get_trade()`를 반복 호출하는 자체 루프로 키를 만든 뒤 upsert → 2026-09-13 2차에서 만든 충돌 방지 경로(`trade_db._insert_with_generated_key`)를 우회 | `orig_key` 없이 `upsert_trade` 호출, 반환된 키 사용 |

### 6-2. 구조 / 기술 부채

| # | 항목 | 상세 | 조치 |
|---|------|------|------|
| 6-2a | 하위 패키지 → 파사드 역참조 | `data/market.py`·`data/cache.py`가 `import data_fetcher`(상위 파사드)를 하고 `getattr(_df_mod, ...)` 우회가 21곳, `_bump_hist_cache_counter`로 카운터를 두 모듈에 수동 동기화. 원인: `tests/test_data_fetcher.py`가 `data_fetcher.X`를 patch하기 때문 | 테스트 patch 대상을 `data.market`/`data.cache`로 변경 → 우회 코드 전부와 파사드의 `fdr/yf/requests/pd/pl/np` top-level import 제거 |
| 6-2b | 죽은 코드 | `ui/history_tab.py`의 no-op 스텁 3개(`load_from_json_only`, `_append_custom_trades`, `_apply_overrides`, 호출자 0건). US 시장 경로(`fetch_us_market_data`/`fetch_us_stock_data_bulk`/`run_bulk_backtest_chunk`, 약 300줄)는 `AllDataFetchThread`·`UniverseTab` 콤보에서 주석 처리되어 도달 불가 | 스텁 삭제. US 경로는 재활성화 계획 확정 후 삭제 또는 설정 플래그로 명시 |
| 6-2c | 미사용 import | `main.py` 14개(json, datetime, `ui.common` 헬퍼 대부분), `data/market.py` 8개(BeautifulSoup, 캐시 카운터 등), `ui/universe_tab.py` 6개, `data/cache.py` 4개, 그 외 8개 파일 | 일괄 정리 후 `ruff`를 requirements-dev에 추가 |
| 6-2d | 의존성 목록 불일치 | `pykrx`는 코드에서 미사용(3-5에서 제외됐으나 목록에 잔존). 실제 사용하는 `beautifulsoup4`·`lxml`은 누락(간접 설치에 의존). 버전 미고정 | requirements.txt 정리 + 버전 핀 |
| 6-2e | 중복 패턴 | 폰트 패밀리 CSS 문자열 17회, 좀비 스레드 정리 코드 3곳(10회), KR 6자리 코드 판별 휴리스틱 5곳, 네이버 시총 '조/억' 파싱 2회(`_fetch_naver_info` 내), `fetch_stock_ma_multi`의 target_year 유/무 분기 복붙 | `ui/common` 폰트 상수, `_retire_thread()`, `is_kr_code()`, `_parse_marcap_krw()` 헬퍼로 추출 |
| 6-2f | 소소한 항목 | `QSettings("MyCompany", "PortfolioManager")` placeholder가 레지스트리에 그대로 기록. `PositionPriceFetchThread`가 `skip_fetch` 생성자 인자를 받는데 호출부는 `thread._skip_fetch = ...`로 사후 주입. `_START_DATE`가 import 시 고정되어 장기 실행 시 lookback 기준일이 밀림. `get_stock_listing` singleflight 캐시가 무기한. `kis_token_cache.json`에 bearer 토큰 평문 저장(gitignore는 됨) | 개별 수정 |

### 6-3. 문서 / 테스트

| # | 항목 | 상세 | 조치 |
|---|------|------|------|
| 6-3a | CLAUDE.md / AGENTS.md 현행성 | "git 아님", `main.py` ~5,900줄, `data_fetcher.py` ~2,150줄, "테스트 없음"으로 기술되어 있으나 실제는 git 46 커밋, `main.py` 313줄, `data/` 패키지 분리, pytest 101개. `archive/backup_*` 수동 백업 규칙도 git 도입 후 재검토 필요. 이 문서를 전제로 움직이는 AI 에이전트가 잘못된 판단을 하게 되므로 우선순위 높음 | 두 문서를 동기 갱신 (AGENTS.md는 CLAUDE.md의 1줄 차이 사본) |
| 6-3b | 테스트 공백 | `_compute_pl_fields`(부분 매도 안분), `_apply_filter`의 월별 요약 행, `fetch_historical_changes`의 `bp`/`abs` 모드가 미검증. 모두 Qt 없이 순수 함수로 테스트 가능 | 테스트 추가 |

### 6-4. 권장 착수 순서

1. ✅ 6-3a CLAUDE.md / AGENTS.md 갱신 (코드 변경 결과를 반영하기 위해 실제로는 마지막에 작성)
2. ✅ 6-1a 로깅 레벨 수정
3. ✅ 6-1b 타이머 이중화 제거
4. ✅ 6-1d 경로 통일 (`src/paths.py`)
5. ✅ 6-1c / 6-1e `history_tab` 잔여 동기 호출·키 생성 정리
6. ✅ 6-2a 파사드 역참조 해소 (테스트 동반 수정)
7. ✅ 6-2b~f 죽은 코드·중복·의존성 정리, 6-3b 테스트 보강

### 6-5. 진행 결과 (2026-09-17)

| # | 조치 내용 |
|---|-----------|
| 6-1a | `main.py`: 파일 핸들러 WARNING→INFO, root DEBUG→INFO (콘솔은 WARNING 유지). 캐시 통계·자동 백업 완료 로그가 `app.log`에 실제로 기록됨 |
| 6-1b | `ui/history_tab.py`의 자체 60초 `_rt_price_timer` 제거. 실시간 갱신은 MainWindow 글로벌 타이머 → `auto_lightweight_tick` 경로 하나로만 동작 |
| 6-1c | `threads/fetch_threads.py`에 `AccountDepositThread` 신설, Fetch 버튼이 이를 사용(버튼 비활성화 + 상태 라벨 표시). 티커 셀 편집 시 종목명 조회는 `SingleStockFetchThread`로 이관, 결과 도착 시 `_on_ticker_name_resolved`에서 갱신 |
| 6-1d | `src/paths.py` 신설(`BASE_DIR` + 11개 경로 상수). `main.py`(app.log·.env), `trade_db.py`, `collectors/kis.py`·`krx.py`, `threads/fetch_threads.py`(AutoBackupThread), `ui/universe_tab.py`·`assets_tab.py`가 모두 이를 사용 — cwd 무관 |
| 6-1e | `_show_add_trade_dialog`의 `get_trade()` 폴링 키 생성 루프 삭제. `orig_key` 없이 `upsert_trade`에 맡기고 반환 키를 레코드에 기록 |
| 6-2a | `data/cache.py`: 카운터 2개 int → `_HIST_CACHE_STATS` dict + `_record_hist_cache_lookup()`; `_log_hist_cache_stats`의 `data_fetcher` 역참조 제거. `data/market.py`: `_bump_hist_cache_counter`와 `getattr(_df_mod, ...)` 우회 전부 삭제, `get_usd_krw_rate`도 모듈 로컬 `fdr` 직접 사용. 파사드의 `fdr/yf/requests/pd/pl/np` top-level import 제거. `tests/test_data_fetcher.py`·`test_assets_calc.py`는 `data.market`/`data.cache`/`data.collectors.yahoo`를 patch하도록 변경(+파사드가 동일 stats 객체를 노출하는지 검증하는 테스트 1개 추가) |
| 6-2b | `history_tab`의 no-op 스텁 3개 삭제. US 시장 경로는 유지(계획 미확정) — CLAUDE.md에 "UI에서 주석 처리됨"으로 명시 |
| 6-2c | 미사용 import·변수 정리(`main.py` 14개 등, ruff F401/F841 37건 → 0건). `ruff.toml`(F 규칙, 재export 모듈은 F401 제외)·`requirements-dev.txt`(pytest, ruff) 신설 |
| 6-2d | `requirements.txt`: 검증된 버전으로 전부 핀, `pykrx` 제거, `beautifulsoup4`·`lxml` 추가, `pytest`는 dev로 이동 |
| 6-2e | `ui/common.FONT_FAMILY_CSS`(17곳 → 상수 1개), `ui/common.retire_thread()`(좀비 스레드 정리 3곳 통합), `data/cache.is_kr_code()`(5곳), `naver._parse_marcap_krw()`(2곳), `fetch_stock_ma_multi`를 `_load_ohlcv_window()` + `_naver_code_for()`로 재구성(target_year 유/무 분기 복붙 제거). 동작 변화 1건: target_year 지정 시 KR 종목코드도 Naver 우선(기존은 FDR) — FDR의 KR 소스 역시 Naver라 데이터는 동일하고, Naver 실패 시 FDR로 폴백 |
| 6-2f | `QSettings` 스코프 `MyCompany/PortfolioManager` → `PortfolioManagement/PortfolioManagement`(기존 값 1회 자동 이전). `PositionPriceFetchThread`의 `skip_fetch`를 생성자 인자로 전달. `_START_DATE` 상수 → `start_date()` 함수(17곳). `_singleflight_cache`(종목 리스팅)에 일 단위 만료 추가. KIS 토큰 평문 캐시는 CLAUDE.md에 문서화만 |
| 6-3a | CLAUDE.md / AGENTS.md 전면 재작성(git 관리, `src/` 구조, `paths.py`, 스레드 규칙, 검증 명령, 새 헬퍼 관례) |
| 6-3b | `tests/test_history_calc.py`(`_compute_pl_fields` 5건, `_build_monthly_rows` 3건 — 월별 요약 로직을 `_apply_filter`에서 순수 staticmethod로 추출)·`tests/test_indicators.py`(`fetch_historical_changes` pct/bp/abs 6건, `is_kr_code`/`start_date`/`_parse_marcap_krw` 3건) 신설 |

**검증**: `python -m compileall src` 통과, `ruff check src` 0건, `pytest src/tests` **119/119** 통과(신규 18개 포함),
`QT_QPA_PLATFORM=offscreen`으로 `import main` + 새 헬퍼 호출 스모크 통과. GUI 수동 확인은 `test_plan.md` 절차로
별도 필요(특히 Fetch 버튼 비동기화, 티커 편집 후 종목명 갱신, Auto Update 해제 시 폴링 중단).

---

## 7. UI 개선 항목 (2026-09-19)

> 사용자 요청("현재 SW의 UI를 검토해서 개선점을 도출할 예정")에 따라 코드 기반으로 진행한 UI 리뷰 결과.
> 이번 1차는 computer-use 스크린샷 없이 `ui/main.py`, `ui/common.py`, `ui/auto_trading_tab.py`,
> `ui/trend_following_tab.py` 정독만으로 도출했다 — 화면 캡처를 곁들인 2차 리뷰는 필요 시 별도 진행.
> **상태**: 7-1~7-4 전부 구현 완료(2026-09-19 7차·10차, 변경 이력 참고). computer-use 화면 캡처를
> 곁들인 2차 리뷰는 필요 시 별도 진행.

### 7-1. Strategy 상위 탭 도입 — ✅ 완료

**구현 (2026-09-19 7차)**: `ui/strategy_tab.py::StrategyTab` 신설 — 내부 `QTabWidget`에
`AutoTradingTab`/`TrendFollowingTab`을 그대로 재사용해 붙이고, 아직 UI가 없는 `MA Cross`는
안내 문구만 있는 서브탭으로 자리를 비워둠. 상단 요약 바는 리밸런싱 쪽은
`compute_weekly_rebalance_signals()`를 그대로 호출(이미 메모리에 있는 Universe 데이터라 네트워크
없음), 추세추종 쪽은 시가총액 상위 30종목(`_TF_SUMMARY_MAX_TICKERS`)에 대해서만
`run_backtest_for_ticker()`를 호출해 최종 행의 `position`으로 보유중/관망을 집계(설계 문서의
"오픈 이슈"에 대한 이번 구현 결정: 전 종목이 아니라 상위 N종목만, 400일 lookback으로 조회 비용
제한) — 새 `threads/fetch_threads.py::StrategySummaryThread`에서 백그라운드로 실행하고 탭이 처음
보일 때(`showEvent`) 1회 자동 실행 + "🔄 Refresh Signals" 버튼으로 수동 재계산. `main.py`는 최상위
탭 5→4개로 축소(`auto_trading_tab`/`trend_following_tab` 직접 참조를 `strategy_tab` 하나로 교체),
`closeEvent`의 스레드 정리도 `StrategyTab.collect_threads_to_stop()`(내부적으로 두 서브탭의
`collect_threads_to_stop()`를 합침) 하나로 교체. 오프스크린 스모크 스크립트로 탭 구조·서브탭
개수·요약 바 텍스트(rebalance/trend-following 함수를 모킹)를 확인. 검증: pytest 220/220, ruff 0건.

| 항목 | 내용 |
|------|------|
| 배경 | 최상위 탭 5개(Universe/History/Assets/Auto Trading/Trend Following) 중 마지막 둘은 사실상 "전략 실행 + 백테스트"라는 같은 역할인데 나란히 최상위에 노출됨. `ma_cross`는 아예 탭이 없어 전략 간 노출 수준도 불균등(3-4·6-x와 별개로 이번 리뷰에서 새로 발견) |
| 결정 | 최상위 탭을 **Universe / History / Assets / Strategy** 4개로 재편. `Auto Trading`·`Trend Following`(추후 `MA Cross`)은 `Strategy` 탭 내부 `QTabWidget` 서브탭으로 이동 |
| 요약 바 | 서브탭 전환기 **위쪽**에 전체 전략 통합 "오늘의 신호" 요약 바를 상시 노출(사용자 선택: 서브탭별 개별 노출이 아니라 상단 통합 방식). rebalance는 `compute_weekly_rebalance_signals()`의 buy/sell 상위 후보 요약, trend_following은 `donchian_signal()` 마지막 행의 `position`/`entry`/`exit`을 관심종목 전반에 대해 가볍게 계산해 "보유중 N · 관망 M" 형태로 요약(전량 백테스트를 상단 바에서 재실행하지는 않음 — 기존 "Run Backtest"/"Run Portfolio" 버튼과는 별개 경로) |
| 구현 방향 | 신규 `ui/strategy_tab.py::StrategyTab(QWidget, ThreadOwnerMixin)` — 내부 `QTabWidget`(기존 `AutoTradingTab`/`TrendFollowingTab` 그대로 재사용) + 상단 요약 바 위젯. `main.py`는 최상위 탭 5→4개로 축소하고 `StrategyTab` 하나만 추가. `closeEvent`의 `collect_threads_to_stop` 체인에 `StrategyTab` 경유 단계 추가 |
| 범위 | `ma_cross`는 아직 UI 자체가 없으므로 이번 1차에서는 서브탭 자리만 비워두거나 "준비 중"으로 표시 — 실제 `MaCrossTab` 구현은 별도 작업으로 분리 |
| 오픈 이슈 | 요약 바가 관심종목 전체에 대해 매번 신호를 재계산하면 탭 전환마다 지연이 생길 수 있음 — `UniverseTab.all_data` 캐시 재사용 여부·갱신 주기는 구현 시 확정 필요 |

### 7-2. 버튼 색상 중앙화 — ✅ 완료

**구현 (2026-09-19 7차)**: `ui/common.py`에 역할별 상수(`_ACTION_BACKTEST_COLOR/_HOVER`,
`_ACTION_PORTFOLIO_COLOR/_HOVER`, `_ACTION_VALIDATE_COLOR/_HOVER`, `_STATUS_SUCCESS_COLOR`,
`_STATUS_FAIL_COLOR`, `_SECONDARY_BUTTON_STYLE`) + `action_button_style(color, hover_color)`
헬퍼 추가. 세 색상 계열을 하나로 합치지 않고 역할별로 이름만 부여한 이유: Auto Trading·Trend
Following의 "Run Backtest"는 이미 같은 `#8e44ad`였고(그대로 `_ACTION_BACKTEST_COLOR` 공유),
"Run Portfolio"/"Validate"는 서로 다른 동작이라 구분 유지가 사용자에게 더 유용. `ui/auto_trading_tab.py`의
Run Backtest, `ui/trend_following_tab.py`의 Run Backtest/Run Portfolio/Validate/Chart/Use
Universe/risk-gate 셀 색상을 전부 상수 참조로 교체.

| # | 현재 상태 | 위치 | 조치 |
|---|-----------|------|------|
| 7-2a | "실행" 계열 버튼 색이 탭마다 제각각: Auto Trading "Run Backtest" `#8e44ad`, Trend Following "Run Backtest"도 동일 `#8e44ad`이지만 "Run Portfolio"는 `#1a5276`, "Validate (IS/OOS)"는 또 다른 `#6c3483` | `ui/auto_trading_tab.py`, `ui/trend_following_tab.py` | `ui/common.py`에 역할별 색상 상수 신설(예: `_ACTION_BACKTEST_COLOR`=`#8e44ad` 계열로 통일, `_ACTION_PORTFOLIO_COLOR`) 후 각 탭이 상수를 참조하도록 교체 |
| 7-2b | "Chart", "Use Universe" 버튼은 스타일 지정이 전혀 없어 OS 기본 버튼으로 노출 — 다른 버튼들과 톤이 어긋남 | `ui/trend_following_tab.py` | 보조 액션용 공통 스타일(무채색 계열) 1종을 `ui/common.py`에 추가해 적용 |
| 7-2c | 상태 표시 색(risk gate PASS `#107c10` / FAIL `#c0392b`)도 인라인으로만 정의되어 있어 다른 곳에서 같은 패턴을 또 하드코딩할 위험 | `ui/trend_following_tab.py` | `_STATUS_SUCCESS_COLOR`/`_STATUS_FAIL_COLOR` 상수화 |
| 참고 | `_ACCENT_COLOR`(`#0078d4`)/`_ACCENT_HOVER_COLOR`는 앱 전역 QSS 기본 버튼에만 쓰이는 "주 액션" 색으로 유지, 위 상수들은 그 옆에 "보조 액션군" 색상 세트로 추가 | `ui/common.py` | — |

### 7-3. 탭 간 컨트롤 밀도 조정 — ✅ 완료

**구현 (2026-09-19 7차)**: `QGroupBox` 대신 토글 `QPushButton`(체크 가능) + 내용을 담은
`QWidget`의 `setVisible()`로 구현(`TrendFollowingTab._make_collapsible()`) — `QGroupBox`는
체크 해제 시에도 프레임·타이틀이 그대로 차지해 실제로 행이 접히지 않는 반면, 이 방식은 기본
`False`(접힘)일 때 본문 위젯 자체가 숨어 세로 공간을 실제로 돌려준다. v2 오버레이 행·v3
포트폴리오/검증 행 모두 기본 접힘, 토글 시 화살표(▶/▼)와 라벨 텍스트가 함께 바뀜. 숨겨진
위젯도 `setValue()`/`text()` 등은 그대로 동작하므로 기존 테스트(`test_trend_following_tab.py`)
영향 없음.

| 항목 | 내용 |
|------|------|
| 현황 | Auto Trading은 2행(신호 계산 버튼+라벨 / 백테스트 lookback 콤보+버튼)인데, Trend Following은 4행(티커·기간 / entry·exit·비용 / v2 오버레이 5종 / v3 포트폴리오+검증 6종)으로 같은 "전략 탭" 레벨에서 복잡도 격차가 큼 |
| 조치 | v2 오버레이 행과 v3 포트폴리오/검증 행을 접이식 `QGroupBox`(또는 토글 버튼)로 감싸 기본 노출을 1~2행 수준으로 축소. v2는 기본값이 전부 off, v3는 별도 버튼으로 트리거되는 독립 기능이라 접었을 때 기존 워크플로에 영향 없음 |
| 연계 | 7-1의 `StrategyTab` 재구성과 함께 진행하면 레이아웃을 한 번에 정리할 수 있음 |

### 7-4. History/Assets 탭 버튼 색상 재사용 충돌 (7-2 후속) — ✅ 완료

**구현 (2026-09-19 10차)**: 아래 "제안하는 역할별 재배치" 표를 그대로 적용. `ui/common.py`에
`_ACTION_INSIGHT_COLOR`/`_ACTION_INSIGHT_HOVER_COLOR`(`#0a3d62`/`#1e5799`, 삭제된 AI Diagnosis
버튼 색 재활용) 신설 + `_SECONDARY_BUTTON_STYLE`을 `#888`→`#6c757d`로 교체(7-4f). `ui/assets_tab.py`:
Add Record 파랑→주황(`#d35400`, History Add Trade와 동일 hex), This Week 초록→회색(`#6c757d`),
Graph 보라(`_ACTION_BACKTEST_COLOR`)→`_ACTION_INSIGHT_COLOR`. `ui/history_tab.py`: Reload
초록(`_BTN_GREEN`)→파랑(`_BTN_BLUE`, Fetch와 동일 역할), Summary 보라(`_BTN_PURPLE`)→신규
`_BTN_INSIGHT`(`_ACTION_INSIGHT_COLOR` 참조), Sort by Date 네이비(`_BTN_NAVY` =
`_ACTION_PORTFOLIO_COLOR`)→신규 체크 가능한 회색 `_BTN_GREY_CHECKABLE`(선택 상태는 더 짙은
회색 `#495057` + 테두리로 구분, 네이비의 체크/호버 구조는 유지). 이제 안 쓰는
`_BTN_GREEN`/`_BTN_PURPLE`/`_BTN_NAVY` 제거. Delete Selected(빨강)·Export/검색 아이콘(회색)·
Trend Following Chart/Use Universe(공용 `_SECONDARY_BUTTON_STYLE`, 색만 자동 반영)는 표 그대로라
변경 없음. 오프스크린 스모크로 버튼 10개의 실제 `styleSheet()` hex를 표와 대조해 확인. 검증:
pytest 219/219, ruff 0건.

> 7-2는 Strategy 탭(`ui/auto_trading_tab.py`/`ui/trend_following_tab.py`) 범위로 구현되어
> `_ACTION_BACKTEST_COLOR`(`#8e44ad`)/`_ACTION_PORTFOLIO_COLOR`(`#1a5276`)/`_ACTION_VALIDATE_COLOR`
> (`#6c3483`)라는 이름 있는 상수로 정리됐다. 사용자가 제공한 Total Assets/Trading History 탭
> 스크린샷을 코드(`ui/assets_tab.py`, `ui/history_tab.py`)와 대조해보니, 정확히 같은 hex가
> 전혀 다른 의미의 버튼에도 쓰이고 있어 — "이 색은 backtest/portfolio/validate를 뜻한다"는
> 7-2의 명명 규칙과 정면으로 충돌한다. History 탭의 AI Diagnosis/Export 버튼은 이후
> (2026-09-19 8차)에 삭제되어 아래 목록에서는 제외했다.

| # | 충돌 | 위치 | 문제 |
|---|------|------|------|
| 7-4a | Assets "Graph" `#8e44ad` = `_ACTION_BACKTEST_COLOR` | `ui/assets_tab.py` | 자산 그래프 보기(읽기전용)가 "백테스트 실행"과 같은 색 |
| 7-4b | History "Summary" `#6c3483` = `_ACTION_VALIDATE_COLOR` | `ui/history_tab.py` | 보유 요약 팝업(읽기전용)이 "IS/OOS 검증 실행"과 같은 색 |
| 7-4c | History "Sort by Date" `#1a5276` = `_ACTION_PORTFOLIO_COLOR` | `ui/history_tab.py` | 정렬 토글이 "포트폴리오 백테스트 실행"과 같은 색 |
| 7-4d | Assets "This Week" · History "Reload" `#107c10` = `_STATUS_SUCCESS_COLOR` | `ui/assets_tab.py`, `ui/history_tab.py` | 7-2c가 초록을 "상태 표시 전용"으로 명시했는데 액션 버튼 2곳이 여전히 같은 색을 씀 |
| 7-4e | Assets "Add Record" `#0078d4`(파랑) vs History "Add Trade" `#d35400`(주황) | `ui/assets_tab.py`, `ui/history_tab.py` | 같은 "새 항목 추가" 동작인데 색이 다름 |
| 7-4f | `_SECONDARY_BUTTON_STYLE`(7-2b 신설, `#888`) vs 기존 Export/검색 버튼 `#6c757d` | `ui/common.py`, `ui/assets_tab.py`, `ui/history_tab.py` | "보조 액션" 역할의 회색이 `#888`/`#6c757d` 두 가지로 갈라짐 |

**제안하는 역할별 재배치** (기존에 이미 쓰이던 값 위주로 재사용, 신규 상수는 `_ACTION_INSIGHT_COLOR` 하나만 추가):

| 역할 | 색상 | 대상 |
|---|---|---|
| 데이터 가져오기/새로고침 | 파랑 `#0078d4`(`_ACCENT_COLOR`) | Fetch, Reload(초록 → 파랑) |
| 전략 실행 (7-2 명명 유지, 탭 범위 한정) | `_ACTION_BACKTEST/PORTFOLIO/VALIDATE_COLOR` | Auto Trading·Trend Following 내부 전용 — 다른 탭에서 재사용 금지 |
| 인사이트 보기 (읽기전용 팝업/차트) | 신규 `_ACTION_INSIGHT_COLOR` = `#0a3d62`(구 AI Diagnosis 색, 삭제 후 비어 있어 재활용) | Assets "Graph", History "Summary" |
| 추가/생성 | 주황 `#d35400`(`_BTN_ORANGE`) | History "Add Trade", Assets "Add Record"(파랑 → 주황) |
| 삭제 | 빨강 `#c0392b` | Assets "Delete Selected" (변경 없음) |
| 상태 표시 전용 (액션 버튼에 쓰지 않음) | 초록 `_STATUS_SUCCESS_COLOR` / 빨강 `_STATUS_FAIL_COLOR` | risk-gate PASS/FAIL 등 상태 셀만 |
| 보조 유틸리티 | 회색 `#6c757d`로 통일(`_SECONDARY_BUTTON_STYLE`도 `#888` → `#6c757d`로 교체) | Export, 검색 아이콘, History "Sort by Date"(네이비 → 회색), Assets "This Week"(초록 → 회색), Trend Following "Chart"/"Use Universe" |

---

## 변경 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-08-21 | 초안 작성 |
| 2026-08-28 | 코드 재조사 후 진행 상황 갱신: 로깅 도입(2-1)·AI 진단·자연어 필터(3-4B/C) 완료 반영, 시세 fallback(3-5) 부분 진행 상태 반영(`fetch_kr_market_data` 장애 사례 기록), 우선순위 매트릭스에 상태 컬럼 추가 |
| 2026-08-28 (2차) | 단기 개선 항목(2-1~2-5) 코드 구현 완료: 남은 무음 예외 로깅 교체, `_HIST_CACHE` hit/miss 모니터링, 상태바 진행 메시지, 자동 백업 스케줄러(`archive/auto_*`, 최근 7개 보관), 거래 입력 다이얼로그 실시간 검증(빨간 테두리+툴팁, 매수일>매도일 경고) 추가. 백업: `archive/backup_20260828_163743/` |
| 2026-08-28 (3차) | 3-2 테스트 인프라 구축 완료: `tests/` 디렉터리 신설, `pytest` 설치 및 `requirements.txt` 추가, 4개 테스트 파일 작성 (76개 테스트 100% pass). `TempDBMixin`으로 실제 DB·JSON 완전 격리. |
| 2026-08-28 (4차) | 3-1 모듈화 분석 반영: main.py 6,490줄/22개 클래스 실측, 클래스별 라인 수·배치 계획·결합도(TradingHistoryTab→MainWindow 직접 참조 0건) 조사. 기대 효과(파일 크기 -87%, AI 컨텍스트 -87%, 테스트 +30~50개) 및 5단계 구현 계획(예상 8~10일) 추가. |
| 2026-08-29 | 3-1 모듈화 Phase 0~3 구현 완료. `main.py` 6,490줄→3,035줄(-53%), 22개 클래스→3개. `threads/fetch_threads.py`(7개 스레드), `threads/realtime.py`(RealtimePriceThread), `ui/widgets.py`(4개 위젯), `ui/dialogs.py`(7개 다이얼로그) 분리 완료. Phase 4(탭 분리)·5(MainWindow 최소화) 잔여. 우선순위 매트릭스 3-1 상태 '부분 완료'로 갱신. |
| 2026-08-29 (2차) | 3-1 모듈화 Phase 4~5 구현 완료로 전체 완료. `TradingHistoryTab`→`ui/history_tab.py`, `TradingRecordTab`→`ui/assets_tab.py`, `MainWindow`에 인라인으로 남아있던 Trading Universe 탭을 `ui/universe_tab.py`의 `UniverseTab`으로 분리. `main.py` 3,035줄→401줄(누계 -94%), `MainWindow` 833줄→247줄. 탭 간 통신은 기존 `status_message` 시그널 패턴을 확장해 구현(`status_text_changed`/`sync_time_changed`/`refresh_started`/`auto_lightweight_tick`). 검증: `py_compile`, `import main`, pytest 76/76, `MainWindow()` 인스턴스화+시그널 전파 스모크 테스트. 백업: `archive/backup_20260829_141942/`. 우선순위 매트릭스 3-1 상태 '완료'로 갱신. |
| 2026-08-29 (3차) | 3-3 pandas/polars 경계 최소화 완료. 재조사 결과 로드맵이 우려한 두 항목(히스토리컬 파이프라인 구성, `_compute_indicators()` 중간 왕복)은 이미 최적 상태였음을 확인. 대신 `fetch_single_stock()`·`fetch_indice_as_stock()`이 동일 pandas 데이터를 `fetch_historical_changes()`에 원본으로 넘겨 내부에서 중복 변환(또는 불필요한 재조회)을 일으키던 실제 낭비 2건을 발견해, 이미 변환된 polars df를 그대로 전달하도록 수정(`_to_polars()`는 polars 입력에 no-op이므로 안전). 검증: 20개 랜덤 시드로 동작 동일성 확인, 벤치마크로 호출당 약 35% 시간 절감 측정, pytest 76/76 통과. 우선순위 매트릭스에 3-3 행 추가(완료). |
| 2026-08-29 (4차) | 3-5 시세 fallback 핵심 완료: `fetch_kr_market_data`의 Naver 리스팅 스크래핑이 실패해도 `fdr.StockListing()`으로 자동 대체하도록 `_fetch_kr_listing_naver`/`_fetch_kr_listing_fdr_fallback`로 분리(8/23 KOSDAQ 전체 실패 사례의 재발 방지). `yf_quote_batch(max_retries=...)` 추가. 당초 계획한 Kiwoom·pykrx 단계는 재조사 결과(Kiwoom은 대량 조회에 부적합, pykrx는 이 환경에서 실제로 빈 데이터만 반환) 제외하고 이유를 문서화. 검증: Naver 강제 실패 시나리오·양쪽 실패 시나리오·재시도 횟수 제한을 스크립트로 확인, pytest 76/76 통과. |
| 2026-08-29 (5차) | 3-6 다크 모드 구현(전체 범위) 후 병합 전 수동 테스트 단계에서 사용자 요청으로 **제외 및 코드 전체 삭제**(`ui/theme.py` 삭제, main.py·ui/*.py의 관련 변경 `git revert`). 자동 검증은 모두 통과했었고 기능 결함이 보고된 것은 아님 — 재도전 시 참고할 수 있도록 방향/설계 노트는 3-6 섹션에 유지. 이 되돌림은 같은 커밋에 함께 있던 3-5 로드맵 문서화도 되돌려버려서 별도로 복원함(3-5 코드 자체는 되돌려지지 않고 그대로 유지됨). 우선순위 매트릭스 3-6 상태 '미착수'로 복귀. |
| 2026-08-29 (6차) | `TradingRecordTab`(Total Assets 탭)이 `trading_record.json`을 잘못된 경로(`ui/`)에서 찾던 Phase 4 회귀 버그를 사용자의 수동 테스트(TEST_PLAN.md)로 발견해 수정. `os.path.dirname(os.path.abspath(__file__))` 기반 경로를 `ui/universe_tab.py`와 동일한 상대경로 방식으로 교체. 실제 데이터(35개 레코드) 정상 로딩 확인. 자동 테스트·스크립트 검증으로는 잡지 못했던 사례 — `TEST_PLAN.md`를 리포지토리에 추가해 병합 전 수동 확인 절차를 문서화. |
| 2026-08-31 | Phase 1 긴급 버그 수정 및 안정화: (1) `gemini_helper.py`의 미지원 모델명(`gemini-3.6-flash`)을 `gemini-2.5-flash` 및 `GEMINI_MODEL` 환경변수 오버라이드로 수정, (2) `data/rebalance.py`의 적자 기업(음수/0 PER) 팩터 추출 시 `None`으로 필터링하여 저평가 왜곡 방지 및 단위테스트 추가, (3) `ui/dialogs.py`의 레거시 `backend_qt5agg` 임포트를 Qt6 표준인 `backend_qtagg`로 통일. 백업: `archive/backup_20260831_095217/`. 검증: pytest 77/77 통과. |
| 2026-08-31 (2차) | Phase 2 아키텍처 정돈 및 데이터 무결성 강화: (1) `ui/common.py` 신설 (`create_font`, `_fmt_num_edit`, `_validate_date_str`, `_validate_positive_number`, `_mk_field_validator`, `atomic_save_json`, `safe_load_json`, UI 상수), (2) `main.py` 및 `ui/*.py`의 모든 지연 임포트(`def _get_*`)를 제거하고 `ui.common` 단방향 임포트로 통일, (3) `universe_cache.json`, `custom_settings.json`, `trading_record.json`, `vkospi_cache.json`에 임시파일 기반 원자적 교체(`atomic_save_json`/`os.replace`) 적용하여 강제 종료 시 파일 깨짐 원천 차단, (4) `tests/test_ui_common.py` 신설. 백업: `archive/backup_20260831_095811/`. 검증: pytest 84/84 통과 (100%), 스모크 테스트 완료. |
| 2026-08-31 (3차) | Phase 3-1 실전 퀀트 백테스트 거래비용 모델링 완료: (1) `data/rebalance.py`의 `run_rebalance_backtest` 및 `_run_walkforward_simulation`에 실전 매수/매도 수수료(기본 0.015%) 및 매도 증권거래세(기본 0.18%)를 반영하여 순수익률(Net Return)과 총 거래비용(Total Cost Amount/Drag %)을 정밀 계산, (2) `threads/fetch_threads.py`의 `RebalanceBacktestThread` 연동, (3) `ui/dialogs.py`의 `BacktestResultDialog`에 순수익률 및 총 거래비용 지표 표시, (4) `tests/test_backtest.py`에 `TestRebalanceTransactionCosts` 단위테스트 추가. 백업: `archive/backup_20260831_100645/`. 검증: pytest 85/85 통과 (100%). |
| 2026-09-13 | 국내주식 관련 기능(예수금 조회, 종목 시세/일봉, 투자자 매매동향, 실시간 시세)을 키움증권 REST API에서 한국투자증권(KIS) REST/WebSocket API로 전면 교체, `data/collectors/kiwoom.py` 삭제 및 `data/collectors/kis.py` 신설(298번째 줄 재조사 결론이 KIS에도 동일 적용 — 대량 조회 불가, 보유 종목 소수 실시간 갱신에만 사용). 예수금 Fetch 버튼의 비밀번호/OTP 입력창 제거(KIS 잔고조회는 비밀번호 불필요). 실시간 시세는 미국주식(Yahoo) 제외, 국내주식만 전환. `requirements.txt`에 `websocket-client` 추가, 키 파일은 기존 Kiwoom 키와 같은 외부 폴더(`D:\Source Code\Kiwoom MCP`)에 `kis_appkey.txt`/`kis_secretkey.txt`로 위치. 백업: `archive/backup_20260913_173206/`. 검증: pytest 85/85 통과, 실제 KIS 응답 필드명은 사용자가 키 파일을 배치한 뒤 스모크 테스트로 최종 확인 예정. |
| 2026-09-13 (2차) | Phase 1 데이터 안정성 강화 및 무결성 개선: (1) `trade_db.py` 및 `ui/history_tab.py`에서 동일 종목/날짜/수량 분할 매수 시 기존 레코드가 덮어씌워지던 버그를 고유 접미사 자동 부여(`orig_key` 충돌 방지)로 해결하고 단위테스트 추가 (`test_orig_key_collision_avoidance_on_new_insert`, `test_batch_upsert_collision_avoidance`), (2) `ui/history_tab.py`의 `_save_custom_trade` 실패 시 무음 처리되던 문제를 `QMessageBox.critical` 알림 및 메모리 롤백 처리로 데이터 유실 방지, (3) `requirements.txt`에 누락된 핵심 라이브러리(`polars`, `matplotlib`, `numpy`) 추가 및 미사용 패키지(`google-cloud-aiplatform`) 정리, (4) `trade_db.py`, `ui/history_tab.py`, `ui/universe_tab.py` 내 레거시 `print` 및 `traceback.format_exc()`를 `logger` 표준 호출로 전면 전환. 백업: `archive/backup_20260913_182003/`. 검증: pytest 87/87 통과 (100%), 스모크 테스트 통과. |
| 2026-09-13 (3차) | Phase 2 UI 반응성 및 성능 최적화: (1) `UniverseTab` 및 `TradingHistoryTab`의 Gemini AI 호출(자연어 필터 파싱, 포트폴리오 진단)을 메인 스레드 동기 호출에서 전용 비동기 백그라운드 스레드(`GeminiFilterThread`, `GeminiDiagnosisThread`)로 전면 이관하여 UI 프리징 완전 해결, (2) `AutoTradingTab`의 대규모 순위/후보 테이블 렌더링 시 `setUpdatesEnabled(False/True)` 배치 업데이트 패턴 적용으로 불필요한 재페인팅 억제, (3) `data/market.py`의 `KR3YT` 40페이지 순차 HTML 스크래핑을 병렬 캐시 기반인 `_get_kr3y_df()`로 일원화하여 불필요한 네트워크 지연 제거, (4) `data/collectors/kis.py`에 국내 정규장 거래시간 가드(`is_krx_market_open()`)를 추가하여 장 마감(야간/주말) 시 6초 웹소켓 대기를 건너뛰고 병렬 REST fallback으로 즉각 처리, (5) `tests/test_phase2.py` 신설. 백업: `archive/backup_20260913_183032/`. 검증: pytest 101/101 통과 (100%), 스모크 테스트 통과. |
| 2026-09-17 | 전체 소스 코드 분석(`src/` 13,429줄 정독, pytest 101/101, AST 미사용 import 스캔) 결과를 섹션 6으로 신설: 동작 버그 5건(2-2 캐시 통계 로그가 핸들러 레벨 때문에 실제로는 미기록, Trading History 실시간 타이머 이중화로 Auto Update 해제 무효, UI 스레드 동기 네트워크 호출 2곳, 상태 파일 cwd 상대경로, 거래 추가 시 orig_key 생성 중복), 기술 부채 6건(`data/`→`data_fetcher` 파사드 역참조 21곳, 죽은 코드, 미사용 import, 의존성 불일치, 중복 패턴, 소소한 항목), 문서/테스트 2건(CLAUDE.md·AGENTS.md 현행성, 테스트 공백). 헤더 기준일·핵심 파일 수치를 현행(`src/` 구조, 313줄 main.py) 기준으로 갱신, 현황 요약에 Auto Trading 탭 추가, 2-2에 회귀 주석, 우선순위 매트릭스에 6-x 행 5개 추가. 코드 변경 없음. |
| 2026-09-17 (2차) | 섹션 6 전 항목 구현(6-4 순서). 동작 버그 5건 수정(로깅 레벨, History 탭 타이머 이중화 제거, 예수금 조회·티커 편집 종목명 조회 스레드화, `src/paths.py` 경로 통일, 거래 추가 키 생성 중복 제거), `data/`→`data_fetcher` 역참조 21곳 제거 및 테스트 patch 대상 교체, 스텁 3개·미사용 import/변수 37건 정리, `ruff.toml`·`requirements-dev.txt` 신설, requirements 핀, 공용 헬퍼 5종 추출(`FONT_FAMILY_CSS`, `retire_thread`, `is_kr_code`, `start_date()`, `_parse_marcap_krw`), `fetch_stock_ma_multi` 분기 통합, QSettings 스코프 정정(자동 이전), 리스팅 캐시 일 단위 만료, 테스트 18개 추가. CLAUDE.md/AGENTS.md 전면 재작성. 검증: compileall, ruff 0건, pytest 119/119, offscreen import 스모크. 상세는 6-5. |
| 2026-09-17 (3차) | `trading.md` 11-5 실행: 전략 코드를 `src/data/`에서 `src/strategy/`로 분리. 사용자가 이동한 `strategy/rebalance/`의 절대 import 갱신, `data/backtest.py` → `strategy/ma_cross.py`, `strategy/__init__.py`·`strategy/trend_following/__init__.py`(스캐폴드) 신설, `data/__init__.py`·`data_fetcher.py`에서 전략 재노출 제거(데이터 계층이 전략 계층을 import하지 않음), 호출자 2곳이 `strategy.rebalance` 직접 import, 테스트를 `tests/strategy/`로 이동하고 `tests/conftest.py`로 sys.path 설정 일원화. CLAUDE.md/AGENTS.md/trading.md/trend_following.md 경로 갱신. 검증: pytest 119/119 (루트·src 양쪽에서), ruff 0건. |
| 2026-09-17 (4차) | 리밸런싱 전략 스펙을 `trading.md`에서 `src/strategy/rebalance/rebalance.md`로 분리(사용자 요청): 1~9장·11장·13장 이동, 10장(수동 매매 베이스라인)·12장(멀티 에이전트 개발 방법론)은 `trading.md`에 잔류, 양쪽 모두 절 번호 유지. 코드 주석·UI 문자열(`ui/auto_trading_tab.py`, `ui/dialogs.py` 등)과 CLAUDE.md/AGENTS.md/trend_following.md의 `trading.md N-x` 인용을 `rebalance.md N-x`로 갱신. 코드 로직 변경 없음. |
| 2026-09-17 (5차) | `trading.md` 삭제(사용자 판단: 별도 파일 불필요). 남아 있던 10장(베이스라인)·12장(멀티 에이전트 개발 방법론)을 `src/strategy/rebalance/rebalance.md`에 합쳐 원문 1~13장 구성을 새 위치에서 그대로 유지. CLAUDE.md/AGENTS.md의 스펙 위치 안내 갱신. |
| 2026-09-17 (6차) | 전략별 폴더+md 규칙 적용 마무리: `strategy/ma_cross.py` → `strategy/ma_cross/`(`__init__.py` 파사드 + `backtest.py`) + 코드 분석 기반 스펙 `ma_cross.md` 신규 작성, 루트 `trend_following.md`를 `src/strategy/trend_following/trend_following.md`로 이동·통합(폴더의 짧은 안내문 흡수). CLAUDE.md/AGENTS.md 갱신. 검증: pytest 119/119, ruff 0건. |
| 2026-09-17 (7차) | 구조 검토 후속 정리: `tests/strategy/test_ma_cross.py` → `tests/strategy/ma_cross/test_backtest.py`(전략별 테스트 폴더 규칙), `data/__init__.py`의 재노출 목록을 `data_fetcher.py` 한 곳으로 통합(중복 제거), 테스트 9개 파일의 `_PROJ_ROOT` sys.path 보일러플레이트 삭제(`conftest.py`로 일원화), `test_plan.md`·`changelog_optimization.md` → `docs/history/`(날짜 접미사), `src/register_secret.py` → `tools/`, 빈 `paperclipai/` 삭제, `.agents/rules/optimize-code.md`를 CLAUDE.md와 동기화(archive 백업 규칙 제거, 경로 갱신), `.vscode/settings.json` extraPaths에 `./src` 추가. 검증: pytest 119/119, ruff 0건. |
| 2026-09-17 (8차) | 구조 검토 잔여 2건 처리. (1) `data/` 내부 지연 import 순환 해소: `frames.py`(`_to_polars`), `listing.py`(종목 리스팅 캐시), `history.py`(`get_historical_data`/`_fetch_historical_uncached`), `fx.py`(USD/KRW) 신설, `_YF_BULK_CACHE`를 `cache.py`로 이동, `indicators.py`·`collectors/yahoo.py`가 하위 모듈을 모듈 수준에서 import — AST 기반 그래프 검사로 순환 0건(지연 import 포함) 확인, `market.py`는 542줄로 축소(기존 이름 재노출 유지). (2) `ui/dialogs.py`(1,926줄) → `ui/dialogs/` 패키지 6개 모듈 + `__init__` 재노출; `ui/history_tab.py` 1,901→1,442줄: 순수 계산 `ui/history_calc.py`, 표 렌더링 `ui/history_table.py`, 보유 요약·AI 진단 팝업 `ui/dialogs/holdings_summary.py`·`ai_diagnosis.py`로 추출(클래스에 위임 메서드 유지). 검증: pytest 119/119, ruff 0건, offscreen 표 렌더링 스모크. |
| 2026-09-19 | 1장(현황 요약)에 상시 갱신용 "소스 코드 구조" 섹션 신설 — 3-1 "현재 파일 구조"는 Phase 0~5 당시의 역사적 스냅샷으로 남겨두고, 지금 시점의 실제 트리(`data/`·`strategy/`·`ui/dialogs/`·`tests/strategy/` 등, 전략별 `<이름>.md` 스펙 위치 포함)를 반영하는 곳을 이 섹션 하나로 일원화. 앞으로 구조가 바뀔 때마다 이 섹션을 갱신하는 것을 관례로 명시. 코드 변경 없음. |
| 2026-09-17 (9차) | 추세추종 전략 구현(`src/strategy/trend_following/`, 스펙 `trend_following.md` 3~4장): `TrendFollowingConfig`, no-lookahead `donchian_signal`(직전 n일 채널, 종가 돌파, 롱온리 단일 포지션), `run_backtest`(position.shift(1) 방식, 총수익·CAGR·변동성·Sharpe·MDD·트레이드/승률/노출·리스크 게이트, 편도 비용 파라미터), `run_backtest_for_ticker`. 테스트 18개 추가(look-ahead 없음 검증 포함) → 137/137. `tests/` 하위에 `__init__.py` 추가(전략별 폴더의 동일 파일명 충돌 해결). 실데이터 예비 백테스트(6종목×3조합, 2021~2026)를 스펙 5장에 기록 — 리스크 게이트 통과 조합 없음, v2 방향(손절·사이징·레짐 필터) 도출. UI 연결 없음. |
| 2026-09-17 (10차) | 추세추종 전략 UI 연결: 메인 윈도우 6번째 탭 "Trend Following"(`ui/trend_following_tab.py`) — 티커/Universe 콤보/시작일/entry_n·exit_n/편도 비용 입력, `TrendFollowingBacktestThread`(신규)로 백그라운드 실행, 지표 요약 행(리스크 게이트 색상)과 트레이드 표, `ui/dialogs/trend_following_chart.py`(종가+채널+진입/청산 마커, 전략 vs 매수&보유 에쿼티). `closeEvent` 스레드 정리에 포함. 헤드리스 UI 테스트 7개 추가 → 144/144. 주문 실행 없음. CLAUDE.md/AGENTS.md 탭 5개로 갱신. |
| 2026-09-17 (11차) | (다른 에이전트 작업, 커밋 `db862dd`에 함께 포함) `data/collectors/naver.py`에 `_KR3Y_API_DISABLED = True` 추가 — 네이버 `interestDailyQuote.naver` 금리 페이지가 모든 코드에 HTTP 410 Gone을 반환해 KR 3년물 금리 조회를 비활성화. `_get_kr3y_df()`는 캐시(없으면 None)만 반환하며 호출자(`data/history.py`, `data/market.py`, `_load_ohlcv_window`)는 None/빈 프레임을 처리하므로 KR3YT 행이 Universe에서 빠지는 것 외 영향 없음. 확인: pytest 145/145. |
| 2026-09-17 (12차) | 추세추종 v2 구현(`strategy/trend_following`, 스펙 3장 v2): 레짐 MA 필터(`regime_ma_n`), ATR 손절(`stop_atr_mult`, trailing/fixed, t−1 종가 시점 확정으로 no-lookahead 유지), 변동성 타깃 사이징(`vol_target_pct`/`vol_n`/`max_weight`, 트레이드 기간 고정 비중). `backtest.py`가 weight 기반 수익·비용 계산, 트레이드에 청산 사유·비중·가격수익률, 요약에 `avg_weight`/exit 카운트/`v2` 추가. 기본값은 모두 off라 v1 결과 불변. UI 탭 3번째 입력 행·트레이드 표 컬럼·차트(레짐 MA, 손절선) 확장. 테스트 15개 추가 → 160/160. 실데이터 8변형 비교를 스펙 5장에 기록: MDD 평균 27→12.5%로 감소하나 Sharpe 0.7대 정체, 게이트 미통과 → 다음 단계는 다종목 분산. |
| 2026-09-17 (13차) | 추세추종 v3 + 검증: `strategy/trend_following/portfolio.py`(종목별 v1/v2 백테스트를 날짜 정렬 후 균등 슬리브 constant mix로 합성, 총노출·포지션 수·종목별 요약), `validation.py`(홀드아웃 + 앵커드 연 단위 워크포워드, 그리드 최적 선택 목적함수 Sharpe/MDD 상한, IS→OOS 순위상관), `backtest.return_metrics()` 공용화. 테스트 15개 추가 → 175/175. 14종목 실데이터: 포트폴리오 v1 20/10 Sharpe 1.09·MDD 13.6%(단일 종목 평균 0.5·27%), 홀드아웃 OOS Sharpe 1.42·MDD 14.0%(순위상관 0.75), 워크포워드 OOS 2019~2026 Sharpe 1.25·MDD 14.0% — MDD 조건 통과, Sharpe 미달(2022 약세장). 매 폴드 100/50 선택으로 파라미터 안정. 스펙 3·4·5·6장 갱신. UI 연결은 미착수. |
| 2026-09-17 (14차) | 추세추종 v3·검증 UI 연결: Trend Following 탭에 포트폴리오 티커 행("Use Universe" 시총 상위 N 채움), "Run Portfolio", "Validate (IS/OOS)"(최근 N년 연 단위 워크포워드 + 홀드아웃, 기본 그리드 12개) 추가. `TrendFollowingPortfolioThread`(이력 조회 진행 표시 포함), `ui/dialogs/trend_following_portfolio.py`의 결과 다이얼로그 2종(에쿼티+총노출 차트·종목별 표 / 폴드 표·OOS 에쿼티·홀드아웃 그리드). 헤드리스 테스트 3개 추가 → 178/178, 실데이터 6종목으로 두 버튼 end-to-end 구동 확인. |
| 2026-09-18 | `ui/common.retire_thread()`의 와일드카드 `thread.disconnect()`를 `blockSignals(True)`로 교체 — 동작은 동일(늦게 도착한 결과가 이미 넘어간 위젯을 건드리지 못하게 차단)하되 `QObject::disconnect: wildcard call disconnects from destroyed signal` 경고가 더는 뜨지 않음. 이어서 외부 AI가 작성한 코드 리뷰(`review.md`, 검증 후 사용자가 삭제)를 실제 코드와 대조 검증(`review_claude.md`, 마찬가지로 삭제) — 대부분 정확, 1-1 메서드명 오탈자(`_save()`→실제는 `on_save()`)와 상단 파일/줄 수 통계 오차 발견. 검증된 항목 3건 수정: (1) **4-1** `strategy/ma_cross/backtest.py`의 `cumulative_return`을 트레이드 수익률 단순 합에서 `growth_factor` 기반 복리 누적으로 변경(`rebalance`/`trend_following`과 계산 방식 통일), `ma_cross.md` 갱신 및 테스트 교체, 랜덤 가격 경로 스크립트로 단순합과 결과가 달라짐을 확인; 같은 문서의 4-3(stale 테스트 경로 `tests/strategy/test_ma_cross.py`→`tests/strategy/ma_cross/test_backtest.py`)도 함께 수정. (2) **1-3** `main.py::closeEvent`에서 MainWindow에 실존하지 않던 죽은 `_backtest_thread` 참조를 실제로 시작되지만 정리 목록에 빠져 있던 `_auto_backup_thread`로 교체. (3) **5-2** `.env.example` 신설(`KRX_AUTH_KEY`/`GOOGLE_API_KEY`/`GEMINI_MODEL`/`KIS_KEY_PATH`). 검증: pytest 178/178, ruff 0건. |
| 2026-09-18 (2차) | **1-1** `TradeEntryDialog` 저장 시 UI 스레드 동기 네트워크 호출(3개 소스 순차 조회로 5~15초 응답 없음) 제거. `threads/fetch_threads.py`에 `TickerValidateThread` 신설 — `on_save()`가 하던 `fetch_single_stock` → yahooquery → Naver(`_fetch_naver_info`) 3단 폴백 조회를 그대로 백그라운드로 이관. `TradeEntryDialog.on_save()`는 로컬 필드 검증만 동기로 남기고, 조회는 스레드를 시작한 뒤 `_on_ticker_validated()`에서 결과를 받아 `result_data` 구성·`accept()`. 검증 중에는 Save/Cancel 버튼을 비활성화하고 `closeEvent()`가 창닫기 요청을 무시해, 스레드가 살아있는 동안 다이얼로그 객체가 파괴되는 경합을 차단. 오프스크린 스모크 스크립트(성공/실패 티커 양쪽 시나리오, `fetch_single_stock`/`_fetch_naver_info` mock)로 버튼 비활성화·닫기 차단·비동기 완료 후 accept 동작을 확인(파일은 스크래치패드에 두고 커밋하지 않음). 검증: pytest 178/178, ruff 0건. |
| 2026-09-18 (3차) | review.md 저위험 항목 일괄 처리. **1-2** `TradingRecordTab.__init__`이 매 레코드마다 `get_usd_krw_rate_for_date`/`get_index_close_for_date`를 동기 호출(첫 세션 진입 시 FX·KOSPI 히스토리 최초 네트워크 fetch 1회씩 발생)하던 것을 제거 — `threads/fetch_threads.py`에 `AssetMetricsPreloadThread` 신설(같은 fetch 함수를 백그라운드에서 호출해 `data/fx.py`·`get_historical_data`의 세션 캐시만 데운다), `_refresh_table_impl()`은 `self._metrics_ready`가 켜지기 전까지 KOSPI/USD 열을 "-"로 렌더링하고 `_on_metrics_preloaded()`에서 실값으로 재렌더링. `TradingRecordTab.collect_threads_to_stop()` 신설 + `main.py::closeEvent`에 연결(이 탭은 이전까지 스레드 정리 목록에 아예 없었음). 오프스크린 스모크로 초기화 시점엔 두 fetch 함수가 호출되지 않고("-" 플레이스홀더 렌더) 프리로드 완료 후 실값으로 갱신됨을 확인. **3-2** `naver.fetch_naver_realtime_prices`의 `_fetch_chunk`가 워커 스레드에서 공유 `results` dict에 직접 쓰던 것을 청크별 dict를 반환하고 메인 스레드에서 `results.update()`로 병합하는 순수 함수형 패턴으로 변경(락 없는 동시 쓰기 제거). 랜덤 30케이스(티커 1~260개, 여러 청크에 걸침)로 병합 결과가 기대값과 정확히 일치함을 스크립트로 확인. **3-3** `data/collectors/kis.py`에 `_kis_oauth_post()` 신설 — 토큰/approval_key 발급 POST를 `naver._fast_kr_history`와 동일한 3회 재시도(1초 간격) 패턴으로 감싸 일시적 네트워크 오류에 대응. KIS가 실제로 rate-limit/키 오류를 HTTP 200 + 에러 바디로 반환하는 경우는 재시도 대상에서 제외(즉시 재시도해봐야 실패 반복 — 기존 `_get_kis_token()`/`_get_kis_approval_key()`의 `ValueError` 처리 그대로 유지). 스크립트로 (1) 2회 실패 후 3회차 성공 시 정상 복구, (2) 항상 실패 시 3회 만에 예외 전파(무한 재시도 아님), (3) `_get_kis_token()` end-to-end 복구를 확인. 검증: pytest 178/178, ruff 0건. |
| 2026-09-18 (4차) | **2-3** Auto Trading 탭 후보/순위 테이블 더블클릭 연계. Buy/Sell 후보 테이블·Full Ranking 테이블에 `cellDoubleClicked`를 연결해 해당 종목의 `StockMaDialog`(20/60일 이평 차트)를 `UniverseTab.show_stock_ma`와 동일한 패턴(`StockMaThread` 비동기 조회 → 완료 시 non-modal 다이얼로그, `_open_dialogs`로 여러 개 동시 유지)으로 띄우도록 구현. 테이블마다 컬럼 배치가 달라(후보 표는 Ticker가 1번 컬럼, 순위 표는 2번 컬럼) 별도 핸들러로 분리. 구현 중 `self.sender()`로 어느 테이블에서 눌렸는지 판별하는 첫 버전이 오프스크린 테스트에서 실패(직접 호출 시 `sender()`가 `None`이라 항상 Sell 표로 오판)해, 각 테이블의 `cellDoubleClicked`를 `lambda row, col: self._on_candidate_double_clicked(self._buy_table, row, col)` 식으로 테이블 자신을 명시적으로 넘기는 방식으로 교체(테스트 가능성도 함께 개선). `AutoTradingTab.collect_threads_to_stop()`에 `_stock_ma_threads` 추가. 오프스크린 스모크로 후보 표·순위 표 양쪽 더블클릭이 올바른 ticker/market으로 차트를 띄움을 확인. 검증: pytest 178/178, ruff 0건. |
| 2026-09-18 (5차) | **2-2** Trading History·Total Assets 탭에 Excel(.xlsx)/CSV Export 버튼 추가. 두 탭 모두 현재 화면에 렌더링된 `QTableWidget`을 그대로 읽어(`item(r,c).text()`) 내보내므로 필터·정렬 상태가 그대로 반영됨(별도 export 전용 데이터 경로를 새로 만들지 않음). `TradingHistoryTab`은 `_COLS`/`_SECTIONS`(기존 컬럼 정의)로부터 헤더를 동적으로 구성해 Buy/Sell 구간에서 중복되는 "Date/Price/Q'ty/Amount/Days/P&L/P&L%" 라벨에 섹션명을 접두(`Buy Date`, `Sell Date` 등)해 모호함 제거 — 헤더 목록을 손으로 중복 작성하지 않아 `_COLS` 변경 시 자동으로 따라감. `TradingRecordTab`은 15개 컬럼이 고정이라 `_EXPORT_HEADERS` 명시적 리스트 사용. xlsx는 `openpyxl`(이미 의존성에 있음), csv는 표준 `csv` 모듈, 저장 경로는 `QFileDialog.getSaveFileName`(파일 필터로 형식 선택, 확장자 자동 보정). 빈 테이블일 때는 안내 메시지만 띄우고 저장하지 않음. 오프스크린 스모크로 두 탭×두 포맷(xlsx/csv) 모두 헤더·데이터 내용이 화면 표시값과 일치함을 `openpyxl`/`csv` 재파싱으로 확인, 빈 테이블 가드도 확인. 검증: pytest 178/178, ruff 0건. |
| 2026-09-18 (6차) | **4-2** (범위 축소: 팩터 가중치 커스터마이징은 보류, Sharpe/변동성 지표만) `strategy/rebalance/backtest.py`에 `_sharpe_and_vol()` 신설 — 주간 에쿼티 커브(리밸런싱 금요일마다 1포인트)의 기간별 수익률로 연 52기간 기준 Sharpe·연환산 변동성 계산, 무위험수익률은 0% 가정(`RebalanceConfig`에 아직 필드 없음 — `trend_following.return_metrics()`와 같은 방식이나 연환산 기준은 주간 vs 일별로 다름). `_summarize_backtest()` 반환 dict에 `sharpe`/`annual_vol_pct` 추가(빈 커브일 때도 0.0으로 채워 키 누락 없음), `BacktestResultDialog` 헤더에 표시. `rebalance.md` 4-3에 산출 지표·연환산 방식 기록. 단위테스트 5개 추가(`tests/strategy/rebalance/test_walkforward.py`: 빈/단일 포인트 0 처리, 분산 0인 flat 커브에서 0-division 아닌 0 반환, 직접 계산한 numpy 기준값과 일치, `_summarize_backtest` 배선 확인) → 183/183. 랜덤 20케이스(2~260포인트) 스크립트로도 수동 계산과 정확히 일치함을 추가 확인. 검증: pytest 183/183, ruff 0건. |
| 2026-09-18 (7차) | **2-1** Trading Universe `StockTable`에 우클릭 컨텍스트 메뉴 "🤖 AI Stock Report" 추가. 원안(리포트+MA차트+거래추가 3개 메뉴)에서 이번 요청 범위인 AI 리포트 1건으로 축소. `gemini_helper.stock_report_summary(item)` 신설 — 새 OHLCV 히스토리를 다시 받지 않고 `StockTable`이 이미 화면에 갖고 있는 지표(현재가, PER, MA20/50 이격도, 52주 고/저 대비, 3D~120D 수익률)만으로 3줄 브리핑(추세/위치, 밸류에이션, 참고 지지·저항)을 Gemini에 요청 — 클릭당 추가 네트워크 호출 없이 즉시 응답. `threads/fetch_threads.py`에 `GeminiStockReportThread`(기존 `GeminiDiagnosisThread`와 동일 패턴), `ui/dialogs/stock_report.py`에 `show_stock_report_result()`(`ai_diagnosis.py`와 동일 구조, `ui.dialogs.__init__`에는 미등록 — 두 모듈 모두 클래스가 아닌 헬퍼 함수라 기존 관례를 따름). `StockTable.contextMenuEvent()`는 클릭된 행의 티커를 `load_data()`에 넘긴 리스트 인덱스가 아니라 화면에 실제 렌더링된 셀 텍스트에서 직접 읽어(`item(row,3).text()`) 정렬 후에도 안전하도록 구현(`setSortingEnabled(True)`라 시각적 행 순서가 로드 순서와 달라질 수 있음). `UniverseTab._on_ai_report_requested`가 `all_data`에서 티커로 전체 dict를 찾아 스레드에 전달, `collect_threads_to_stop()`에도 추가. 오프스크린 스모크로 성공/미지원 티커/API 실패 3개 경로 확인(실제 QMenu 팝업 클릭은 headless에서 검증 불가라 시그널 직접 emit으로 대체). CLAUDE.md/AGENTS.md 다이얼로그 모듈 목록에 `stock_report` 추가. 검증: pytest 183/183, ruff 0건. |
| 2026-09-19 (2차) | 구조 재점검 후속 조치 1~2단계. (1) US 마켓 판별 튜플 4곳(`PositionPriceFetchThread` 3곳은 `"S&P500"` 누락, `history_tab._refresh_summary`는 포함)을 `data.cache.is_us_market()` 하나로 통합 — `S&P500` 포지션이 가격 조회에서는 KR로, 요약에서는 US로 분류되던 불일치 해소. (2) `_refresh_summary`의 KR/US/총자산 집계·`curr_days`·비중 계산을 `ui.history_calc.summarize_positions()`(순수 함수)로 분리하고 테스트 5건 추가(총자산 계산은 이전까지 테스트 0건); `_build_ui`에서 항상 만들어지는 위젯에 대한 `hasattr` 방어 코드 제거. (3) 테스트 공백 보강: `data.listing._singleflight_cache`(일 단위 만료·LRU·동시 호출 dedup), `data.frames._to_polars`(문자열 Date 컬럼도 캐스팅하도록 보강), `ui.history_table` 셀 팩토리·`fill_table_rows` 컬럼 배치. `test_phase2.py`를 `test_kis_realtime`/`test_history_bonds`/`test_gemini_threads`/`test_auto_trading_tab`으로 분리, trend_following 테스트 5개 파일의 `_frame` 복사본을 `tests/strategy/trend_following/frames.py`로 통합. pytest 184→214. |
| 2026-09-19 (3차) | 구조 재점검 후속 조치 3단계(거대 메서드 분할, 동작 불변 검증). (1) `ui/dialogs/stock_ma.py` `StockMaDialog.__init__`(약 890줄, 중첩 함수 16개)을 우측 패널 테이블·축별 플롯·hover·팬/줌/스크롤바·토글 4종 등 25개 메서드로 재구성, 미사용 `diff_fmt` 제거. 오프스크린 특성화 스크립트로 9가지 입력(KR/US 종목, 지수, WTI 선물 커브, VIX, 채권 bp, 데이터 없음, MA 컬럼 누락)에 대해 축·선·범례·테이블·토글 시퀀스·휠 줌·드래그 팬·스크롤바·Y줌 스냅샷을 전후 비교 → 차이 0. (2) `history_tab._build_ui`(380줄)를 `_build_position_card`/`_build_metrics_card`/`_build_metrics_grid`/`_build_controls_row`/`_build_history_table`과 모듈 수준 위젯 팩토리·스타일 상수로 분할, `_SectionTable`을 `ui.history_table.SectionTable`(+`SECTIONS`)로 이동. 레이아웃 트리·위젯 속성·시그널 수신자 수 덤프 전후 비교 → 클래스명 변경 외 차이 0. |
| 2026-09-19 (4차) | 구조 재점검 후속 조치 4단계(정리). (1) `ui.common.ThreadOwnerMixin` 신설 — 탭마다 손으로 나열하던 `collect_threads_to_stop()`을 `_track_thread()` 등록 기반으로 통일. 기존 목록에서 빠져 있던 Universe 탭의 경량 갱신·AI 필터 스레드, History 탭의 AI 진단 스레드가 종료 시 정리 대상에 포함됨. (2) `QThread.finished`에 람다를 연결하던 4곳(Universe 추가/시작 시 재조회, Universe·Auto Trading MA 차트, History 티커 편집 종목명 조회) 제거 — `SingleStockFetchThread.finished`가 `ticker`, `StockMaThread.finished`가 `market`/`change_mode`를 함께 emit하도록 확장해 슬롯을 바운드 메서드로 전환(CLAUDE.md 규칙 준수). 티커 편집 후 종목명 반영은 해당 티커를 가진 모든 레코드에 적용. (3) `data_fetcher.py` 파사드를 실제 외부 사용 이름 약 30개로 축소(79→30; 내부 캐시 이름 대부분 제거). (4) 잔여 `hasattr(self, …)` 방어 코드 제거, `AGENTS.md`를 `CLAUDE.md` 포인터로 교체(내용 중복·드리프트 방지). 검토 후 유지로 결정한 항목: `MainWindow`의 네이티브 상태바(일시 메시지)와 하단 `status_label`(지속 상태)은 역할이 다름, `main.py`의 `os._exit()`는 `terminate()`된 QThread 안의 ThreadPoolExecutor 워커가 종료를 막는 경우의 안전장치. 미결: 저장소에 추적 중이던 옵시디언 볼트(`Portfolio Management/`)가 작업 트리에서 삭제되고 `obsidian/`으로 옮겨진 상태 — 추적 여부는 사용자 결정 필요. pytest 214→222. |
| 2026-09-19 (5차) | 섹션 7(UI 개선 항목) 신설 — 코드 기반 UI 리뷰(computer-use 스크린샷 미승인으로 이번 1차는 코드만 정독) 결과를 정리: (1) 최상위 탭을 Universe/History/Assets/Strategy 4개로 재편하고 Auto Trading·Trend Following(추후 MA Cross)을 `StrategyTab` 내부 서브탭으로 이동, 서브탭 위에 전체 전략 통합 "오늘의 신호" 요약 바 상시 노출(사용자 결정: 서브탭별 개별 노출이 아닌 상단 통합 방식), (2) `ui/auto_trading_tab.py`·`ui/trend_following_tab.py`의 액션 버튼 색상(`#8e44ad`/`#1a5276`/`#6c3483` 등 하드코딩 혼재, "Chart"/"Use Universe"는 무스타일)을 `ui/common.py` 역할별 상수로 중앙화, (3) Trend Following 탭의 4행 컨트롤(v2 오버레이·v3 포트폴리오/검증)을 접이식 그룹으로 감싸 Auto Trading 수준의 밀도로 축소. 설계만 확정, 구현은 로컬 CLI 세션에서 착수 예정. 코드 변경 없음. |
| 2026-09-19 (6차) | 사용자 요청으로 Trading Universe 탭의 AI Filter(자연어 조건 → 필터) 기능 전면 삭제: `ui/universe_tab.py`의 "🤖 AI Filter" 버튼·`_show_ai_filter_dialog`·`_on_ai_filter_finished`·관련 상태 필드 제거, `ui/widgets.py`의 `StockTable._numeric_conditions`·`set_ai_conditions`·`clear_ai_filter`(및 `apply_col_filters`의 조건 매칭 블록·`_ops` 딕셔너리) 제거, `threads/fetch_threads.py`의 `GeminiFilterThread` 삭제, `gemini_helper.py`의 `nl_to_filter`·`_COLUMN_METADATA`·`_NL_FILTER_SCHEMA` 삭제, `tests/test_gemini_threads.py`에서 관련 테스트 2건 제거. AI Stock Report/AI 포트폴리오 진단 등 다른 Gemini 기능은 그대로 유지. 검증: pytest 222/222, ruff 0건. |
| 2026-09-19 (7차) | 섹션 7(UI 개선 항목) 1~3 전체 구현. **7-1**: `ui/strategy_tab.py::StrategyTab` 신설(최상위 탭 5→4개, `Auto Trading`/`Trend Following`을 내부 서브탭으로, `MA Cross`는 안내문 자리만 확보) + "Today's Signals" 요약 바(rebalance는 캐시된 Universe 데이터로 즉시 계산, trend following은 시총 상위 30종목만 `StrategySummaryThread`로 백그라운드 계산 — 전 종목 스캔의 지연 우려는 이번 구현에서 상위 N종목 제한으로 해소), `main.py`/`closeEvent`를 `StrategyTab` 하나로 단순화. **7-2**: `ui/common.py`에 역할별 버튼 색상(`_ACTION_BACKTEST/PORTFOLIO/VALIDATE_COLOR` + `action_button_style()`), 보조 버튼 스타일(`_SECONDARY_BUTTON_STYLE`), 상태색(`_STATUS_SUCCESS/FAIL_COLOR`) 상수화 후 Auto Trading·Trend Following 버튼 전체 교체. **7-3**: `TrendFollowingTab._make_collapsible()`(체크 가능한 토글 버튼 + `QWidget.setVisible()`)으로 v2 오버레이·v3 포트폴리오/검증 행을 기본 접힘 처리 — `QGroupBox`는 체크 해제해도 프레임이 차지하는 공간이 그대로라 선택하지 않음. 오프스크린 스모크 스크립트(`StrategyTab` 구조·버튼 스타일·기본 접힘 상태·요약 바 텍스트를 rebalance/trend-following 함수 모킹으로 확인)로 검증. CLAUDE.md 탭 구성·`src/ui/`·`src/threads/` 설명 갱신. 검증: pytest 220/220(main.py 탭 구조 변경에 직접 의존하는 테스트 없음 — 오프스크린 스모크로 별도 확인), ruff 0건. |
| 2026-09-19 (8차) | 사용자 요청으로 Trading History 탭의 AI Diagnosis(포트폴리오 진단)·Export(xlsx/csv 내보내기) 기능 전면 삭제: `ui/history_tab.py`의 "🤖 AI Diagnosis"/"📥 Export" 버튼, `_show_ai_diagnosis`/`_on_ai_diagnosis_finished`/`_cancel_ai_diagnosis`/`_display_ai_diagnosis_result`/`_export_headers`/`_on_export_clicked`와 관련 상태 필드(`_ai_diagnosis_thread`, `_ai_diagnosis_loading_dlg`), 이제 쓰이지 않는 `_BTN_DEEP_BLUE`/`_SECTIONS`(export 전용)·`csv`/`QFileDialog` import 제거. `ui/dialogs/ai_diagnosis.py` 삭제(다른 호출자 없음 확인), `threads/fetch_threads.py`의 `GeminiDiagnosisThread` 삭제, `gemini_helper.py`의 `portfolio_diagnosis` 삭제(다른 호출자 없음), 전용 테스트만 있던 `tests/test_gemini_threads.py` 삭제. Total Assets 탭(`ui/assets_tab.py`)의 Export 버튼은 이번 요청 범위 밖이라 유지(`openpyxl` 의존성도 그대로). AI Stock Report 등 다른 Gemini 기능은 영향 없음. CLAUDE.md의 `dialogs/` 모듈 목록·`gemini_helper.py` 설명 갱신. 오프스크린 스모크로 버튼 목록에 AI Diagnosis/Export가 더는 없음을 확인. 검증: pytest 219/219, ruff 0건. |
| 2026-09-19 (9차) | 7-4 신설(7-2 후속, 미착수): 사용자가 제공한 Total Assets/Trading History 탭 스크린샷을 `ui/assets_tab.py`·`ui/history_tab.py`와 대조해 7-2에서 이름 붙인 `_ACTION_BACKTEST/PORTFOLIO/VALIDATE_COLOR`가 다른 탭의 무관한 버튼(Assets "Graph", History "Summary"/"Sort by Date")에 같은 hex로 재사용되고 있음을 발견 — 이름 규칙과 실제 사용이 충돌. 그 외 상태 전용으로 못박은 초록이 액션 버튼("This Week"/"Reload")에 남아있는 점, "Add Record"(파랑)와 "Add Trade"(주황)의 색 불일치, `_SECONDARY_BUTTON_STYLE`(`#888`)과 기존 Export 회색(`#6c757d`)의 이중화도 함께 정리. 신규 상수 1개(`_ACTION_INSIGHT_COLOR`, 삭제된 AI Diagnosis 색 `#0a3d62` 재활용)만 추가하고 나머지는 기존 값으로 재배치하는 안을 표로 기록. 코드 변경 없음 — 구현은 로컬 CLI 세션에서 착수 예정. |
| 2026-09-19 (10차) | 7-4에서 표로 확정한 재배치를 그대로 구현. `ui/common.py`에 `_ACTION_INSIGHT_COLOR`/`_ACTION_INSIGHT_HOVER_COLOR` 신설 + `_SECONDARY_BUTTON_STYLE`을 `#888`→`#6c757d`로 통일. `ui/assets_tab.py`: Add Record 파랑→주황, This Week 초록→회색, Graph 보라→`_ACTION_INSIGHT_COLOR`. `ui/history_tab.py`: Reload 초록→파랑(Fetch와 동일 역할), Summary 보라→신규 `_BTN_INSIGHT`, Sort by Date 네이비(`_ACTION_PORTFOLIO_COLOR`와 충돌)→신규 체크 가능한 회색 `_BTN_GREY_CHECKABLE`(선택 상태는 `#495057` + 테두리로 구분); 이제 안 쓰는 `_BTN_GREEN`/`_BTN_PURPLE`/`_BTN_NAVY` 제거. Delete Selected·Export·검색 아이콘·Trend Following Chart/Use Universe는 표 그대로라 변경 없음. 오프스크린 스모크로 버튼 10개의 `styleSheet()` hex를 표와 대조 확인. 검증: pytest 219/219, ruff 0건. |
| 2026-09-19 (11차) | 사용자 요청으로 Trading History 표의 매매 이력 정렬 순서를 뒤집음 — 최근에 매입한 종목이 맨 위에 오도록 수정(이전에는 오래된 순으로 위에서 아래, `scrollToBottom()`으로 최근 항목을 자동 스크롤해 보여주던 방식). `ui/history_tab.py::_apply_filter()`: 기본 모드(닫힌 거래 다음 열린 거래 그룹핑은 그대로 유지)와 "Sort by Date" 모드(닫힌/열린 통합 월별 그룹) 양쪽 모두 `buy_date` 정렬을 `reverse=True`로 전환. `_fill_table()`의 `scrollToBottom()`을 `scrollToTop()`으로 교체(최근 항목이 이제 상단에 있으므로). `ui/history_calc.py::build_monthly_rows()`의 "ascending 가정" 독스트링을 정렬 방향에 무관하다는 설명으로 정정(월별 그룹핑은 첫 등장 순서 기준이라 방향과 무관하게 안전 — 코드 변경 없음). 오프스크린 스모크로 두 모드 모두 최근 매입일이 맨 위에 옴을 확인. 검증: pytest 219/219, ruff 0건. |
| 2026-09-19 (12차) | 사용자 요청으로 Trading Universe 탭의 "Market:" 콤보 위에 있던 "Trading Universe" 타이틀 라벨 삭제(`ui/universe_tab.py::_build_ui`). 검증: pytest 219/219, ruff 0건. |
| 2026-09-19 (13차) | `docs/ui.md`(디자인 핸드오프 4개 목업 `*.dc.html`의 진단·개선안·전역 규칙 집약본, CLAUDE.md에서 UI 변경 시 따를 전역 규칙으로 명시)에 따른 4개 탭 UI 재설계. 공통: `ui/colors.py`(PROFIT=빨강/LOSS=파랑 단일 규칙, `heatmap_bg()` 알파 스케일, Auto Trading 배지용 `ACTION_BUY/SELL`은 가격 방향 색과 충돌하지 않도록 별도 축), `ui/theme.py`(디자인 토큰 + `app_qss()` 전역 QSS, `QPushButton#primary/#danger` objectName 역할, `main.py`의 인라인 스타일시트 대체·Fusion 스타일), `create_numeric_font()`(숫자 셀 등폭 숫자), `NumericItem`(QTableWidgetItem이 EditRole/DisplayRole을 같은 저장소로 취급해 콤마 포맷 숫자 열이 문자열로 정렬되던 버그 수정 — `history_table.py`의 `ni()/pi()`는 정렬 비활성이라 잠복 상태로 미수정). Trading Universe(`widgets.py`·`universe_tab.py`): 22열→13열(`ColSpec` 단일 스펙, Name+Ticker 통합 식별 셀+상태 배지, 52W 단일 레인지 바, 신규 Chg(`cache._TD_PERIODS`에 "1d" 추가), Trend 미니차트; Div(50)·5D·10D 삭제는 사용자 승인), 행별 버튼 3개→우클릭 메뉴/더블클릭/Ctrl+C, 지수·채권 행은 Market Rail 카드로 분리, 툴바 ALL/KOSPI/KOSDAQ. Trading History(`history_table.py`·`history_tab.py`·`history_calc.py`): `COLUMNS` 단일 스펙에서 SECTIONS·라벨·폭 파생, Open/Closed 마커+배지 델리게이트, 월별 요약을 span 그룹 헤더 행으로(더블클릭 편집 잠복 버그 차단), KPI 스트립, All/Open/Closed·1M/3M/YTD/All·30일 규칙 토글. Total Assets(`assets_tab.py`): 읽기 전용 값을 QLineEdit→QLabel, `GroupedHeaderView(sortable=)` 추가로 헤더 클릭 정렬 활성(기본 최신순 고정). Strategy(`strategy_tab.py`·`auto_trading_tab.py`): 한 줄 요약 문자열→시그널 카드 4장+계산 시각·소요 시간, 추세추종 커버리지 배너+검사 범위 콤보(30/60/100/All), MA Cross를 비활성 "Coming soon" 탭으로, Buy/Sell 후보 표 2개+13열 랭킹 표를 단일 랭킹 표(액션 배지·점수 바·7개 요인 히트 셀·Held 마커, All/Buy/Sell/Hold 필터)로 통합, 고정 폭, 8pt 회색 고지→본문 크기 경고 배너. 행별 실시간 추세추종 상태는 목업 진단 9건 어디에도 없고 행마다 조회 비용이 늘어 미구현. 검증: pytest 229/229, ruff 0건, 오프스크린 렌더·스모크. |
| 2026-09-19 (14차) | Trading Universe 버그 2건 수정. (1) Name–Price 열 사이 빈 간격: 고정 열(frozen Name) 오버레이 `QTableWidget`의 위젯 폭만 열 0에 맞추고 내부 열 폭은 Qt 기본 100px로 남아 있어, 식별 셀이 100px에서 잘리고 나머지가 빈 배경으로 그려지던 문제 — `widgets.py::_reposition_frozen()`에서 `_frozen.setColumnWidth(0, col_w)` 동기화, 헤더 수동 드래그 경로는 `sectionResized`→`_on_section_resized` 연결. 회귀 테스트 2개 추가(`test_universe_table_sort.py`). (2) `Could not parse stylesheet of object QFrame(name="RailCard")` 경고: 비-f-string 줄의 `}}` 오타 + Qt QSS가 지원하지 않는 네 모서리 `border-radius` 축약형 — `border-top-right-radius`/`border-bottom-right-radius`로 분리(`universe_tab.py::_make_rail_card`). Qt 메시지 핸들러로 4개 탭+Rail 카드 3종 생성 시 스타일시트 경고 0건 확인. 검증: pytest 231/231, ruff 0건. |
| 2026-09-19 (15차) | 소스 분석 개선 1번 묶음(A+B+E). (A) `threads/fetch_threads.py::_AUTO_BACKUP_FILES`에 `TRADING_RECORD_FILE`(Total Assets 주간 스냅샷, 외부 API로 복구 불가능한 수기 입력 데이터) 추가 — review_agy.md #1 해소; CLAUDE.md·main.py 주석 동기화. (B) `ui/history_tab.py::_save_overrides(records)` — 편집 1건마다 closed/overridden/custom 거래 전부를 upsert하던 것을 호출자가 넘긴 변경 레코드만 upsert하도록 변경(호출처 5곳이 `[rec]` 또는 변경 목록 전달) — review_agy.md #8 해소. (E) `ui/history_table.py::ni()/pi()/wi()`를 `NumericItem`으로 교체 — `setData(EditRole)`+`setText()`가 같은 저장소를 덮어써 정렬을 켜면 콤마 포맷 숫자가 문자열 비교되던 잠복 버그 제거(Universe 12차 수정과 동일 원인). 테스트 3개 추가(`test_history_table.py` 숫자 정렬, `test_thread_owner.py` 백업 목록·부분 upsert). 검증: pytest 234/234, ruff 0건. |
| 2026-09-19 (16차) | 소스 분석 개선 2~4번 묶음. **2번(C·D)**: `assets_tab.py::_show_graph()`에 `_metrics_ready` 가드 추가 — 프리로드 전에는 `_rate_kospi_for_date()`가 UI 스레드에서 첫 네트워크 조회를 하던 경로 차단(CLAUDE.md 스레드 규칙 준수); `StrategySummaryThread`의 추세추종 검사를 순차 루프에서 `ThreadPoolExecutor(max_workers=8)`로(커버리지 "All" 시 수 분→수십 초). **3번(O·G)**: `tests/test_ui_smoke.py` 신설 — 앱 QSS를 건 채 4개 탭 생성·렌더 + Rail 카드 3종 + Strategy 계산/필터/더블클릭, Qt 메시지 핸들러로 스타일시트 파싱 경고를 실패로 승격(14차 RailCard 회귀 방지); 테마 정리 — `theme.py`에 `QLabel#muted/#faint` 추가, `setStyleSheet` hex 직접 지정 28곳→4곳(남은 4곳은 배너 전용 톤·경고색), pre-theme 버튼 헬퍼(`action_button_style`, `_ACTION_*_COLOR` 6개, `_SECONDARY_BUTTON_STYLE`, `_ACCENT_COLOR/_HOVER`) 삭제 및 Compute/Run Backtest/Save 버튼을 `#primary` 역할로, 나머지 액션 버튼은 중립 외곽선으로(docs/ui.md 1.6). **4번(H·I·F·J·K)**: `ui/ma_chart.py::StockMaLauncherMixin`(Universe·Auto Trading의 StockMaThread→StockMaDialog 열기 중복 제거); `ui/delegates.py` 신설 — 델리게이트 7종을 `CellDelegate` 기반(배경·마커·배지·이름/메타 공통 프리미티브)으로 통합, `widgets.py` 1,291→1,092줄; 매수/매도/신규 입력 다이얼로그 `on_save` 안에서 금액 미입력 시 `price*qty` 통일(호출자 보정 제거); Universe `_stretch_columns`의 `viewport*0.99` 제거(우측 14px 빈 띠); `except: pass` 8곳에 `logger.debug(exc_info=True)`. 테스트 +5(스모크 3, 병렬 요약 1, 뷰포트 폭 1). CLAUDE.md `ui/` 목록 갱신. 검증: pytest 239/239, ruff 0건. |
| 2026-09-19 (17차) | 사용자 요청 "전체 폰트 통일" — 앱 전체를 맑은 고딕 Semilight 한 글꼴로. 조사: Qt 위젯은 QSS를 걸어도 전부 `Malgun Gothic Semilight`(weight 300)로 해석되고 있었고, 다른 글꼴은 숫자 셀의 Consolas(`create_numeric_font`, 5곳)뿐; matplotlib 차트는 폰트 설정이 없어 기본 DejaVu Sans(한글 없음 → 종목명 제목이 □로 깨질 수 있음). 수정: `ui/common.py`에 `FONT_FAMILIES`(단일 출처, `FONT_FAMILY_CSS`도 여기서 생성)와 pt 크기 체계 `FONT_TITLE/KPI/HEADING/BODY/SMALL/CAPTION` 신설, `create_numeric_font` 삭제(숫자 셀·KPI 값 → `create_font(..., "Semilight")`), `apply_matplotlib_font()`(rcParams font.family + unicode_minus)를 `main.py` 시작 시 호출. QSS의 px 크기(KPI 10/17px, path 9px) → pt로, 크기 이탈값 정리(메인 타이틀 18→16, 리포트 제목 12→11, KPI 보조 7→8). docs/ui.md 1.3(등폭 숫자 규칙)은 폐기 표기, CLAUDE.md 폰트 규약 갱신. 테스트 +4(`test_ui_common.py::TestOneAppFont`). 검증: 실제 Windows 플랫폼 프로브로 숫자 셀 `Malgun Gothic Semilight`/Semilight 해석 및 한글 차트 제목 누락 글리프 경고 0건 확인. pytest 243/243, ruff 0건. |
| 2026-09-19 (18차) | "MA Chart Redesign" 목업(claude.ai/design, `DesignSync.get_file`로 수신) 구현 — `ui/dialogs/stock_ma.py` 전면 재작성(823→약 640줄), 진단 12건·Phase A–D 전부 반영(상세는 docs/ui.md 5A). A) 거래량·RSI 밴드 PROFIT/LOSS(앱에서 유일하게 남아 있던 US식 초록=상승 제거), MA 4개를 `ui/colors.py::MA_RAMP` 단일 accent 램프, 종가 먹색. B) 범례 삭제 → 각 선 우측 끝 값 배지(겹침 밀어내기), 마커 제거. C) twin axis 4개 제거 — RSI 독립 패널, ax1 Div 중복 삭제, EqualWeight 리베이스; 이격도 밴드 6겹→3겹, `MA_DIV_NEUTRAL_PCT` 상수를 표와 공유, 기준선 100 중앙 대칭. D) 배타 뷰 세그먼트(Short/Long/Close only/Divergence/EW, `QButtonGroup`), 기간 버튼 1M~3Y/All(+보이는 구간 Y 자동 맞춤), 패널 토글(Volume/Divergence/RSI, 동적 gridspec), 세 패널 공통 십자선 + 고정 리드아웃 행(mplcursors 툴팁 대체), 헤더(가격·전일 대비·기준일), 글자 최소 9pt. 수급/선물 표·Company Information·채권 Y± 유지. 테스트 `tests/test_stock_ma_dialog.py` 10개(색·램프·끝 라벨·기간·패널·십자선·단순/무데이터 차트). 검증: pytest 253/253, ruff 0건. |
| 2026-09-19 (19차) | 외부 AI 리뷰(`review_agy.md`, 2차) 검증 후 1번 묶음 구현. (1) `trade_db.backup_to(dest)` 신설 — `sqlite3.Connection.backup()` 온라인 백업 API로 `AutoBackupThread`의 DB 복사를 대체(커밋된 WAL 프레임 포함, 락은 API가 처리); 체크포인트+`copy2` 경로와 `checkpoint_wal()` 삭제. (2) Trading History 거래 삭제 UI — 표 우클릭 "Delete Trade..." 메뉴 + Delete 키(`QShortcut`, WidgetShortcut), 확인 후 `trade_db.delete_trade(orig_key)` 및 메모리 목록 제거, 월 요약 행은 제외(그동안 `delete_trade`는 있었지만 UI 경로가 없어 오입력 거래를 DB로만 지울 수 있었음). (3) `gemini_helper`: 종목 리포트 프롬프트에 한국어 출력 지시 추가, `_generate`가 모듈 `_MODEL` 재사용(환경변수 중복 조회 제거); CLAUDE.md의 응답 언어 주석 갱신. (4) `data/cache.py::safe_float` 함수 내부 `import math` 2곳 → 모듈 상단. 테스트 +2(`test_trade_db.py::TestBackupTo` 무결성·행 포함, `test_thread_owner.py` 삭제 확인/취소 경로). 검증: pytest 255/255, ruff 0건. |
| 2026-09-19 (20차) | 외부 AI 리뷰 2번 갈래 + 놓친 항목: 다이얼로그 색 토큰화 일괄. `ui/colors.py`에 `WARN`(주의 표기: 건너뛴 종목, ATR 스톱 청산, 게이트 초과 MDD) 추가. 구형 `#c0392b`/`#2980b9`(토큰과 다른 빨강·파랑)를 PROFIT/LOSS/FLAT로: `trade_history.py`(10곳), `trend_following_tab.py`, `trend_following_chart/portfolio.py`, `backtest_result.py`, `holdings_summary.py`; 시리즈 식별색(전략 곡선·벤치마크·총자산 KRW/USD/KOSPI)은 방향 색이 아니므로 ACCENT/TEXT/TEXT_MUTED로, 매수/매도·진입/청산·채널 상하단은 ACTION_BUY/ACTION_SELL로, 게이트 PASS/FAIL·OOS Sharpe 강조는 `_STATUS_*` 상수로 통일. `IndexMaDialog`: 지수별 색·MA10/20/50 청록·빨강·주황 → 먹색 종가 + `MA_RAMP`, 점 마커 제거(Stock MA 차트와 동일 어휘). `HoldingsSummaryDialog`: `build_holdings_summary()` 분리, `NumericItem`으로 헤더 클릭 정렬 활성(기본 P/L 내림차순), 로컬 QSS 삭제(전역 테마 적용). `StockTradeHistoryDialog` Close 버튼의 pre-theme solid 스타일 제거. 그 외 정확히 토큰과 같은 값의 hex(`#c3c6d4`→TEXT_EMPTY, `#1c1e2c`→TEXT, `#444444`→TEXT_SUB, `#d0d0d0`→LINE, `#ffffff`→SURFACE 등) 치환. 남긴 hex: 표 섹션 헤더 팔레트(`history_table._SECTION_COLOR`, `assets_tab._GROUPS`), tPER 저평가/고평가 판정색, `_STATUS_*` 정의. 테스트 +2(`test_holdings_summary.py`). 검증: pytest 257/257, ruff 0건. |
| 2026-09-19 (21차) | 외부 AI 리뷰 3번 갈래. (3) `ui/universe_tab.py`: 전체 갱신 후 사용자 추가 종목을 재조회하는 `SingleStockFetchThread`가 N개 동시에 완료될 때마다 정렬+`_reload_table_and_rail()`+`filter_table()`을 N회 실행하던 것을, 시작 경로(`is_startup=True`)에서는 150ms 단발 `QTimer`로 디바운스해 마지막 도착 후 1회만 렌더(`_render_startup_batch`); 사용자가 직접 추가할 때는 즉시 렌더 유지. `on_finished_all`과 중복이던 정렬 키를 `_sort_key`/`_sort_all_data`로 통합. (10) `tests/test_trade_dialogs.py` 신설 — Buy/Sell/Entry 다이얼로그의 금액 미입력 시 `price*qty` 폴백, 날짜 정규화, 매수일>매도일 차단, 매도일 입력 시 가격 필수, 빈 티커 거부, 티커 검증 스레드 기동·잠금·해제, 미확인 티커 시 다이얼로그 유지(10개). `tests/test_universe_tab.py` 신설 — 시작 배치 1회 렌더·정렬, 수동 추가 즉시 렌더(2개). 검증: pytest 269/269, ruff 0건. |
| 2026-09-19 (22차) | 외부 AI 리뷰 4→6→5. **(4) MA Cross**: `strategy/ma_cross/`를 다른 전략과 같은 3단으로 분리 — `config.py::MaCrossConfig`(fast/slow 창, 진입·익절·과열 배수, 기본값 = 종전 리터럴), `signals.py`(`entry_signal`/`exit_condition`/`next_true_index`), `backtest.py`(체결·집계, `run_backtest_for_stock`이 `summary`·`df` 추가 반환); `ui/ma_cross_tab.py::MaCrossTab` 신설(종목 입력/Universe 선택, 연도 필터, 파라미터 스핀, `#primary` 실행 버튼, KPI 스트립, 종가+MA 램프 차트에 진입▲/청산▼, 정렬 가능한 트레이드 표)과 `MaCrossBacktestThread`; StrategyTab의 비활성 "Coming soon" 탭을 실제 서브탭으로 교체. **(6) KIS 하이브리드**: `fetch_kis_realtime_prices`가 장중이라도 종목 수 < `_KIS_WS_MIN_TICKERS`(4)면 WebSocket 세션(연결·구독·틱 대기·해제·종료) 없이 병렬 REST로 직행, 기본 틱 대기 6s→3s(`_KIS_WS_TIMEOUT`). **(5) 자산 스냅샷 SQLite 통합**: `portfolio.db`에 `asset_records(date PK, total, manual)` 테이블, `trade_db.load_asset_records()/save_asset_records()`(전체 교체 1트랜잭션), `_migrate_asset_records_json()`이 테이블이 비어 있을 때만 `trading_record.json`을 1회 가져옴(실제 파일로 드라이런: 날짜·금액 전부 일치); `TradingRecordTab`이 JSON 대신 DB 사용, `main.py`의 `init_db()`를 탭 생성 전으로 이동, 자동 백업 목록에서 JSON 제거(DB에 포함). CLAUDE.md·`ma_cross.md` 갱신. 테스트 +15(ma_cross 설정·신호·탭 9, KIS 하이브리드 1, asset_records 왕복·1회 마이그레이션 2, 기타). 검증: pytest 284/284, ruff 0건. |
| 2026-09-19 (23차) | 사용자 요청: Trading Universe에서 지수·금리·원자재(S&P500 … WTI)를 종목과 같은 표 행으로 — 현재가와 변화율을 종목과 나란히 보기 위함. docs/ui.md 2.4의 Market rail(카드 분리)은 폐기 표기. `ui/widgets.py::_populate_row`가 `change_mode`별 단위로 포맷: 가격 `pct` 지수 `2,500.13`·`bp` `3.12%`·`abs` `18.40`/`$88.50`, 변화 `+1.2%`/`-2bp`/`+0.75`(모멘텀 열도 동일, `_fmt_change`), 히트맵은 pct·bp만(bp는 25bp 기준 `_BP_HEAT_SCALE`), Cap은 지수 행 `-`. `data/indicators.py` abs 모드의 기간 변화가 과거 레벨 자체를 저장하던 잠복 결함을 레벨 차이(현재−과거)로 수정(그동안 VIX/WTI 변화율을 어디서도 표시할 수 없었음). `universe_tab.py`에서 `_is_rail_row`/`_make_rail_card`/`_refresh_market_rail`/rail 레이아웃 삭제, `_reload_table_and_rail`→`_reload_table`, 경량 갱신 경로도 all_data 전체 기준으로 단순화. 지수 행은 정렬 키상 표 맨 위, 시장 필터는 ALL에서만 표시. 테스트: 단위 포맷 검증 1개 추가, 스모크 유니버스에 지수·채권·VIX·WTI 행 포함, abs 모드 테스트 갱신. 검증: pytest 284/284, ruff 0건. |
| 2026-09-19 (24차) | 사용자 요청: Trading Universe 표에서 Chg·MA20 Div 열 폭을 3D와 동일하게(`_NARROW_W=58`, weight 0.62) 통일하고, Price·Cap은 고정 80px(7자리 원화 가격/시총이 꽉 차지 않도록; 사용자 후속 지시 2회 반영)로, 10D 모멘텀 열을 3D 옆에 다시 추가(`ui/widgets.py` `COLUMNS`/`COL_D10`/`_MOMENTUM_COLS`; `changes["10d"]`는 `_TD_PERIODS`에 이미 있어 추가 조회 없음). 13열→14열, Trend 미니차트도 5개 점 사용. 검증: pytest, ruff. |
| 2026-09-19 (25차) | 사용자 요청 3건(Trading Universe). (1) "Columns:" 열 그룹 토글 버튼(Price/Value/Momentum)과 (2) Density 콤보(Compact/Normal/Spacious) 제거 — `TOGGLE_GROUPS`, `StockTable.set_column_group_visible/set_density/DENSITY_ROW_HEIGHTS`, `UniverseTab._on_column_group_toggled/_on_density_changed/_apply_column_group_and_density_settings` 삭제, 행 높이는 `StockTable.ROW_HEIGHT=28`(구 compact)로 고정, `custom_settings.json`의 `column_groups`/`density` 키는 로드 시 폐기. `ColSpec.group`은 의미 버킷 문서용으로만 유지(`history_table`은 자체 섹션 파생에 사용). (3) 식별 셀에서 ticker·market 메타를 이름 아래 두 번째 줄이 아닌 **이름 오른쪽 같은 줄**에 배치(`delegates.py::draw_name_and_meta` — 메타 폭은 유지하고 이름이 먼저 생략됨; Auto Trading의 `RankStockDelegate`도 같은 프리미티브라 동일 적용). 검증: pytest, ruff, 오프스크린 렌더. |
| 2026-09-19 (26차) | 사용자 요청: Trading Universe 툴바의 ALL/KOSPI/KOSDAQ/Target 필터 버튼 크기를 통일(종전 Target List 기준 100px·10pt, `UniverseTab._filter_button` 헬퍼), "Target List" → "Target"으로 라벨 축약. 검증: pytest, ruff. |
| 2026-09-19 (27차) | 사용자 보고: MA 차트 다이얼로그 우측 "Supply & Demand Trend" 표의 숫자가 잘려 보임. 원인: 순매수 수량이 7–8자리(예 -1,234,567)인데 열이 `Stretch` 모드 + 표 최소폭 360px + 스플리터 비율 10:3이라 좁은 창에서 셀이 `1,23…`으로 생략. 수정: `StockMaDialog._fit_side_table()` — 헤더/셀 중 가장 넓은 텍스트의 폰트 메트릭 + 셀 패딩으로 열 폭을 정하고, 합계+스크롤바 폭을 표 최소폭으로 고정; 스플리터는 우측 패널 stretch 0·접힘 불가로 차트만 늘고 줄게 변경(선물 곡선 표에도 동일 적용). 테스트 1개 추가(8자리 수량이 열 폭에 들어가는지). 검증: pytest, ruff. |
| 2026-09-19 (28차) | 사용자 재보고: 수급 표에서 SK하이닉스 가격(7자리 `1,849,000`)이 여전히 잘림. 27차의 셀 패딩 22px는 테마 패딩(8+8)+스타일 텍스트 마진(3+3)의 합계 그대로라 여유가 0 — 실제 화면의 힌팅/DPI 차이로 1–2px 초과 시 생략됨. `_SIDE_CELL_PAD` 22→36, 폭 계산 전에 `ensurePolished()`로 전역 QSS가 확정한 폰트로 측정. 테스트의 최소 여유도 상향. 검증: pytest, ruff. |
| 2026-09-20 (1차) | 사용자 요청: Trading History KPI 스트립(Total Asset~Withdrawal)의 필드 간 간격이 라벨/서브텍스트 길이에 따라 들쭉날쭉하던 것을 균등화. `_build_kpi_strip`의 각 셀을 `QWidget`으로 감싸고, 전체 셀 중 가장 넓은 `sizeHint` 폭을 계산해 모든 셀의 `minimumWidth`로 통일(기존 `addLayout(box, 1)`의 동일 stretch factor만으로는 셀별 콘텐츠 폭 차이가 그대로 남아 간격이 달라 보였음). 오프스크린 렌더로 7개 셀이 동일 폭(예: 260px)임을 확인. 검증: pytest, ruff. |
| 2026-09-20 (2차) | 사용자 요청: 최상위 4개 탭(Trading Universe/Trading History/Total Assets/Strategy) 라벨 폰트를 기존 대비 120%·bold로. `main.py`의 메인 `QTabWidget`에 `objectName("MainTabs")` 부여 후 `ui/theme.py`에 `QTabWidget#MainTabs QTabBar::tab { font-size: 12pt; font-weight: 700; }` 추가(기존 전역 `QTabBar::tab` 10pt/400 규칙은 유지되어 Strategy 탭 내부의 서브탭(Auto Trading/Trend Following/MA Cross)에는 영향 없음 — objectName 없는 별도 `QTabWidget`이라 스코프 밖). 오프스크린 렌더로 최상위 탭만 폰트가 커진 것을 tabRect 크기 비교로 확인. 검증: pytest, ruff. |
| 2026-09-20 (3차) | 사용자 요청: Trading History에서 KR/US/Total 포지션 요약 표(`_build_position_card`)와 상태 필터 All/Open/Closed 버튼 삭제, 남은 항목(Fetch/Reload/Add Trade, Summary, Sort by Date, Current Holdings 콤보, Search, 기간 필터 1M/3M/YTD/All, 30일 규칙 토글, 입금 상태·경로 라벨)을 한 줄로 통합. `_build_position_card`/`_build_actions_card`/공용 `_create_card` 헬퍼 삭제, `_build_controls_row` 하나로 병합(카드 프레임 없이 툴바 형태). `_state_filter`/`_state_buttons`/`_on_state_filter_changed` 삭제(`_apply_filter`는 이미 `getattr(..., "All")` 기본값이라 상태 필터 제거 후에도 전체 표시 그대로 동작). `_refresh_summary`에서 표 갱신 블록 제거, KPI 갱신만 유지. 미사용 임포트(`QTableWidgetItem`, `QColor`, `QFont`) 정리. `history_calc.summarize_positions`의 kr/us 세부 필드는 그대로 유지(다른 테스트가 검증 중이라 손대지 않음). 검증: pytest 285/285, ruff 0건. |
| 2026-09-20 (4차) | 사용자 요청 3건(Strategy 탭). (1) 서브탭 라벨 "Auto Trading" → "Weekly Rebalance"(`strategy_tab.py::_sub_tabs.addTab`), 탭 내부 제목도 "Auto Trading — Weekly Rebalance Signals" → "Weekly Rebalance Signals"로 중복 표기 제거, CLAUDE.md 프로젝트 개요 갱신. (2) "Full Ranking" 필터 버튼(All/Buy/Sell/Hold) 글씨 잘림 — 원인은 초기 라벨이 아니라 `_apply_filter()`가 계산 후 `"Hold (123)"`처럼 카운트를 붙이는데 고정폭 56px는 이 확장된 텍스트 기준이 아니었음; 100px로 확대. (3) `_SIGNAL_COLUMNS`의 팩터 열(PER/MA20Div/MA50Div/52wHigh%/Ret20D%/Ret60D%/MA20Slope1W%) 값·헤더 잘림 — 볼드 헤더 텍스트(예: 13자 "MA20Slope1W%")가 고정폭보다 넓었던 게 원인, 58~84px → 64~120px로 확대(`MA20Slope1W%` 84→120이 가장 컸음). 검증: pytest 285/285, ruff 0건, 오프스크린으로 버튼/열 폭·타이틀 텍스트 확인. |
| 2026-09-20 (5차) | 사용자 후속 지시: Weekly Rebalance 표에서 Rank/Stock 열 폭을 기존 210의 2배인 420으로, PER~MA20Slope1W% 7개 팩터 열은 모두 MA20Slope1W%(120px, 4차에서 정한 값)를 기준으로 동일 폭으로 통일. `_SIGNAL_COLUMNS`에 `_FACTOR_COL_W = 120` 상수 도입해 7개 열이 한 값을 공유하도록 정리. 검증: pytest 285/285, ruff 0건. |
| 2026-09-20 (6차) | 외부 AI 리뷰(`review_agy.md`, 3차) 검증 후 가장 심각한 데이터 정합성 버그 2건 수정. **#5 분할 매도 시 잔여 수량 누락**: `history_tab.py::_on_cell_double_clicked`의 Sell 편집 분기가 `sell_qty < qty`(부분 매도)인데도 레코드 전체를 `_closed_data`로 옮겨, 남은 보유 수량이 `summarize_positions()`(open_data만 순회)에서 완전히 사라지던 버그 — 원본 레코드는 판매된 수량만큼(`qty`/`buy_amount` 비례 축소)으로 마감 처리하고, 남은 수량은 `buy_amount`를 나머지 비율로 분할한 새 오픈 레코드로 분리해 `trade_db.upsert_trade()`(키 자동 생성, `_save_custom_trade`와 동일 경로)로 별도 저장. **#2 `list.remove(rec)` 값 동등성 삭제**: `_delete_selected_trades()`와 위 Sell 편집 분기 양쪽에서 쓰이던 `rec in source: source.remove(rec)`(동일 필드값의 다른 행을 오삭제할 위험)를 `orig_key` 기준 리스트 컴프리헨션 필터로 교체. `tests/test_thread_owner.py`에 회귀 테스트 2개 추가(분할 매도 시 잔여 오픈 포지션 검증, 동일 필드값 두 레코드 중 선택한 것만 삭제되는지 검증). 검증: pytest 287/287, ruff 0건. |
| 2026-09-20 (7차) | 외부 AI 리뷰(`review_agy.md`, 3차) 나머지 검증된 항목 처리(#1 KPI 라벨 불일치는 표시 시맨틱을 바꾸는 판단이 필요해 보류). **#4 캐시 스탬피드**: `data/history.py::get_historical_data`에 `data/listing.py::_singleflight_cache`와 같은 취지의 키별 `threading.Lock`(`_HIST_KEY_LOCKS`) 도입 — 캐시 미스 시 같은 `(ticker, start)`를 동시에 요청한 스레드들이 각자 네트워크 조회를 하지 않고 첫 스레드의 결과를 기다렸다 재사용(다른 키는 그대로 병렬). 20스레드 동시 요청 스크립트로 같은 키는 fetch 1회·0.2초(직렬 시 4초)로, 다른 키 5개는 여전히 병렬(0.2초)로 끝남을 확인 후 `tests/test_data_fetcher.py`에 회귀 테스트 2개로 고정. **#6 `backup_to()` Busy 방어**: `trade_db.backup_to()`의 `src.backup(dst)`를 `pages=100, sleep=0.01`로 — 전체를 한 트랜잭션으로 묶지 않고 청크 사이에 메인 스레드 쓰기가 끼어들 틈을 준다. **#7 MA20 중복 계산**: `data/indicators.py`의 `ma20_roc_1w` 블록이 `ma20_div` 블록에서 이미 구한 `ma20`을 재사용하도록(같은 `np.mean(closes[-20:])`를 두 번 계산하지 않음 — n≥25는 항상 n≥20을 함의하므로 안전). **#3 변수 섀도잉**: `threads/fetch_threads.py::StockMaThread.run()`에서 임포트한 `start_date` 함수를 가리던 지역 변수를 `first_date_str`로 개명(다른 곳의 `start_date` 사용에는 영향 없음). **#9 잔여 주석**: `assets_tab.py`의 `# ---JSON load/save ---`를 `# ---SQLite asset_records load/save ---`로 정정. (검증 결과 #3의 FONT_* 토큰 지적은 기각 — 리터럴 폰트 크기가 8곳에 74회 나오는 기존 관례이고 전부 `FONT_*` 스케일 값과 일치해 두 파일만의 문제가 아님.) 검증: pytest 289/289, ruff 0건. |
| 2026-09-20 (8차) | 외부 AI 리뷰(`review_agy.md`, 3차) #1 KPI 라벨/계산 불일치 처리 — 사용자에게 두 방안(라벨을 계산에 맞추기 vs 계산을 라벨에 맞추기) 중 "계산을 라벨에 맞춘다"(리뷰 방안 A)를 확인받고 구현. `ui/history_calc.py::summarize_positions()`의 `total`(eval+deposit+withdrawal, "Total Asset" 카드에 그대로 노출되면서도 서브텍스트 "Valuation + cash"엔 출금액이 빠져 있던 값)과 `total_invest`(eval+deposit, 즉 NAV이면서 라벨은 "Total Invest"/서브텍스트 "Cost basis"였던 값)를 `nav`(eval+deposit, position_w 분모로도 그대로 재사용)와 `cumulative_asset`(nav+withdrawal)로 분리, 실제 매수원가는 이미 계산돼 있던 `cost_total`을 그대로 노출. `history_tab.py`: KPI 스트립에 카드 1개 추가(`total_asset`/`cumulative_asset`/`total_pl`/`total_pl_pct`/`principal`/`cost_basis`/`deposit`/`withdrawal` 7→8칸, 균등폭 로직은 카드 수와 무관하게 그대로 동작), `cost_basis` 카드가 `agg['cost_total']`을 표시하도록(기존 `total_invest` 카드는 개명), `total_asset_updated` 시그널이 이제 NAV를 emit(다운스트림 `TradingRecordTab.update_live_asset`의 "Current Total Asset" 자동입력도 "현재 계좌에 있는 금액"이라는 의미에 맞게 개선 — 출금액까지 포함된 값을 보여주던 것도 실은 같은 버그였음). 기존 `agg["total"]`/`agg["total_invest"]`를 직접 검증하던 테스트 2개를 새 키로 갱신, `test_thread_owner.py`에 KPI 스트립 8칸·값·시그널을 한 번에 고정하는 회귀 테스트 1개 추가. 오프스크린 스크립트로 8칸 렌더·균등폭·라벨-값 일치를 확인(NAV 1,700 / Cumulative 1,800 / Cost Basis 1,000 / Total P/L -1,200 예시). `docs/ui.md` 3.6 KPI 스트립 목록 갱신. 검증: pytest 290/290, ruff 0건. |
