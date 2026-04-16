# morch 멀티 에이전트 오케스트레이터 기술 사양서

**문서 버전**: 1.0 (코드 기준: `multi-agent-orchestrator` v0.2.0)  
**대상 독자**: 아키텍처 검토, 온보딩, 운영·보안 검토 담당자  
**CLI 이름**: `morch` (`pyproject.toml`의 `project.scripts`에 정의)

---

## 1. 개요

`morch`(multi-agent orchestrator)는 **파일 기반** 또는 **GitHub 네이티브** 방식으로 Cursor, Claude, Codex 세 에이전트의 리뷰 파이프라인을 조율하는 도구이다. 데이터베이스 없이 디스크의 YAML·마크다운 아티팩트와 명시적 상태 머신으로 동작하며, 어댑터 패턴으로 에이전트 호출 방식(수동·CLI·스텁)을 분리한다.

**설계 원칙 요약**

| 원칙 | 설명 |
|------|------|
| 파일 기반 상태 | 태스크 상태는 `state.yaml` 등이 단일 진실 공급원(SSOT) |
| 이중 상태 | `TaskState`(워크플로 단계)와 `RunStatus`(실행 엔진 상태)는 직교 |
| 도메인 순수성 | `domain/`은 I/O 없음 |
| 어댑터 역량 | `AdapterCapability`에 따라 `RunOrchestrator` 루프 동작이 달라짐 |

---

## 2. 아키텍처 — 도메인 주도 설계 4계층

소스 루트는 `src/orchestrator/`이며, 의존성 방향은 **CLI → application → (domain + infrastructure + adapters)** 이다. 도메인은 인프라에 의존하지 않는다.

### 2.1 `domain/` — 순수 로직

외부 I/O·네트워크·파일 시스템에 의존하지 않는다.

**핵심 모델** (`models.py` 등)

| 유형 | 설명 |
|------|------|
| `Task` | 집합 루트, YAML 직렬화 |
| `AgentRole` | `cursor`, `claude`, `codex` |
| `TaskState` | 파일 아티팩트 워크플로의 단계(아래 §4 참조) |
| `RunStatus` | 실행 엔진 상태(아래 §4 참조) |
| `ReviewOutcome`, `ArtifactSpec`, `StateTransition` | 리뷰 결과·아티팩트 명세·전이 기록 |
| `AdapterCapability` | `manual` / `semi_auto` / `automatic` |
| `ExecutionStatus`, `ExecutionResult` | 어댑터 1회 호출 결과 |

**상태 머신** (`state_machine.py`)

- `{현재_상태: [허용_다음_상태들]}` 형태의 전이표
- `validate_transition()` — 미선언 전이 시 `InvalidTransitionError`

**워크플로 정의** (`workflow.py`)

- 사이클 1·2 아티팩트 카탈로그
- `resolve_next_step()` — 현재 상태·사이클·아티팩트 존재 여부로 다음 조치 결정

**GitHub 전용 도메인** (`github_models.py`, `github_workflow.py`)

- `GitHubTaskState`, `GitHubTask`, `WORK_TYPE_LABELS`, `validate_github_transition()` 등
- `resolve_github_next_step()` — 이슈·PR·리뷰 단계에 따른 다음 에이전트·액션

**에러** (`errors.py`)

- 공통: `OrchestratorError`, `InvalidTransitionError`, `TaskNotFoundError`, `TaskAlreadyExistsError`, `ArtifactMissingError`, `MaxCyclesExceededError`, `WorkflowConfigError`
- **리소스 제한 계층** (v0.2.0):
  - 기준: `AgentResourceLimitError` — 토큰·레이트·쿼터 등 한도 초과의 상위 클래스
  - 하위: `AgentTokenLimitError`, `AgentRateLimitError`, `AgentQuotaLimitError`, `AgentProviderRefusalError`
- `classify_resource_error(agent, stderr, exit_code) -> Optional[AgentResourceLimitError]` — CLI `stderr`와 종료 코드를 패턴 매칭해 상기 예외로 분류; 매칭 없으면 `None`

**프로비넌스** (`provenance.py`)

- GitHub에 노출되는 이슈 댓글·PR 본문·리뷰 본문에 **논리적 에이전트 신원**을 명시하는 문자열·템플릿 함수 모음 (§7)

---

### 2.2 `application/` — 유스케이스

