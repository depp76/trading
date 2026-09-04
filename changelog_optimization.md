# 최적화 변경 내역 (2026-08-11)

코드 분석을 통해 발견한 성능/구조적 이슈 7건을 수정했다. 수정 전 원본 파일은
`archive/backup_20260811_180741/`에 백업되어 있다.

## 1. `trade_db.py` — 불필요한 `PRAGMA foreign_keys=ON` 제거

- **파일/위치**: `trade_db.py` `_connect()`
- **문제**: FK 제약이 없는 단일 테이블(`trades`) 스키마인데도 매 커넥션 open마다
  `PRAGMA foreign_keys=ON`을 실행 — 순수 오버헤드.
- **조치**: 해당 PRAGMA 호출 제거.

## 2. `trade_db.py` — `trades` 테이블에 인덱스 추가

- **파일/위치**: `trade_db.py` `init_db()`
- **문제**: `get_open_trades()` / `get_closed_trades()`는 `sell_date`로,
  `load_all_trades()`는 `buy_date`로 필터/정렬하지만 인덱스가 없어 매번
  풀 테이블 스캔 발생.
- **조치**: `idx_trades_buy_date`, `idx_trades_sell_date` 인덱스를
  `CREATE INDEX IF NOT EXISTS`로 추가. 기존 `portfolio.db`에는 다음 앱 실행 시
  `init_db()` 호출로 자동 적용됨 (데이터 변경 없음, 안전).

## 3. `trade_db.py` / `main.py` — 배치 upsert로 N+1 커넥션 문제 해결

- **파일/위치**: `trade_db.py`에 `upsert_trades(records: list)` 신설,
  `main.py` `_save_overrides()`에서 사용.
- **문제**: `_save_overrides()`가 거래 기록을 순회하며 `upsert_trade()`를
  건별로 호출 → 매 건마다 SQLite 커넥션 open/close + commit(WAL fsync 포함).
  거래 내역이 늘수록 선형으로 느려짐.
- **조치**: 커넥션 1개 + 트랜잭션 1개로 전체 레코드를 `executemany()`로 처리하는
  `upsert_trades()` 배치 함수를 추가하고 `_save_overrides()`가 이를 사용하도록 변경.
  단건 저장 경로(`_save_custom_trade()`의 `upsert_trade()` 호출)는 그대로 유지.

## 4. `data_fetcher.py` — `_HIST_CACHE`를 FIFO에서 진짜 LRU로 변경

- **파일/위치**: `data_fetcher.py` `_HIST_CACHE`, `get_historical_data()`
- **문제**: 1000개 캡에 도달하면 `next(iter(_HIST_CACHE))`로 "가장 먼저 삽입된"
  항목을 삭제 — 캐시 히트 시 순서 갱신이 없어 실제로는 LRU가 아닌 FIFO로 동작.
  세션 초반에 조회한 인기 종목이 밀려나고, 나중에 한 번만 조회한 종목이 캐시를
  차지하는 역전 현상이 발생해 불필요한 재조회(네트워크+파싱)를 유발.
- **조치**: `dict` → `collections.OrderedDict`로 교체하고, 캐시 히트 시
  `move_to_end()`를 호출해 최근 사용 항목을 뒤로 이동. 캐시가 가득 차면
  `popitem(last=False)`로 실제 최장 미사용 항목을 제거.

## 5. `data_fetcher.py` / `main.py` — Yahoo Finance quote 배치 조회 로직 통합

- **파일/위치**: `data_fetcher.py`에 `yf_quote_batch()` 공통 헬퍼 신설.
  - `data_fetcher.py` `fetch_us_realtime_prices()`
  - `data_fetcher.py` `fetch_us_stock_data_bulk.process_chunk()`
  - `main.py` `PositionPriceFetchThread._compute_pl` 준비 코드
- **문제**: 위 3곳에 청킹 → crumb 발급 → 재시도 3회 → 401 시 crumb 갱신 로직이
  거의 동일하게 중복 구현되어 있어, 버그 수정이나 재시도 정책 변경 시 3곳을
  각각 고쳐야 하고 서로 드리프트될 위험이 있음.
- **조치**: `yf_quote_batch(symbols, timeout, chunk_size)` 하나로 통합하고
  3개 호출부 모두 이를 사용하도록 리팩터링. 각 호출부는 반환된 raw quote
  dict에서 필요한 필드(가격/시가총액/PER/종목명 등)만 추출.

## 6. `main.py` — `UniverseLightweightFetchThread`의 불필요한 deepcopy 제거

- **파일/위치**: `main.py` `UniverseLightweightFetchThread.__init__`
- **문제**: 60초 주기 타이머(`global_auto_timer`)마다 KOSPI/KOSDAQ/지수 등
  450개 이상의 중첩 dict 전체를 `copy.deepcopy()` — `run()`은 각 항목의
  최상위 키(`price`, `changes`, `usd_price`)만 재할당할 뿐 중첩 값을 직접
  mutate하지 않으므로 완전한 deepcopy는 불필요한 작업.
