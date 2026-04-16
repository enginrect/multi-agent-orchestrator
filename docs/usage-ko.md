# morch 사용 가이드 (한국어)

이 문서는 **morch**(multi-agent orchestrator) CLI의 설치부터 일상적인 워크플로까지 한 번에 따라 할 수 있도록 정리한 **완전한 한국어 사용자 안내**입니다.  
기본 CLI 이름은 `morch`이며, `orchestrator`는 동일한 진입점의 별칭입니다.

---

## 1. morch란?

**morch**는 **Cursor**, **Claude**, **Codex** 세 에이전트를 묶어 **멀티 에이전트 코드 리뷰 워크플로**를 돌리기 위한 **오케스트레이터 CLI**입니다.

- **역할**: 리뷰 단계(구현 → 1차 리뷰 → 최종 리뷰 등)를 **명시적인 상태 머신**으로 추적하고, 필요 시 **수동 개입(에스컬레이션)**으로 넘깁니다.
- **협업 모델**: 에이전트는 서로 다른 계정·머신에서 동작할 수 있으며, 핵심은 **파일(아티팩트) 기반**으로 작업을 이어 받는다는 점입니다.
- **지원 흐름**:
  - **프롬프트 기반**(로컬 마크다운)
  - **파일 아티팩트 파이프라인**(로컬 구조화 리뷰)
  - **GitHub 네이티브**(이슈·브랜치·PR 중심)

자세한 아티팩트 순서와 상태 전이는 `docs/workflow.md`, GitHub 측면은 `docs/github-workflow.md`, 명령 전체는 `docs/morch.md`를 참고하세요.

---

## 2. 설치

저장소를 클론한 뒤 가상 환경을 만들고 개발 의존성까지 포함해 설치합니다.

