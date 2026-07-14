# Natural-Language Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add deterministic natural-language consultation so an Agent-Skill-capable main loop can select a named vendor or canonical model while preserving existing routing, safety, jobs, and timeouts.

**Architecture:** Natural-language interpretation stays in skills/omnilane/SKILL.md. The dispatcher gains one structured --vendor selector, while a data-only consult lane provides one candidate per model vendor. Explicit targets use consult --vendor; unnamed tasks keep existing lanes; capability-only questions do not dispatch.

**Tech Stack:** Bash 3.2-compatible shell, Markdown Agent Skills, routing text, existing shell test harness.

## Global Constraints

- No classifier-model call, parser service, database, browser, or Live UI.
- --vendor accepts only codex, claude, grok, or gemini.
- Explicit vendors never silently fall back.
- Vendor absent from a lane exits 2; configured vendor with missing CLI exits 4.
- Calls without --vendor retain current fallback behavior.
- Consultation defaults to advise; work requires explicit edit intent and workdir in the skill contract.
- Reuse parse_lane_segment; never use eval.
- configure.sh must not edit or collapse the multi-vendor consult lane.
- Keep Bash 3.2 compatibility.
- Live UI remains outside this branch.

---

## File Map

- scripts/dispatch.sh: parse and resolve --vendor.
- tests/run.sh: prove selection, error codes, no fallback, overrides, lane visibility, configurator exclusion.
- routing.yaml: publish consult.
- scripts/configure.sh: omit consult from its single-candidate menu.
- skills/omnilane/SKILL.md: natural-language decision order and model aliases.
- commands/route.md, hooks/routing-instruction.md, bin/omnilane: expose the contract.
- routing.local.yaml.example and five README files: manual override and localized usage.

---

### Task 1: Deterministic vendor selection

**Files:**

- Modify: tests/run.sh
- Modify: scripts/dispatch.sh

**Interfaces:**

- Consumes: parse_lane_segment, vendor_available, RESOLVED_FIELDS, runner interface.
- Produces: resolve_chain CHAIN [REQUESTED_VENDOR] and --vendor codex|claude|grok|gemini.
- Internal returns: 0 selected, 2 malformed segment, 4 matching CLI unavailable, 5 requested vendor absent.

- [ ] **Step 1: Write the failing test**

Add before installer tests in tests/run.sh:

~~~bash
test_vendor_selector() {
  local name="explicit vendor selector" home bin selected automatic
  local rc_absent rc_unavailable rc_invalid rc_missing missing_out
  home="$TEST_ROOT/vendor-selector"; bin="$home/bin"
  mkdir -p "$home" "$bin"

  cat > "$bin/fake-codex" <<'EOF'
#!/usr/bin/env bash
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'codex selected\n' > "$out"
EOF
  cat > "$bin/fake-claude" <<'EOF'
#!/usr/bin/env bash
printf 'claude selected %s\n' "$*"
EOF
  chmod +x "$bin/fake-codex" "$bin/fake-claude"

  printf 'probe: codex codex-default low | claude claude-default high | grok grok-default -\n' \
    > "$home/routing.local.yaml"

  selected="$(OMNILANE_HOME="$home" CODEX_BIN="$bin/fake-codex" \
    CLAUDE_BIN="$bin/fake-claude" GROK_BIN="$bin/missing-grok" \
    bash "$ROOT/scripts/dispatch.sh" --vendor claude --model claude-override \
      --effort max probe x 2>/dev/null)"
  automatic="$(OMNILANE_HOME="$home" CODEX_BIN="$bin/fake-codex" \
    CLAUDE_BIN="$bin/fake-claude" GROK_BIN="$bin/missing-grok" \
    bash "$ROOT/scripts/dispatch.sh" probe x 2>/dev/null)"

  OMNILANE_HOME="$home" CODEX_BIN="$bin/fake-codex" \
    bash "$ROOT/scripts/dispatch.sh" --vendor gemini probe x >/dev/null 2>&1
  rc_absent=$?
  OMNILANE_HOME="$home" CODEX_BIN="$bin/fake-codex" GROK_BIN="$bin/missing-grok" \
    bash "$ROOT/scripts/dispatch.sh" --vendor grok probe x >/dev/null 2>&1
  rc_unavailable=$?
  OMNILANE_HOME="$home" bash "$ROOT/scripts/dispatch.sh" \
    --vendor vote probe x >/dev/null 2>&1
  rc_invalid=$?
  missing_out="$(OMNILANE_HOME="$home" bash "$ROOT/scripts/dispatch.sh" --vendor 2>&1)"
  rc_missing=$?

  if [[ "$selected" != claude\ selected* ]]; then
    fail "$name" "requested Claude was not selected: $selected"
  elif [[ "$selected" != *'--model claude-override'* ||
          "$selected" != *'--effort max'* ]]; then
    fail "$name" "model or effort override was lost: $selected"
  elif [[ "$automatic" != "codex selected" ]]; then
    fail "$name" "normal fallback changed: $automatic"
  elif [[ "$rc_absent" -ne 2 ]]; then
    fail "$name" "absent vendor should exit 2, got $rc_absent"
  elif [[ "$rc_unavailable" -ne 4 ]]; then
    fail "$name" "missing requested CLI should exit 4, got $rc_unavailable"
  elif [[ "$rc_invalid" -ne 2 ]]; then
    fail "$name" "invalid vendor should exit 2, got $rc_invalid"
  elif [[ "$rc_missing" -ne 2 || "$missing_out" != *"needs a value"* ]]; then
    fail "$name" "missing --vendor value did not fail cleanly"
  else
    pass "$name"
  fi
}
~~~

Invoke test_vendor_selector after test_watchdog_timeout_resolution.

- [ ] **Step 2: Prove the test fails**

Run: bash tests/run.sh

Expected: explicit vendor selector fails because --vendor is unknown.

- [ ] **Step 3: Implement one filtered resolver**

Initialize OVERRIDE_VENDOR="", show [--vendor V] in dispatch usage, and add --vendor to the existing value-taking flag case.

Replace resolve_chain with:

~~~bash
resolve_chain() {
  local chain="$1" requested_vendor="${2:-}" seg i=0 vendor
  RESOLVED_SPEC=""; RESOLVED_IDX=0; RESOLVED_TOTAL=0; RESOLVED_FIELDS=()
  local SEGS=() F=()
  IFS='|' read -ra SEGS <<< "$chain"
  RESOLVED_TOTAL="${#SEGS[@]}"
  for seg in "${SEGS[@]}"; do
    i=$((i + 1))
    [[ -n "${seg// /}" ]] || continue
    parse_lane_segment "$seg" || {
      echo "omnilane: malformed quoted routing segment: $seg" >&2
      return 2
    }
    F=("${PARSED_FIELDS[@]}")
    vendor="${F[0]:-}"
    if [[ -n "$requested_vendor" ]]; then
      [[ "$vendor" == "$requested_vendor" ]] || continue
      RESOLVED_SPEC="$seg"; RESOLVED_FIELDS=("${F[@]}"); RESOLVED_IDX="$i"
      if vendor_available "$vendor"; then return 0; fi
      return 4
    fi
    if [[ "$vendor" == "off" ]] || vendor_available "$vendor"; then
      RESOLVED_SPEC="$seg"; RESOLVED_FIELDS=("${F[@]}")
      RESOLVED_IDX="$i"; return 0
    fi
  done
  [[ -n "$requested_vendor" ]] && return 5
  return 1
}
~~~

Validate after lane and mode:

~~~bash
if [[ -n "$OVERRIDE_VENDOR" ]] &&
   ! [[ "$OVERRIDE_VENDOR" =~ ^(codex|claude|grok|gemini)$ ]]; then
  echo "omnilane: invalid vendor '$OVERRIDE_VENDOR' (codex|claude|grok|gemini)" >&2
  exit 2
