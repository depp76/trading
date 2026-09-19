# Portfolio Management — UI 명세

PyQt6 데스크톱 앱(`src/ui/`)의 4개 탭 재설계 명세. 각 항목은 현재 소스의 진단에서 도출되었고, 대응하는 mockup 파일이 있습니다.

| 탭 | mockup | 진단 |
| --- | --- | --- |
| Trading Universe | `Trading Universe Redesign.dc.html` | 12건 |
| Trading History | `Trading History Redesign.dc.html` | 10건 |
| Total Assets | `Total Assets Redesign.dc.html` | 9건 |
| Strategy | `Strategy Redesign.dc.html` | 9건 |

---

## 1. 전역 규칙

### 1.1 색 — 상승/하락·손익

**앱 전체에서 단 하나의 규칙을 쓴다. 빨강 = 이익/상승, 파랑 = 손실/하락** (한국 시장 관행).

```python
# ui/colors.py (신규) — 두 탭이 같은 곳에서 가져온다
PROFIT = "#d1453b"   # 이익, 상승
LOSS   = "#3b6fc4"   # 손실, 하락
FLAT   = "#75798c"   # 0, 미해당
```

현재 위반 사항:

- `widgets.py` `_C_BG_BLUE_SEVERE` — 변화율 +30% 이상에 **파란** 배경 (상승=파랑)
- `widgets.py` `_populate_row` col 11/13 — High/Low Diff 양수에 `color_red` (상승=빨강)
- `history_table.py` `col_red`/`col_blue` — 이익=빨강 (관행과 일치)

→ 같은 행 안에서 빨강의 의미가 뒤집히고, 탭 간에도 어긋난다. `PROFIT`/`LOSS` 두 상수로 통일한다.

### 1.2 색 농도 — 히트맵

- 모든 모멘텀/변화율 컬럼은 **하나의 공통 스케일**을 쓴다: `±10%`에서 최대 농도.
- 농도는 `alpha = 0.04 + min(|v|/10, 1) × 0.20` (배경), 글자색은 항상 `PROFIT`/`LOSS` 원색.
- 임계값을 `if` 사다리로 하드코딩하지 않는다. 컬럼 스펙의 `scale` 값으로 분리한다.
- 범례를 표 하단에 상시 노출한다.

현재: `Div(20)`은 120/110/105/102/98/93, `Div(50)`은 130/110/107/103/98/90, 변화율은 ±30/±15/0 — 세 가지 스케일이 각각 코드에 박혀 있다.

### 1.3 숫자

- ~~숫자 셀은 전부 **tabular numerals**(또는 monospace). 자릿수가 세로로 맞아야 한다.~~
  **2026-09-19 사용자 결정으로 폐기**: 앱 전체를 맑은 고딕 Semilight 한 글꼴로 통일한다
  (숫자 셀·KPI 값·matplotlib 차트 포함, `ui/common.py` `FONT_FAMILIES`/`apply_matplotlib_font()`).
  숫자 셀에 별도 등폭 글꼴(Consolas)을 쓰지 않는다.
- 우측 정렬. 소수 자릿수는 컬럼별로 고정.
- 통화 기호·단위는 셀이 아니라 **헤더**에 (`억원`, `원`, `%`, `배`).

### 1.4 밀도

행 높이 토글을 제공하고 기본은 컴팩트.

| 단계 | 행 높이 | 셀 세로 패딩 |
| --- | --- | --- |
| 컴팩트 (기본) | 28px | 5px |
| 보통 | 34px | 8px |
| 여유 | 42px | 12px |

### 1.5 컬럼 폭

**고정 폭 금지.** 모든 컬럼은 `최소폭 + 비율`로 정의한다.

```
minmax(<최소폭>, <비율>fr)
```