```bash
git clone https://github.com/enginrect/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

- **요구 사항**: Python 3.11 이상 (`pyproject.toml` 기준).
- 설치 후 `morch --help` 또는 `orchestrator --help`로 진입점이 보이면 성공입니다.

---

## 3. 설정: `morch setup`

에이전트 CLI(**cursor**, **claude**, **codex**)를 자동으로 찾고, 필요하면 **대화형으로 실행 파일 경로**를 물어봅니다.

| 동작 | 설명 |
|------|------|
| 자동 감지 | `PATH`에서 `cursor`, `claude`, `codex` 바이너리 탐색 |
| 사용자 지정 경로 | 찾지 못한 경우, 대화형 모드에서 **파일 경로** 입력 가능 |
| 저장 위치 | 사용자 홈의 **`~/.morch/config.yaml`**에 `agent_paths` 등으로 저장 |

실행 예:

```bash
morch setup
```

- 이미 설치된 도구는 버전·인증 상태 요약이 출력됩니다.
- **찾지 못한** 도구는 설치 힌트(공식 설치 안내)가 나오고, 프롬프트에서 바이너리 **절대 경로**를 넣을 수 있습니다(파일이 실제로 존재할 때만 반영).
- 마지막에 `Configuration saved to: ~/.morch/config.yaml` 형태로 저장 경로가 표시됩니다.

> **참고**: 프로젝트의 워크스페이스·템플릿·`max_cycles` 등 **런타임 동작 설정**은 보통 `configs/*.yaml`과 `--config`로 주며, `morch config show`로 **최종적으로 어떤 값이 적용되는지** 확인할 수 있습니다.

---

## 4. 인증: `morch auth status`, `morch auth <도구> login`

GitHub·Git 작업과 에이전트 CLI가 실제로 사용 가능한지 확인합니다.

### 전체 상태

```bash
morch auth status
```

### 도구별 상태

```bash
morch auth cursor status
morch auth claude status
morch auth codex status
morch auth github status
morch auth git status
```

### 로그인 안내 보기

CLI는 “로그인을 대신 수행”하기보다, **어떤 명령을 실행하면 되는지** 안내합니다.

```bash
morch auth claude login
morch auth github login
```

- **Claude**: 일반적으로 `claude auth status` 등으로 인증 여부를 확인합니다.
- **Codex**: 환경 변수 `OPENAI_API_KEY` 또는 `~/.codex/auth.json` 등으로 인증 상태를 추정합니다.
- **Cursor**: 데스크톱 앱 쪽 인증으로 관리되는 경우가 많습니다.
- **GitHub 워크플로**: `gh` CLI 권한과 저장소 접근이 중요합니다(`gh repo view owner/repo` 등으로 사전 확인 권장).

---

## 5. 상태 확인: `morch doctor`, `morch agents doctor`

### 시스템 전반

```bash
morch doctor
```

- 필수 도구 설치·인증 요약
- 설정에 따른 **에이전트 순서**와 역할(첫 번째 = 구현자, 이후 = 리뷰어)
- `agents.enabled` 검증 오류가 있으면 함께 표시

### 설정된 에이전트만

```bash
morch agents doctor
```

각 에이전트가 “준비됨 / 설치됨 / 누락”인지와 수정 힌트를 출력합니다.

---

## 6. 환경 설정: `morch config show`

현재 로드되는 설정의 **실효 값**을 출력합니다.

```bash
morch config show
# 또는
morch --config configs/default.yaml config show
```

### 6.1 설정 파일 형식 (YAML)

기본 예시는 `configs/default.yaml`입니다.

```yaml
workspace_dir: ./workspace
template_dir: ./templates/artifacts
max_cycles: 2
default_target_repo: ""

agents:
  enabled: [cursor, claude, codex]
```

| 항목 | 의미 |
|------|------|
| `workspace_dir` | 활성/보관 작업 디렉터리 루트 |
| `template_dir` | 아티팩트 템플릿 경로 |
| `max_cycles` | 최대 리뷰 사이클(초과 시 에스컬레이션 등) |
| `default_target_repo` | `--target-repo` 생략 시 기본 대상 경로 |
| `agents.enabled` | **에이전트 순서**(아래 참고) |

### 6.2 에이전트 순서

- **지원 에이전트**: `cursor`, `claude`, `codex`만 (2~3명).
- **첫 번째** = 구현(implementation), **나머지** = 리뷰 순서.
- 순서만 바꿔 보고 싶을 때:

```bash
morch agents order cursor claude codex
morch agents order claude codex
```

출력된 YAML 조각을 설정 파일에 붙여 넣어 **영구 반영**합니다.

### 6.3 GitHub 설정

```yaml
github:
  repo: "owner/repo-name"
  base_branch: "main"
  branch_pattern: "{type}/issue-{issue}/{agent}/cycle-{cycle}"
  pr_title_pattern: "[{type}][Issue #{issue}][{agent}] {summary}"
  labels:
    claimed: "orchestrator:claimed"
    in_progress: "orchestrator:in-progress"
    review: "orchestrator:review"
    approved: "orchestrator:approved"
  local_repo_path: ""
```

- CLI에서 `--repo`를 주면 설정의 `github.repo`를 덮어쓸 수 있습니다.
- `local_repo_path`는 로컬 클론 경로를 고정할 때 사용합니다(비어 있으면 현재 작업 디렉터리 등 규칙에 따름).

### 6.4 어댑터 설정

`adapters`를 **생략**하면, 활성화된 에이전트에 대해 **기본 CLI 어댑터**가 자동 생성됩니다. 수동/스텁/타임아웃 조정이 필요하면 명시합니다.

```yaml
adapters:
  cursor:
    type: cursor-cli
    settings:
      timeout: 600
  claude:
    type: claude-cli
    settings:
      timeout: 300
  codex:
    type: codex-cli
    settings:
      timeout: 600
```

예시 파일: `configs/adapters-stub.yaml`, `configs/adapters-mixed.yaml`, `configs/adapters-real.yaml`. 상세는 `docs/adapters.md`.

### 6.5 환경 변수: `MORCH_LOG_LEVEL`

애플리케이션 로깅은 `orchestrator` 로거 네임스페이스를 사용하며, 레벨은 환경 변수로 조정합니다.

| 변수 | 설명 |
|------|------|
| `MORCH_LOG_LEVEL` | 예: `DEBUG`, `INFO`, `WARNING`, `ERROR`. 기본값은 `INFO`. |

로그 파일 기본 위치: **`~/.morch/logs/morch.log`** (콘솔은 상대적으로 덜 시끄럽게, 파일에 상세 기록).

```bash
export MORCH_LOG_LEVEL=DEBUG
morch doctor
```

---

## 7. 로컬 워크플로: 파일 아티팩트 파이프라인

**한 디렉터리에 마크다운 아티팩트를 쌓으며** Cursor → Claude → Codex 순으로 진행합니다. 상태는 `workspace/active/<작업명>/` 아래 파일로 관리됩니다.

### 한 번에 실행(권장)

```bash
morch --config configs/adapters-mixed.yaml run task my-feature \
  --target-repo /path/to/target/repo \
  --description "짧은 설명"
```

### 수동 단계별

```bash
morch task init my-feature --target-repo /path/to/repo
# 00-scope.md 편집 → 구현 → 01-cursor-implementation.md 작성
morch task advance my-feature
# … 리뷰 아티팩트 작성 후 advance 반복
morch task validate my-feature
morch task archive my-feature   # 승인 후 보관
```

### 재개·관찰

```bash
morch status task my-feature
morch resume task my-feature
morch watch task my-feature
morch task list
```

사이클 1·2에서 요구되는 파일 이름과 역할은 `docs/workflow.md`의 표를 따릅니다.

---

## 8. GitHub 네이티브 워크플로

이슈를 클레임하고, 브랜치·PR·리뷰를 통해 진행합니다. **팀에 보이는** 작업에 적합합니다.

```bash
morch run github 42 --repo owner/name --type feat
```

- `--prompt-file`로 상세 지시를 붙일 수 있습니다.
- 로컬 클론은 `--local-repo` 또는 설정의 `github.local_repo_path`로 지정합니다.

재개·상태:

```bash
morch resume github issue-42
morch status github issue-42 --repo owner/name
morch watch task issue-42
```

---

## 9. 프롬프트 파일

이슈 제목/본문만으로는 부족할 때, **마크다운 프롬프트 파일**을 “진짜 요구사항”으로 씁니다.

| 경로 | Git 추적 | 용도 |
|------|----------|------|
| `.morch/prompts/` | 보통 무시(로컬 작업) | 사용자 편집 프롬프트 |
| `templates/prompts/` | 저장소에 포함 | 배포용 템플릿 |

### 템플릿 복사

```bash
morch prompt list-templates
morch prompt init smoke-test --output .morch/prompts/smoke.md
```

### 실행 시 첨부

```bash
morch run github 42 --repo owner/name --prompt-file .morch/prompts/issue-42.md
morch issue start --repo owner/name --title "제목" --prompt-file .morch/prompts/task.md
```

프롬프트 내용은 작업 디렉터리에 `prompt.md` 등으로 남아 감사 추적에 도움이 됩니다.

### 프롬프트만으로 로컬 실행

```bash
morch run prompt ./요구사항.md --target-repo /path/to/repo
```

---

## 10. 이슈 플로: 생성, 목록, 조회, 재오픈, 시작

| 목적 | 명령 |
|------|------|
| 이슈 생성 | `morch issue create --repo owner/name --title "제목" --body "본문"` |
| 프롬프트 포함 생성 | `morch issue create ... --prompt-file .morch/prompts/x.md` |
| 목록 | `morch issue list --repo owner/name [--state open\|closed\|all]` |
| 상세 | `morch issue view 42 --repo owner/name` |
| 재오픈 | `morch issue reopen 42 --repo owner/name` |
| 생성 직후 워크플로 시작 | `morch issue start --repo owner/name --title "..." [--prompt-file ...] [--type feat\|test 등]` |

`--repo`는 설정 파일의 `github.repo`로 대체할 수 있습니다.

---

## 11. 리뷰 플로: 사이클, 최대 사이클, 에스컬레이션

- **사이클**: Codex가 변경을 요청하면 Cursor·Claude 응답 아티팩트를 거쳐 **다음 사이클**로 진행합니다. 파일 이름은 `docs/workflow.md`의 Cycle 1 / Cycle 2 표를 따릅니다.
- **`max_cycles`**: 설정의 최대 사이클 수(기본 예: `2`). 한도를 넘기면 **에스컬레이션**되어 사람이 개입해야 합니다.
- **리뷰 결과 키워드**: 아티팩트의 `**Status**:` 등으로 `approved`, `changes-requested`, `minor-fixes-applied`를 구분합니다.

로컬에서 상태만 밀고 싶을 때:

```bash
morch task advance my-task --outcome approved
```

(자동 파싱과 함께 쓰일 수 있음.)

---

## 12. 머지·릴리스 동작: 사람 게이트 머지 정책

- **자동 머지 없음**: morch는 **기본 브랜치로의 머지를 자동 수행하지 않습니다.** 파이프라인은 승인·검증 단계까지를 다루고, **머지 시점과 여부는 사람이 결정**합니다.
- **셀프호스팅·사내 GitHub Enterprise**: `gh` 인증 대상 호스트만 다를 뿐, **머지 게이트 정책(사람 승인)**은 동일하게 적용하는 것을 권장합니다. 조직 정책에 따라 PR 머지는 CI, 코드 오너, 관리자 규칙 등 **GitHub 쪽 설정**에서 최종 확정됩니다.
- **릴리스**: morch 자체는 릴리스 태깅 도구가 아니라, **리뷰·PR 라이프사이클 조율**에 집중합니다. 배포는 별도 파이프라인을 사용하세요.

---

## 13. 문제 해결

| 증상 | 조치 |
|------|------|
| 에이전트 CLI를 찾을 수 없음 | `morch setup`으로 경로 저장, `morch auth <도구> status`로 확인 |
| 인증 실패 | `morch auth status`, `morch auth <도구> login` 안내에 따라 로그인·토큰·API 키 확인 |
| 타임아웃·리소스 | `~/.morch/logs/morch.log`에서 원인 추적, `adapters.*.settings.timeout` 상향 |
| API 속도 제한 | 일시 중지 후 **지연(backoff)**을 두고 재시도, 프롬프트/작업을 작은 단위로 분할 |
| GitHub “repo not found” | `gh repo view owner/name`, 권한·조직 SSO |
| 브랜치 생성 실패 | 기본 브랜치 이름·권한·로컬 클론 깨끗한지 확인 |

---

## 14. 예제: 자주 쓰는 워크플로

### 14.1 처음 설치 후 점검

```bash
source .venv/bin/activate
morch setup
morch doctor
morch auth status
```

### 14.2 로컬 파일 파이프라인(실제 CLI 혼합 설정)

```bash
morch --config configs/adapters-mixed.yaml run task feat-login \
  --target-repo ~/src/my-app \
  --description "OAuth 로그인 추가"
morch watch task feat-login
```

### 14.3 GitHub 이슈에서 바로 시작

```bash
morch prompt init github-issue-task --output .morch/prompts/login.md
# login.md 편집 후
morch issue start --repo myorg/my-app --title "[feat] OAuth 로그인" \
  --prompt-file .morch/prompts/login.md --type feat
```

### 14.4 기존 이슈에 연결

```bash
morch run github 123 --repo myorg/my-app --prompt-file .morch/prompts/issue-123.md
morch status github issue-123 --repo myorg/my-app
```

---

## 부록: 호환 별칭

레거시 스크립트 호환을 위해 `orchestrator` 명령과 일부 숨겨진 하위 명령이 남아 있습니다. 새 스크립트는 **`morch` + 중첩 서브커맨드**(`run task`, `resume task`, `status task` 등)를 권장합니다.

---

## 더 읽을 거리

- `README.md` — 개요
- `docs/architecture.md` — 구조
- `docs/workflow.md` — 아티팩트·사이클
- `docs/morch.md` — 명령·어댑터·설정 예시
- `docs/github-workflow.md` — GitHub 상세
- `docs/adapters.md` — 어댑터 레퍼런스
