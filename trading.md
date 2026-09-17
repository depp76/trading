# Trading — 개발 방법론 & 매매 베이스라인

> 기준일: 2026-09-17 (최초 작성 2026-08-28)
> 목적: 매매/포트폴리오 알고리즘 개발에 공통으로 적용되는 (1) 기존 수동 매매 이력 베이스라인과
> (2) 멀티 에이전트 개발 방법론을 기록하는 문서. 개별 전략의 스펙은 각 전략 패키지의 문서로
> 분리되어 있다:
> - 주간 리밸런싱: `src/strategy/rebalance/rebalance.md` (2026-09-17 이 문서의 1~9장·11장·13장을 이동)
> - 추세추종(Donchian): `trend_following.md`(루트, 전체 스펙) + `src/strategy/trend_following/trend_following.md`(패키지 안내)
> 절 번호는 분리 전 번호(10장, 12장, 13장)를 그대로 유지한다 — 다른 문서·커밋 메시지의
> "trading.md 12-5" 같은 인용이 그대로 유효하도록. 이 문서 안에서 인용하는 1~9장·11장
> (예: 8-A, 11-4)은 `rebalance.md`의 해당 절이다.
> 관련 파일: `ROADMAP.md`(전체 로드맵 — 이 문서는 ROADMAP의 하위 작업 문서)

---

## 목차

