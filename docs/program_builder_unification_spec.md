# Program Builder Unification Spec

## Goal

Unify AI-generated and manual program creation into one editable draft workflow so users can:

- start from an empty draft and build manually
- start from an AI-generated draft and edit manually
- build a partial draft and ask AI to complete missing parts
- ask AI to evaluate a draft without mutating it
- apply AI suggestions selectively

The published runtime program remains the existing `TrainingProgram.current_program` JSON shape.

## Current State

There are two separate creation paths:

- AI path:
  - prompt -> OpenAI -> validated program JSON
  - stored directly in `TrainingProgram.current_program`
- manual path:
  - stored in relational rows:
    - `ManualProgramDraft`
    - `ManualProgramDay`
    - `ManualProgramExercise`
  - compiled to JSON only at publish time

This split causes product and engineering friction:

- AI programs are hard to edit structurally
- manual programs cannot be completed or reviewed by AI in a first-class way
- two builders will continue to diverge unless a shared draft layer is introduced

## Product Requirements

The unified builder must support:

1. Create empty draft manually
2. Create AI-seeded draft from prompt
3. Edit any draft manually
4. Ask AI to:
   - complete missing days
   - complete one day
   - improve exercise selection
   - generate warmups
   - evaluate the draft
5. Publish any draft to an active `TrainingProgram`
6. Preserve prior published programs and workout history
7. Avoid destructive AI rewrites outside the requested scope
8. Allow future revision history and compare/revert

## Non-Goals For First Delivery

- collaborative editing by multiple users
- real-time patch streaming
- direct editing of published `TrainingProgram`
- full diff UI across nested program structures
- AI auto-applying changes without review

## Proposed Architecture

### Core Decision

Introduce a new unified editable draft model family and keep `TrainingProgram` as the published/runtime artifact.

Do not make `TrainingProgram` itself editable.

### Why

- training, workout history, evaluations, and progression already rely on the published JSON shape
- runtime programs should stay stable once activated
- drafts need richer metadata and AI-state than published programs do

## Proposed Data Model

### New Models

#### `ProgramDraft`

Purpose:
- single editable container for both manual and AI-assisted drafting

Suggested fields:

- `user`
- `name`
- `goal_summary`
- `duration_weeks`
- `weight_unit`
- `program_notes`
- `source`
  - `manual`
  - `ai_seeded`
  - `hybrid`
- `status`
  - `draft`
  - `published`
  - `archived`
- `request_prompt`
- `ai_context_notes`
- `last_ai_action`
- `published_program`
- `created_at`
- `updated_at`
- `published_at`

#### `ProgramDraftDay`

Purpose:
- editable day node within a draft

Suggested fields:

- `draft`
- `day_key`
- `name`
- `day_type`
- `notes`
- `ai_locked`
- `sort_order`

#### `ProgramDraftExercise`

Purpose:
- editable exercise row with both library link and draft-time snapshot

Suggested fields:

- `day`
- `exercise`
- `block_type`
- `order`
- `prescription_type`
- `sets_count`
- `target_reps`
- `target_seconds`
- `load_guidance`
- `target_effort_rpe`
- `rest_seconds_override`
- `notes`
- `ai_locked`

Optional but recommended snapshot fields:

- `snapshot_name`
- `snapshot_modality`
- `snapshot_image_url`
- `snapshot_video_url`
- `snapshot_instructions`

These help preserve draft stability when the exercise library changes later.

#### `ProgramDraftRevision`

Purpose:
- audit and rollback of AI and manual changes

Suggested fields:

- `draft`
- `revision_number`
- `created_by_user`
- `source`
  - `manual`
  - `ai`
  - `system`
- `action_type`
  - `seed_full_program`
  - `complete_missing_days`
  - `complete_day`
  - `evaluate_program`
  - `rewrite_day`
  - `manual_edit`
- `summary`
- `draft_snapshot_json`
- `ai_request_payload`
- `ai_response_payload`
- `created_at`

#### `ProgramDraftAiRun`

Purpose:
- operational logging for AI actions, parallel to `ProgramGenerationRequest`

Suggested fields:

- `draft`
- `user`
- `action_type`
- `scope_payload`
- `prompt_text`
- `llm_model`
- `prompt_version`
- `status`
- `raw_llm_response`
- `validated_payload`
- `error_message`
- `token_usage_input`
- `token_usage_output`
- `created_at`

## Relationship To Existing Models

### Keep

- `TrainingProgram`
- `ProgramGenerationRequest`
- existing training/runtime session models

### Migrate Away From

- `ManualProgramDraft`
- `ManualProgramDay`
- `ManualProgramExercise`