| 컴포넌트 | 역할 |
|----------|------|
| `TaskService` | 파일 아티팩트 태스크 생명주기: 초기화, `advance`, 아카이브, 목록, 다음 단계 조회 |
| `GitHubTaskService` | GitHub 네이티브 태스크의 생성·상태 갱신·저장소 연동 조율 |
| `ArtifactService` | 워크플로 스펙 대비 아티팩트 존재 검증, 리뷰 아티팩트의 `**Status**:` 파싱 |
| `RunOrchestrator` | 파일 아티팩트 파이프라인 **단일 명령 실행 루프**: 다음 단계 해석 → 어댑터 호출 → COMPLETED/WAITING/FAILED 처리 |
| `GitHubRunOrchestrator` | GitHub 이슈·브랜치·PR·리뷰 흐름에 맞춘 실행 루프 |
| `PromptRunner` | 마크다운 프롬프트를 에이전트 순서대로 통과시키는 실행 (§6) |
| `WorkflowEngine` | 레거시 단일 스텝 조율·`next` 등 명령용 지시 생성 |

---

### 2.3 `infrastructure/` — I/O 및 외부 연동

| 컴포넌트 | 역할 |
|----------|------|
| `FileStateStore` | 태스크별 `state.yaml` 읽기/쓰기, `workspace/active/`·`archive/` 관리 |
| `ConfigLoader` | YAML에서 `OrchestratorConfig` 로드, `adapters` 등 |
| `AuthChecker` | 에이전트 CLI·도구 인증 상태 점검 (`morch auth` 등과 연계) |
| `RunLogger` | 태스크 디렉터리에 **JSONL** append-only `run.log` — 감사·디버깅 |
| `GitHubService` | GitHub API 래퍼(이슈·PR·리뷰·댓글 등) |
| `TemplateRenderer` | `templates/artifacts/` 마크다운 템플릿 로드·치환 |
| `SetupService` **(v0.2.0)** | `morch setup`, PATH·커스텀 경로에서 에이전트 바이너리 탐지, `~/.morch/config.yaml` 저장 |
| `Logger` **(v0.2.0)** | Python `logging` 기반 구조화 로그, `MORCH_LOG_LEVEL`, `~/.morch/logs/morch.log` (§9) |

---

### 2.4 `adapters/` — 에이전트 인터페이스

모든 어댑터는 `AgentAdapter`를 구현한다: `name`, `capability`, `execute()`, `health_check()`.

| 클래스 | 설정 키 (`type`) | 요약 |
|--------|------------------|------|
| `ManualAdapter` | `manual` | 지시문·템플릿 생성 후 `WAITING` |
| `StubAdapter` | `stub` | 테스트용 자동 완료 |
| `CommandAdapter` | `command` | 임의 외부 명령 subprocess |
| `CursorCommandAdapter` | `cursor-cli` | Cursor CLI; 명령 미설정 시 사실상 수동 대기 |
| `ClaudeCommandAdapter` | `claude-cli` | Claude CLI + 프롬프트 |
| `CodexCommandAdapter` | `codex-cli` | Codex CLI + 프롬프트 |

팩토리: `adapters/factory.py`의 `create_adapter()`, `create_adapters_from_config()`, `create_default_adapters()`.

---

## 3. 워크플로 모델

morch는 **세 가지 관점**의 워크플로를 구분한다.

### 3.1 파일 아티팩트 워크플로

태스크 디렉터리에 마크다운 아티팩트를 누적하며 진행한다.

**대표 흐름**

```
태스크 초기화 → 범위 정의(00-scope) → 구현(01) → Claude 리뷰(02)
→ (필요 시) Cursor 재작업(03) → Codex 최종 리뷰(04) → 승인 시 최종 승인(05)
→ 아카이브
```

- 사이클 2에서는 `06`–`09` 아티팩트로 동일 패턴 반복 가능(최대 2사이클, 초과 시 `escalated`).
- 상태 전이는 `TaskState` + `state_machine`으로 강제된다.

### 3.2 GitHub 네이티브 워크플로

저장소의 **이슈·브랜치·PR·리뷰**가 1차 산출물이다.

**대표 흐름**

```
이슈 클레임/연동 → 브랜치에서 Cursor 구현 → PR 오픈
→ Claude PR 리뷰 → (필요 시) Cursor 재작업 루프
→ Codex 최종 리뷰 → 승인(APPROVED) → 머지(MERGED) 또는 에스컬레이션(ESCALATED)
```

상세는 §5.

### 3.3 프롬프트 기반 워크플로