10. [기존 매매 이력 분석 (베이스라인)](#10-기존-매매-이력-분석-베이스라인)
12. [멀티 에이전트 개발 방법론](#12-멀티-에이전트-개발-방법론)
13. [변경 이력](#13-변경-이력)

(1~9장·11장은 `src/strategy/rebalance/rebalance.md`로 이동)

---

## 10. 기존 매매 이력 분석 (베이스라인)

> 신규 전략(리밸런싱·추세추종 등)의 성과와 비교할 공통 베이스라인. 여기서 인용하는 4-1·8-B
> 등은 `src/strategy/rebalance/rebalance.md`의 절이다.

> `portfolio.db`의 `trades` 테이블(총 88건, 2026-08-31 기준)을 분석한 결과. 신규 알고리즘의
> 성과를 비교할 **베이스라인(수동 매매 실적)**으로 활용.

### 10-1. 전체 요약

| 지표 | 값 |
|------|-----|
| 전체 거래 | 88건 (청산 85건 / 보유 중 3건) |
| 총 실현손익 | +285,392,432원 |
| 총 매수원가(청산분) | 1,281,355,800원 |
| 가중평균 수익률 | **+22.3%** |
| 승률 | 63.5% (54승 31패) |
| 평균 익절 / 평균 손절 | +6,819,160원(+46.2%) / -2,672,329원(-10.6%) |
| Profit Factor | 4.45 (총이익 ÷ 총손실) |
| 평균 보유기간(중앙값) | 53.5일 (41일) |

승률 대비 손익비가 매우 좋은 구조 — 이기는 폭(+46%)이 지는 폭(-11%)의 4배 이상.

### 10-2. 종목 집중도 — 반도체 2종목이 수익의 92%

| 종목 | 거래수 | 실현손익 | 평균수익률 | 승률 |
|------|-------|---------|-----------|------|
| 삼성전자 | 17 | +165,554,210 | +50.7% | 76.5% |
| SK하이닉스 | 10 | +96,625,025 | +63.4% | 80.0% |
| 그 외 33종목 합계 | 58 | +23,213,197 | - | - |

삼성전자·SK하이닉스 두 종목이 전체 거래의 32%(27/85건)에 불과하지만 총 실현손익의
**91.9%**를 차지 — 향후 알고리즘의 성과를 비교할 때 "삼성전자/SK하이닉스 제외 시 성과"도
별도로 확인해볼 가치가 있음(4-1의 팩터 스코어링이 이 정도 종목 집중을 만들어내는지, 아니면
더 분산되는지가 중요한 비교 포인트).

최근 SK하이닉스 매매(2026-06-26 매수 2,664,000원 → 07-28 매도 1,583,000원, **-40.7%**)는
직전 급등 이후 고점 매수 사례 — 알고리즘이 모멘텀 팩터만으로 이런 추격매수를 걸러내지
못한다면 8-B(레짐 오버레이)가 특히 중요해짐.

### 10-3. 분기별 실현손익 추이

| 분기 | 거래수 | 실현손익 | 승률 |
|------|-------|---------|------|
| 2025 Q1 | 2 | +16,241 | 50% |
| 2025 Q2 | 15 | +6,984,356 | 53% |
| 2025 Q3 | 9 | +723,060 | 67% |
| 2025 Q4 | 11 | +837,989 | 45% |
| 2026 Q1 | 24 | +103,505,300 | **88%** |
| 2026 Q2 | 14 | +63,878,389 | 50% |
| 2026 Q3 (~08월) | 10 | +109,447,097 | 60% |

### 10-4. 보유기간 분포 (청산 85건)

0~3일 2건 · 4~7일 8건 · 8~14일 12건 · 15~30일 12건 · 31~60일 22건 · 61~90일 9건 ·
91~180일 20건 — 단타보다는 수주~수개월 스윙 위주. 4장의 "주간" 리밸런싱 주기와 실제
매매 습관(스윙)이 어느 정도 맞아떨어짐 — 최소 보유기간 제약까지는 필요 없어 보임.

### 10-5. 현재 보유 중 (미실현, 2026-08-31 기준)

삼성전자만 3회 분할매수로 보유 중: 07-31(85주 @247,000) · 08-05(120주 @247,000) ·
08-14(185주 @271,500) — 총 390주, 매수원가 100,877,360원. 현재 보유 종목도 삼성전자
단일 종목.

### 10-6. 데이터 품질 메모 — 해결됨

`sell_date` 컬럼의 0-패딩 불일치는 이후 `fix: normalize buy/sell date input to zero-padded
YYYY-MM-DD` 커밋으로 해결됨(입력 검증 로드맵 항목 반영).

---

## 12. 멀티 에이전트 개발 방법론

> 이 장의 예시는 리밸런싱 전략의 다음 단계(8-A~8-D 등)를 재료로 쓴다. 여기서 인용하는
> 2장·8장·9장·11장 등은 2026-09-17 분리 이후 `src/strategy/rebalance/rebalance.md`의 절이다.

> 이 장은 기존 `multi_agent_guide.md`를 이 문서로 통합한 것이다 — 앞으로 매매/포트폴리오
> 추천 알고리즘 관련 개발은 전부 이 문서 안에서 계획하고 기록한다. 11장의 소스 구조와
> 8장의 다음 단계 목록을 그대로 재료로 써서, "역할을 어떻게 나누고 동시에 어떻게 지시할지"를
> 구체적으로 다룬다.

### 12-1. 배경 — 지금까지 이 프로젝트에서 실제로 있었던 일

| 에이전트 | 실행 위치 | 실행 권한 | 실제로 한 일 |
|---------|----------|-----------|------------------------------|
| Claude (이 세션) | 클라우드 샌드박스 + 파일 브릿지 | 쉘/git 직접 실행 불가 — 파일 읽기·쓰기와 커맨드 안내만 가능 | 문서화(ROADMAP.md/trading.md), 단기 로드맵 코드 구현, git 설정 안내, 매매이력 분석(10장) |
| Antigravity CLI (Sonnet 모델) | 사용자 PC 로컬 쉘 | 파일 시스템·git·pytest 전권 | 3-2 테스트 인프라, 3-1 모듈화(`ui/`/`threads/`/`data/` 분리), **이 문서의 3-1 알고리즘 실제 구현(`data/rebalance.py`, `ui/auto_trading_tab.py`)**, 이후 지속적인 성능/버그 개선 |
| 사용자 | PC | 최종 결정권자 | 제안 검토·승인, git 커맨드 실행, 병합 결정 |

지금까지는 **"한 번에 한 에이전트만 저장소를 만지고 번갈아 작업"**하는 방식이었고, 이
방식이 성립하려면 한쪽이 시작하기 전에 항상 최신 상태를 확인해야 했다(git이 없으면 아예
불가능 — 실제로 git 도입 전엔 "고쳤다는데 반영이 안 됨" 혼란이 있었다). 아래부터는 이를
한 단계 발전시켜 **진짜 동시에 서로 다른 작업을 지시하는 방법**을 다룬다.

### 12-2. 핵심 원칙

- **git 없이는 멀티 에이전트를 하지 않는다.** git이 "지금 상태가 어느 에이전트가 만든
  것인지"를 구분하는 유일한 방법이다.
- **파일 단위가 아니라 브랜치/모듈 단위로 나눈다.** 11장에서 `strategy/rebalance/`를 미리
  패키지화해 두는 이유가 바로 이것 — 모듈 경계가 곧 작업 분할 경계가 되게 만든다.
- **큰 작업은 별도 브랜치 + 단계별 원자적 커밋.**
- **병합 전 검증(테스트)을 강제한다.**
- **원치 않는 결과는 삭제 대신 revert 커밋으로 남긴다.**
- **커밋 메시지에 작업 주체(에이전트/모델)를 남긴다.**
- **이 문서 자체를 에이전트 간 공유 상태판으로 쓴다** — 실제로 `ui/auto_trading_tab.py`가
  이 문서의 섹션 번호를 코드 docstring에 인용하고 있을 정도로 이미 그렇게 쓰이고 있다.

### 12-3. 파일시스템 격리 — `git worktree`

같은 폴더를 두 에이전트가 동시에 건드리면 한쪽이 저장하는 순간 다른 쪽이 읽던 내용과
어긋난다. **진짜 동시 작업의 전제 조건은 각 에이전트가 서로 다른 물리적 폴더에서 일하는
것**이다.

```powershell
cd "D:\Source Code\Portfolio Management"

git worktree add ../PM-agentA -b feat/rebalance-sector-cap      # Agent A (8-A)
git worktree add ../PM-agentB -b feat/rebalance-regime-overlay  # Agent B (8-B)
```

작업이 끝나면:

```powershell
cd "D:\Source Code\Portfolio Management"
git worktree remove ../PM-agentA
git worktree remove ../PM-agentB
```

> 이 세션(Claude)은 사용자 PC에서 쉘 명령을 직접 실행하지 못하므로, worktree 생성 자체는
> 사용자가 위 명령을 실행해야 한다. 이후 `PM-agentB` 폴더를 Claude 데스크톱 앱에서 추가로
> 연결하면 이 세션도 그 폴더를 대상으로 파일 브릿지 작업을 할 수 있다.

### 12-4. 작업 지시서 템플릿

```
[작업 지시서]
목표: <한 문장>
작업 폴더/브랜치: <예: D:\Source Code\PM-agentA, 브랜치 feat/rebalance-sector-cap>
허용 범위(이 파일/디렉토리만 수정): <예: strategy/rebalance/sector.py(신규), tests/strategy/rebalance/test_sector.py>
금지 범위(절대 건드리지 말 것 — 다른 에이전트가 동시에 작업 중): <예: strategy/rebalance/classify.py, ui/auto_trading_tab.py>
완료 기준: <예: pytest 전부 통과, rebalance.md 8장 해당 항목에 근거 남길 것>
완료 후 보고 형식: <예: 커밋 해시, 변경 파일 목록, 테스트 결과>
```

### 12-5. 실전 예시 — 8장 다음 단계를 동시에 배분

8장 표를 그대로 이용한다. **선행 조건: 11-4의 1~4단계(패키지화, `config.py`, `signals.py`
분리)가 master에 병합되어 있어야 아래 배분이 성립한다** — H/I 없이 A/B를 먼저 시키면
결국 `classify.py`에서 둘 다 충돌한다.

- **Agent A (Antigravity CLI, `PM-agentA`, `feat/rebalance-sector-cap`)** — 8-A 섹터 집중도
  제약. 허용 범위: `strategy/rebalance/sector.py`(신규), `tests/strategy/rebalance/test_sector.py`,
  `strategy/rebalance/signals.py`(섹터cap 호출 **한 줄 추가만**, 기존 로직 수정 금지). 금지
  범위: `strategy/rebalance/classify.py`, `ui/`. 참고 자료: 2장의 pykrx
  `get_market_sector_classifications()`, 8-1-A 스펙. 완료 기준: 섹터별 최대 비중 제약 함수
  + pytest.
- **Agent B (Antigravity CLI 두 번째 인스턴스 또는 사용자가 시차를 두고 실행,
  `PM-agentB`, `feat/rebalance-regime-overlay`)** — 8-B VKOSPI 레짐 오버레이. 허용 범위:
  `strategy/rebalance/regime.py`(신규), `tests/strategy/rebalance/test_regime.py`,
  `strategy/rebalance/signals.py`(레짐 호출 **한 줄 추가만**). 금지 범위:
  `strategy/rebalance/classify.py`, `ui/`. 참고 자료: `data/collectors/krx.py`의
  `fetch_vkospi_history()`, 8-1-B 스펙.
- **Agent C (순차, A/B 병합 후 진행)** — 8-C 포지션 사이징 개선. 허용 범위:
  `strategy/rebalance/sizing.py`(신규), `strategy/rebalance/walkforward.py`(alloc 계산 한 줄 교체).
  A/B와 파일이 거의 겹치지 않지만, `signals.py`를 함께 건드릴 수 있어 A/B 병합 이후 시작
  권장.
- **Agent D (언제든 독립적으로)** — 8-D 페이퍼 트레이딩 로그. 허용 범위: `trade_db.py`(신규
  테이블), `ui/auto_trading_tab.py`. A/B/C와 파일이 거의 무관해 아무 때나 진행 가능.

A와 B가 `signals.py`를 동시에 건드리는 것(각자 호출 한 줄)은 12-2의 "파일 단위가 아니라
모듈 단위로 나눈다" 원칙에 대한 유일한 예외다 — 8-2에서 설명한 대로 "로직 수정"이 아니라
"호출 추가"이기 때문에 허용하되, 병합 시 `signals.py`의 diff만은 반드시 사람이 눈으로
확인한다(12-6의 자동 diff 확인만으로 충분하지 않은 유일한 파일).

### 12-6. 병합 프로토콜 — 작업은 병렬, 통합은 직렬

```powershell
cd "D:\Source Code\Portfolio Management"
git checkout master
git merge feat/rebalance-sector-cap        # A 먼저
# pytest 실행 — 통과 확인
git merge feat/rebalance-regime-overlay    # A 반영된 master 기준으로 B
# pytest 다시 실행
```

두 번째 병합 전에는 `git diff master...feat/rebalance-regime-overlay`로 A의 변경과 실제
충돌 여부를 마지막으로 확인한다.

### 12-7. 공용 파일(trading.md, ROADMAP.md, requirements.txt 등) 다루는 법

- 이 문서(trading.md)처럼 "현재 상태"를 기록하는 문서는 각 에이전트가 완료한 뒤, 병합이
  끝난 시점에 한 번만 갱신 — 8장/9장처럼 번호가 붙은 항목 단위로 기록해 두면 "이번엔
  8-A만 체크 표시"처럼 부분 갱신이 쉬워진다.
- 정말 동시에 여러 명이 이 문서에 쓸 일이 생기면, 서로 다른 장(예: 8장은 A가, 10장은 B가)에만
  쓰게 하면 텍스트 충돌이 나더라도 git이 대부분 자동으로 풀어준다.
- `requirements.txt`처럼 항목 추가형 파일은 서로 다른 패키지를 추가하는 한 병합 시 문제
  없음.
- **코드 변경과 문서 갱신을 같은 커밋 사이클로 묶는다.** 12-11에서 실제로 겪었듯,
  `trading.md`는 git 커밋 단위가 아니라 "파일을 직접 읽고 쓰는" 방식으로 다뤄지면 12-2의
  보호를 못 받는다. 코드를 커밋할 때 관련 문서 갱신도 같은 커밋(또는 바로 다음 커밋)에
  포함시킬 것.

### 12-8. 도구별 특성 비교

| | Claude (이 세션) | Antigravity CLI |
|---|---|---|
| 실행 위치 | 클라우드 샌드박스 | 사용자 PC 로컬 |
| 쉘/git 직접 실행 | 불가 | 가능 |
| 동시 작업(worktree) 투입 시 역할 | 별도 연결된 worktree 폴더에서 파일 단위 구현, 브랜치/커밋/테스트는 사용자가 대행 | worktree 생성부터 커밋·테스트·병합까지 스스로 실행 가능 |
| 강점 | 계획 수립, 문서화, 코드 리뷰, 소규모 변경 | 멀티 파일 리팩토링, pytest 실행·반복 수정 루프 |
| 리스크 | 실행을 못 하니 "적용됐다고 착각"하기 쉬움 | 확인 없이 범위를 넓게 바꿀 수 있어 범위 통제 필요 (`--mode=accept-edits`, `toolPermission`, `permissions.allow/deny`로 조절) |

### 12-9. Claude Code 자체의 멀티 에이전트 기능 (대안/보완)

지금까지 12장은 "Claude Code라는 도구 자체엔 없는 기능을 `git worktree` + 수동 지시서로
직접 구현"하는 방법이었다. 그런데 **Claude Code(공식 CLI, 이 세션이나 Antigravity CLI와는
별개의 도구 — 사용자가 로컬에 설치해 쓸 수 있음)는 이런 멀티 에이전트 조율 기능을 이미
내장하고 있다.** 이 프로젝트에서 Claude Code CLI를 쓴다면 12-3/12-4의 수동 절차 상당수를
대체할 수 있으므로 대안으로 기록해 둔다. (2026-09 기준, 공식 문서 확인)

**제공되는 5가지 방식**

| 방식 | 조율 주체 | 이 프로젝트에 맞는 용도 |
|------|-----------|------------------------|
| Subagent | 메인 세션이 설명(description) 매칭으로 위임 | 8장 A~D처럼 담당 범위가 뚜렷한 반복 작업 |
| `--worktree` 플래그 | 사용자가 터미널마다 직접 실행 | 12-3에서 수동으로 하던 `git worktree add`를 CLI가 대신 처리 |
| Agent teams (실험적) | 리드 에이전트 + 팀원들이 공유 태스크리스트로 직접 메시지 주고받음 | A/B가 `signals.py`에 훅을 붙이는 것처럼 약간의 조율이 필요한 작업 |
| Agent view | 백그라운드로 여러 작업 디스패치 후 한 화면에서 모니터링 | 8장 표 전체를 한 번에 던져놓고 진행상황만 확인하고 싶을 때 |
| Dynamic workflows | 스크립트가 다수 서브에이전트를 오케스트레이션(최대 1,000개) | 이 프로젝트 규모(4~5개 작업)엔 과함 — 참고만 |

**8장 예시(A~D)에 적용한다면 — 가장 실용적인 조합**

1. `.claude/agents/` 아래에 서브에이전트 정의 파일을 4개 만든다(예:
   `rebalance-sector.md`, `rebalance-regime.md`, `rebalance-sizing.md`,
   `paper-trading-log.md`). 각 파일의 frontmatter에 `description`으로 담당 범위(8-1의 허용/
   금지 범위 그대로)를 적고, `isolation: worktree`를 켜면 **Claude Code가 알아서 각
   서브에이전트에게 독립된 워크트리를 만들어준다** — 12-3의 `git worktree add`를 손으로
   칠 필요가 없어짐.
2. 메인 Claude Code 세션에서 "8-A와 8-B를 각각 rebalance-sector, rebalance-regime
   서브에이전트에게 동시에 맡겨줘"라고 지시하면, 12-4의 "작업 지시서"에 해당하는 내용이
   `description`/frontmatter로 이미 고정돼 있으므로 매번 다시 타이핑할 필요가 없다.
3. 완전히 독립된 세션으로 띄우고 싶으면 터미널을 나눠 `claude --worktree feat/rebalance-sector-cap`,
   `claude --worktree feat/rebalance-regime-overlay`처럼 각각 실행 — 12-3과 동일한 결과를
   CLI 한 줄로 얻는다.
4. 병합은 여전히 12-6대로 순차적으로 한다 — Claude Code의 어떤 멀티 에이전트 기능도
   "병합은 직렬로" 원칙을 대신해주지 않는다.

**주의**: 이 세션(Cowork)과 지금까지 문서 전반에서 언급한 Antigravity CLI는 Claude Code와
별개의 도구다. Antigravity CLI를 계속 주력으로 쓴다면 12-1~12-9의 수동 절차를 그대로
따르고, 로컬에서 Claude Code CLI도 병행/전환한다면 이 절만 참고해 12-3/12-4를 CLI 기능으로
대체하면 된다 — 두 경우 모두 12-2 핵심 원칙(git 기반, 모듈 단위 분할, 순차 병합)은 동일하게
적용된다.

### 12-11. 실전 사례 1 — 이 문서 자체의 동시 편집 충돌 (2026-09-04 실제 발생)

12장 전체가 이론이 아니라는 걸 보여준 사건이 실제로 있었다 — **`trading.md` 자신이
충돌 사례가 됐다.**

**무슨 일이 있었나**: Antigravity CLI가 11-4의 1~3단계(패키지화, `config.py` 신설)를
실행하고 그 결과를 이 문서에 직접 기록하는 동안, 다른 세션(이 Claude 세션)도 비슷한
시간대에 12-9(Claude Code 멀티 에이전트 기능)를 이 문서에 추가하고 있었다. 파일 브릿지가
"마지막으로 읽은 이후 디스크 내용이 바뀌었다"를 감지해 충돌로 표시했고, 두 변경을 손으로
병합해야 했다(13장 변경이력의 마지막 두 줄이 그 기록).

**왜 발생했나**: `strategy/rebalance/` 안의 코드 파일들은 git 브랜치+커밋으로 관리되어 12-2
("git 없이는 멀티 에이전트를 하지 않는다") 원칙이 정상 작동했다. 하지만 `trading.md`
자체는 두 세션 모두 "파일을 직접 읽고 쓰는" 방식이었지 커밋 단위로 다룬 게 아니었다 —
**문서도 코드처럼 취급하지 않으면 12-2의 보호를 못 받는다**는 걸 실제로 보여준 것.

**교훈 — 12-7에 규칙으로 추가**:
1. 코드 변경과 문서 갱신은 같은 커밋 사이클로 묶는다 — 실제로 11-4 3단계 커밋 기록에도
   `RebalanceConfig` 코드 변경과 trading.md 갱신이 함께 들어갔어야 했다.
2. 여러 에이전트가 동시에 이 문서를 고칠 가능성이 있으면 12-7대로 **장 단위로 담당을
   분리**한다 — 이번 충돌도 서로 다른 장(11장 vs 12장)이었기 때문에 병합 자체는 어렵지
   않았다. 같은 장(예: 둘 다 8장)을 동시에 고쳤다면 훨씬 풀기 어려웠을 것.
3. 충돌이 나면 "누구 버전이 최신이냐"로 한쪽을 버리지 않고 **둘 다 반영**한다 — 이번에도
   12-9를 지우지 않고 11-4 완료 기록을 그 위에 재적용하는 방식으로 처리했다. `force`로
   한쪽만 취하는 방식은 쓰지 않았다.

### 12-12. 실전 사례 2 — 8-A/8-B를 지금 바로 동시 실행하는 전체 워크스루

> 11-4의 1~4단계(패키지화, `config.py`, `signals.py` 분리)가 전부 완료된 상태 — 12-5의
> 선행 조건이 충족됐으므로, 이 절은 그 배분표를 실제로 실행하는 단계별 절차다. 실제
> 파일 경로와 함수 시그니처(2026-09-04 기준 `strategy/rebalance/config.py`/`signals.py`/
> `classify.py` 실제 코드)를 그대로 써서, 복사해서 바로 쓸 수 있게 했다.

**0단계 — 사전 확인**

```powershell
cd "D:\Source Code\Portfolio Management"
git status                     # 미커밋 변경 없는지
git log --oneline -3           # config.py/signals.py 커밋이 master에 있는지 확인
pytest tests/ -q                # 기존 테스트 전부 통과하는 기준선 확보
```

**1단계 — worktree 생성**

```powershell
git worktree add ../PM-agentA -b feat/rebalance-sector-cap
git worktree add ../PM-agentB -b feat/rebalance-regime-overlay
```

**2단계 — Agent A에게 그대로 전달할 프롬프트**

```
[작업 지시서 — 8-A 섹터 집중도 제약]
작업 폴더: D:\Source Code\PM-agentA (브랜치 feat/rebalance-sector-cap)
목표: 리밸런싱 매수 후보가 특정 섹터에 쏠리지 않도록 섹터당 최대 종목수 제약을 추가한다.

1. strategy/rebalance/sector.py 신규 생성:
   - _fetch_sector_map(date: str, market: str) -> dict[str, str]
     pykrx get_market_sector_classifications()를 감싸고, data/cache.py 패턴대로
     일 단위로 캐시할 것.
   - apply_sector_cap(buy_candidates: list, sector_map: dict, max_per_sector: int) -> list
     buy_candidates는 순위(rank)순으로 이미 정렬되어 있다고 가정. 앞에서부터 담되,
     이미 담긴 섹터별 종목 수가 max_per_sector에 도달하면 건너뛰고 다음 순위 종목으로
     대체(greedy). sector_map에 없는 티커는 그대로 통과(섹터 미상 종목까지 막지 않음).

2. strategy/rebalance/signals.py의 compute_weekly_rebalance_signals 안, 아래 줄
     classification = _classify_buy_sell_hold(ranked, current_holdings, top_n_by_market, band_multiplier)
   바로 다음에 이 한 줄만 추가:
     classification["buy_candidates"] = apply_sector_cap(
         classification["buy_candidates"], sector_map, RebalanceConfig().max_per_sector
     )
   이 함수의 다른 로직은 절대 건드리지 말 것.

3. tests/strategy/rebalance/test_sector.py 신규 생성 — apply_sector_cap 단위 테스트
   (특정 섹터가 상위권을 5개 이상 차지하는 가상 후보 리스트를 만들어 max_per_sector=2일
   때 실제로 2개만 남고 다음 순위로 대체되는지 확인).

허용 범위: strategy/rebalance/sector.py(신규), tests/strategy/rebalance/test_sector.py(신규),
          strategy/rebalance/signals.py(2번 항목 한 줄만)
금지 범위: strategy/rebalance/classify.py, ui/, strategy/rebalance/regime.py, strategy/rebalance/sizing.py
완료 기준: pytest tests/ 전부 통과(기존 테스트 + 신규 테스트)
완료 후 보고: 커밋 해시, 변경 파일 목록, pytest 결과 — trading.md는 직접 갱신하지 말 것
             (문서 갱신은 병합 후 5단계에서 한 번에, 12-11의 교훈 적용)
```

**3단계 — Agent B에게 그대로 전달할 프롬프트**

```
[작업 지시서 — 8-B VKOSPI 레짐 오버레이]
작업 폴더: D:\Source Code\PM-agentB (브랜치 feat/rebalance-regime-overlay)
목표: 시장 변동성(VKOSPI)이 급등한 주간에는 신규 매수 후보 수를 줄인다.

1. strategy/rebalance/regime.py 신규 생성:
   - compute_market_regime(vkospi_series) -> dict
     최근 60일 평균/표준편차 대비 현재 값의 z-score를 구해
     {"regime": "normal"|"elevated"|"crisis", "buy_scale": 1.0|0.5|0.0} 반환.
     z < RebalanceConfig().vkospi_elevated_z 이면 normal/1.0,
     elevated_z <= z < crisis_z 이면 elevated/0.5, z >= crisis_z 이면 crisis/0.0.

2. strategy/rebalance/signals.py의 compute_weekly_rebalance_signals 안,
   (Agent A의 한 줄이 먼저 병합되어 있다면 그 다음에) 아래 세 줄만 추가:
     regime = compute_market_regime(fetch_vkospi_history())
     n_keep = int(len(classification["buy_candidates"]) * regime["buy_scale"])
     classification["buy_candidates"] = classification["buy_candidates"][:n_keep]
   sell_candidates/hold는 절대 건드리지 말 것 — 레짐과 무관하게 매도는 항상 허용되어야 함.

3. tests/strategy/rebalance/test_regime.py 신규 생성 — compute_market_regime 단위 테스트
   (VKOSPI가 평시/급등/폭등 수준일 때 각각 normal/elevated/crisis로 분류되는지 확인).

허용 범위: strategy/rebalance/regime.py(신규), tests/strategy/rebalance/test_regime.py(신규),
          strategy/rebalance/signals.py(2번 항목 세 줄만)
금지 범위: strategy/rebalance/classify.py, ui/, strategy/rebalance/sector.py, strategy/rebalance/sizing.py
완료 기준: pytest tests/ 전부 통과
완료 후 보고: 커밋 해시, 변경 파일 목록, pytest 결과
```

**4단계 — 병합 (12-6 그대로, 실제 브랜치명 대입)**

```powershell
cd "D:\Source Code\Portfolio Management"
git checkout master
git merge feat/rebalance-sector-cap
pytest tests/ -q
git diff master...feat/rebalance-regime-overlay -- strategy/rebalance/signals.py
# ↑ A/B가 signals.py에 추가한 줄들이 실제로 안 겹치는지 사람이 눈으로 확인 (12-5의 유일한 예외)
git merge feat/rebalance-regime-overlay
pytest tests/ -q
git worktree remove ../PM-agentA
git worktree remove ../PM-agentB
```

**5단계 — 문서 갱신 (병합이 끝난 뒤 한 번만)**

- 8장 표의 A/B 행에 ✅ 표시, 9장 체크리스트의 해당 항목 체크
- 13장 변경이력에 실제 커밋 해시와 함께 한 줄 추가

이 워크스루는 8-C(포지션 사이징)에도 그대로 재사용 가능하다 — 차이는 8-C는 A/B 병합 후에
시작해야 한다는 점뿐이다(8장 표 참고).

### 12-13. 체크리스트

**시작 전**: `git status` 확인 · 필요하면 worktree 분리(12-3) · 8장 표에서 파일이 겹치지
않는 항목만 동시 배분 · 12-4 템플릿으로 지시서 작성

**작업 중**: 단계별 원자적 커밋 유도 · 범위 확장 감지 시 즉시 개입 · 금지 범위 침범 조짐
보이면 중단

**병합 전**: 테스트 실행·기록 · `git diff`로 계획 대비 범위 확인 · 12-6대로 순차 병합 ·
원치 않는 결과는 revert 커밋 · 커밋 메시지에 작업 주체 남기기 · 이 문서(8·9장) 갱신

---

---

## 13. 변경 이력

> 2026-09-17 이전의 변경 이력(리밸런싱 알고리즘 관련)은 `src/strategy/rebalance/rebalance.md` 13장에 있다.

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-09-17 | 리밸런싱 전략 스펙(1~9장·11장·13장 변경 이력)을 `src/strategy/rebalance/rebalance.md`로 분리 이동(사용자 요청). 이 문서에는 10장(베이스라인)·12장(멀티 에이전트 개발 방법론)만 남기고 절 번호는 유지. |
