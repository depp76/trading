# Portfolio Management — 발전 로드맵

> 기준일: 2026-08-29 (최초 작성 2026-08-21, 실제 코드 상태 재조사 후 갱신)  
> 현재 상태: PyQt6 단일 사용자 데스크톱 앱 (한국/미국 주식 포트폴리오 추적)  
> 핵심 파일: `main.py` (~401 줄), `data_fetcher.py` (~2,246 줄), `trade_db.py`, `gemini_helper.py`, `ui/`, `threads/`

---

## 목차

1. [현황 요약](#1-현황-요약)
2. [단기 개선 (1~4주)](#2-단기-개선-14주)
3. [중기 개선 (1~3개월)](#3-중기-개선-13개월)
4. [장기 개선 (3개월+)](#4-장기-개선-3개월)
5. [우선순위 매트릭스](#5-우선순위-매트릭스)

---

## 1. 현황 요약

### 현재 기능

| 탭 | 기능 |
|----|------|
| **Trading Universe** | KOSPI / KOSDAQ / NASDAQ 100 / S&P 500 종목 워치리스트, 실시간 시세, MA 지표, 필터 팝업 |
| **Trading History** | 거래 기록 수동 입력, SQLite 영속화 (`portfolio.db`), 오버라이드 지원 |
| **Total Assets** | 날짜별 자산 합계 테이블 + matplotlib 그래프, 환율·KOSPI 연동 |

### 기술 스택

- **UI**: PyQt6 + matplotlib (QtAgg 백엔드)
- **데이터**: pykrx / FinanceDataReader / yfinance / yahooquery / Naver / Kiwoom REST
- **연산**: polars (내부) → pandas (외부 라이브러리 경계)
- **DB**: SQLite (WAL 모드), `trade_db.py`
- **AI**: `google-genai` (`gemini_helper.py`로 포트폴리오 진단·자연어 필터에 실사용 중), `google-cloud-aiplatform` (requirements에 포함, 미사용)

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

### 3-5. 데이터 — 실시간 시세 안정성 — 부분 진행

**현황 (2026-08-28 확인)**: 일부 경로엔 이미 개별 fallback이 존재 (`fetch_us_stock_data_bulk`:
yfinance→FDR→yahooquery, `fetch_investor_trend`: Kiwoom→Naver). 다만 국내 시세 조회
(`fetch_kr_market_data`)에는 fallback이 없어, 8/23 `app.log`에 네이버 응답 타임아웃으로
KOSDAQ 조회 전체가 실패한 사례가 실제로 기록됨.  
**방향**: (기존 계획 유지, 우선순위는 `fetch_kr_market_data`로 조정)

- 시세 소스별 fallback 체인 명시화:
  ```
  Kiwoom REST → Naver → Yahoo Finance → pykrx (당일 마감가)
  ```
- 각 소스 응답 실패 시 다음 소스로 자동 전환, 소스 정보 UI에 표시
- `yf_quote_batch()` 확장: 오류 시 재시도 횟수 설정 가능하도록
- **우선순위 조정**: `fetch_kr_market_data`에 fallback을 먼저 추가 (실제 장애가 발생한 경로)

---

### 3-6. UX — 다크 모드 지원 — 미착수

**현황 (2026-08-28 확인)**: `setPalette`/`dark_background` 사용 코드 없음, 여전히
시스템 팔레트 기본값 (라이트 모드 가정).  
**방향**:

- `QApplication.setPalette()`로 다크/라이트 토글
- matplotlib 그래프도 `plt.style.use('dark_background')` 연동
- `custom_settings.json`에 `"theme": "dark"` 저장

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

- Google Cloud Storage에 `portfolio.db` 주기적 업로드 (기존 `register_secret.py` 인프라 활용)
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

> `상태` 컬럼은 2026-08-29 재조사 결과 반영. 완료 항목은 우선순위 재산정 대상에서 제외.

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
| 3-5 | 시세 fallback | 높음 | 중간 | ⭐⭐ 중간 | 부분 진행 |
| 4-3 | 조건부 알림 | 높음 | 중간 | ⭐⭐ 중간 | 미착수 |
| 4-5 | 섹터 분석 | 중 | 중간 | ⭐⭐ 중간 | 미착수 |
| 3-6 | 다크 모드 | 낮음 | 낮음 | ⭐ 낮음 | 미착수 |
| 4-4 | 백테스트 UI | 중 | 높음 | ⭐ 낮음 | 미착수 |
| 4-1 | 웹 전환 | 높음 | 매우 높음 | ⭐ 낮음 | 미착수 |
| 4-2 | 클라우드 동기화 | 중 | 높음 | ⭐ 낮음 | 미착수 |

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
