# Portfolio Management — 발전 로드맵

> 기준일: 2026-08-28 (최초 작성 2026-08-21, 실제 코드 상태 재조사 후 갱신)  
> 현재 상태: PyQt6 단일 사용자 데스크톱 앱 (한국/미국 주식 포트폴리오 추적)  
> 핵심 파일: `main.py` (~6,490 줄), `data_fetcher.py` (~2,560 줄), `trade_db.py`, `gemini_helper.py`

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

### 3-1. 아키텍처 — 파일 분리 (모듈화) — 미착수

**현황 (2026-08-28 확인)**: `main.py`가 오히려 ~5,900 → ~6,250 줄로 더 커짐 (여전히 단일 파일).  
**문제**: 기능 탐색/수정이 어렵고, 충돌 없이 병렬 작업 불가

**목표 구조**:

```
portfolio_mgmt/
├── main.py              # 진입점 + MainWindow (최소화)
├── ui/
│   ├── universe_tab.py  # Trading Universe 탭
│   ├── history_tab.py   # Trading History 탭
│   ├── assets_tab.py    # Total Assets 탭
│   ├── dialogs.py       # 입력/편집 다이얼로그
│   └── widgets.py       # StockTable, GroupedHeaderView 등 공통 위젯
├── threads/
│   ├── fetch_threads.py # AllDataFetchThread, UniverseLightweightFetchThread 등
│   └── realtime.py      # RealtimePriceThread, IndexMaThread 등
├── data_fetcher.py      # (현행 유지, 점진적 분리)
└── trade_db.py          # (현행 유지)
```

**진행 방식**: 
1. `archive/backup_<date>/` 백업 후 시작
2. 클래스 단위로 하나씩 분리 → `py_compile` 확인 → GUI 구동 확인
3. 한 번에 전체 리팩터링 금지 (롤백 어려움)

---

### 3-2. 테스트 인프라 구축 — 미착수

**현황 (2026-08-28 확인)**: `tests/` 디렉터리 없음. 여전히 `py_compile` + 수동
smoke-test 스크립트로만 검증하는 방식 유지.  
**방향**:

```
tests/
├── test_trade_db.py      # SQLite CRUD, 인덱스, 배치 upsert
├── test_data_fetcher.py  # LRU 캐시, yf_quote_batch (mock 기반)
├── test_backtest.py      # run_backtest_strategy 결과 일치 검증
└── test_assets_calc.py   # 환율/KOSPI 캐시, 자산 계산 로직
```

- `pytest` + `unittest.mock` 사용
- GUI 테스트 제외 (headless 불가), 비즈니스 로직만
- GitHub Actions 없이 로컬 `pytest` 실행으로도 충분

---

### 3-3. 성능 — pandas/polars 경계 최소화

**현황**: `_to_polars()`가 FDR/yfinance/yahooquery pandas 결과를 polars로 변환하는 오버헤드  
**방향**:

- 히스토리컬 데이터 파이프라인을 처음부터 polars로 구성 (라이브러리가 pandas 반환 시 `pl.from_pandas()` 1회만)
- `_compute_indicators()`의 중간 pandas 왕복 제거
- 실제 속도 개선 측정 후 진행 여부 결정

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

> `상태` 컬럼은 2026-08-28 재조사 결과 반영. 완료 항목은 우선순위 재산정 대상에서 제외.

| # | 항목 | 영향 | 난이도 | 우선순위 | 상태 |
|---|------|------|--------|----------|------|
| 2-1 | 로깅 도입 | 중 | 낮음 | — | ✅ 완료 |
| 2-2 | 캐시 효율 모니터링 | 낮음 | 낮음 | — | ✅ 완료 |
| 2-3 | 상태바 피드백 | 중 | 낮음 | — | ✅ 완료 |
| 2-4 | 자동 백업 | 높음 | 낮음 | — | ✅ 완료 |
| 2-5 | 입력 유효성 검사 | 중 | 낮음 | — | ✅ 완료 |
| 3-4B/C | AI 진단/자연어 필터 | 높음 | 중간 | — | ✅ 완료 |
| 3-2 | 테스트 인프라 | 높음 | 중간 | ⭐⭐⭐ 높음 | 미착수 |
| 3-4A | AI 종목 리포트 | 높음 | 중간 | ⭐⭐⭐ 높음 | 미착수 |
| 3-1 | 모듈화 | 높음 | 높음 | ⭐⭐ 중간 | 미착수 |
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