fi
~~~

Resolve explicit targets with:

~~~bash
if [[ -n "$OVERRIDE_VENDOR" ]]; then
  if resolve_chain "$CHAIN" "$OVERRIDE_VENDOR"; then
    :
  else
    resolve_rc=$?
    case "$resolve_rc" in
      2) exit 2 ;;
      4)
        echo "omnilane: requested vendor '$OVERRIDE_VENDOR' is configured for lane '$LANE' but its CLI is unavailable" >&2
        exit 4
        ;;
      5)
        echo "omnilane: requested vendor '$OVERRIDE_VENDOR' is not configured for lane '$LANE'" >&2
        exit 2
        ;;
      *)
        echo "omnilane: could not resolve requested vendor '$OVERRIDE_VENDOR' for lane '$LANE'" >&2
        exit 2
        ;;
    esac
  fi
else
  resolve_chain "$CHAIN" || {
    echo "omnilane: no vendor CLI available for lane '$LANE' (chain:$CHAIN)." >&2
    echo "omnilane: install a vendor CLI or override the lane in ~/.omnilane/routing.local.yaml" >&2
    exit 4
  }
fi
~~~

- [ ] **Step 4: Verify and commit**

~~~bash
bash -n scripts/dispatch.sh tests/run.sh
bash tests/run.sh
git diff --check
git add scripts/dispatch.sh tests/run.sh
git commit -m "feat: add explicit vendor selection"
~~~

Expected: all tests pass; no syntax or diff errors.

---

### Task 2: Multi-vendor consult lane

**Files:**

- Modify: tests/run.sh
- Modify: routing.yaml
- Modify: scripts/configure.sh

**Interfaces:**

- Consumes: Task 1 --vendor.
- Produces: consult and a configurator menu that excludes only consult.

- [ ] **Step 1: Add the failing test**

~~~bash
test_consult_lane_and_configurator() {
  local name="consult lane stays multi-vendor" home listed
  home="$TEST_ROOT/consult"; mkdir -p "$home"
  listed="$(OMNILANE_HOME="$home" CODEX_BIN=/usr/bin/true CLAUDE_BIN=/usr/bin/true \
    GROK_BIN=/usr/bin/true AGY_BIN=/usr/bin/true \
    bash "$ROOT/scripts/dispatch.sh" --list 2>&1)"
  printf '\n' | HOME="$home" OMNILANE_HOME="$home/.omnilane" \
    CODEX_BIN=/usr/bin/true CLAUDE_BIN=/usr/bin/true \
    GROK_BIN=/usr/bin/true AGY_BIN=/usr/bin/true \
    bash "$ROOT/scripts/configure.sh" > "$home/configure.out" 2>&1

  if [[ "$listed" != *'consult:'* ||
        "$listed" != *'codex gpt-5.6-sol max'* ]]; then
    fail "$name" "consult lane missing from effective routing"
  elif grep -Eq '^  [0-9]+\) consult$' "$home/configure.out"; then
    fail "$name" "single-candidate configurator exposed consult"
  else
    pass "$name"
  fi
}
~~~

Invoke it after test_vendor_selector. Run bash tests/run.sh; expect this new test to fail.

- [ ] **Step 2: Add consult as routing data**

Add after taste-final in routing.yaml:

~~~text
consult:         codex gpt-5.6-sol max | claude claude-opus-4-8 high | grok grok-4.5 - | gemini "Gemini 3.1 Pro (High)" -  # direct named-model consultation; use --vendor to prevent fallback
~~~

- [ ] **Step 3: Protect consult from configure.sh**

Replace lane discovery with:

~~~bash
LANES=()
while IFS= read -r line; do
  if [[ "$line" =~ ^([a-z][a-z0-9-]*): ]]; then
    lane="${BASH_REMATCH[1]}"
    [[ "$lane" == "consult" ]] || LANES+=("$lane")
  fi