- 최소폭은 그 컬럼에 들어갈 **가장 긴 값**의 폭(예: `1,849,000` → 86px).
- 최소폭 합계가 창 폭을 넘으면 가로 스크롤을 허용한다. 스크롤을 끈 채 강제로 욱여넣지 않는다.
- 가로 스크롤이 생기는 표는 식별 열(종목명·티커)을 좌측 고정한다.

현재 위반: `widgets.py` `MIN_W=38` + `ScrollBarAlwaysOff` → 값이 잘리는데 볼 방법이 없다. `assets_tab.py` `ResizeMode.Fixed` → 창을 넓혀도 그대로.

### 1.6 버튼

- **주 동작 1개**만 강조(accent 1px 아웃라인, 채움 없음). 나머지는 중립 아웃라인.
- 파괴적 동작(삭제)은 상시 노출하지 않는다 — 행 hover 또는 우클릭 메뉴. 확인 단계 필수.
- 버튼 색은 **의미**(주/보조/위험)를 나타낸다. 기능 종류별로 색을 배정하지 않는다.

현재 위반: `_BTN_BLUE`/`_BTN_ORANGE`/`_BTN_GREY`/`_BTN_INSIGHT` 4~5종의 채도 높은 fill이 툴바에 나열.

### 1.7 상태 표기

- 상태(보유/관심/타깃/청산)는 **배지 + 행 좌측 3px 마커**로 표기한다.
- 셀 배경색을 상태 채널로 쓰지 않는다 — 지브라 줄무늬와 충돌하고 인쇄·색각이상에서 사라진다.
- 용어는 하나로 통일한다. 현재 헤더는 `Pf`, 버튼 라벨은 `On`/`Tg`/`-`, 배경은 노랑/하늘색으로 세 가지가 같은 상태를 가리킨다.

### 1.8 표 상호작용

- 행 선택 + 키보드 탐색 허용(`SelectionBehavior.SelectRows`). 편집만 막는다.
- `Ctrl+C` — 선택 행 TSV 복사.
- `Enter` / 더블클릭 — 상세(MA 차트) 열기.
- 정렬 상태는 **데이터 갱신 후에도 유지**한다.

현재 위반: `widgets.py` `NoSelection` + `NoFocus` → 복사도 키보드 탐색도 불가.

### 1.9 데이터 갱신 정합성

**정렬 후 부분 갱신은 데이터 인덱스가 아니라 티커로 행을 찾아야 한다.**

```python
# 현재 (버그): data 인덱스를 뷰 행 번호로 그대로 사용
def update_changed_rows(self, data, changed_rows, highlights=None):
    for row in changed_rows:
        self._populate_row(row, data[row], highlights)
```

`setSortingEnabled(True)`이므로 사용자가 정렬한 뒤에는 data 인덱스 ≠ 화면 행. 60초 경량 갱신이 **다른 종목 행에 가격을 쓴다.**

→ `ticker → 현재 행` 매핑을 유지하거나, `QAbstractTableModel` + `QSortFilterProxyModel`로 전환해 정렬/필터와 데이터 갱신을 분리한다.

### 1.10 갱신 상태 표시

자동갱신 토글·주기·마지막 갱신 시각·실패 경고를 **헤더 한 곳에** 모은다. 현재는 토글이 우상단, 상태 라벨이 창 하단으로 갈라져 있다.

```
● Auto update · 60s   last 09:41:22
```

- 정상: 초록 점 / 지연(주기의 2배 초과): 주황 점 / 실패: 빨강 점 + 사유

---

## 2. Trading Universe

### 2.1 구조

```
헤더:   앱명 · 탭 · [Auto update 상태] · [+ Ticker]
툴바:   [검색] [ALL|KOSPI|KOSDAQ] [저장된 뷰…] ····· [컬럼 그룹] [밀도]
Market rail:  지수·금리·원자재 카드 (표에서 분리)
표:     13열, 유동 폭, 가로 스크롤 없음
푸터:   건수 · 정렬 상태 · 모멘텀 공통 스케일 범례
```

