# GymKompis Implementation Plan

## 1. Product Summary

GymKompis is a Django web application for generating, following, logging, and evaluating gym programs.

Core goals:

- Users can register and log in.
- v2 users can register and sign in with Google.
- Users can request a training program in plain English.
- The app sends the request to OpenAI and stores the returned program JSON in the database as `current_program`.
- Users can open a `Train` page, choose a day from their active program, and log actual performed exercise data.
- A single JSON document should represent the full performed workout for that day, updated exercise-by-exercise as the user submits progress.
- Premium users can get AI evaluation of logged workouts automatically, and all users can request evaluation on demand.
- When generating a new plan, the system can attach recent workout history so the LLM can adapt the next plan.

## 2. Recommended Architecture

### Stack

- Backend: Django
- API/UI: Django templates first, with optional HTMX for smoother interaction
- Database: Supabase PostgreSQL
- Hosting: Render web service
- Auth: Django auth initially, `django-allauth` for Google in v2
- Payments/subscriptions: Stripe after MVP, with premium feature gating modeled from day one
- OpenAI integration: server-side service layer
- Background jobs: recommended for LLM tasks using Celery/RQ or Render background worker

### High-level design

- Django remains the source of truth for users, plans, workout logs, evaluations, and subscription state.
- Supabase is used as managed PostgreSQL, not as the primary auth provider.
- OpenAI calls are encapsulated in service modules so prompts, schemas, retries, and logging stay isolated from views.
- JSON is stored where the product explicitly needs flexible AI output and workout snapshots.
- Relational models should still be used around the JSON to support querying, permissions, analytics, and future growth.

## 3. Main User Flows

### 3.1 Registration and login

Phase 1:

- Email/password registration
- Login/logout
- Password reset
- Profile page

Phase 2:

- Google sign-in and sign-up using `django-allauth`
- Optional account linking between password and Google login

### 3.2 Generate training plan

1. User opens `Create Program`
2. User describes goals and constraints in plain English
3. Backend validates input and collects optional context:
   - age
   - training experience
   - injuries/limitations
   - gym equipment access
   - preferred training days
   - session length
4. Backend optionally attaches recent workout history
5. Backend calls OpenAI with strict JSON schema instructions
6. Response JSON is validated
7. Valid program JSON is stored in `current_program`
8. Previous program is archived for history/versioning

### 3.3 Train workflow

1. User opens `Train`
2. App shows active program days as a simple list:
   - Monday - Full Body Strength A
   - Tuesday - Day Off
   - Wednesday - Lower Body + Core
3. User selects a day
4. App displays planned exercises for that day
5. For each exercise, user sees planned:
   - sets
   - reps
   - weight
   - notes/focus
6. User enters actual performance:
   - performed sets
   - performed reps
   - performed weight
   - optional per-set effort/RPE
   - notes/pain/difficulty
7. User submits exercise
8. Backend updates one workout-session JSON document for that day
9. After workout completion, premium flow can trigger evaluation automatically

### 3.4 Request evaluation

- Manual evaluation button available after a workout and from workout history
- Premium users can also receive automatic evaluation after workout completion
- Users should also be able to request an evaluation across multiple sessions or a date range such as a week or month
- Evaluation should summarize:
  - adherence
  - progression
  - fatigue/recovery signals
  - next-step suggestions

### 3.5 Generate a new plan using previous work

- User requests a new program
- Backend includes a selected number of recent completed workouts
- LLM is instructed to consider:
  - consistency
  - completed loads/reps
  - difficulty feedback
  - skipped exercises
  - joint pain / fatigue notes
- The number of historical workouts included should be configurable per user, with a sensible system default

## 4. Recommended JSON Formats

The proposed JSON in the prompt should be improved for consistency, validation, and easier rendering.

### 4.1 Recommended `current_program` JSON

Use an array of day objects instead of an array containing day-name keys. This is easier to validate and iterate through.

```json
{
  "version": 1,
  "program_name": "Strength & Mobility 60+ (Golf Support)",
  "goal_summary": "Build full-body strength, improve mobility, and support golf performance.",
  "duration_weeks": 8,
  "days_per_week": 4,
  "days": [
    {
      "day_key": "monday",
      "day_label": "Monday",
      "name": "Full Body Strength A",
      "type": "training",
      "notes": "Controlled tempo, joint-friendly loading.",
      "exercises": [
        {
          "exercise_key": "leg-press-machine",
          "name": "Leg Press (Machine)",
          "order": 1,
          "modality": "machine",
          "set_plan": [
            {
              "set_number": 1,
              "target_reps": "10-12",
              "load_guidance": "Start light to moderate",
              "target_effort_rpe": 7
            },
            {
              "set_number": 2,
              "target_reps": "10-12",
              "load_guidance": "Start light to moderate",
              "target_effort_rpe": 7
            },
            {
              "set_number": 3,
              "target_reps": "10-12",
              "load_guidance": "Start light to moderate",
              "target_effort_rpe": 8
            }
          ],
          "rest_seconds": 90,
          "focus": "Leg strength, joint-friendly",
          "instructions": "Use full foot pressure and avoid locking knees.",
          "video_url": "https://example.com/videos/leg-press-machine"
        }
      ]
    },
    {
      "day_key": "tuesday",
      "day_label": "Tuesday",
      "name": "Day Off",
      "type": "rest",
      "notes": "Optional walking and mobility work.",
      "exercises": []
    }
  ]
}
```