- **조치**: `copy.deepcopy(current_data)` → `[dict(item) for item in current_data]`
  (항목별 얕은 복사)로 교체.

## 7. `main.py` — `_selected_date()`의 중복 계산 제거

- **파일/위치**: `main.py` `TradingRecordTab._selected_date()`
- **문제**: `_friday_dates()`를 한 호출 안에서 두 번(참 여부 체크 + `zip` 인자)
  호출 — 매번 연초부터 오늘까지 주 단위로 순회하며 문자열을 재생성.
- **조치**: 한 번만 계산해 변수에 저장 후 재사용.

---

## 검증 방법

- git이 없는 프로젝트라 수정 전 `main.py`, `data_fetcher.py`, `trade_db.py`
  원본을 `archive/backup_20260811_180741/`에 백업.
- 세 파일 모두 `py_compile`로 문법 검증 통과, `main.py` 전체 import 성공
  (런타임 `NameError`/`ImportError` 없음).
- `trade_db.py`: 임시 SQLite DB에서 `upsert_trades()` 배치 저장, 인덱스 생성 여부
  (`sqlite_master` 조회), `get_open_trades()`/`get_closed_trades()` 동작을
  스모크 테스트로 확인.
- `data_fetcher.py`: `_HIST_CACHE`의 LRU 승격(`move_to_end`)·추방(`popitem`) 동작을
  스모크 테스트로 확인.
- `yf_quote_batch()`: 단일/멀티 청크 분할, 401 → crumb 갱신 → 재시도, 티커 매핑
  (`.` ↔ `-`)까지 mock 기반 테스트 5종으로 확인.
- 실제 데스크톱 GUI 구동 테스트(디스플레이 필요)는 환경 제약으로 진행하지 못함 —
  다음 실행 시 Universe 자동갱신, Trading History 저장/불러오기 정상 동작 여부를
  확인할 것을 권장.

## 이번에 손대지 않은 항목

- **pandas/polars 혼용 오버헤드** (`_to_polars()`): FDR/yfinance/yahooquery
  3개 라이브러리가 모두 pandas만 반환하는 구조적 제약이라 근본 해결은 아키텍처
  재설계 수준. 이미 `_HIST_CACHE`/`_YF_BULK_CACHE`로 상당 부분 상쇄되어 있어
  현재로선 추가 조치 불필요로 판단.
- **광범위한 `except Exception: pass`**: `data_fetcher.py` 전반에 수십 곳
  퍼져 있으며 동작 자체는 바뀌지 않는 로깅/가시성 개선 성격이라, 범위를 좁혀
  별도로 진행하는 것을 권장 (예: `print` 대신 `logging` 모듈 도입 등).

---

# 최적화 변경 내역 2차 (2026-08-12)

1차 이후 새로 분석해 발견한 7건을 수정했다. 수정 전 원본 파일은
`archive/backup_20260812_170231/`에 백업되어 있다.

## 1. `data_fetcher.py` — `fetch_us_stock_data_bulk`의 `_yf_lock`을 세마포어로 완화

- **파일/위치**: `data_fetcher.py` `fetch_us_stock_data_bulk.process_chunk`
- **문제**: 청크를 최대 6개까지 병렬 처리하도록 `ThreadPoolExecutor`로 설계해
  놓고, 정작 각 청크에서 가장 비싼 호출인 `yf.download()`를
  `with _yf_lock:` (단일 `threading.Lock`)으로 감싸 사실상 전체 청크가
  순차 실행됨.
- **조치**: `threading.Lock()` → `threading.Semaphore(3)`으로 교체해 동시
  실행 수를 3개로 제한. yfinance 내부 공유 상태의 알려진 스레드 안전성
  이슈를 고려해 완전 제거 대신 부분 병렬화로 절충.

## 2. `data_fetcher.py` — `run_backtest_strategy` 매도 조건 스캔 벡터화

- **파일/위치**: `data_fetcher.py` `run_backtest_strategy()`
- **문제**: 매수 신호마다 `for i in range(b+1, len(df))`로 매도 조건을
  파이썬 레벨에서 한 행씩 스캔 — 이력 일수(최대 1825일) × 매수 신호 수만큼
  누적 비용 발생.
- **조치**: 매수가에 무관한 조건(MA 이격도/데드크로스)은 배열 전체에 대해
  한 번만 계산 후 역방향 누적 최소값으로 "다음 발생 인덱스"를 사전 계산.
  매수가 의존 조건(수익률 +30%)은 매수 시점 이후 구간의 `Close` 누적
  최댓값(`np.maximum.accumulate`)에 `np.searchsorted`를 적용해 첫 도달
  인덱스를 구함. 두 결과 중 더 이른 인덱스를 매도 시점으로 사용.
  랜덤 시나리오 500회에 대해 원본 로직과 결과 완전 일치 확인.

## 3. `main.py` — `TradingRecordTab._refresh_table`의 날짜 중복 파싱 제거

- **파일/위치**: `main.py` `_refresh_table_impl()`
- **문제**: 행마다 `cur_dt`/`prev_dt`를 KRW/USD weekly 블록과 KOSPI weekly
  블록에서 각각 `strptime`으로 두 번 계산.