- 입력: **단일 마크다운 파일**(초기 요구사항·지시).
- `PromptRunner`가 설정된 에이전트 순서대로 어댑터를 호출한다.
- 첫 에이전트는 마크다운 내용을 지시로 받고, 이후 에이전트는 앞 단계 출력을 검토하는 패턴에 가깝다.
- CLI: `morch run prompt <path.md>` (§6).

---

## 4. 어댑터 — 에이전트 호출 방식

### 4.1 역량(`AdapterCapability`)과 실행 루프

| 역량 | RunOrchestrator 동작 요약 |
|------|---------------------------|
| `automatic` | 호출 → 성공 시 전진 → 루프 계속 |
| `semi_auto` | 호출 후 `COMPLETED` 또는 `WAITING` 가능 |
| `manual` | 지시 생성 후 `WAITING`; 사람이 아티팩트 완성 후 `resume` |

에이전트별 어댑터가 없으면 `fallback_adapter`(보통 manual)로 대체하고, 폴백도 없으면 실행이 `suspended` 될 수 있다.

### 4.2 설정 예시 (개념)

```yaml
adapters:
  cursor:
    type: cursor-cli
    settings:
      command: null   # 미설정 시 수동에 가깝게 동작
      timeout: 600
  claude:
    type: claude-cli
    settings: {}
  codex:
    type: codex-cli
    settings: {}
```

`adapters` 섹션이 없으면 `create_default_adapters()`로 Cursor/Claude/Codex CLI 어댑터를 기본 생성할 수 있다.

---

## 5. 태스크 상태 모델

### 5.1 파일 아티팩트: `TaskState`

| 값 | 의미 |
|----|------|
| `initialized` | 태스크 생성 직후 |
| `cursor_implementing` | Cursor 구현 단계 |
| `claude_reviewing` | Claude 리뷰 |
| `cursor_reworking` | Claude 변경 요청에 따른 재작업 |
| `codex_reviewing` | Codex 최종 리뷰 |
| `approved` | 승인됨 |
| `escalated` | 최대 사이클 등으로 인한 인간 에스컬레이션 |
| `archived` | 아카이브됨 |

### 5.2 실행 엔진: `RunStatus`

`TaskState`와 **직교**한다. 실행 루프가 “지금 무엇을 하고 있는가”를 나타낸다.

| 값 | 의미 |
|----|------|
| `idle` | 실행 없음 |
| `running` | 루프 실행 중 |
| `waiting_on_cursor` | Cursor 단계 대기(수동 등) |
| `waiting_on_claude` | Claude 단계 대기 |
| `waiting_on_codex` | Codex 단계 대기 |
| `completed` | 실행 완료(종료 상태 도달) |
| `suspended` | 오류·어댑터 부재 등으로 중단 |

### 5.3 GitHub 네이티브: `GitHubTaskState`

| 값 | 의미 |
|----|------|
| `issue_claimed` | 이슈에 워크플로 연결됨 |
| `cursor_implementing` | 브랜치에서 구현 중 |
| `pr_opened` | PR 생성됨 |
| `claude_reviewing` | Claude가 PR 리뷰 |
| `cursor_reworking` | 리뷰 피드백 반영 |
| `codex_reviewing` | Codex 최종 리뷰 |
| `approved` | PR 승인 처리됨(워크플로 관점) |
| `escalated` | 인간 개입 필요 |
| `merged` | 머지 완료 |

전이는 `GITHUB_TRANSITIONS`와 `validate_github_transition()`으로 검증한다.

---

## 6. GitHub 네이티브 워크플로 상세

### 6.1 단계와 책임

1. **이슈** — 요구사항·범위의 앵커; morch는 이슈 번호·제목·레이블(작업 유형 등)과 연동할 수 있다.
2. **브랜치** — Cursor가 구현; 명명 규칙은 `github_workflow`의 헬퍼(이슈 번호·작업 유형·사이클 등)에 따른다.
3. **PR** — 구현 결과물의 통합 뷰; 본문에 프로비넌스 블록이 붙을 수 있다(§7).
4. **리뷰 사이클** — Claude가 1차 PR 리뷰, Codex가 최종 리뷰; 변경 요청 시 Cursor 재작업 후 다시 Codex(및 필요 시 Claude)로 회귀.
5. **승인·머지** — 저장소 정책과 §8 머지 정책을 따른다.

### 6.2 `resolve_github_next_step()` 개념

상태별로 `GitHubNextStep`이 반환된다: 담당 `AgentRole`, `action`(예: `implement`, `open_pr`, `review_pr`, `rework`, `final_review`), 사람이 읽을 `instruction`, 전이 후 `state_after`.