### 2.2 표 컬럼

| # | 컬럼 | 단위 | 최소폭 | 비율 | 비고 |
| --- | --- | --- | --- | --- | --- |
| 1 | Name / Ticker | — | 150 | 2.4 | 종목명 + `티커 · 시장` + 상태 배지 |
| 2 | Price | 원 | 86 | .95 | |
| 3 | Chg | 전일 % | 62 | .7 | PROFIT/LOSS |
| 4 | Cap | 억원 | 62 | .7 | 조 단위 축약 |
| 5 | 52W Range | 저·현재·고 | 112 | 1.5 | **레인지 바 1칸** |
| 6 | tPER | 배 | 54 | .55 | <12 초록 / >60 빨강 |
| 7 | fPER | 배 | 54 | .55 | |
| 8 | MA20 Div | 기준 100 | 94 | 1.05 | 미니 바 + 수치 |
| 9–12 | 3D / 20D / 60D / 120D | % | 58 | .62 | 공통 ±10% 히트 |
| 13 | 1Y | 추세 | 80 | 1.0 | 스파크라인 |

- 헤더는 2단: 라벨 + 단위(소문자, 9px).
- `tPER`/`fPER`는 대문자 변환하지 않는다.

### 2.3 52W 레인지 바

현재 4열(`52W High`, `High Diff`, `52W Low`, `Low Diff`)이 사실상 "지금 어디쯤인가" 한 질문이다. 한 칸으로 압축:

```
저가 ────┬──────── 고가
        현재 위치(%)
```

- 위치 > 80% → PROFIT 색 마커 / < 25% → LOSS / 그 외 중립
- 정확한 수치는 hover tooltip 또는 상세.

### 2.4 Market rail

`is_index` / `is_bond` / `change_mode in ('bp','abs')` 행은 표에서 제외하고 상단 카드로 분리한다.

- 이유: 지수(포인트)·금리(bp)·WTI($)·개별주(원)가 한 컬럼에서 정렬되면 의미가 없다. 지수 행은 `Market Cap`·`PER`이 전부 `-`이고, 정렬 시 결측을 `float('-inf')`로 밀어 넣어야 한다.
- 카드: 라벨 · 값 · 변화(단위 자동: `%` / `bp` / `$`).

### 2.5 컬럼 그룹

22열을 4그룹으로 묶고 토글한다: **가격 · 밸류 · 모멘텀 · 수급**. 기본은 가격+밸류+모멘텀.

### 2.6 저장된 뷰

필터 조합을 이름으로 저장하고 건수 배지를 붙인다. 예: `관심 42` `타깃 12` `저PER 58` `신고가 근접 9`.

컬럼 필터는 Market 하나(`FILTER_COLS = {2}`)에서 확장한다 — 수치 컬럼은 범위 필터, 범주 컬럼은 체크박스.

---

## 3. Trading History

### 3.1 섹션 구조는 유지한다

`Trading | Buy | Sell | Position | Past` 4단 구조는 거래의 생애주기를 그대로 담고 있으므로 **유지한다.** 개선은 구조가 아니라 표현에서 한다.

### 3.2 표 컬럼 (19열)

| 그룹 | span | 컬럼 |
| --- | --- | --- |
| Trading | 1 | Company (종목명 + `티커 · 시장` + 상태 배지) |
| Buy | 4 | Date · Price · Q'ty · Amount |
| Sell | 7 | Date · Days · Price · Q'ty · Amount · P/L · P/L % |
| Position | 4 | Days · Price · P/L · P/L % |
| Past | 3 | 5D · 10D · 20D |

- 2단 헤더(그룹 + 컬럼)를 **공식 구조**로 삼는다. 그룹 라벨은 본문과 같은 그리드 위에서 `span`으로 묶는다.
- 그룹 상단에 2px 색 규칙: Trading 중립 / Buy 초록 / Sell PROFIT / Position accent / Past 연회색.
- 엑셀 헤더 매핑은 **임포터에만** 둔다. 화면 구조가 엑셀 파일 구조를 미러링하지 않는다.