- **조치**: 루프 진입 전 전체 레코드의 날짜를 한 번만 파싱해
  `parsed_dates` 리스트로 캐싱하고, `is_weekly` 플래그를 계산해 두
  블록에서 재사용. 날짜 파싱 실패 시 해당 레코드만 non-weekly로 처리하도록
  개별 예외 처리 유지 (원본과 동일한 견고성).

## 4. `main.py` — `_refresh_table`에 배치 리페인트 최적화 적용

- **파일/위치**: `main.py` `TradingRecordTab._refresh_table`
- **문제**: `TradingRecordTab`의 서브 테이블(`_fill_table`), `UniverseTable`
  등 다른 큰 테이블에는 이미 `setUpdatesEnabled(False)`로 렌더링 중 리페인트를
  억제하는 패턴이 적용돼 있는데 `_refresh_table`만 빠져 있었음.
- **조치**: 실제 렌더링 로직을 `_refresh_table_impl()`로 분리하고,
  `_refresh_table()`이 `setUpdatesEnabled(False)` → `_refresh_table_impl()`
  → `finally: setUpdatesEnabled(True)`로 감싸도록 변경. (이 테이블은 생성
  시점에 정렬이 항상 꺼져 있어 `setSortingEnabled` 토글은 불필요.)

## 5. `main.py` — `_show_graph`가 `_refresh_table`과 환율/코스피 캐시 공유

- **파일/위치**: `main.py` `TradingRecordTab._rate_kospi_for_date`(신설),
  `_refresh_table_impl`, `_show_graph`
- **문제**: `get_usd_krw_rate_for_date`/`get_index_close_for_date`는
  내부적으로 polars `.filter()`(O(N) 스캔)를 매 호출마다 수행하는데,
  `_refresh_table`은 날짜별 dict 캐시로 한 번만 계산하지만 `_show_graph`는
  이 캐시를 공유하지 않고 레코드마다 매번 다시 스캔.
- **조치**: 두 값을 함께 캐싱하는 `_rate_kospi_for_date(date_str)`를
  탭 인스턴스 캐시(`self._date_metrics_cache`)로 신설하고, `_refresh_table`
  과 `_show_graph` 모두 이를 통해 조회하도록 변경. 그래프를 다시 열거나
  테이블을 재갱신해도 이미 조회한 날짜는 재스캔하지 않음.

## 6. `data_fetcher.py` — 중첩 `ThreadPoolExecutor` 워커 수 조정

- **파일/위치**: `data_fetcher.py` `fetch_us_stock_data_bulk.process_chunk`
- **문제**: 외부 청크 루프(최대 6워커) 안에서 청크마다 다시
  `ThreadPoolExecutor(max_workers=10)`로 종목 단위 처리 — 최악의 경우
  동시 스레드 수가 60개까지 치솟음.
- **조치**: 내부 워커 수를 10 → 5로 축소해 최대 동시 스레드 수를 30개로
  제한.

## 7. `register_secret.py` — `shell=True` + 사용자 입력 조합 제거

- **파일/위치**: `register_secret.py` `register_secret()`
- **문제**: 사용자가 입력한 `secret_name`이 `shell=True`인
  `subprocess.run` 인자 리스트에 그대로 들어가 있었음. Windows에서
  `shell=True`는 `cmd.exe /c`를 거치므로 시크릿 이름에 `&`, `|` 같은 셸
  메타문자가 섞이면 의도치 않은 명령이 실행될 위험이 있음.
- **조치**: `shutil.which('gcloud')`로 `gcloud.cmd`의 전체 경로(확장자 포함)를
  미리 찾아 `shell=False`로 실행하도록 변경. Windows에서 `.cmd`/`.bat`
  실행 파일은 전체 경로를 지정하면 셸 없이도 OS가 올바르게 실행하므로
  동작은 동일하게 유지됨.

---

## 검증 방법 (2차)

- 네 파일 모두 `py_compile`/`ast.parse` 통과, `main.py`/`data_fetcher.py`
  전체 import 성공 (런타임 `NameError`/`ImportError` 없음).
- `run_backtest_strategy`의 벡터화된 매도 스캔 로직을 원본 파이썬 루프
  구현과 함께 별도 스크립트에 옮겨 랜덤 시나리오(길이 5~400, 매수 신호
  0~n/3개) 500회를 생성해 두 구현의 반환값(거래 수/승수/누적 수익률/
  매수·매도 날짜 리스트)이 전부 일치하는지 확인 (NaN 구간 포함).
- `_rate_kospi_for_date` 캐시가 동일 날짜 재조회 시 실제 조회 함수를
  다시 호출하지 않는지 mock으로 스모크 테스트.
- 실제 데스크톱 GUI 구동 테스트(디스플레이 필요)는 환경 제약으로 진행하지
  못함 — 다음 실행 시 US 대량 시세 조회 속도, Trading History 그래프/
  테이블 정상 동작 여부를 확인할 것을 권장.