These become legacy once the new unified draft path is live.

## Migration Strategy

### Phase 1: Add New Models

Add new `ProgramDraft*` models without deleting existing manual models.

### Phase 2: Backfill Existing Manual Drafts

Create a data migration or management command that:

- creates `ProgramDraft` from each `ManualProgramDraft`
- creates `ProgramDraftDay` from each `ManualProgramDay`
- creates `ProgramDraftExercise` from each `ManualProgramExercise`
- preserves `published_program` and `published_at`
- marks `source = manual`

### Phase 3: Read Switch

Move builder views from `ManualProgram*` to `ProgramDraft*`.

### Phase 4: Write Switch

Stop creating new `ManualProgram*` rows.

### Phase 5: Deprecation

After verification and a release buffer:

- remove old manual models and their code paths

## Canonical Serialization Contract

Two converters are required.

### `draft_to_program_json(draft) -> dict`

Responsibility:

- compile unified draft rows into the existing `CURRENT_PROGRAM_SCHEMA`
- preserve exact runtime shape expected by training/evaluation flows

### `program_json_to_draft(program_json, *, user, metadata) -> ProgramDraft`

Responsibility:

- create or replace a draft from AI-generated JSON
- allow cloning an existing published `TrainingProgram` into an editable draft

These converters become the boundary between editable and published program worlds.

## Service Layer Design

### New Services

#### Draft services

- `create_empty_draft(user, initial_payload)`
- `clone_training_program_to_draft(program)`
- `clone_draft(draft)`
- `publish_program_draft(draft)`
- `lock_day_for_ai(day, locked=True)`
- `lock_exercise_for_ai(entry, locked=True)`

#### Serialization services

- `draft_to_program_json(draft)`
- `program_json_to_draft(program_json, user, source, request_prompt="")`
- `draft_snapshot_json(draft)`

#### AI draft services

- `seed_draft_with_ai(user, prompt_text)`
- `complete_draft_scope_with_ai(draft, action_type, scope_payload)`
- `evaluate_draft_with_ai(draft)`
- `apply_ai_result_to_draft(draft, ai_payload, scope_payload)`

#### Revision services

- `create_draft_revision(draft, source, action_type, summary, snapshot_json, ...)`
- `restore_draft_revision(revision)`

## AI Operation Model

AI should operate on explicit actions, not a single generic rewrite.

### Initial Supported AI Actions

1. `seed_full_program`
2. `complete_missing_days`
3. `complete_day`
4. `generate_warmup`
5. `evaluate_program`

### Later Actions

1. `rewrite_day`
2. `rewrite_selection`
3. `rebalance_week`
4. `progression_pass`

## AI Scope Rules

Every AI action must declare scope.

Examples:

- complete only `wednesday`
- fill only days with zero main exercises
- keep `monday` and `friday` unchanged
- preserve locked days and locked exercises

### Scope Payload Shape

Suggested shape:

```json
{
  "target_days": ["wednesday", "friday"],
  "locked_days": ["monday"],
  "locked_exercise_ids": [123, 124],
  "fill_only_empty_slots": true,
  "preserve_day_order": true,
  "preserve_existing_exercises": true
}
```

## AI Output Strategy

### Recommendation

Use scoped replacement payloads, not whole-draft replacement.

Examples:

- for `complete_day`, AI returns one normalized day payload
- for `complete_missing_days`, AI returns only the missing day payloads
- for `generate_warmup`, AI returns only warmup block payload
- for `evaluate_program`, AI returns findings only

This reduces accidental rewrites.

### Why Not Full Draft Replacement

- too easy to overwrite unrelated user work
- harder to explain diffs
- harder to trust

## AI Prompt Contracts

### Seed Full Program

Input:

- user prompt
- profile context
- history summary
- target schema

Output:

- complete `CURRENT_PROGRAM_SCHEMA` JSON

### Complete Draft Scope

Input:

- action type
- draft snapshot
- locked days/exercises
- requested scope
- output schema for the requested scope only

Output:

- scoped normalized payload

### Evaluate Draft

Input:

- full draft snapshot
- optional profile context

Output:

Structured review object:

```json
{
  "summary": "...",
  "findings": [
    {
      "severity": "high|medium|low",
      "type": "balance|recovery|exercise_selection|progression|session_length",
      "target": "monday|draft|exercise:<id>",
      "message": "...",
      "suggested_fix": "..."
    }
  ],
  "suggested_actions": [
    {
      "action_type": "rewrite_day",
      "target_day": "thursday",
      "reason": "..."
    }
  ]
}
```

Evaluation must be non-destructive by default.

## UI / UX Specification