### 3.3 미해당 칸

청산 행의 Position 4칸, 보유 행의 Sell 7칸은 비어 있는 게 정상이다.

- 굵은 `-` 대신 **얇은 em dash `—` + 흐린 색**(`#c3c6d4`).
- 상태 배지(보유/청산)가 왜 비었는지 즉시 설명한다.
- 상태 필터는 **행만 거른다.** 컬럼을 접거나 숨기지 않는다.

### 3.4 월 요약

현재 `kind == "monthly"` 행이 데이터 행과 같은 표에 들어가 정렬·필터 대상이 된다. "Sort by Date"를 누르면 요약이 흩어진다.

→ **그룹 헤더 행**으로 분리한다(정렬 대상 아님).

```
2026년 09월    거래 3건   매수액 41,368,000   실현손익 -1,100,000   승률 0%
```

### 3.5 30일 규칙

`hide_past_info = (kind == "closed" and curr_days > 30)` — 청산 후 30일이 지나면 현재가·평가손익을 숨긴다. 이 규칙을 **툴바 토글로 노출**한다: `청산 30일 이후 현재가 숨김`. 끄면 전체 표시.

### 3.6 KPI 스트립

읽기 전용 `QLineEdit` 9개(`_make_ro_edit`)를 텍스트 KPI 스트립으로 대체한다. 입력 가능한 항목(원금·입금·출금)만 실제 필드로 남긴다.

`Total Asset · Total P/L · Total P/L(%) · Principal · Total Invest · Deposit · Withdrawal`

### 3.7 배경색

4종(`#ffffff` / `#f5f7fa` 지브라, `#edfbf0` 보유 민트, `#fff5e6` 월요약 크림)을 **지브라 1종**으로 줄인다. 상태는 좌측 마커 + 배지로, 요약은 행 형태 자체를 다르게.

---

## 4. Total Assets

### 4.1 구조

```
헤더:   앱명 · 탭 · [KRW|USD] · USD/KRW 환율 · [Export]
KPI:    Current Total Asset · Weekly P/L · Cumulative P/L · vs KOSPI · 기록 주차
차트:   주간 추이 (자산 vs KOSPI, 첫 주 = 100 재계산, 초과수익 음영) — 상시 노출
입력:   이번 주 기록 [W38 · 2026-09-18] [금액(자동 채움)] [기록 저장]
표:     10열
```

### 4.2 서브 헤더 라벨을 채운다

현재 `_COLS_SUB`의 15개 중 9개가 빈 문자열이고 `sub_h=0`이라 서브 헤더가 아예 렌더되지 않는다. KOSPI 3열, Weekly P/L 2열, Cumulative P/L 2열이 무엇인지 알 수 없다.

| 그룹 | span | 서브 라벨 |
| --- | --- | --- |
| Date | 1 | Week · Date |
| KOSPI | 3 | 종가 · 주간 % · 누적 % |
| Total Assets | 1 | KRW (또는 USD) |
| Weekly P/L | 2 | 금액 · % |
| Cumulative P/L | 2 | 금액 · % |
| 초과수익 | 1 | vs KOSPI (%p) |

### 4.3 통화 토글

KRW 7열 + USD 6열 = 15열을 **토글 하나 + 10열**로 줄인다. 환산율은 헤더에 한 번만 표기.

### 4.4 차트를 탭 안으로

`_show_graph` 모달의 시리즈를 그대로 상단 인라인 차트로 옮긴다. 이 탭의 데이터는 주간 시계열 하나이므로 추이가 기본 뷰여야 한다. 모달은 확대·상세용으로만 남긴다.

- 자산: accent 2px 실선 / KOSPI: 회색 1.4px / 초과수익: PROFIT 14% 음영
- 첫 주 = 100으로 재계산해 두 시리즈를 같은 축에 둔다.