### 6.3 오케스트레이터와 GitHub 서비스

- `GitHubRunOrchestrator`가 단계를 진행시키고, `GitHubService`가 API 호출을 담당한다.
- Codex 등이 샌드박스에서 GitHub에 직접 리뷰를 올리지 못하는 경우, 오케스트레이터가 `provenance.comment_relayed_review()` 등으로 대리 게시할 수 있다.

---

## 7. 프롬프트 기반 워크플로

| 항목 | 내용 |
|------|------|
| 입력 | 로컬 마크다운 파일 경로 |
| 실행기 | `PromptRunner` |
| 동작 | `AgentsConfig`의 에이전트 순서에 따라 어댑터 파이프라인 실행; 단계별 `PromptStepRecord` 누적 |
| 결과 | `PromptRunResult` — `run_status`, `waiting_on`, 각 스텝의 `ExecutionStatus` |
| 보조 | 태스크 이름 자동 생성·`RunLogger` 연동, `user_hints`로 재개 안내 등 |

파일 아티팩트 워크플로의 고정된 `00`–`09` 파일명 없이, **단일 프롬프트에서 빠르게 에이전트 체인을 시험**할 때 유리하다.

---

## 8. 프로비넌스 모델 — 에이전트 신원 귀속

GitHub에 보이는 모든 텍스트(이슈 타임라인, PR 본문, 리뷰 본문, 폴백 댓글)에서 **동일 OS/토큰을 쓰더라도 논리적 역할이 구분**되도록 한다.

- `AGENT_IDENTITIES`: Orchestrator, Cursor, Claude, Codex에 대한 표시 이름과 `@handle` 스타일 식별자.
- `agent_sig(key)` — 마크다운에 삽입할 서명 문자열.
- 함수 예: `comment_issue_claimed`, `comment_pr_opened`, `comment_review_started`, `comment_review_completed`, `comment_rework_requested`, `comment_approved`, `comment_fallback_review`, `comment_relayed_review`, `pr_body_block`, `review_header`, `fix_commit_prefix`.

**목적**: 감사 가능성, 혼동 방지, “누가 무엇을 말했는가”에 대한 일관된 귀속.

---

## 9. 머지 정책

| 구분 | 정책 |
|------|------|
| 기본 | **머지는 사람이 결정**한다. morch는 PR 승인·워크플로 완료 **상태**까지를 다루며, 프로덕션 브랜치에 대한 최종 머지는 조직의 GitHub 보호 규칙·승인 정책에 따른다. |
| 예외 | **자체 호스팅(self-hosted) morch** 환경에서만 자동 머지·봇 머지 등을 허용할 수 있다. 이 경우에도 브랜치 보호·필수 검사·감사 로그 요구사항을 별도로 정의해야 한다. |

`provenance.comment_approved()` 문구에도 “Ready for **human merge**”에 해당하는 표현이 포함된다.

---

## 10. v0.2.0 신규 기능

### 10.1 설정 플로: `morch setup`

- **명령**: `morch setup` — 대화형 에이전트 경로 설정.
- **자동 감지**: `SetupService`가 `cursor` / `claude` / `codex` 바이너리를 PATH(및 `~/.morch/config.yaml`의 `agent_paths`)에서 찾는다.
- **지속화**: `~/.morch/config.yaml`에 `agent_paths` 저장.

### 10.2 에이전트 자동 감지

- PATH 스캔 및 사용자 지정 경로.
- `--version` 유사 호출로 **버전 문자열** 확인.
- **인증 검증**: 도구별로 로그인·토큰 상태를 점검하고 메시지를 남긴다(`AgentDetectionResult.authenticated` 등).

### 10.3 구조화된 로깅

- Python `logging` — 루트 로거 이름 `orchestrator`, 콘솔(주로 WARNING 이상) + 파일.
- **환경 변수**: `MORCH_LOG_LEVEL` (기본 `INFO`).
- 태스크 단위 **JSONL**: `RunLogger` → `<task-dir>/run.log`.
- 애플리케이션 로그 파일: `~/.morch/logs/morch.log` (상세 포맷에 agent/phase 필드).

### 10.4 리소스 제한 처리

- 도메인 예외 계층: `AgentResourceLimitError` 및 토큰·레이트·쿼타·프로바이더 거부 하위 클래스.
- `classify_resource_error()` — subprocess `stderr` 패턴과 HTTP 유사 코드(429, 503)로 분류해 상위에서 재시도·백오프 UI에 활용 가능.