### Why this format is better

- Stable keys for frontend rendering
- Easier schema validation
- Easier versioning
- Easier to support rest days, cardio days, and rehab days
- Easier to extend later with warmups, supersets, progression rules, and deload weeks
- Supports linking each exercise to a demonstration video

### 4.2 Recommended workout log JSON

This JSON should represent one performed workout session for one user on one date for one planned day.

```json
{
  "version": 1,
  "program_id": 42,
  "program_version": 3,
  "workout_date": "2026-04-02",
  "planned_day_key": "monday",
  "planned_day_label": "Monday",
  "planned_day_name": "Full Body Strength A",
  "status": "in_progress",
  "started_at": "2026-04-02T18:30:00Z",
  "completed_at": null,
  "exercises": [
    {
      "exercise_key": "leg-press-machine",
      "name": "Leg Press (Machine)",
      "order": 1,
      "modality": "machine",
      "planned": {
        "set_plan": [
          {
            "set_number": 1,
            "target_reps": "10-12",
            "load_guidance": "Start light to moderate",
            "target_effort_rpe": 7
          },
          {
            "set_number": 2,
            "target_reps": "10-12",
            "load_guidance": "Start light to moderate",
            "target_effort_rpe": 7
          },
          {
            "set_number": 3,
            "target_reps": "10-12",
            "load_guidance": "Start light to moderate",
            "target_effort_rpe": 8
          }
        ]
      },
      "actual_sets": [
        {
          "set_number": 1,
          "completed": true,
          "reps": 12,
          "weight": 80,
          "effort_rpe": 7
        },
        {
          "set_number": 2,
          "completed": true,
          "reps": 12,
          "weight": 80,
          "effort_rpe": 8
        }
      ],
      "exercise_notes": "Felt stable, slight knee stiffness during first set.",
      "submitted_at": "2026-04-02T18:42:00Z"
    }
  ],
  "session_notes": "",
  "evaluation_status": "not_requested"
}
```

### Recommendation on storage

To satisfy the product requirement, store the workout as one JSON field per session and update it as exercises are submitted.

Also recommended:

- Keep one relational `WorkoutSession` record per workout
- Store the snapshot JSON in a `session_json` field
- Optionally also keep normalized `ExerciseLog` / `SetLog` rows later for analytics

This hybrid approach gives flexibility now without blocking future reporting features.

### 4.3 Recommended v1 `current_program` schema contract

The example above should be treated as a real v1 schema contract, not only as an illustration.

Required top-level fields:

- `version`
- `program_name`
- `goal_summary`
- `duration_weeks`
- `days_per_week`
- `weight_unit`
- `days`

Top-level field rules:

- `version`: integer, must be `1`
- `program_name`: string, 1 to 120 chars
- `goal_summary`: string, 1 to 500 chars
- `duration_weeks`: integer, 1 to 24
- `days_per_week`: integer, 1 to 7
- `weight_unit`: enum: `kg`, `lb`
- `program_notes`: optional string, max 1000 chars
- `days`: array, 1 to 7 items

Each `day` object:

- `day_key`: enum: `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, `saturday`, `sunday`
- `day_label`: string
- `name`: string, 1 to 120 chars
- `type`: enum: `training`, `rest`, `cardio`, `mobility`, `rehab`
- `notes`: optional string, max 500 chars
- `exercises`: array, may be empty for non-training days

Each `exercise` object:

- `exercise_key`: lowercase slug string, max 80 chars
- `name`: string, 1 to 120 chars
- `order`: integer, minimum 1
- `modality`: enum: `barbell`, `dumbbell`, `machine`, `bodyweight`, `cable`, `kettlebell`, `band`, `mobility`, `cardio`, `other`
- `focus`: optional string, max 300 chars
- `instructions`: string, 1 to 1000 chars
- `video_url`: nullable URL string, max 500 chars
- `rest_seconds`: integer, 0 to 600
- `notes`: optional string, max 500 chars
- `set_plan`: array, 1 to 10 items

Each `set_plan` item:

- `set_number`: integer, 1 to 20
- `target_reps`: string, 1 to 20 chars
- `load_guidance`: optional string, max 100 chars
- `target_effort_rpe`: nullable number, 1 to 10

Validation rules:

- `additionalProperties` should be treated as false for all schema objects
- `video_url` should be present for every exercise, but may be `null`
- `training` days should normally contain one or more exercises
- `rest` days should normally contain zero exercises

### 4.4 Recommended v1 `workout_session` schema contract

Required top-level fields:

- `version`
- `program_id`
- `program_version`
- `workout_date`
- `planned_day_key`
- `planned_day_name`
- `weight_unit`
- `status`
- `exercises`

Top-level field rules:

- `version`: integer, must be `1`
- `program_id`: integer, minimum 1
- `program_version`: integer, minimum 1
- `workout_date`: ISO date string
- `planned_day_key`: string
- `planned_day_label`: optional string
- `planned_day_name`: string
- `weight_unit`: enum: `kg`, `lb`
- `status`: enum: `in_progress`, `completed`, `abandoned`
- `started_at`: nullable ISO datetime
- `completed_at`: nullable ISO datetime
- `session_notes`: optional string, max 1000 chars
- `evaluation_status`: enum: `not_requested`, `queued`, `completed`, `failed`
- `exercises`: array

Each logged `exercise` object:

- `exercise_key`: lowercase slug string
- `name`: string
- `order`: integer, minimum 1
- `modality`: string
- `status`: enum: `pending`, `completed`, `skipped`
- `planned`: object containing `set_plan`
- `actual_sets`: array
- `exercise_notes`: optional string, max 500 chars
- `submitted_at`: nullable ISO datetime

Each planned set inside `planned.set_plan`:

- `set_number`: integer, minimum 1
- `target_reps`: string
- `load_guidance`: optional string
- `target_effort_rpe`: nullable number, 1 to 10

Each `actual_set` item:

- `set_number`: integer, minimum 1
- `completed`: boolean
- `reps`: nullable integer, 0 to 1000
- `weight`: nullable number, 0 to 2000
- `effort_rpe`: nullable number, 1 to 10
- `notes`: optional string, max 300 chars

Validation rules:

- `additionalProperties` should be treated as false for all schema objects
- `actual_sets` can be partially filled while the workout is in progress
- exercise `status` should switch to `completed` when the user submits the exercise

### 4.5 Recommended v1 `history_summary` schema contract

This JSON should be derived by the backend and attached when asking the LLM to generate a new plan using previous work.

Required fields:

- `version`
- `session_count`
- `date_range`
- `adherence_summary`
- `exercise_trends`

Field rules:

- `version`: integer, must be `1`
- `session_count`: integer, 0 to 100
- `date_range.start_date`: ISO date string
- `date_range.end_date`: ISO date string
- `adherence_summary.completed_sessions`: integer, minimum 0
- `adherence_summary.skipped_sessions`: integer, minimum 0
- `adherence_summary.average_effort_rpe`: nullable number, 1 to 10
- `exercise_trends`: array of exercise trend objects
- `reported_issues`: optional array of short strings

Each `exercise_trends` item:

- `exercise_key`: string
- `name`: string
- `best_recent_weight`: nullable number
- `best_recent_reps`: nullable integer
- `trend_note`: string, max 300 chars

Recommended example:

```json
{
  "version": 1,
  "session_count": 8,
  "date_range": {
    "start_date": "2026-03-01",
    "end_date": "2026-03-31"
  },
  "adherence_summary": {
    "completed_sessions": 8,
    "skipped_sessions": 1,
    "average_effort_rpe": 7.4
  },
  "exercise_trends": [
    {
      "exercise_key": "leg_press_machine",
      "name": "Leg Press (Machine)",
      "best_recent_weight": 110,
      "best_recent_reps": 12,
      "trend_note": "Load increased steadily over the period."
    }
  ],
  "reported_issues": [
    "Mild knee stiffness noted during the first working set on two sessions."
  ]
}
```

## 5. Suggested Django Data Model

### 5.1 User and profile

`User`

- Django user model
- Use a custom user model from day one

`UserProfile`

- user
- display_name
- birth_year or age_range
- training_experience
- injuries_limitations
- equipment_access
- preferred_language
- timezone
- preferred_weight_unit (`kg`, `lb`)
- subscription_tier (`free`, `premium`)
- onboarding_completed
- plan_history_window_sessions (optional override for how many recent completed sessions to include in new-plan requests)

### 5.2 Program models

`TrainingProgram`

- user
- name
- status (`active`, `archived`, `draft`)
- request_prompt
- current_program (JSONField)
- version_number
- source (`ai_generated`, `manual`)
- created_at
- updated_at

`ProgramGenerationRequest`

- user
- prompt_text
- attached_history_summary (JSONField or TextField)
- llm_model
- prompt_version
- raw_llm_response
- validated_program_json
- status
- error_message
- token_usage_input
- token_usage_output
- created_at

### 5.3 Workout models

`WorkoutSession`

- user
- program
- planned_day_key
- planned_day_label
- planned_day_name
- workout_date
- status (`in_progress`, `completed`, `abandoned`)
- session_json (JSONField)
- submission_version
- last_exercise_submission_at
- started_at
- completed_at
- created_at
- updated_at

Recommended constraints:

- one active in-progress session per user per workout date per planned day
- protect exercise submission updates with optimistic locking or transactional update checks

`WorkoutEvaluation`

- user
- evaluation_type (`session`, `period`)
- workout_session (nullable for period evaluations)
- evaluation_start_date (nullable)
- evaluation_end_date (nullable)
- included_session_ids (JSONField or relational link table)
- requested_by_user
- auto_generated
- llm_model
- prompt_version
- input_json
- evaluation_json
- summary_text
- created_at

### 5.4 Billing/subscription models

If Stripe is added:

`Subscription`

- user
- plan_code
- status
- stripe_customer_id
- stripe_subscription_id
- current_period_end
- created_at
- updated_at

`BillingEvent`

- user
- subscription
- stripe_event_id
- event_type
- payload
- processed_at
- created_at

## 6. App / Module Breakdown

Recommended Django apps:

- `accounts` for auth, profile, Google login
- `programs` for plan generation and storage
- `training` for day selection and workout logging
- `evaluations` for AI workout analysis
- `subscriptions` for premium gating
- `core` for shared utilities, settings helpers, base templates
- `ops` optional later for audit/event logging, admin support views, and background-job helpers

## 7. Pages and Endpoints

### Pages

- Landing page
- Register / login / logout
- Dashboard
- Profile / onboarding
- Create program
- Current program view
- Train day list
- Train day detail
- Workout history
- Workout evaluation view
- Subscription / upgrade page

### Recommended backend endpoints

- `POST /programs/generate/`
- `GET /programs/current/`
- `GET /train/`
- `GET /train/<session_or_day>/`
- `POST /train/session/start/`
- `POST /train/session/<id>/exercise/<exercise_key>/submit/`
- `POST /train/session/<id>/complete/`
- `POST /evaluations/workout/<id>/request/`
- `POST /evaluations/period/request/`

If using HTMX, these can remain regular Django views returning partial templates.

## 8. OpenAI Integration Plan

### 8.1 Program generation

The program-generation service should:

- Accept structured user input plus free-text goal description
- Attach recent workout context when available
- Send a prompt asking for strict JSON only
- Ask the LLM to include a demonstration video link for each exercise where available
- Validate against a server-side schema before saving
- Reject or repair malformed output

### 8.2 Evaluation service

The workout-evaluation service should:

- Accept either:
  - one workout session JSON
  - multiple workout sessions / a date-range summary for period evaluation
- Compare planned vs actual across the selected scope
- Produce structured evaluation JSON plus short user-facing feedback
- Be gated by subscription tier for automatic evaluations
- Allow manual evaluation if product rules permit

Recommended evaluation modes:

- `session` evaluation for immediate post-workout feedback
- `period` evaluation for weekly, monthly, or custom multi-session feedback

### 8.3 Important implementation detail

Do not trust raw LLM JSON directly.

Use:

- strict schema instructions
- server-side validation
- retry/reformat logic
- logging of invalid responses for debugging

Video-link note:

- The app should treat LLM-provided video links as untrusted external data
- Validate that `video_url` is a properly formed URL before storing/displaying it
- Consider restricting to allowed domains in a later version if link quality becomes inconsistent

## 9. Plan Generation Context Strategy

When requesting a new plan, attach a compact summary of recent workouts instead of sending unlimited raw history.

Recommended initial rule:

- Include the most recent 6 to 12 completed workout sessions
- Build a compact derived summary:
  - exercises completed
  - top recent loads
  - rep ranges achieved
  - skipped exercises
  - average effort
  - pain/difficulty notes

This will reduce token cost and improve signal quality.

Implementation note:

- Store a system default for included history
- Allow a per-user override without changing the generation pipeline

## 10. Premium Logic

Free tier suggestion:

- register/login
- generate program with monthly limit
- log workouts
- manually request limited evaluation

Premium tier suggestion:

- unlimited or higher-limit plan generation
- automatic post-workout evaluation
- more history-aware program updates
- deeper feedback and progression suggestions
- longer workout history retention and analytics

## 11. Security and Reliability

- Use a custom user model from the start
- Store secrets in Render environment variables
- Never expose OpenAI keys in frontend code
- Enforce per-user access control on all plans and sessions
- Add rate limiting for generation and evaluation endpoints
- Sanitize and validate user input sizes
- Add audit logging for AI requests and failures
- Use CSRF protection and secure session settings
- Use PostgreSQL JSONB indexing where helpful
- Use transactional updates or idempotency protection for exercise submission endpoints
- Track user timezone so workout dates and period evaluations are computed correctly

## 12. Operations and Support

- Add Django admin support for users, programs, workout sessions, evaluations, and subscriptions
- Log prompt version and model name for every LLM-backed generation/evaluation request
- Add structured application logging for generation failures and webhook processing
- Add basic healthcheck endpoint for Render
- Add error monitoring later, for example Sentry
- Add a management command or admin action to re-run failed program generation/evaluation jobs safely

## 13. Deployment Plan

### Render

- Create Render web service for Django app
- Use Gunicorn
- Run migrations during deploy
- Configure static files with WhiteNoise or external storage later
- Set environment variables:
  - `DJANGO_SECRET_KEY`
  - `DEBUG`
  - `DATABASE_URL`
  - `OPENAI_API_KEY`
  - `ALLOWED_HOSTS`
  - `CSRF_TRUSTED_ORIGINS`
  - Google OAuth credentials in v2
  - Stripe keys when subscriptions are added

### Supabase

- Create PostgreSQL database in Supabase
- Use Supabase connection string as Django `DATABASE_URL`
- Enable backups and connection pooling if needed
- Keep Django migrations as the schema source of truth

## 14. Recommended Milestones

### Milestone 1: Foundation

- Create Django project and apps
- Configure Supabase PostgreSQL
- Configure Render deployment
- Create custom user model
- Build registration, login, logout, profile
- Add base templates, navigation, and HTMX integration
- Add healthcheck endpoint and baseline logging

### Milestone 2: Program generation

- Create program request form
- Build OpenAI generation service
- Validate and store `current_program`
- Show active program in UI
- Store prompt version, model name, and token usage for generation requests

### Milestone 3: Training workflow

- Build `Train` day list
- Build day detail page
- Create `WorkoutSession`
- Update `session_json` as each exercise is submitted
- Add workout history page
- Add duplicate-submit protection and safe session-update logic

### Milestone 4: Evaluation

- Add manual evaluation flow
- Add premium gating
- Add automatic evaluation after completed workout for premium users
- Add period evaluation by date range or selected sessions
- Store prompt version, model name, and token usage for evaluations

### Milestone 5: Smarter plan refresh

- Summarize recent workouts
- Attach history to new plan requests
- Archive previous programs
- Respect per-user history-window preferences

### Milestone 6: v2 features

- Google sign-in
- analytics dashboard
- notifications/reminders

### Milestone 7: Billing

- Add minimal Stripe integration
- Launch one premium monthly plan
- Add hosted Stripe Checkout flow
- Sync subscription state through Stripe webhooks
- Add cancel flow

## 15. Testing Strategy

### Backend tests

- model tests for program/session JSON handling
- service tests for prompt building and response validation
- permission tests for user data isolation
- workflow tests for creating and updating workout sessions
- billing tests for premium access rules
- timezone/date-range tests for workout grouping and period evaluations
- idempotency/concurrency tests for repeated exercise submissions

### Integration tests

- full registration/login flow
- generate plan flow
- start workout and submit exercises
- request evaluation
- request period evaluation for multiple sessions
- generate new plan with history attached
- exercise resubmission / duplicate-click handling
- premium-gated vs free-gated flows

### Manual QA checklist

- malformed LLM JSON handling
- empty/rest day rendering
- duplicate exercise submissions
- partial workout save/resume
- single-session vs period evaluation behavior
- premium vs free restrictions
- deployment env var issues on Render
- timezone-sensitive date boundaries
- broken or missing external video links

## 16. Confirmed Design Decisions

The following design choices are now confirmed for the current implementation plan.

### 1. Authentication scope

- Phase 1 uses Django email/password authentication
- Google sign-in remains a v2 feature

### 2. User model

- Use a custom Django user model from day one

### 3. Frontend style

- Use Django templates with HTMX

### 4. Program JSON schema status

- A recommended v1 schema contract is now documented in this plan
- The implementation should keep schema versioning in mind from the start

### 5. Workout log storage

- Use the recommended hybrid model
- Store one `WorkoutSession` row per performed day
- Store the full performed workout in `session_json`

### 6. Workout logging granularity

- Store effort/RPE per set

### 7. Session submission flow

- Users submit one exercise at a time
- Each submission updates the same workout-session JSON document

### 8. Program versioning

- Archive previous programs
- Keep one active current program

### 9. History attached to new-program generation

- Use the recommended compact history-summary approach
- Default to the most recent 6 to 12 completed sessions
- Make the number of included sessions configurable per user, with a system default

### 10. LLM execution mode

- Use synchronous requests in the initial release

### 11. Evaluation access

- Free users get limited manual evaluations
- Premium users get automatic and richer evaluation access
- The evaluation system should support both single-session and period-based evaluation scopes

### 12. Premium scope

- Premium includes automatic post-workout evaluation
- Premium includes higher or unlimited generation limits
- Premium includes richer adaptation, feedback, and analytics features

### 13. Billing implementation timing

- Stripe is intentionally delayed until after MVP core flows are stable
- Premium logic should still be modeled in code from the start so Stripe can plug in later without redesign
- First billing release should use a minimal Stripe scope

### 14. Language support

- Keep the app translation-ready
- Store user language preference
- Start with English-oriented output and a structure that can support Swedish later

### 15. Onboarding depth

- Collect enough structured profile data to improve plan quality:
  - goals
  - experience
  - injuries/limitations
  - equipment access
  - preferred training days
  - session length
- The onboarding structure should be easy to evolve in future releases

### 16. Data modeling approach

- Use the recommended hybrid relational + JSON design

### 17. Mobile-first training UX

- Treat mobile usability as a first-class requirement from the start

### 18. Safety and validation

- Use strict schema validation for LLM outputs
- Use conservative prompt rules and validation/retry logic

### 19. Exercise naming strategy

- Start with flexible AI-generated exercise names
- Plan for an internal exercise catalog later
- Ask the LLM to include a video link for each exercise in the returned JSON where possible

### 20. Day and modality support

- The program schema should support multiple day types from the start:
  - training
  - rest
  - cardio
  - mobility
  - rehab
- Training content should be flexible enough to include:
  - barbell/free weights
  - dumbbells
  - machines
  - bodyweight/calisthenics
  - mobility work

## 17. Remaining Decisions

These items still remain open:

### 1. Final approval of the recommended v1 program schema

- The plan now contains a recommended v1 schema contract
- This still needs product-level confirmation before implementation starts

## 18. Suggested Improvements Beyond the Initial Request

These would add real product value:

- onboarding questionnaire to improve prompt quality
- exercise substitution feature when equipment is unavailable
- progression recommendations week to week
- deload week detection based on fatigue/performance
- pain flagging and safe fallback suggestions
- workout streaks and adherence metrics
- coach/admin dashboard for support
- export workout history to CSV/PDF
- email reminders or push reminders
- PR tracking for selected lifts
- notes on sleep, soreness, and recovery
- mobile-first training screen for gym use
- warm-up and cooldown blocks in program JSON
- rest timer per exercise
- supersets/circuits support in future JSON version

## 19. Recommended Evaluation Schemas

To support both immediate workout feedback and longer-range analysis, the app should support two evaluation JSON shapes.

### 18.1 Session evaluation schema

Use this for evaluating one completed workout session.

```json
{
  "version": 1,
  "evaluation_type": "session",
  "session_id": 123,
  "overall_summary": "Solid session with good adherence and appropriate effort.",
  "adherence_score": 88,
  "effort_summary": "Effort was mostly in the target range.",
  "recovery_flag": "low",
  "progression_signals": [
    "Leg press performance was consistent across sets."
  ],
  "exercise_feedback": [
    {
      "exercise_key": "leg_press_machine",
      "comment": "You completed the planned volume with stable effort.",
      "suggested_next_step": "Increase weight slightly next session if form remains strong."
    }
  ],
  "recommendations": [
    "Keep the same rep range next session.",
    "Add a longer warm-up if knee stiffness continues."
  ]
}
```

### 18.2 Period evaluation schema

Use this for evaluating multiple sessions, for example the last week, last month, or a custom set of sessions.

```json
{
  "version": 1,
  "evaluation_type": "period",
  "evaluation_scope": {
    "scope_type": "date_range",
    "start_date": "2026-03-01",
    "end_date": "2026-03-31",
    "session_ids": [101, 104, 110, 115]
  },
  "summary": {
    "overall_adherence_score": 82,
    "consistency_score": 88,
    "progression_score": 74,
    "recovery_risk": "low"
  },
  "highlights": [
    "You trained consistently three times per week.",
    "Lower-body loads improved across the month."
  ],
  "exercise_trends": [
    {
      "exercise_key": "leg_press_machine",
      "name": "Leg Press (Machine)",
      "trend": "improving",
      "note": "Weight and reps both increased over the period."
    }
  ],
  "issues": [
    "Two upper-body sessions were skipped.",
    "Effort was high on consecutive days in week three."
  ],
  "recommendations": [
    "Increase leg press load modestly in the next block.",
    "Keep one easier recovery session each week."
  ]
}
```

### 18.3 Recommended v1 evaluation schema contracts

The evaluation examples above should also be treated as schema contracts rather than loose examples.

Session evaluation required fields:

- `version`
- `evaluation_type`
- `session_id`
- `overall_summary`
- `adherence_score`
- `effort_summary`
- `recovery_flag`
- `progression_signals`
- `recommendations`

Session evaluation field rules:

- `version`: integer, must be `1`
- `evaluation_type`: must be `session`
- `session_id`: integer, minimum 1
- `overall_summary`: string, 1 to 1000 chars
- `adherence_score`: integer, 0 to 100
- `effort_summary`: string, max 500 chars
- `recovery_flag`: enum: `none`, `low`, `moderate`, `high`
- `progression_signals`: array of strings, max 10 items
- `exercise_feedback`: optional array with per-exercise feedback objects
- `recommendations`: array of strings, 1 to 8 items

Each `exercise_feedback` item:

- `exercise_key`: string
- `comment`: string, max 400 chars
- `suggested_next_step`: optional string, max 200 chars

Period evaluation required fields:

- `version`
- `evaluation_type`
- `evaluation_scope`
- `summary`
- `highlights`
- `recommendations`

Period evaluation field rules:

- `version`: integer, must be `1`
- `evaluation_type`: must be `period`
- `evaluation_scope.scope_type`: enum: `date_range`, `session_list`
- `evaluation_scope.start_date`: nullable ISO date string
- `evaluation_scope.end_date`: nullable ISO date string
- `evaluation_scope.session_ids`: array of integers
- `summary.overall_adherence_score`: integer, 0 to 100
- `summary.consistency_score`: integer, 0 to 100
- `summary.progression_score`: integer, 0 to 100
- `summary.recovery_risk`: enum: `none`, `low`, `moderate`, `high`
- `highlights`: array of strings, max 10 items
- `exercise_trends`: optional array of trend objects
- `issues`: optional array of strings
- `recommendations`: array of strings, 1 to 8 items

Each `exercise_trends` item:

- `exercise_key`: string
- `name`: string
- `trend`: enum: `improving`, `stable`, `declining`, `mixed`
- `note`: string, max 300 chars

Validation rules:

- `additionalProperties` should be treated as false for all schema objects
- session evaluations should reference exactly one completed workout session
- period evaluations should reference either a date range, a session list, or both

### Recommendation

- Keep both schema types in v1 design even if the first implementation starts with session evaluation
- Store the evaluation type and selected scope in the `WorkoutEvaluation` model
- Reuse the same evaluation page pattern for both session and period reports

## 20. Minimal Stripe Architecture

Stripe should be added only after the core MVP is stable. The first billing implementation should stay intentionally small.

### Initial billing scope

- one premium monthly subscription plan
- hosted Stripe Checkout for starting a subscription
- webhook-driven subscription state sync
- simple cancel flow
- no yearly plan in the first release
- no coupons or promo codes in the first release
- no free trial in the first release
- no complex upgrade/downgrade logic in the first release

### Recommended models

`Subscription`

- user
- provider (`stripe`)
- plan_code (`premium_monthly`)
- status (`inactive`, `trialing`, `active`, `past_due`, `canceled`, `incomplete`)
- stripe_customer_id
- stripe_subscription_id
- stripe_price_id
- current_period_start
- current_period_end
- cancel_at_period_end
- created_at
- updated_at

`BillingEvent`

- user
- subscription
- stripe_event_id
- event_type
- payload (JSONField)
- processed_at
- created_at

### Recommended Stripe flows

#### Start subscription

1. Authenticated user clicks `Upgrade`
2. Django creates a Stripe Checkout Session for the premium monthly price
3. User completes payment on Stripe-hosted Checkout
4. Stripe redirects back to the app
5. Webhook confirms the subscription state and updates local records
6. App enables premium features only after webhook-confirmed activation

#### Cancel subscription

1. User clicks `Cancel premium`
2. Django updates the Stripe subscription to cancel at period end
3. Local `Subscription` record is updated after webhook confirmation
4. Premium access remains until the paid period ends

#### Failed payment / billing issue

- Stripe webhook updates local status to `past_due` or related state
- App can show billing warning in the account page
- Premium behavior can be reduced based on business rules

### Recommended webhook events to handle

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

### Important implementation rules

- Use Stripe webhooks as the source of truth for subscription status
- Do not grant long-term premium access based only on the redirect from Checkout
- Keep feature gating centralized in application code
- Keep plan codes internal so app logic does not depend directly on Stripe display names
- Log processed billing events for traceability and replay safety

### Feature-gating approach

The app should be built now so these checks already exist:

- `user_has_premium(user)`
- `can_auto_evaluate(user)`
- `can_request_manual_evaluation(user)`
- `can_generate_program(user)`

This lets the MVP run with simple application-managed premium flags before Stripe is connected.

## 21. Recommended First Release Scope

For a strong MVP, keep v1 focused on:

- email/password auth
- profile/onboarding
- AI-generated program stored in `current_program`
- train page with day selection
- exercise-by-exercise logging into one session JSON
- workout history
- manual evaluation
- support for both single-session and date-range evaluation requests
- program regeneration using recent workout summaries

Then add in v2:

- Google sign-in
- premium subscription billing
- automatic evaluation
- analytics and richer coaching features

## 22. Detailed Implementation Plan

This section translates the product plan into an execution order suitable for building the app.

### Phase 0: Project setup and scaffolding

Deliverables:

- Create Django project and app structure
- Configure dependency management
- Add base settings split for local and production
- Configure PostgreSQL connection through environment variables
- Add WhiteNoise, Gunicorn, and a healthcheck endpoint
- Create base template, layout, navigation, flash-message pattern, and HTMX integration

Implementation tasks:

1. Create project:
   - `gymkompis/`
   - `core/`
   - `accounts/`
   - `programs/`
   - `training/`
   - `evaluations/`
   - `subscriptions/`
2. Configure:
   - custom user model
   - static files
   - template dirs
   - login/logout redirects
   - Render-ready settings
3. Add local `.env` support and production env-var documentation
4. Add baseline logging configuration and healthcheck route

### Phase 1: Accounts and onboarding

Deliverables:

- registration/login/logout/password reset
- user profile editing
- onboarding form capturing training context
- language, timezone, weight unit, and history-window preferences

Implementation tasks:

1. Implement custom user model with email-based auth
2. Add `UserProfile` and onboarding forms
3. Build protected dashboard shell for signed-in users
4. Add Django admin configuration for user/profile management
5. Add tests for auth flow and profile permissions

### Phase 2: Program generation

Deliverables:

- program request form
- LLM prompt builder
- schema validation for `current_program`
- storage of generation request metadata and active program
- current program view page

Implementation tasks:

1. Create `TrainingProgram` and `ProgramGenerationRequest` models
2. Implement schema validation module for `current_program`
3. Build prompt composer using:
   - onboarding data
   - user free-text request
   - optional history summary
4. Implement OpenAI service wrapper:
   - model selection
   - prompt version tracking
   - retries for invalid schema output
   - usage logging
5. Build create-program page and current-program page
6. Archive old active program when a new one becomes active
7. Add tests for validation, storage, and access control

### Phase 3: Training workflow

Deliverables:

- train day list
- start/resume workout session
- day detail page with one-exercise-at-a-time logging
- stored `session_json`
- workout history list/detail

Implementation tasks:

1. Create `WorkoutSession` model and session helper service
2. Add logic to create or resume one in-progress session for a user/day/date
3. Render planned exercises from `current_program`
4. Build per-exercise form:
   - actual reps
   - actual weight
   - per-set effort
   - notes
5. Submit with HTMX partial updates
6. Protect against duplicate submissions:
   - transaction
   - version check or row lock
7. Add complete-workout flow and session status transitions
8. Add tests for partial progress, duplicate-clicks, and permissions

### Phase 4: Evaluation workflows

Deliverables:

- manual single-session evaluation
- manual period evaluation
- premium gating
- optional automatic evaluation after workout completion
- evaluation history/detail views

Implementation tasks:

1. Create `WorkoutEvaluation` model
2. Implement schema validation modules for:
   - session evaluation
   - period evaluation
3. Build evaluation scope selection:
   - current session
   - selected sessions
   - date range
4. Build evaluation prompt builder and OpenAI service wrapper
5. Save prompt version, model, and usage metadata
6. Add UI for viewing evaluation results
7. Add premium rules:
   - free manual limits
   - premium auto evaluation
8. Add tests for gating, scope selection, and evaluation storage

### Phase 5: Smarter regeneration using history

Deliverables:

- backend history summarizer
- user preference for attached-history window
- new-plan generation informed by recent work

Implementation tasks:

1. Build `history_summary` generator from completed sessions
2. Respect user-level history window override
3. Include movement trends, adherence, effort, and pain notes
4. Pass compact summary into new plan requests
5. Add tests to verify the correct sessions are included

### Phase 6: Premium architecture without Stripe

Deliverables:

- centralized feature-gating helpers
- premium-ready data model
- upgrade page placeholder

Implementation tasks:

1. Implement helpers:
   - `user_has_premium`
   - `can_generate_program`
   - `can_request_manual_evaluation`
   - `can_auto_evaluate`
2. Add per-plan and per-feature limits in configuration
3. Build upgrade page and account status display
4. Add tests for free vs premium behavior

### Phase 7: Ops and production hardening

Deliverables:

- admin usability
- structured logs
- deployment checklist
- production validation and rollback safety

Implementation tasks:

1. Register all key models in Django admin
2. Add structured logs around LLM requests and failures
3. Add management commands for:
   - seed demo data
   - replay or retry failed generations/evaluations
4. Validate Render deployment:
   - migrations
   - static files
   - env vars
   - healthcheck
5. Validate Supabase connection pooling and backups

### Phase 8: Stripe integration

Deliverables:

- minimal Stripe subscription flow
- webhook handling
- synced premium status

Implementation tasks:

1. Create `Subscription` and `BillingEvent` flows
2. Add Stripe Checkout session creation
3. Add webhook endpoint and event verification
4. Update local subscription records from webhook events
5. Expose cancel-at-period-end action in account UI
6. Add tests for webhook processing and premium-state changes

### Suggested build order inside the codebase

1. Project/settings/bootstrap
2. Accounts/onboarding
3. Program generation and validation
4. Training session creation and logging
5. Evaluation flows
6. History-aware regeneration
7. Premium gating
8. Production hardening
9. Stripe

### Suggested definition of done for MVP

The MVP should be considered complete when all of the following are true:

- a user can register and complete onboarding
- a user can generate a valid program and see it rendered
- a user can start a workout and log exercises one at a time
- the workout is recoverable and stored correctly in `session_json`
- a user can request both single-session and period evaluations
- a user can generate a new plan that includes recent training history
- free vs premium rules are enforced in code
- the app deploys successfully on Render with Supabase
- core tests pass and key manual QA scenarios are verified

## 23. Final Recommendation

The best implementation approach is a hybrid relational + JSON design:

- store `current_program` as validated JSON in the program record
- store each performed workout as a `WorkoutSession` row with a `session_json` snapshot
- use relational records for ownership, history, permissions, and future analytics

This keeps the AI-driven parts flexible while giving the application a stable backend structure that will scale much better than a JSON-only design.