### 4.5 총자산 자동 채움

`history_tab.py`의 `total_asset_updated` 시그널 값을 입력란에 **자동 채운다.** 사용자는 확인만 한다. 수기로 고치면 기록에 `manual: true` 플래그를 남겨 이후 비교가 가능하게 한다.

### 4.6 기타

- 표 첫 열에 `W38` 배지 + 날짜를 함께 표기(현재 W 번호는 콤보에만 있다).
- 주간 손익·초과수익 컬럼은 정렬을 허용한다(기본은 시간순).
- 삭제는 행 hover로 옮긴다.

---

## 5. Strategy

### 5.1 구조

```
헤더:      앱명 · 탭 · 계산 시각/소요 시간 · [시그널 다시 계산]
시그널 카드: 매수 후보 · 매도 후보 · 추세 보유 · 관망  (4장)
커버리지:   경고 배너 — 검사 범위와 그 이유
서브탭:     Auto Trading · Trend Following · MA Cross(준비중, 비활성)
표:        단일 랭킹 표 11열
고지:      투자 유의 배너 (본문 크기)
```

### 5.2 시그널 카드

현재 한 줄 f-string(`_summary_lbl`)을 카드 4장으로 분해한다.

| 카드 | 값 | 보조 |
| --- | --- | --- |
| 매수 후보 | 5 종목 | 리밸런스 밴드 상단 진입 |
| 매도 후보 | 3 종목 | 시장별 순위 25위 밖으로 이탈 |
| 추세 보유 | 12 / 30 | 20일 채널 상단 돌파 유지 |
| 관망 | 18 / 30 | 오류 0건 · 400일 워밍업 |

- 상태(`computing...`)와 오류(`computation failed (…)`)는 **카드와 다른 영역**에 표시한다. 결과 라벨을 덮어쓰지 않는다.
- 헤더에 계산 시각과 소요 시간. 일정 시간이 지나면 "오래됨" 표시.

### 5.3 커버리지 배너

`_TF_SUMMARY_MAX_TICKERS = 30` 제약을 결과 문자열 괄호가 아니라 **배너로** 노출한다.

> 추세추종 시그널은 시총 상위 30종목만 검사합니다(전체 312종목 중 9.6%). 조회 비용 때문이며, 나머지 282종목의 신호는 계산되지 않았습니다. **[범위 넓히기 →]**

범위 확장 버튼은 예상 소요 시간을 함께 안내한다.

### 5.4 단일 랭킹 표

Buy 후보 표 + Sell 후보 표 + Full Ranking 13열 = 같은 결과의 3중 복제를 **표 하나 + 상태 필터**로 합친다.

| 그룹 | span | 컬럼 |
| --- | --- | --- |
| 종목 | 1 | Rank · 종목명 · `티커 · 시장` |
| 시그널 | 2 | Action 배지 · Score (바 + 수치) |
| 점수 기여 요인 | 6 | PER · MA20Div · MA50Div · 52wHigh% · Ret20D% · Ret60D% |
| 추세추종 | 2 | 상태(보유 중/관망) · 보유 |

- **기여 요인 셀은 점수 기여도에 비례한 accent 농도**로 칠한다. 왜 그 종목이 후보인지 같은 줄에서 읽힌다.
- Action 배지: 매수(초록 아웃라인) · 매도(PROFIT 아웃라인) · 보유(accent) · `—`.
- 🟢🔴 이모지를 쓰지 않는다.
- 컬럼 폭은 최소폭+비율로 고정한다(`ResizeToContents` 금지 — 계산할 때마다 열이 움직인다).

### 5.5 미구현 탭

MA Cross는 **비활성 탭 + `준비중` 배지**로 명시한다. 회색 안내 문구 한 줄짜리 빈 탭이 다른 두 전략과 같은 무게를 갖지 않게 한다.