### 10.5 이중 언어 문서

- 영문 아키텍처·워크플로 문서(`docs/architecture.md`, `docs/workflow.md` 등)와 본 한국어 기술 사양을 병행해 유지한다.

---

## 11. 알려진 제한사항

| 영역 | 제한 |
|------|------|
| 확장성 | 중앙 DB 없음; 대규모 동시 태스크·다중 머신 조율은 파일 락·외부 시스템으로 보완 필요 |
| GitHub API | 속도 제한·권한 범위·조직 정책에 의존; 대규모 리포지토리에서 클론·diff 비용 발생 |
| 에이전트 CLI | 버전·플래그 차이로 `classify_resource_error()`가 오탐·미탐할 수 있음 |
| 자동화 범위 | Cursor CLI 미구성 시 사실상 수동 단계 증가 |
| 보안 | 로컬 `state.yaml`·`run.log`에 민감 정보가 들어갈 수 있음 — 저장소 커밋·백업 시 주의 |
| 워크플로 깊이 | 파일 아티팩트는 **최대 2 사이클**; 그 이상은 `escalated` |
| 실행 루프 | 무한 루프 방지를 위한 최대 반복 상한 존재(설정값은 구현 참조) |

---

## 12. 디렉터리·파일 빠른 참조

```
src/orchestrator/
├── domain/
│   ├── models.py, state_machine.py, workflow.py, errors.py
│   ├── provenance.py
│   ├── github_models.py, github_workflow.py
├── application/
│   ├── task_service.py, github_task_service.py, artifact_service.py
│   ├── run_orchestrator.py, github_run_orchestrator.py
│   ├── prompt_runner.py, workflow_engine.py
├── infrastructure/
│   ├── file_state_store.py, config_loader.py, template_renderer.py
│   ├── run_logger.py, logger.py, setup_service.py
│   ├── auth_checker.py, github_service.py
├── adapters/
│   ├── base.py, manual.py, stub.py, command.py
│   ├── cursor.py, claude_adapter.py, codex.py, factory.py
└── cli.py
```

---

## 13. 용어 정리

| 용어 | 설명 |
|------|------|
| morch | 본 프로젝트의 CLI 진입점 이름 (`morch`) |
| 아티팩트 | 태스크 디렉터리 내 마크다운 등 산출물 |
| SSOT | `state.yaml` 등 단일 진실 공급원 |
| 프로비넌스 | 산출물에 대한 논리적 작성자·역할 귀속 정보 |

---

## 부록 A. 주요 CLI 명령 (개요)

아래는 `cli.py` 도움말에 기반한 **기능 그룹** 요약이다. 정확한 플래그는 `morch --help` 및 하위 명령 help를 따른다.

| 영역 | 예시 명령 | 설명 |
|------|-----------|------|
| 진단 | `morch doctor` | 시스템 헬스 체크 |
| 인증 | `morch auth status`, `morch auth <tool> status` | 도구별 인증 상태 |
| 에이전트 | `morch agents list`, `morch agents doctor`, `morch agents order` | 순서·준비 상태 |
| 설정 | `morch config show`, `morch setup` | 유효 설정 표시·초기 설정 |
| 실행 | `morch run prompt`, `morch run task`, `morch run github` | 프롬프트·파일·GitHub 파이프라인 |
| 이슈 | `morch issue create`, `list`, `view`, `reopen`, `start` | GitHub 이슈 연동 |
| 프롬프트 | `morch prompt list-templates`, `morch prompt init` | 템플릿 목록·로컬 파일 생성 |
| 재개·상태 | `morch resume task|github`, `morch status task|github`, `morch watch task` | 중단된 실행 재개·조회 |
| 태스크 | `morch task init`, `advance`, `validate`, `archive`, `list` | 파일 아티팩트 태스크 관리 |

---

## 부록 B. 환경 변수 및 경로

| 이름 / 경로 | 용도 |
|-------------|------|
| `MORCH_LOG_LEVEL` | 애플리케이션 로그 레벨 (기본 `INFO`) |
| `~/.morch/config.yaml` | `morch setup`으로 저장하는 에이전트 바이너리 경로 등 |
| `~/.morch/logs/morch.log` | 구조화 애플리케이션 로그 파일 |
| `<workspace>/active/<task>/run.log` | 태스크별 JSONL 실행 로그 (`RunLogger`) |

---