### Replace Separate Entry Points With One Builder

Current:

- `Generate Program`
- `Manual Program`

Target:

- `Program Builder`

Entry choices:

1. Start empty
2. Start with AI
3. Import active program into draft
4. Open existing draft

### Draft Detail Screen

Core sections:

- draft metadata
- day list
- day editor
- exercise search/add/edit
- AI actions panel
- summary sidebar

### AI Actions Panel

Actions:

- Complete selected day
- Complete all incomplete days
- Generate warmups for unlocked days
- Evaluate draft
- Improve selected day

### Locking UI

Allow user to mark:

- day locked for AI
- exercise locked for AI

Suggested UI:

- day-level toggle near day title
- exercise-level toggle in exercise row

### Evaluation UI

Show:

- summary
- findings ordered by severity
- optional actions

Allow:

- apply selected suggestion
- create revised copy
- ignore

## Builder Summary Improvements

Add a sidebar or summary panel with:

- days per week
- total training days
- rough weekly muscle coverage
- movement balance
- session size estimate
- unresolved gaps:
  - days with no main exercises
  - exercises with incomplete prescriptions
  - missing warmups

This makes incomplete drafts obvious before AI even runs.

## Validation Rules

### Draft-Level Validation

Allow incomplete drafts.

Draft rules should be softer than publish rules.

Examples:

- day may exist without exercises
- exercise may exist with incomplete notes
- draft may contain unresolved empty days

### Publish-Level Validation

Publishing must still validate full program JSON against `CURRENT_PROGRAM_SCHEMA`.

Additionally enforce:

- no duplicate day keys
- no training day with zero main exercises
- valid set plan counts and prescription types

## Compatibility Requirements

### Training

No changes to runtime training session logic in first release.

`TrainingProgram.current_program` remains the source for:

- training day rendering
- workout session creation
- progression
- substitutions
- evaluations

### Workout History

No backfill required.

Published programs remain immutable artifacts referenced by sessions.

## Rollout Plan

### Phase A: Foundations

- add `ProgramDraft*` models
- add draft serialization services
- add revision model
- add tests for conversion both directions

### Phase B: AI Seeded Drafts

- change AI program generation to create a `ProgramDraft`
- keep current `TrainingProgram` publish path
- add “review before publish” flow

### Phase C: Manual Builder Migration

- point manual builder to `ProgramDraft*`
- preserve current manual UX
- add publish from unified draft

### Phase D: AI Assist On Drafts

- complete day
- complete missing days
- evaluate draft

### Phase E: Locking + Revision UX

- day/exercise lock controls
- revision history
- restore revision

### Phase F: Remove Legacy Manual Models

- migrate data
- remove `ManualProgram*` code paths

## Suggested File Changes

### New files

- `programs/draft_services.py`
- `programs/draft_ai.py`
- `programs/draft_serialization.py`
- `programs/draft_validators.py`

### Existing files to refactor

- `programs/models.py`
- `programs/views.py`
- `programs/forms.py`
- `programs/services.py`
- `programs/manual_services.py`
- `programs/tests.py`
- builder templates under `templates/programs/`

## Key Technical Risks

### 1. Scope Drift In AI Edits

Risk:
- AI changes unrelated days or exercises

Mitigation:

- scoped inputs
- scoped output schema
- merge only requested targets
- keep revisions

### 2. Draft / Published Divergence

Risk:
- draft shape drifts away from runtime schema assumptions

Mitigation:

- one canonical converter
- publish-time schema validation
- integration tests from draft -> published program -> training day

### 3. Migration Complexity

Risk:
- manual drafts become inconsistent during transition

Mitigation:

- dual-write only if necessary
- otherwise migrate once and switch reads quickly

### 4. User Trust In AI

Risk:
- users feel AI overwrites their work

Mitigation:

- explicit action scope
- locks
- preview before apply
- revision restore

## Testing Strategy

### Unit Tests

- draft serialization
- draft validation
- AI scoped merge behavior
- lock enforcement

### Integration Tests

- manual draft -> publish -> training session
- AI-seeded draft -> manual edit -> publish
- partial draft -> AI complete day -> publish
- draft evaluation returns findings without mutation

### Regression Tests

- published program JSON remains schema-valid
- workout history still works for old `TrainingProgram`
- substitutions/progression still read published program correctly

## Recommendation Summary

Build a new unified editable `ProgramDraft` layer.

Do not merge AI and manual at the published program level.

Make AI an assistant that operates on explicit scoped draft actions.

Keep `TrainingProgram` as the compiled immutable runtime program.

This gives the best long-term product model with the least risk to training and history features.

