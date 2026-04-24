# Prompt Inventory

This file lists the prompt-building functions currently used in GymKompis, what each prompt is for, where it is defined, and which OpenAI API path uses it.

It is meant as a maintenance reference, not as a full copy of every dynamic prompt payload. Many prompts are assembled from live user, draft, or exercise data at runtime.

## 1. Program Generation

### 1.1 Full program generation

- Purpose: create a brand new structured training program from a free-text user request.
- Source files:
  - [programs/prompts.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/prompts.py)
  - [programs/services.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/services.py)
- Builder functions:
  - `build_program_generation_instructions()`
  - `build_program_generation_input(prompt_text, profile_context, history_summary, schema)`
- OpenAI call site:
  - `programs.services._generate_llm_program()`
- Output expectation:
  - strict JSON matching `CURRENT_PROGRAM_SCHEMA`

Instruction summary:
- Return strict JSON only
- Include warmups when appropriate
- Use `reps` vs `time` correctly
- Do not invent extra properties
- Keep the plan realistic and beginner-safe

Runtime input includes:
- user free-text request
- profile context
- workout history summary
- schema requirements

### 1.2 Program prompt examples

- Purpose: provide UI examples for the user before generation.
- Source files:
  - [programs/example_prompts.json](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/example_prompts.json)
  - [programs/prompt_examples.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/prompt_examples.py)
- Used by:
  - [templates/programs/generate_program.html](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/templates/programs/generate_program.html)

These are not system prompts sent to OpenAI. They are starter examples shown to the user.

## 2. Unified Draft AI

### 2.1 Complete selected draft days

- Purpose: fill missing or selected days inside an existing editable draft.
- Source files:
  - [programs/prompts.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/prompts.py)
  - [programs/draft_services.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/draft_services.py)
- Builder functions:
  - `build_program_completion_instructions()`
  - `build_program_completion_input(draft_snapshot, target_day_keys, profile_context, history_summary)`
- OpenAI call site:
  - `programs.draft_services.complete_program_draft_with_ai()`
- Output expectation:
  - full program JSON, while preserving non-target days as much as possible

Instruction summary:
- Return strict JSON only
- Preserve the existing draft where possible
- Change target days only
- Keep non-target days aligned with the incoming draft
- Include `instructions`, `rest_seconds`, and `set_plan` for every exercise

Runtime input includes:
- current draft snapshot
- target day keys
- profile context
- history summary

### 2.2 Evaluate draft

- Purpose: review an editable draft without mutating it.
- Source files:
  - [programs/prompts.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/prompts.py)
  - [programs/draft_services.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/draft_services.py)
- Builder functions:
  - `build_program_evaluation_instructions()`
  - `build_program_evaluation_input(draft_snapshot, profile_context, history_summary)`
- OpenAI call site:
  - `programs.draft_services.evaluate_program_draft_with_ai()`
- Output expectation:
  - JSON object with `summary`, `findings`, and `suggested_actions`

Instruction summary:
- Return strict JSON only
- Do not rewrite the draft
- Produce structured findings with severity and suggested fixes

## 3. Exercise Library AI

### 3.1 Exercise instruction drafting

- Purpose: generate short exercise instructions for library entries.
- Source files:
  - [programs/library.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/library.py)
- Builder function:
  - `build_instruction_prompt(exercise_payload)`
- OpenAI call site:
  - `programs.library.generate_ai_instruction()`
- Output expectation:
  - plain text only, 2-3 sentences

Instruction summary:
- plain text only
- no markdown
- include setup
- include main movement cue
- include one safety/form cue
- avoid medical claims

Note:
- There is also a non-AI deterministic fallback template in `build_seed_instruction(payload)`.

### 3.2 Exercise metadata normalization

- Purpose: fill missing exercise metadata like category, movement pattern, and muscle groups.
- Source files:
  - [programs/library.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/library.py)
- Builder function:
  - `build_metadata_prompt(exercise_payload)`
- OpenAI call site:
  - `programs.library.generate_ai_exercise_metadata()`