done < "$OMNILANE_REPO/routing.yaml"
~~~

- [ ] **Step 4: Verify and commit**

~~~bash
bash -n scripts/configure.sh tests/run.sh
bash tests/run.sh
bash scripts/dispatch.sh --list | grep '^consult:'
git diff --check
git add routing.yaml scripts/configure.sh tests/run.sh
git commit -m "feat: add multi-vendor consult lane"
~~~

Expected: all tests pass; consult appears in --list but never as a numbered configurator option.

---

### Task 3: Agent Skill contract

**Files:**

- Modify: skills/omnilane/SKILL.md
- Modify: commands/route.md
- Modify: hooks/routing-instruction.md
- Modify: bin/omnilane

**Interfaces:**

- Consumes: consult and --vendor.
- Produces: deterministic informational, vendor, alias, automatic-lane, and permission decisions.

- [ ] **Step 1: Add natural-language rules and aliases**

Add before Rules in SKILL.md:

~~~markdown
## Natural-language consultation

Users may speak normally; they do not need lane names.

1. Capability-only question (which model, 哪個模型, 誰適合) → answer from
   the effective table; do not dispatch unless execution is also requested.
2. Generic vendor name (Claude, Codex, Grok, Gemini) → run
   dispatch.sh --vendor <vendor> consult "<task>".
3. Canonical model alias → pass its vendor, model, and effort from the table.
   Never silently substitute another model family.
4. No named target → classify into an existing lane and dispatch normally.
5. Unknown or ambiguous nickname → ask for clarification; do not guess or run.

| Alias | Vendor | Model | Effort |
|---|---|---|---|
| Opus | claude | claude-opus-4-8 | high |
| Fable | claude | claude-fable-5 | high |
| Sonnet | claude | claude-sonnet-5 | high |
| Haiku | claude | claude-haiku-4-5 | - |
| Sol | codex | gpt-5.6-sol | max |
| Terra | codex | gpt-5.6-terra | max |
| Luna | codex | gpt-5.6-luna | medium |
| Grok 4.5 | grok | grok-4.5 | - |
| Gemini Pro | gemini | Gemini 3.1 Pro (High) | - |
| Gemini Flash | gemini | Gemini 3.5 Flash (High) | - |

Examples:

- Ask Opus to challenge this architecture →
  dispatch.sh --vendor claude --model claude-opus-4-8 --effort high consult ...
- 請 Grok 查最新公開資訊 → dispatch.sh --vendor grok consult ...
- 哪個模型適合檢查大型 repo？ → answer only.

Consultation defaults to advise. Use --mode work --workdir <dir> only for an
explicit edit request. Missing explicit targets fail clearly; never remove
--vendor to obtain a fallback.
~~~

Add consult to the skill lane table.

- [ ] **Step 2: Update command, hook, and help**

commands/route.md must accept lane text, unnamed free-form tasks, and explicit targets. Its command template includes [--vendor V].

Add to hooks/routing-instruction.md:

~~~text
If the user explicitly names Claude, Codex, Grok, Gemini, or a canonical model
alias, use the omnilane skill's consult rules and keep --vendor in the dispatch;
an explicit target must not silently fall back.
~~~

Change bin/omnilane help to:

~~~text
  omnilane route [--vendor V] [flags] LANE "TASK"
                                        dispatch or consult a model
~~~

- [ ] **Step 3: Verify and commit**

~~~bash
rg -n 'Natural-language consultation|Ask Opus|--vendor claude|Unknown or ambiguous' skills/omnilane/SKILL.md
rg -n -- '--vendor|explicit target|silently fall back' commands/route.md hooks/routing-instruction.md bin/omnilane
bash -n bin/omnilane
git diff --check
git add skills/omnilane/SKILL.md commands/route.md hooks/routing-instruction.md bin/omnilane
git commit -m "feat: add natural-language consultation rules"
~~~

Expected: all searches match; syntax and diff checks pass.

---

### Task 4: Localized documentation