### 5.6 고지

"리서치·백테스트용 신호 생성기이며 투자 자문이 아닙니다"를 8pt 회색(`create_font(8)`, `#888`)에서 **표 하단 고정 배너, 본문 크기(12px), 경고 톤**으로 올린다.

---

## 6. Qt 적용 — 목업과 실제 앱이 달라 보이는 이유

목업은 브라우저 렌더링이고 앱은 Qt 위젯입니다. 탭 코드를 고쳐도 **전역 스타일시트가 전부를 덮어쓰기 때문에** look & feel이 바뀌지 않습니다. 이 절을 **Phase 0**으로 먼저 처리해야 나머지 작업의 결과가 화면에 나타납니다.

### 6.1 현재 전역 스타일시트가 강제하는 것

`src/main.py` 하단 `app.setStyleSheet(...)`:

| 규칙 | 현재 값 | 결과 |
| --- | --- | --- |
| `QMainWindow` | `background-color: #f0f0f0` | 창 전체가 회색. 목업의 `#fbfbfd` 바탕이 안 나온다 |
| `QPushButton` | `background: #0078d4; color: white; padding: 8px 16px` | **앱의 모든 버튼이 파란 채움.** "주 동작만 강조" 규칙(§1.6)이 원천 차단된다 |
| `QHeaderView::section` | `background: #e0e0e0; border: 1px solid #d0d0d0; padding: 4px; bold` | 헤더가 회색 상자. 목업의 연보라 헤더·2단 그룹 규칙이 묻힌다 |
| `QTabBar::tab` | `background: #e0e0e0; padding: 10px 30px; bold` | 탭이 크고 무겁다. 목업의 컴팩트 pill과 다르다 |
| `QTableWidget` | `alternate-background-color: #f9f9f9; gridline-color: #d0d0d0` | 지브라·격자가 목업(`#fafbfe` / `#f0f1f8`)보다 어둡고 진하다 |
| `QLineEdit` / `QComboBox` | `padding: 5px; border: 1px solid #c0c0c0` | 입력창이 두껍고, 읽기 전용 값도 같은 모양 |

즉 **목업과 앱의 차이는 대부분 탭 코드가 아니라 이 40줄에서 발생합니다.**

### 6.2 Phase 0 — 테마 모듈 분리

전역 스타일시트를 `src/ui/theme.py`로 옮기고 §1의 토큰에서 생성합니다.