## 부록 C. 파일 아티팩트 시퀀스 (사이클 1 요약)

워크플로 상세는 영문 `docs/workflow.md`와 동일하다. 사이클 1에서의 **필수/조건부** 개념만 한국어로 요약한다.

| 순번 | 파일 (패턴) | 작성자 | 필수 | 목적 |
|------|----------------|--------|------|------|
| 0 | `00-scope.md` | Cursor | 예 | 목표·수락 기준·범위 |
| 1 | `01-cursor-implementation.md` | Cursor | 예 | 구현 요약·변경 파일 |
| 2 | `02-claude-review-cycle-1.md` | Claude | 예 | 리뷰·`**Status**` |
| 3 | `03-cursor-response-cycle-1.md` | Cursor | 변경 요청 시 | 재작업 응답 |
| 4 | `04-codex-review-cycle-1.md` | Codex | 예 | 최종 리뷰 |
| 5 | `05-final-approval.md` | Codex | 승인 시 | 서명·승인 기록 |

사이클 2는 `06`–`09` 아티팩트로 대응한다. Codex가 변경을 요청하면 사이클이 진행되고, 상한 초과 시 `escalated`로 전이된다.

---

## 부록 D. 파일 아티팩트 상태 머신 (개념도)

아래는 `docs/architecture.md` 다이어그램에 대응하는 **텍스트 요약**이다. 실제 허용 전이는 `state_machine.py`의 테이블이 권위를 가진다.

```
initialized → cursor_implementing → claude_reviewing ─┬→ codex_reviewing ─┬→ approved → archived
                        ↑                             │                  │
                        │                             └ cursor_reworking ┘
                        └ (claude/codex changes-requested, 사이클 내)
                        
codex_reviewing → escalated (최대 사이클 등)
```

---

## 부록 E. GitHub 네이티브 단계와 담당자 (요약)

| 단계 | 대표 `GitHubTaskState` | 주 담당 `AgentRole` | 산출 |
|------|------------------------|---------------------|------|
| 이슈 확정 | `issue_claimed` | Orchestrator/운영자 | 이슈·브랜치 정보 연결 |
| 구현 | `cursor_implementing` | Cursor | 커밋·푸시 |
| PR | `pr_opened` | Cursor | PR 생성 |
| 1차 리뷰 | `claude_reviewing` | Claude | PR 리뷰·소규모 수정 커밋 가능 |
| 재작업 | `cursor_reworking` | Cursor | 피드백 반영 |
| 최종 리뷰 | `codex_reviewing` | Codex | 승인/변경 요청/에스컬레이션 |
| 종료 | `approved` / `merged` / `escalated` | 사람·정책 | 머지는 §9 |

---

## 부록 F. `PromptRunner` 실행 개념 (단계)

1. 마크다운 프롬프트 파일을 읽는다.  
2. 내부적으로 태스크 컨텍스트(이름·상태)를 준비하고 `RunLogger`에 기록할 수 있다.  
3. `AgentsConfig`에 정의된 순서대로 각 `AgentRole`의 어댑터를 가져온다(없으면 폴백).  
4. 각 스텝마다 `ExecutionResult`를 받아 `PromptStepRecord`에 누적한다.  
5. `WAITING`이면 `RunStatus`가 대기 상태로 남고, 운영자는 `resume` 힌트에 따라 재개한다.  
6. 파이프라인이 끝나면 `PromptRunResult.is_complete`로 완료 여부를 판단한다.

파일 아티팩트 워크플로와 달리 **고정 파일명 세트가 없으므로**, 빠른 실험·단일 문서 주도 작업에 적합하다.

---

## 부록 G. 운영·보안 참고 (요약)

- **비밀 관리**: GitHub 토큰·SSH 키는 환경 변수 또는 OS 비밀 저장소에 두고, 저장소에 커밋하지 않는다.  
- **로그**: `run.log`·`morch.log`에 프롬프트 일부·경로가 남을 수 있으므로, 공유·아카이브 전에 마스킹 정책을 적용한다.  
- **재현성**: 동일 태스크 디렉터리를 여러 머신에서 공유할 때는 파일 동시 편집에 주의한다.  
- **버전 고정**: CI·운영에서는 `morch`와 에이전트 CLI 버전을 핀(pin)하여 `classify_resource_error()` 패턴과 동작을 안정화한다.

---

*본 문서는 구현 세부사항이 변경될 수 있으므로, 충돌 시 소스 코드 및 영문 `docs/`를 우선한다.*
