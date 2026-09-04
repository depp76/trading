# feat/3-1-modularize → master 병합 전 테스트 계획

> 작성일: 2026-08-29
> 대상 커밋: `ef33bef`~`f20eca3` (9개 커밋, master 대비 +7,108/-6,394줄)
> 목적: 대규모 구조 리팩터링(Phase 0~5) + 3-3/3-5/3-6 기능 변경이 실제 동작에
> 영향이 없는지(리팩터링) / 의도대로 동작하는지(신규 기능) 병합 전 확인

## 0. 이 문서를 쓰는 이유

이 브랜치의 작업은 코드 실행 환경(헤드리스, GUI 없음)에서 `py_compile` /
`import` / `pytest` / 스크립트 기반 동작 비교로는 검증했지만, **실제 화면을
보고 클릭해보는 검증은 전혀 하지 못했습니다.** 특히 아래 항목들은 반드시
사용자가 직접 실행해서 확인해야 합니다:

- Phase 4~5: `TradingHistoryTab`/`TradingRecordTab`/`UniverseTab`을 분리하며
  시그널로 재배선한 탭 간 연동(예: Universe 새로고침 → History 탭도 갱신)
- 3-6: 다크 모드의 실제 색상 대비·레이아웃 (자동 검증은 "스타일시트 문자열에
  올바른 색상 코드가 들어있는가"까지만 확인했고, 눈으로 본 적은 없음)
- 3-5: `fetch_kr_market_data` fallback은 Naver가 실제로 실패하는 상황을
  인위적으로 재현하기 어려워, 정상 경로만 확인 가능

## 1. 사전 준비

1. **백업 확인**: `archive/backup_20260829_141942/`, `archive/backup_20260829_162024/`
   에 리팩터링 전 `main.py`/`data_fetcher.py` 스냅샷이 있음 (문제 발생 시 대조용).
2. **자동 테스트 먼저 실행** (사람이 손대기 전 기본 방어선):
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/ -v
   ```
   76개 전부 통과해야 함. 실패하면 이후 수동 테스트 진행 의미 없음 — 여기서 중단.
3. **앱 실행 전 현재 브랜치 확인**: `git branch --show-current` → `feat/3-1-modularize`
4. `custom_settings.json` / `portfolio.db` / `universe_cache.json` 등 실제
   데이터는 이미 있는 그대로 사용(합성 데이터로 바꿀 필요 없음 — 오히려 실제
   데이터로 테스트해야 회귀를 더 잘 잡음).

## 2. 5분 스모크 테스트 (가장 먼저, 실패 시 즉시 중단)

```powershell
.\.venv\Scripts\python.exe main.py
```

- [ ] 콘솔에 `Starting Portfolio Management...` 출력 후 창이 뜬다 (예외로 죽지 않음)
- [ ] 창 제목이 "Portfolio Management", 탭 3개(Trading Universe / Trading History / Total Assets) 보임
- [ ] Trading Universe 탭에 종목 목록이 로딩된다 (캐시 즉시 표시 → 잠시 후 최신 데이터로 갱신)
- [ ] 상태바(창 최하단)에 진행 메시지("KOSPI 시세 조회 중…" 등)가 잠깐 떴다 사라진다
- [ ] 창을 닫는다(X 버튼) → **콘솔이 멈추지 않고 즉시 프롬프트로 복귀**한다
  (`closeEvent`의 스레드 정리 로직이 Phase 5에서 `UniverseTab.collect_threads_to_stop()`로
  바뀐 부분 — 여기서 멈추면 좀비 스레드 회귀 의심)

여기까지 문제없으면 아래 상세 체크리스트로.

## 3. Trading Universe 탭 (Phase 5 — 가장 큰 구조 변경, 최우선 점검)

`MainWindow`에 인라인으로 있던 코드가 통째로 `ui/universe_tab.py`의
`UniverseTab`으로 옮겨지고, `MainWindow`와는 5개 시그널
(`status_text_changed`/`sync_time_changed`/`status_message`/`refresh_started`/
`auto_lightweight_tick`)로만 통신하도록 바뀜. 이 배선이 끊어지면 아래처럼
"조용히 아무 일도 안 일어나는" 형태로 나타남 — 에러 메시지가 안 뜨는 게
오히려 의심 신호일 수 있음.

- [ ] **Refresh 버튼** 클릭 → 종목 목록이 다시 로딩되고, 상단 상태 텍스트가
      "Status: KOSPI: Loading (n/N) …" → "Data Loaded. Total N stocks (...)" 로 바뀜
- [ ] Refresh 완료 후 **"Update Time: " 라벨**이 현재 시각으로 갱신됨
- [ ] **Add Ticker**: Market 콤보에서 KOSPI/KOSDAQ 선택 → Ticker 입력(예: `005930`) →
      Enter 또는 "+ Add Ticker" → 목록에 추가되고 상태 텍스트에 "Added '...' " 표시
      - [ ] 이미 있는 티커를 다시 추가 시도 → "이미 있음" 메시지, 중복 추가 안 됨
- [ ] **Search** 입력창에 종목명/티커 일부 입력 → 표가 실시간으로 필터링됨
- [ ] **Target List** 버튼(체크 가능) 토글 → 필터 결과가 On/Tg 하이라이트된
      종목만으로 좁혀짐
- [ ] 표의 **Tg 버튼**(칩) 클릭 → 상태가 순환(-→On→Tg→-)하고 배경색이 바뀜,
      `custom_settings.json`의 `highlights`에 반영되는지 확인(재시작 후에도 유지)
- [ ] 표의 **MA 버튼** 클릭 → 해당 종목 MA20/MA50 차트 다이얼로그가 뜸
      (다크모드 테스트 시 이 차트도 함께 확인 — 3-6 항목 참고)
- [ ] 표의 **Del 버튼** 클릭 → 확인 다이얼로그 → 확인 시 목록에서 제거되고
      `custom_settings.json`의 `deleted`에 기록됨
- [ ] **🤖 AI Filter** 버튼 → 자연어 조건 입력(예: "KOSPI stocks with high market cap") →
      Apply → 필터가 적용되고 버튼이 초록색으로 바뀜 → "Reset Filter"로 원복
      (Gemini API 키가 없거나 네트워크 문제면 "AI 변환 실패" 메시지가 뜨는지도 확인 — 에러가
      조용히 묻히면 안 됨)
- [ ] **Auto Update (1 min) 체크박스**: 체크 해제 → 재체크 → 1분 대기 →
      상태 텍스트가 "Auto-updated prices."로 갱신됨 (전체 재조회가 아니라 가벼운
      갱신인지: 스크롤 위치가 유지되는지로 판단 가능)
      - [ ] 이 자동 갱신 시점에 **Trading History 탭의 실시간 시세도 함께 갱신**되는지
        확인 (Phase 5에서 `auto_lightweight_tick` 시그널로 새로 연결한 부분 — 예전엔
        `MainWindow`가 직접 호출했음)

## 4. Trading History 탭 (Phase 4)

- [ ] 탭 전환 시 기존 거래 내역(포지션/청산 내역)이 `portfolio.db`에서 정상 로딩됨
- [ ] 대시보드 카드(Position Summary, Total Asset/P/L/Deposit 등 숫자)가
      정상적으로 표시됨 (금액 포맷, 색상 — 이익 빨강/손실 파랑)
- [ ] **➕ Add Trade** → 매수 기록 추가 다이얼로그 → 저장 → 표에 반영
- [ ] 기존 행 더블클릭 → Buy/Sell 수정 다이얼로그 → 값 변경 후 저장 → 반영
- [ ] **🔄 Reload** 클릭 → DB에서 다시 로딩
      - [ ] 이 Reload가 **Trading Universe 탭의 Refresh**를 눌렀을 때도 자동으로
        같이 실행되는지 확인 (Phase 5의 `refresh_started` 시그널 — Universe를
        새로고침하면 History도 같이 리로드되던 기존 동작이 유지되는지가 핵심)
- [ ] **🔄 Fetch** (예수금 조회, Kiwoom 연동) — Kiwoom 키 파일이 있는 환경에서만
      정상 동작 확인 가능. 없으면 에러 메시지가 뜨는지만 확인(죽지 않는지)
- [ ] **Sort by Date** 토글 → 정렬 기준 전환 확인
- [ ] **Search Company** → 검색어 입력 → 매칭 결과 다이얼로그
- [ ] **Summary** 버튼 → 보유 종목 요약 다이얼로그
- [ ] **🤖 AI Diagnosis** → 로딩 다이얼로그 → 결과 다이얼로그(Gemini) 표시
- [ ] 1분 실시간 시세 타이머가 동작하며 포지션 P/L이 갱신되는지 (몇 분 대기)
- [ ] 앱 재시작 후 principal/deposit/withdrawal 입력값이 유지되는지 (QSettings)

## 5. Total Assets 탭 (Phase 4)

- [ ] 날짜 콤보에서 주차 선택 → 자산 입력 → **➕ Add Record** → 표에 추가
- [ ] **🗑 Delete Selected** → 선택 행 삭제
- [ ] **📅 This Week** → 최신 주차로 콤보 이동
- [ ] Live Asset / Weekly P/L 라벨이 Trading History 탭의 실시간 총자산과
      연동되어 갱신되는지 (Phase 4에서 `total_asset_updated` 시그널로 연결된 부분,
      로직 자체는 안 바뀌었지만 파일이 옮겨졌으니 확인)
- [ ] **📈 그래프** 버튼 → 누적 수익률 그래프 다이얼로그 (KOSPI 대비 비교 포함)

## 6. 다크 모드 (3-6, 신규 기능 — 반드시 육안 확인 필요)

자동 검증은 "스타일시트 문자열에 맞는 색상 코드가 들어갔는지"까지만 했고,
실제로 보기 좋은지/대비가 충분한지는 전혀 확인하지 못했습니다.

1. [ ] 헤더의 **🌙 Dark Mode** 체크박스 체크 → 상태바에 "재시작하면 적용됩니다" 메시지
2. [ ] 앱 종료 후 재실행 → 전체적으로 어두운 테마로 뜨는지
3. 탭별로 돌아다니며 아래를 확인 (특히 **글자가 안 보이거나 흰 배경이 남아있는
   부분**이 있는지 — 자동 검증으로는 못 잡는 영역):
   - [ ] Trading Universe: 표, 헤더, Tg 칩, MA/AI Filter 다이얼로그
   - [ ] Trading History: 대시보드 카드 3개, 메인 테이블, Buy/Sell 다이얼로그,
     Summary/AI Diagnosis 다이얼로그, StockTradeHistoryDialog(특히 합계 행 —
     이번에 흰색 `fillRect` 버그를 고친 부분)
   - [ ] Total Assets: 테이블, 그래프 다이얼로그
   - [ ] MA 차트에 마우스 올렸을 때 나오는 **호버 툴팁 박스** (mplcursors) —
     이번에 흰 배경 고정 문제를 고친 부분, 텍스트가 보이는지 확인
   - [ ] 매트플롯립 차트 배경(MA차트/백테스트/자산그래프)이 어두운 테마로 바뀌는지
4. [ ] 다시 체크 해제 → 재시작 → 라이트 모드로 정상 복귀하는지
5. [ ] `custom_settings.json`에 `"theme": "dark"` / `"light"` 키가 남는지 확인
   (다른 키(`added`/`deleted`/`highlights`)가 손상되지 않았는지도 같이 확인)

## 7. 3-3 / 3-5 회귀 확인 (동작은 그대로여야 하는 부분)

- [ ] Trading Universe에서 지수(코스피/코스닥 등) 및 개별 종목의 등락률 열
      (5D/10D/20D 등, 52주 High/Low)이 이전과 동일하게 정상적으로 계산되어
      표시되는지 (3-3에서 `fetch_historical_changes`에 넘기는 값을 바꿨으므로,
      숫자가 이상하면 이 부분 의심)
- [ ] KOSPI/KOSDAQ 새로고침이 평소처럼(느려지거나 죽지 않고) 완료되는지 (3-5에서
      리스팅 실패 시 fallback 경로를 추가했지만 정상 경로는 그대로이므로 체감
      속도 차이가 없어야 함 — 오히려 실패 fallback이 잘못 걸려서 매번 FDR
      경로로 빠지면 느려질 수 있음, `app.log`에 `"Naver listing empty"` 경고가
      반복적으로 찍히는지 확인)

## 8. app.log 확인

테스트 세션 동안 발생한 새 에러/경고를 확인:

```powershell
Get-Content app.log -Tail 100
```

- [ ] 이번 리팩터링과 무관해 보이는 기존 에러(API 타임아웃 등)는 무시 가능
- [ ] `AttributeError`, `ImportError`, `circular import` 등 구조적 에러가 있으면 회귀 의심
- [ ] `fetch_kr_market_data: all listing sources failed` 가 찍혔다면 그 시점에
      실제로 Universe 탭이 비어있었는지 대조

## 9. 통과 기준 및 병합

- 2절(스모크) + 3~5절(탭별 기능) + 6절(다크모드 육안) + 7절(회귀) 항목이 모두
  체크되고, 8절에서 구조적 에러가 없으면 병합 가능하다고 판단합니다.
- 문제를 발견하면: 어느 절/항목에서 실패했는지, `app.log`의 관련 라인, 재현
  방법을 알려주시면 해당 커밋을 찾아 수정하겠습니다 (커밋이 기능 단위로
  쪼개져 있어 `git bisect` 없이도 커밋 메시지만으로 원인 범위를 좁힐 수 있음).
- 병합 방법은 이전에 "지금은 유지"로 답변하셨으므로, 이 체크리스트 통과 후
  다시 여쭤보고 진행하겠습니다 (fast-forward 가능, 충돌 없음 확인됨).