```python
# src/ui/theme.py
from ui.colors import PROFIT, LOSS, FLAT

BG          = "#fbfbfd"   # 앱 바탕
SURFACE     = "#ffffff"   # 카드·표 본문
ZEBRA       = "#fafbfe"   # 짝수 행
HDR_BG      = "#f3f5fe"   # 표 헤더
GRP_BG      = "#f8f9fe"   # 그룹 헤더
TEXT        = "#1c1e2c"
TEXT_SUB    = "#595d6c"
TEXT_MUTED  = "#75798c"
TEXT_FAINT  = "#9397ab"
TEXT_EMPTY  = "#c3c6d4"   # 미해당 —
LINE_STRONG = "#cfd3e5"
LINE        = "#e4e7f5"
LINE_SOFT   = "#f0f1f8"
ACCENT      = "#9184d9"
ACCENT_TEXT = "#5d5294"
ACCENT_BG   = "#f5f4ff"

def app_qss(font_css: str) -> str:
    return f"""
    QMainWindow, QWidget#Page {{ background-color: {BG}; }}

    /* 버튼: 기본은 중립 아웃라인. 강조는 objectName 으로만. */
    QPushButton {{
        background: {SURFACE}; color: {TEXT_SUB};
        border: 1px solid {LINE_STRONG}; border-radius: 6px;
        padding: 5px 12px; font-weight: 400; {font_css}
    }}
    QPushButton:hover    {{ background: {HDR_BG}; }}
    QPushButton:pressed  {{ background: {LINE}; }}
    QPushButton:disabled {{ color: {TEXT_EMPTY}; border-color: {LINE_SOFT}; }}
    QPushButton#primary {{
        background: {SURFACE}; color: {ACCENT_TEXT}; border: 1px solid {ACCENT};
    }}
    QPushButton#primary:hover {{ background: {ACCENT_BG}; }}
    QPushButton#danger  {{ color: {PROFIT}; border-color: #f0c4c1; }}
    QPushButton#danger:hover {{ background: #fdf0ef; }}

    QTableWidget, QTableView {{
        background-color: {SURFACE};
        alternate-background-color: {ZEBRA};
        gridline-color: {LINE_SOFT};
        selection-background-color: {ACCENT_BG};
        selection-color: {TEXT};
        border: 1px solid {LINE}; border-radius: 8px;
        {font_css}
    }}
    QTableWidget::item, QTableView::item {{ padding: 2px 8px; }}
    QTableWidget::item:hover {{ background: {ACCENT_BG}; }}

    QHeaderView::section {{
        background-color: {HDR_BG}; color: {TEXT_MUTED};
        border: none; border-bottom: 1px solid {LINE_STRONG};
        border-right: 1px solid {LINE};
        padding: 5px 8px; font-weight: 600; {font_css}
    }}

    QTabWidget::pane {{ border: 1px solid {LINE}; background: {SURFACE}; border-radius: 8px; }}
    QTabBar::tab {{
        background: transparent; color: {TEXT_MUTED};
        border: 1px solid transparent; border-radius: 6px;
        padding: 5px 12px; margin-right: 2px; font-weight: 400; {font_css}
    }}
    QTabBar::tab:selected {{ background: {ACCENT_BG}; color: {ACCENT_TEXT}; border-color: #c9c2f3; }}
    QTabBar::tab:hover:!selected {{ background: {HDR_BG}; }}

    QLineEdit, QComboBox {{
        background: {SURFACE}; color: {TEXT};
        border: 1px solid {LINE_STRONG}; border-radius: 6px;
        padding: 4px 9px; {font_css}
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
    QLineEdit[readOnly="true"] {{ background: transparent; border: none; color: {TEXT}; }}

    QLabel#kpiLabel {{ color: {TEXT_FAINT}; font-size: 10px; font-weight: 600; }}
    QLabel#kpiValue {{ color: {TEXT}; font-size: 17px; }}
    QLabel#kpiSub   {{ color: {TEXT_FAINT}; font-size: 10px; }}
    """
```

`main.py`는 이것만 호출합니다:

```python
from ui.theme import app_qss
app.setStyleSheet(app_qss(FONT_FAMILY_CSS))
```

그리고 **위젯별 `setStyleSheet` 하드코딩을 전부 제거**합니다. 현재 위반:

- `history_tab.py` — `_BTN_BLUE` `_BTN_ORANGE` `_BTN_GREY` `_BTN_INSIGHT` `_INPUT_STYLE` `_COMBO_STYLE` `_CARD_STYLE` `_POS_TABLE_STYLE`
- `assets_tab.py` — `add_btn` `del_btn` `today_btn` `graph_btn` `export_btn` 각각의 인라인 QSS
- `widgets.py` — `btn_tg` `btn_ma`(보라 `#7d3c98`) `btn_del`(빨강 `#e74c3c`), `tg_filter_btn`(`#87CEEB`)

→ 전역 QSS + `setObjectName("primary" | "danger")` 두 가지로 대체합니다.

### 6.3 Qt에서 재현되지 않는 것과 대체 수단