- Output expectation:
  - JSON with:
    - `category`
    - `movement_pattern`
    - `primary_muscles`
    - `secondary_muscles`
    - `stabilizers`
    - `equipment`

Instruction summary:
- return JSON only
- use arrays of strings for muscle fields
- keep values concise and practical

### 3.3 Missing exercise suggestion

- Purpose: draft a new exercise record when the user searches for something missing from the library.
- Source files:
  - [programs/library.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/library.py)
  - [templates/programs/partials/manual_day_shell.html](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/templates/programs/partials/manual_day_shell.html)
- Builder function:
  - `build_exercise_suggestion_prompt(search_query)`
- OpenAI call site:
  - `programs.library.generate_ai_exercise_suggestion()`
- Output expectation:
  - JSON with:
    - `name`
    - `aliases`
    - `brand`
    - `line`
    - `modality`
    - `library_role`
    - `equipment`
    - `category`
    - `movement_pattern`
    - `primary_muscles`
    - `secondary_muscles`
    - `stabilizers`
    - `supports_reps`
    - `supports_time`
    - `is_static`
    - `unilateral`
    - `instructions`

Instruction summary:
- return JSON only
- use concise gym-library values
- keep `brand` and `line` empty when the query is generic

Note:
- There is also a deterministic fallback in `_deterministic_exercise_suggestion(search_query)`.

## 4. Exercise Image Generation

### 4.1 Instruction image prompt

- Purpose: generate an instructional illustration for an exercise.
- Source files:
  - [programs/image_generation.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/image_generation.py)
- Builder function:
  - `build_exercise_image_prompt(exercise)`
- OpenAI call site:
  - `programs.image_generation._generate_openai_image_bytes()`
  - via `client.images.generate(...)`
- Output expectation:
  - bitmap image, not text

Prompt summary:
- create a clean anatomy-aware instructional fitness illustration
- include exercise name
- include equipment
- include movement pattern
- emphasize primary muscles
- specify unilateral vs bilateral pose
- require neutral background
- forbid logos, watermarks, and text overlays
- ask for realistic proportions and mechanically plausible pose

The prompt is dynamic and also optionally includes:
- secondary muscles
- stabilizers

## 5. Prompt Versioning And Settings

These settings are used to label or route prompt-driven features:

- `OPENAI_MODEL`
- `OPENAI_PROGRAM_PROMPT_VERSION`
- `OPENAI_IMAGE_MODEL`
- `OPENAI_IMAGE_SIZE`
- `OPENAI_IMAGE_QUALITY`

Relevant call sites:
- [programs/services.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/services.py)
- [programs/draft_services.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/draft_services.py)
- [programs/library.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/library.py)
- [programs/image_generation.py](/abs/path/c:/Users/erikj/Documents/Python%20Projects/GymKompis/programs/image_generation.py)

## 6. Quick Index

- Program generation:
  - `programs.prompts.build_program_generation_instructions`
  - `programs.prompts.build_program_generation_input`
- Draft completion:
  - `programs.prompts.build_program_completion_instructions`
  - `programs.prompts.build_program_completion_input`
- Draft evaluation:
  - `programs.prompts.build_program_evaluation_instructions`
  - `programs.prompts.build_program_evaluation_input`
- Exercise instructions:
  - `programs.library.build_instruction_prompt`
  - `programs.library.build_seed_instruction`
- Exercise metadata:
  - `programs.library.build_metadata_prompt`
- Missing-exercise suggestion:
  - `programs.library.build_exercise_suggestion_prompt`
  - `programs.library._deterministic_exercise_suggestion`
- Exercise image prompt:
  - `programs.image_generation.build_exercise_image_prompt`
- User-visible example prompts:
  - `programs/example_prompts.json`

## 7. Maintenance Note

If you change or add prompts, update this file and keep the source location links aligned with the actual builder functions. The safest rule is:

- if a function returns instructions or payload text for OpenAI, add it here
- if a JSON file is shown as a prompt example to the user, add it here