**Files:**

- Modify: README.md
- Modify: README.zh-TW.md
- Modify: README.zh-CN.md
- Modify: README.ja.md
- Modify: README.ko.md
- Modify: routing.local.yaml.example

**Interfaces:**

- Consumes: public consult and --vendor contracts.
- Produces: discoverable usage in every existing locale.

- [ ] **Step 1: Update tables, command references, and exit semantics**

Add consult to all five lane tables. Add [--vendor V] to each dispatch reference. State exit 2 for invalid/absent target configuration and exit 4 for a configured target with unavailable CLI.

- [ ] **Step 2: Add exact localized examples**

Use:

~~~text
README.md:       Ask Opus to challenge this architecture.
README.zh-TW.md: 請 Opus 挑戰這個架構。
README.zh-CN.md: 请 Opus 挑战这个架构。
README.ja.md:    Opus にこのアーキテクチャを厳しく検討してもらって。
README.ko.md:    Opus에게 이 아키텍처를 비판적으로 검토해 달라고 해줘.
~~~

Each section states: generic vendor names use the configured consult candidate; canonical aliases pin a family; unavailable explicit targets do not fallback; capability-only questions spend no model call.

- [ ] **Step 3: Document manual overrides**

Add to routing.local.yaml.example:

~~~text
# consult is a multi-vendor direct-target chain. configure.sh intentionally
# skips it because that menu writes one candidate per lane. If overriding it,
# retain every vendor you want to address by name:
# consult: codex gpt-5.6-sol max | claude claude-opus-4-8 high | grok grok-4.5 - | gemini "Gemini 3.1 Pro (High)" -
~~~

- [ ] **Step 4: Verify and commit**

~~~bash
for f in README.md README.zh-TW.md README.zh-CN.md README.ja.md README.ko.md; do
  grep -q 'consult' "$f" || exit 1
  grep -q -- '--vendor' "$f" || exit 1
  grep -q 'Opus' "$f" || exit 1
done
grep -q '^# consult is a multi-vendor' routing.local.yaml.example
git diff --check
git add README.md README.zh-TW.md README.zh-CN.md README.ja.md README.ko.md routing.local.yaml.example
git commit -m "docs: explain natural-language model consultation"
~~~

Expected: exit 0 and clean diff check.

---

### Task 5: Delivery gate

**Files:**

- Verify only; modify only to fix demonstrated failures.

**Interfaces:**

- Consumes: Tasks 1-4.
- Produces: a branch safe for review and later Live UI stacking.

- [ ] **Step 1: Syntax and lint**

~~~bash
bash -n bin/omnilane install.sh scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh tests/run.sh
shellcheck -S warning bin/omnilane install.sh scripts/*.sh scripts/lib/*.sh scripts/runners/*.sh tests/run.sh
~~~

Expected: exit 0, no warnings.

- [ ] **Step 2: Full behavior suite**

Run bash tests/run.sh.

Expected: every test passes, including explicit vendor selector and consult lane stays multi-vendor.

- [ ] **Step 3: Routing and documentation smoke**

~~~bash
bash scripts/dispatch.sh --list
bash bin/omnilane help
rg -n 'consult|--vendor' routing.yaml skills/omnilane/SKILL.md commands/route.md hooks/routing-instruction.md README*.md routing.local.yaml.example
~~~

Expected: consult and --vendor appear on every intended surface.

- [ ] **Step 4: Repository integrity**

~~~bash
git diff origin/main...HEAD --check
git status --short
git log --oneline --decorate origin/main..HEAD
~~~

Expected: clean diff, clean worktree, only scoped commits.

- [ ] **Step 5: Final adversarial review**

Reject any path that silently falls back after --vendor, accepts exec/vote/off as target, exposes consult in configure.sh, substitutes another family for Opus, or has a test that fails to prove the selected runner and overrides.

Expected: no P0/P1. Fix evidence-backed findings, repeat Steps 1-4, then commit the precise fix.