| 목업의 표현 | Qt 대체 |
| --- | --- |
| `box-shadow` | 쓰지 않는다. 1px 경계선 + 배경 대비로 층을 만든다 (`QGraphicsDropShadowEffect`는 성능 비용이 크다) |
| 셀 단위 `border-radius` | 불가. 표 바깥 프레임에만 적용 |
| CSS grid / `minmax()` | 컬럼 스펙(§1.5)을 `resizeEvent`에서 픽셀로 계산해 `setColumnWidth` |
| 셀 안의 미니 바 / 레인지 바 / 스파크라인 | `QStyledItemDelegate.paint()` + `QPainter`. 셀 위젯(`setCellWidget`)을 행마다 심지 않는다 |
| 상태 배지(pill) | 델리게이트에서 `drawRoundedRect` + 텍스트. 또는 `QLabel` 하나를 셀 위젯으로 (행 수가 적을 때만) |
| `conic-gradient` 도넛 | matplotlib 또는 `QPainter.drawPie` |
| 행 hover 시 액션 노출 | `setMouseTracking(True)` + `entered` 시그널로 델리게이트가 아이콘을 그린다 |
| `text-wrap: pretty` | 없음. 긴 텍스트는 `QLabel.setWordWrap(True)` |
| 폰트 `Inter` / `IBM Plex Mono` | 본문은 기존 Malgun Gothic Semilight 유지. **숫자만** `QFont("Consolas")` 또는 `setStyleName`으로 tabular 확보 |

### 6.4 폰트·DPI

- 목업은 CSS px, Qt는 pt(폰트) + px(QSS). `create_font(9)` ≈ 12px @96dpi. 명세의 px 값을 pt로 옮길 때 **× 0.75**.
- 고DPI에서 QSS의 px 값은 스케일되지 않으므로, 패딩·경계선은 작은 값(1–2px)으로 두고 크기는 폰트가 결정하게 합니다.
- `app.setStyle("Fusion")`을 권합니다. Windows 기본 스타일은 QSS 일부(특히 `QTabBar`, `QHeaderView`)를 네이티브 렌더링으로 무시합니다.

---

## 7. 적용 순서

| Phase | 내용 | 대상 파일 | 규모 |
| --- | --- | --- | --- |
| **0** | **전역 스타일시트를 `ui/theme.py`로 분리 · 위젯별 인라인 QSS 제거 · Fusion 스타일** | `main.py` `app.setStyleSheet` · `history_tab.py` `_BTN_*` · `assets_tab.py` · `widgets.py` | **1–2일** |
| 1 | 색 상수 전역 통일 · 숫자 tabular · 히트맵 임계값 상수화 | `widgets.py` `_C_*` `_populate_row` · `history_table.py` `col_red/col_blue` | 1–2일 |
| 2 | 정렬·갱신 정합성 (ticker 매핑 또는 Model/Proxy 전환) | `widgets.py` `load_data` `update_changed_rows` | 3–5일 |
| 3 | 컬럼 폭 체계 (최소폭+비율) · 고정 열 · 컬럼 그룹 · 밀도 | `widgets.py` `_stretch_columns` · `assets_tab.py` `widths` | 3–4일 |
| 4 | 서브 헤더 라벨 · 통화 토글 · 인라인 차트 | `assets_tab.py` `_COLS_SUB` `_show_graph` | 3–4일 |
| 5 | 시그널 카드 · 커버리지 배너 · 단일 랭킹 표 | `strategy_tab.py` · `auto_trading_tab.py` | 5–7일 |
| 6 | 지수/원자재 분리 · 저장된 뷰 · 행 선택·복사 · 월 요약 그룹 헤더 | `universe_tab.py` · `history_calc.py` · `history_table.py` | 1주+ |

**Phase 0을 먼저 하지 않으면 이후 작업의 결과가 화면에 나타나지 않습니다** — `main.py`의 전역 QSS가 버튼·헤더·탭·표 색을 전부 덮어쓰기 때문입니다. Phase 1–2는 코드 변경량 대비 체감이 가장 크고, Phase 2는 실제 데이터 오염 가능성을 막는 수정이므로 우선순위가 높습니다.
