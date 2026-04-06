from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import HttpResponseForbidden, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
import re

from .forms import (
    AddExerciseToDayForm,
    ExerciseImageCopyForm,
    ExerciseImagePromptForm,
    ExerciseLibraryFilterForm,
    LibraryAdminFilterForm,
    LibraryEnrichAdminForm,
    LibraryExerciseReviewForm,
    LibraryImportAdminForm,
    ManualDayCopyForm,
    ManualExerciseConfigForm,
    ManualProgramDayForm,
    ManualProgramDraftForm,
    ProgramGenerateForm,
    UserExerciseSubmissionForm,
)
from .image_generation import (
    attach_preview_image_to_exercise,
    build_exercise_image_preview,
    build_exercise_image_prompt,
    copy_exercise_image_to_targets,
    delete_exercise_image_preview,
)
from .library import (
    create_user_exercise_submission,
    enrich_exercise_metadata,
    exercise_metadata_gaps,
    find_duplicate_exercise_groups,
    generate_ai_exercise_suggestion,
    import_exercise_library,
    merge_exercise_duplicates,
    root_exercise_queryset,
    suggested_exercise_updates,
    visible_exercise_queryset,
)
from .manual_services import DAY_ORDER, copy_manual_day, create_manual_exercise_for_day, publish_manual_program
from .models import DAY_KEY_CHOICES, Exercise, ManualProgramDay, ManualProgramDraft, ManualProgramExercise, TrainingProgram
from .prompt_examples import load_program_prompt_examples
from .services import build_program_profile_context, generate_program_for_user, restore_program_for_user
from .structure import get_day_blocks


def _exercise_filter_choices(user=None):
    base_queryset = visible_exercise_queryset(user) if user else root_exercise_queryset().filter(is_active=True)
    modalities = list(Exercise.Modality.choices)
    brands = list(base_queryset.values_list("brand", "brand").distinct().order_by("brand"))
    return modalities, [item for item in brands if item[0]]


FILTER_FIELDS = ("query", "modality", "brand", "library_role", "supports_time")
LIBRARY_IMAGE_PREVIEW_SESSION_KEY = "library_admin_image_preview"
IMAGE_COPY_NAME_ALIASES = {
    "ab": "abdominal",
    "abs": "abdominal",
    "crunches": "crunch",
}

SEARCH_FIELDS = (
    "name",
    "aliases",
    "brand",
    "line",
    "category",
    "movement_pattern",
    "equipment",
)


def _get_manual_day_filter_data(request):
    source = request.GET if request.method == "GET" else request.POST
    data = QueryDict(mutable=True)
    for field in FILTER_FIELDS:
        value = source.get(field)
        if value:
            data[field] = value
    return data


def _build_filter_hidden_fields(filter_form):
    if not filter_form.is_bound or not filter_form.is_valid():
        return []
    fields = []
    for key, value in filter_form.cleaned_data.items():
        if value in ("", None, False):
            continue
        fields.append((key, "on" if value is True else str(value)))
    return fields


def _search_tokens(query: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^0-9A-Za-z]+", (query or "").lower())
        if len(token) >= 2
    ]


def _exercise_search_filter(query: str, *, include_external_id: bool = False) -> Q:
    query = (query or "").strip()
    if not query:
        return Q()

    fields = SEARCH_FIELDS + (("external_id",) if include_external_id else ())
    filter_query = Q()
    for field in fields:
        filter_query |= Q(**{f"{field}__icontains": query})

    tokens = _search_tokens(query)
    if len(tokens) > 1:
        for field in fields:
            token_query = Q()
            for token in tokens:
                token_query &= Q(**{f"{field}__icontains": token})
            filter_query |= token_query
    return filter_query


def _entry_summary(entry: ManualProgramExercise) -> str:
    if entry.prescription_type == ManualProgramExercise.PrescriptionType.TIME:
        return f"{entry.sets_count} x {entry.target_seconds or '-'} sec"
    return f"{entry.sets_count} x {entry.target_reps or '-'}"


def _group_exercise_results_by_category(exercises):
    grouped = {}
    for exercise in exercises:
        category = exercise.category or "Uncategorized"
        grouped.setdefault(category, []).append(exercise)
    return [
        {"category": category, "exercises": items}
        for category, items in sorted(grouped.items(), key=lambda item: item[0].lower())
    ]


def _default_manual_day_name(day_key: str) -> str:
    return dict(DAY_KEY_CHOICES).get(day_key, day_key.title())


def _sync_manual_draft_days(draft: ManualProgramDraft, selected_day_keys: list[str]) -> None:
    selected_day_keys = list(dict.fromkeys(selected_day_keys))
    existing_days = {day.day_key: day for day in draft.days.all()}

    for day_key in list(existing_days):
        if day_key not in selected_day_keys:
            existing_days[day_key].delete()

    for day_key in selected_day_keys:
        if day_key in existing_days:
            continue
        ManualProgramDay.objects.create(
            draft=draft,
            day_key=day_key,
            name=_default_manual_day_name(day_key),
            day_type="training",
            notes="",
        )


def _manual_day_workspace_context(request, day, expanded_entry_id=None, invalid_entry_forms=None, submission_form=None):
    invalid_entry_forms = invalid_entry_forms or {}
    filter_data = _get_manual_day_filter_data(request)
    submission_query = (filter_data.get("query") or "").strip()
    modalities, brands = _exercise_filter_choices(request.user)
    filter_form = ExerciseLibraryFilterForm(filter_data or None, modality_choices=modalities, brand_choices=brands)
    selected_exercise_ids = list(day.manual_exercises.values_list("exercise_id", flat=True))
    exercise_queryset = visible_exercise_queryset(request.user).exclude(id__in=selected_exercise_ids).order_by("name")
    if filter_form.is_valid():
        query = filter_form.cleaned_data.get("query")
        if query:
            exercise_queryset = exercise_queryset.filter(_exercise_search_filter(query))
        modality = filter_form.cleaned_data.get("modality")
        if modality:
            exercise_queryset = exercise_queryset.filter(modality=modality)
        brand = filter_form.cleaned_data.get("brand")
        if brand:
            exercise_queryset = exercise_queryset.filter(brand=brand)
        library_role = filter_form.cleaned_data.get("library_role")
        if library_role:
            if library_role == Exercise.LibraryRole.BOTH:
                exercise_queryset = exercise_queryset.filter(library_role=library_role)
            else:
                exercise_queryset = exercise_queryset.filter(library_role__in=[library_role, Exercise.LibraryRole.BOTH])
        if filter_form.cleaned_data.get("supports_time"):
            exercise_queryset = exercise_queryset.filter(supports_time=True)

    entries = list(day.manual_exercises.select_related("exercise").all())
    warmup_entries = []
    main_entries = []
    for entry in entries:
        item = {
            "entry": entry,
            "form": invalid_entry_forms.get(entry.id) or ManualExerciseConfigForm(instance=entry, prefix=f"entry-{entry.id}"),
            "is_expanded": str(entry.id) == str(expanded_entry_id),
            "summary": _entry_summary(entry),
        }
        if entry.block_type == ManualProgramExercise.BlockType.WARMUP:
            warmup_entries.append(item)
        else:
            main_entries.append(item)

    exercise_results = list(exercise_queryset[:40])
    return {
        "filter_form": filter_form,
        "exercise_results": exercise_results,
        "exercise_result_groups": _group_exercise_results_by_category(exercise_results),
        "warmup_entries": warmup_entries,
        "main_entries": main_entries,
        "active_filter_fields": _build_filter_hidden_fields(filter_form),
        "expanded_entry_id": expanded_entry_id,
        "submission_form": submission_form,
        "submission_query": submission_query,
        "show_submission_prompt": bool(submission_query and not exercise_results),
    }


def _build_manual_day_copy_form(day, data=None):
    available_days = list(day.draft.days.exclude(pk=day.pk))
    return ManualDayCopyForm(data=data, available_days=available_days, prefix="copy"), available_days


def _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context):
    context = {
        "draft": draft,
        "day": day,
        "day_form": day_form,
        "copy_form": copy_form,
        "copy_target_days": copy_target_days,
        **workspace_context,
    }
    template_name = "programs/partials/manual_day_shell.html" if getattr(request, "htmx", False) else "programs/manual_program_day_detail.html"
    return render(request, template_name, context)


def _manual_day_redirect(draft, day, filter_fields=None, expanded_entry_id=None):
    url = reverse("manual_program_day_detail", args=[draft.id, day.id])
    params = QueryDict(mutable=True)
    for key, value in filter_fields or []:
        params[key] = value
    if expanded_entry_id:
        params["expanded"] = str(expanded_entry_id)
    query_string = params.urlencode()
    return redirect(f"{url}?{query_string}" if query_string else url)


def _format_admin_value(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in ("", None):
        return "-"
    return str(value)


def _normalized_copy_name_tokens(value: str) -> set[str]:
    tokens = set()
    for token in "".join(char.lower() if char.isalnum() else " " for char in (value or "")).split():
        tokens.add(IMAGE_COPY_NAME_ALIASES.get(token, token))
    return tokens


def _is_reasonable_image_copy_match(source, candidate) -> bool:
    if not source or not candidate or source.pk == candidate.pk:
        return False
    if source.movement_pattern and candidate.movement_pattern:
        if source.movement_pattern.strip().lower() != candidate.movement_pattern.strip().lower():
            return False
    elif source.category and candidate.category:
        if source.category.strip().lower() != candidate.category.strip().lower():
            return False
    if source.modality and candidate.modality and source.modality != Exercise.Modality.OTHER and candidate.modality != Exercise.Modality.OTHER:
        if source.modality != candidate.modality:
            return False

    source_tokens = _normalized_copy_name_tokens(source.name)
    candidate_tokens = _normalized_copy_name_tokens(candidate.name)
    if source_tokens and candidate_tokens and not (source_tokens & candidate_tokens):
        return False
    return True


def _review_form_initial(exercise):
    metadata = suggested_exercise_updates(exercise, review_only=False)
    raw_suggested = {field: payload["suggested"] for field, payload in metadata.items()}
    return LibraryExerciseReviewForm.initial_from_exercise(exercise, suggested_values=raw_suggested)


def _library_admin_redirect(request):
    url = reverse("library_admin")
    return_query = request.POST.get("return_query", "").strip()
    return redirect(f"{url}?{return_query}" if return_query else url)


def _library_admin_image_redirect(request, *, selected_exercise_id=None):
    url = reverse("library_admin_images")
    params = QueryDict(mutable=True)
    for field in FILTER_FIELDS:
        value = request.POST.get(field) or request.GET.get(field)
        if value:
            params[field] = value
    selected_value = selected_exercise_id or request.POST.get("selected") or request.GET.get("selected")
    if selected_value:
        params["selected"] = str(selected_value)
    query_string = params.urlencode()
    return redirect(f"{url}?{query_string}" if query_string else url)


def _clear_library_image_preview(request):
    preview = request.session.pop(LIBRARY_IMAGE_PREVIEW_SESSION_KEY, None)
    if preview:
        delete_exercise_image_preview(preview.get("storage_name"))
        request.session.modified = True


def _current_library_image_preview(request, selected_exercise):
    preview = request.session.get(LIBRARY_IMAGE_PREVIEW_SESSION_KEY)
    if not preview or not selected_exercise:
        return None
    if preview.get("exercise_id") != selected_exercise.id:
        return None
    storage_name = preview.get("storage_name")
    if not storage_name or not default_storage.exists(storage_name):
        request.session.pop(LIBRARY_IMAGE_PREVIEW_SESSION_KEY, None)
        request.session.modified = True
        return None
    return {
        "storage_name": storage_name,
        "image_url": default_storage.url(storage_name),
        "image_source": preview.get("image_source", ""),
        "prompt": preview.get("prompt", ""),
    }


def _library_admin_image_queryset(filter_form):
    queryset = root_exercise_queryset().filter(is_active=True).order_by("name")
    if filter_form.is_valid():
        query = filter_form.cleaned_data.get("query")
        if query:
            queryset = queryset.filter(_exercise_search_filter(query))
        modality = filter_form.cleaned_data.get("modality")
        if modality:
            queryset = queryset.filter(modality=modality)
        brand = filter_form.cleaned_data.get("brand")
        if brand:
            queryset = queryset.filter(brand=brand)
        library_role = filter_form.cleaned_data.get("library_role")
        if library_role:
            if library_role == Exercise.LibraryRole.BOTH:
                queryset = queryset.filter(library_role=library_role)
            else:
                queryset = queryset.filter(library_role__in=[library_role, Exercise.LibraryRole.BOTH])
        if filter_form.cleaned_data.get("supports_time"):
            queryset = queryset.filter(supports_time=True)
    return queryset


def _library_image_copy_candidates(exercise, *, filtered_results=None, use_filtered_results=False):
    if not exercise:
        return []
    if use_filtered_results and filtered_results is not None:
        ordered = []
        seen_ids = set()
        for candidate in filtered_results:
            if candidate.pk == exercise.pk or candidate.pk in seen_ids:
                continue
            if not _is_reasonable_image_copy_match(exercise, candidate):
                continue
            ordered.append(candidate)
            seen_ids.add(candidate.pk)
        return ordered
    return [
        candidate
        for candidate in
        root_exercise_queryset()
        .filter(is_active=True, name__iexact=exercise.name)
        .exclude(pk=exercise.pk)
        .order_by("brand", "line", "external_id")
        if _is_reasonable_image_copy_match(exercise, candidate)
    ]


def _build_library_image_copy_form(exercise, *, data=None, filtered_results=None, use_filtered_results=False):
    candidates = _library_image_copy_candidates(
        exercise,
        filtered_results=filtered_results,
        use_filtered_results=use_filtered_results,
    )
    return (
        ExerciseImageCopyForm(
            data=data,
            available_exercises=candidates,
            initial={"source_exercise_id": exercise.id} if exercise and data is None else None,
        )
        if exercise
        else None,
        candidates,
    )


def _library_admin_reports(filter_form):
    queryset = _library_admin_filtered_queryset(filter_form)
    only_incomplete = filter_form.fields["only_incomplete"].initial
    limit = filter_form.fields["limit"].initial or 25
    if filter_form.is_valid():
        only_incomplete = filter_form.cleaned_data.get("only_incomplete")
        limit = filter_form.cleaned_data.get("limit") or limit

    if only_incomplete:
        queryset = [
            exercise
            for exercise in queryset
            if exercise_metadata_gaps(exercise)
            or exercise.verification_status == Exercise.VerificationStatus.PENDING_REVIEW
        ]
    else:
        queryset = list(queryset)

    exercises = list(queryset[:limit])
    reports = []
    for exercise in exercises:
        gaps = exercise_metadata_gaps(exercise)
        suggestions = suggested_exercise_updates(exercise, review_only=True)
        reports.append(
            {
                "exercise": exercise,
                "gaps": gaps,
                "gap_labels": ", ".join(gaps) if gaps else "Complete",
                "metadata_summary": f"Missing: {', '.join(gaps)}" if gaps else "Metadata: Complete",
                "needs_verification": exercise.verification_status == Exercise.VerificationStatus.PENDING_REVIEW,
                "verification_label": exercise.get_verification_status_display(),
                "suggestions": [
                    {
                        "field": field.replace("_", " ").title(),
                        "current_display": _format_admin_value(payload["current"]),
                        "suggested_display": _format_admin_value(payload["suggested"]),
                    }
                    for field, payload in suggestions.items()
                ],
                "has_suggestions": bool(suggestions),
                "review_form": LibraryExerciseReviewForm(prefix=f"review-{exercise.id}", initial=_review_form_initial(exercise)),
            }
        )
    return reports


def _library_admin_filtered_queryset(filter_form):
    queryset = root_exercise_queryset().order_by("name", "external_id")
    if filter_form.is_valid():
        query = filter_form.cleaned_data.get("query")
        if query:
            queryset = queryset.filter(_exercise_search_filter(query, include_external_id=True))
        brand = filter_form.cleaned_data.get("brand")
        if brand:
            queryset = queryset.filter(brand=brand)
    return queryset


@login_required
def current_program_view(request):
    program = (
        TrainingProgram.objects.filter(user=request.user, status=TrainingProgram.Status.ACTIVE)
        .order_by("-created_at")
        .first()
    )
    recent_manual_draft = ManualProgramDraft.objects.filter(user=request.user).order_by("-updated_at").first()
    return render(
        request,
        "programs/current_program.html",
        {"program": program, "recent_manual_draft": recent_manual_draft},
    )


@login_required
def program_history_view(request):
    programs = TrainingProgram.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "programs/program_history.html", {"programs": programs})


@login_required
def generate_program_view(request):
    form = ProgramGenerateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            program = generate_program_for_user(request.user, form.cleaned_data["prompt_text"])
        except Exception as exc:
            messages.error(request, f"Program generation failed: {exc}")
        else:
            messages.success(request, "A new training program has been generated.")
            return redirect("program_detail", program_id=program.id)

    profile_context = build_program_profile_context(request.user)
    profile_data_used = [
        item
        for item in [
            ("Age", profile_context.get("age")),
            ("Training experience", profile_context.get("training_experience")),
            ("Injuries / limitations", profile_context.get("injuries_limitations")),
            ("Equipment access", profile_context.get("equipment_access")),
            ("Weight unit", profile_context.get("preferred_weight_unit")),
            ("Language", profile_context.get("preferred_language")),
        ]
        if item[1]
    ]

    return render(
        request,
        "programs/generate_program.html",
        {
            "form": form,
            "prompt_examples": load_program_prompt_examples(),
            "profile_data_used": profile_data_used,
        },
    )


@login_required
def program_detail_view(request, program_id):
    program = get_object_or_404(TrainingProgram, pk=program_id, user=request.user)
    program_json = program.current_program
    program_days = [{"day": day, "blocks": get_day_blocks(day)} for day in program_json.get("days", [])]
    return render(
        request,
        "programs/program_detail.html",
        {"program": program, "program_json": program_json, "program_days": program_days},
    )


@login_required
def restore_program_view(request, program_id):
    program = get_object_or_404(TrainingProgram, pk=program_id, user=request.user)
    if request.method != "POST":
        return redirect("program_detail", program_id=program.id)
    try:
        restored_program = restore_program_for_user(request.user, program)
    except Exception as exc:
        messages.error(request, f"Could not restore program: {exc}")
        return redirect("program_history")
    messages.success(
        request,
        f"{program.name} was restored as version {restored_program.version_number} and set as your active program.",
    )
    return redirect("program_detail", program_id=restored_program.id)


@login_required
def manual_program_list_view(request):
    drafts = ManualProgramDraft.objects.filter(user=request.user).order_by("-updated_at")
    return render(request, "programs/manual_program_list.html", {"drafts": drafts})


@login_required
def manual_program_create_view(request):
    initial = {"weight_unit": request.user.profile.preferred_weight_unit}
    form = ManualProgramDraftForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        draft = form.save(commit=False)
        draft.user = request.user
        draft.save()
        messages.success(request, "Manual program draft created.")
        return redirect("manual_program_detail", draft_id=draft.id)
    return render(request, "programs/manual_program_create.html", {"form": form})


@login_required
def manual_program_detail_view(request, draft_id):
    draft = get_object_or_404(ManualProgramDraft, pk=draft_id, user=request.user)
    form = ManualProgramDraftForm(instance=draft, prefix="draft")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_draft":
            form = ManualProgramDraftForm(request.POST, instance=draft, prefix="draft")
            if form.is_valid():
                selected_day_keys = form.cleaned_data["selected_days"]
                form.save()
                _sync_manual_draft_days(draft, selected_day_keys)
                messages.success(request, "Draft details updated and days synced.")
                return redirect("manual_program_detail", draft_id=draft.id)
        if action == "delete_day":
            day = get_object_or_404(ManualProgramDay, pk=request.POST.get("day_id"), draft=draft)
            day.delete()
            messages.success(request, "Day removed from draft.")
            return redirect("manual_program_detail", draft_id=draft.id)
        if action == "publish":
            try:
                program = publish_manual_program(draft)
            except Exception as exc:
                messages.error(request, f"Could not publish manual plan: {exc}")
            else:
                messages.success(request, "Manual plan published and activated.")
                return redirect("program_detail", program_id=program.id)

    days = sorted(draft.days.prefetch_related("manual_exercises__exercise").all(), key=lambda day: DAY_ORDER.get(day.day_key, 99))
    day_panels = []
    for day in days:
        exercise_summaries = [
            {
                "name": entry.exercise.name,
                "modality": entry.exercise.get_modality_display(),
                "category": entry.exercise.category or "Uncategorized",
            }
            for entry in day.manual_exercises.select_related("exercise").all()
        ]
        day_panels.append(
            {
                "day": day,
                "exercise_summaries": exercise_summaries,
            }
        )
    return render(
        request,
        "programs/manual_program_detail.html",
        {"draft": draft, "form": form, "day_panels": day_panels},
    )


@login_required
def manual_program_day_detail_view(request, draft_id, day_id):
    draft = get_object_or_404(ManualProgramDraft, pk=draft_id, user=request.user)
    day = get_object_or_404(ManualProgramDay, pk=day_id, draft=draft)
    day_form = ManualProgramDayForm(instance=day, prefix="day")
    copy_form, copy_target_days = _build_manual_day_copy_form(day)
    invalid_entry_forms = {}
    submission_form = None
    expanded_entry_id = request.GET.get("expanded")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_day":
            day_form = ManualProgramDayForm(request.POST, instance=day, prefix="day")
            if day_form.is_valid():
                day_form.save()
                messages.success(request, "Day details updated.")
                return redirect("manual_program_day_detail", draft_id=draft.id, day_id=day.id)
        if action == "copy_day":
            copy_form, copy_target_days = _build_manual_day_copy_form(day, data=request.POST)
            if copy_form.is_valid():
                selected_ids = {int(day_id) for day_id in copy_form.cleaned_data["target_day_ids"]}
                target_days = [item for item in copy_target_days if item.id in selected_ids]
                copy_manual_day(day, target_days)
                copied_labels = ", ".join(item.day_label for item in target_days)
                messages.success(request, f"{day.day_label} copied to {copied_labels}.")
                workspace_context = _manual_day_workspace_context(request, day, expanded_entry_id=expanded_entry_id)
                if getattr(request, "htmx", False):
                    copy_form, copy_target_days = _build_manual_day_copy_form(day)
                    return _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context)
                return _manual_day_redirect(
                    draft,
                    day,
                    workspace_context["active_filter_fields"],
                    expanded_entry_id=expanded_entry_id,
                )
        if action == "add_exercise":
            add_form = AddExerciseToDayForm(request.POST, prefix="add")
            if add_form.is_valid():
                exercise = get_object_or_404(visible_exercise_queryset(request.user), pk=add_form.cleaned_data["exercise_id"])
                entry = create_manual_exercise_for_day(day, exercise, add_form.cleaned_data["block_type"])
                messages.success(request, f"{exercise.name} added to {day.day_label}.")
                workspace_context = _manual_day_workspace_context(request, day, expanded_entry_id=entry.id)
                if getattr(request, "htmx", False):
                    return _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context)
                return _manual_day_redirect(
                    draft,
                    day,
                    workspace_context["active_filter_fields"],
                    expanded_entry_id=entry.id,
                )
        if action == "update_entry":
            entry = get_object_or_404(ManualProgramExercise, pk=request.POST.get("entry_id"), day=day)
            entry_form = ManualExerciseConfigForm(request.POST, instance=entry, prefix=f"entry-{entry.id}")
            if entry_form.is_valid():
                entry_form.save()
                messages.success(request, f"{entry.exercise.name} updated.")
                workspace_context = _manual_day_workspace_context(request, day)
                if getattr(request, "htmx", False):
                    return _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context)
                return _manual_day_redirect(draft, day, workspace_context["active_filter_fields"])
            messages.error(request, f"Please correct the settings for {entry.exercise.name}.")
            invalid_entry_forms[entry.id] = entry_form
            expanded_entry_id = entry.id
        if action == "delete_entry":
            entry = get_object_or_404(ManualProgramExercise, pk=request.POST.get("entry_id"), day=day)
            entry.delete()
            messages.success(request, "Exercise removed from day.")
            workspace_context = _manual_day_workspace_context(request, day)
            if getattr(request, "htmx", False):
                return _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context)
            return _manual_day_redirect(draft, day, workspace_context["active_filter_fields"])
        if action == "generate_ai_exercise_suggestion":
            submission_query = (request.POST.get("query") or "").strip()
            if not submission_query:
                messages.error(request, "Enter an exercise name before using AI search.")
            else:
                try:
                    suggestion = generate_ai_exercise_suggestion(submission_query)
                except Exception as exc:
                    messages.error(request, f"AI exercise search failed: {exc}")
                else:
                    submission_form = UserExerciseSubmissionForm(
                        prefix="submission",
                        initial=UserExerciseSubmissionForm.initial_from_suggestion(
                            suggestion,
                            submission_query=submission_query,
                            source_kind=Exercise.SourceKind.AI_SUGGESTED,
                        ),
                    )
                    messages.success(request, f"Drafted a new exercise suggestion for '{submission_query}'.")
        if action == "save_user_exercise_submission":
            submission_form = UserExerciseSubmissionForm(request.POST, prefix="submission")
            if submission_form.is_valid():
                payload = {
                    "name": submission_form.cleaned_data["name"],
                    "aliases": LibraryExerciseReviewForm.parse_text_list(submission_form.cleaned_data["aliases"]),
                    "brand": submission_form.cleaned_data["brand"],
                    "line": submission_form.cleaned_data["line"],
                    "modality": submission_form.cleaned_data["modality"],
                    "library_role": submission_form.cleaned_data["library_role"],
                    "equipment": submission_form.cleaned_data["equipment"],
                    "category": submission_form.cleaned_data["category"],
                    "movement_pattern": submission_form.cleaned_data["movement_pattern"],
                    "primary_muscles": LibraryExerciseReviewForm.parse_text_list(submission_form.cleaned_data["primary_muscles"]),
                    "secondary_muscles": LibraryExerciseReviewForm.parse_text_list(submission_form.cleaned_data["secondary_muscles"]),
                    "stabilizers": LibraryExerciseReviewForm.parse_text_list(submission_form.cleaned_data["stabilizers"]),
                    "supports_reps": submission_form.cleaned_data["supports_reps"],
                    "supports_time": submission_form.cleaned_data["supports_time"],
                    "is_static": submission_form.cleaned_data["is_static"],
                    "instructions": submission_form.cleaned_data["instructions"],
                }
                exercise, created = create_user_exercise_submission(
                    request.user,
                    payload,
                    submission_query=submission_form.cleaned_data["submission_query"],
                    source_kind=submission_form.cleaned_data["source_kind"] or Exercise.SourceKind.USER_SUBMITTED,
                )
                if created:
                    messages.success(
                        request,
                        f"{exercise.name} was added to your library and is pending staff review.",
                    )
                else:
                    messages.info(request, f"{exercise.name} already exists in your visible library.")
                workspace_context = _manual_day_workspace_context(request, day, expanded_entry_id=expanded_entry_id)
                if getattr(request, "htmx", False):
                    return _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context)
                return _manual_day_redirect(
                    draft,
                    day,
                    workspace_context["active_filter_fields"],
                    expanded_entry_id=expanded_entry_id,
                )
            messages.error(request, "Please review the suggested exercise details before saving.")

    workspace_context = _manual_day_workspace_context(
        request,
        day,
        expanded_entry_id=expanded_entry_id,
        invalid_entry_forms=invalid_entry_forms,
        submission_form=submission_form,
    )
    return _manual_day_render(request, draft, day, day_form, copy_form, copy_target_days, workspace_context)


@login_required
def library_admin_images_view(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff access required.")

    modalities, brands = _exercise_filter_choices(request.user)
    filter_form = ExerciseLibraryFilterForm(
        request.GET or None,
        modality_choices=modalities,
        brand_choices=brands,
    )
    exercise_results = list(_library_admin_image_queryset(filter_form)[:40])
    active_filter_fields = _build_filter_hidden_fields(filter_form)
    selected_exercise = None
    selected_value = request.POST.get("selected") or request.GET.get("selected")
    if selected_value and selected_value.isdigit():
        selected_exercise = next((exercise for exercise in exercise_results if exercise.id == int(selected_value)), None)
        if selected_exercise is None:
            selected_exercise = get_object_or_404(root_exercise_queryset().filter(is_active=True), pk=int(selected_value))
    prompt_form = None
    copy_form, copy_candidates = _build_library_image_copy_form(
        selected_exercise,
        filtered_results=exercise_results,
        use_filtered_results=bool(active_filter_fields),
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate_image_preview":
            prompt_form = ExerciseImagePromptForm(request.POST)
            if prompt_form.is_valid():
                exercise = get_object_or_404(root_exercise_queryset().filter(is_active=True), pk=prompt_form.cleaned_data["exercise_id"])
                _clear_library_image_preview(request)
                try:
                    preview = build_exercise_image_preview(exercise, prompt_form.cleaned_data["prompt"])
                except Exception as exc:
                    messages.error(request, f"Image generation failed for {exercise.name}: {exc}")
                else:
                    request.session[LIBRARY_IMAGE_PREVIEW_SESSION_KEY] = {
                        "exercise_id": exercise.id,
                        "storage_name": preview["storage_name"],
                        "image_source": preview["image_source"],
                        "prompt": prompt_form.cleaned_data["prompt"],
                    }
                    request.session.modified = True
                    messages.success(request, f"Generated a preview image for {exercise.name}.")
                return _library_admin_image_redirect(request, selected_exercise_id=exercise.id)
            if selected_exercise:
                messages.error(request, "Please correct the image prompt before generating a preview.")
        if action == "save_generated_image":
            exercise = get_object_or_404(root_exercise_queryset().filter(is_active=True), pk=request.POST.get("exercise_id"))
            preview = _current_library_image_preview(request, exercise)
            if not preview:
                messages.error(request, "No generated preview is available to save.")
                return _library_admin_image_redirect(request, selected_exercise_id=exercise.id)
            attach_preview_image_to_exercise(
                exercise,
                storage_name=preview["storage_name"],
                prompt=preview["prompt"],
                image_source=preview["image_source"],
                mark_reviewed=True,
            )
            request.session.pop(LIBRARY_IMAGE_PREVIEW_SESSION_KEY, None)
            request.session.modified = True
            messages.success(request, f"Saved the generated image for {exercise.name}.")
            return _library_admin_image_redirect(request, selected_exercise_id=exercise.id)
        if action == "ignore_generated_image":
            exercise = get_object_or_404(root_exercise_queryset().filter(is_active=True), pk=request.POST.get("exercise_id"))
            _clear_library_image_preview(request)
            messages.info(request, f"Ignored the generated preview for {exercise.name}.")
            return _library_admin_image_redirect(request, selected_exercise_id=exercise.id)
        if action == "copy_saved_image":
            exercise = get_object_or_404(root_exercise_queryset().filter(is_active=True), pk=request.POST.get("source_exercise_id"))
            copy_form, copy_candidates = _build_library_image_copy_form(
                exercise,
                data=request.POST,
                filtered_results=exercise_results,
                use_filtered_results=bool(active_filter_fields),
            )
            if copy_form.is_valid():
                target_ids = {int(item) for item in copy_form.cleaned_data["target_exercise_ids"]}
                targets = [candidate for candidate in copy_candidates if candidate.id in target_ids]
                try:
                    copied_ids = copy_exercise_image_to_targets(exercise, targets)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return _library_admin_image_redirect(request, selected_exercise_id=exercise.id)
                messages.success(request, f"Copied the saved image to {len(copied_ids)} exercise records.")
                return _library_admin_image_redirect(request, selected_exercise_id=exercise.id)
            messages.error(request, "Select at least one target exercise before copying the image.")

    preview = _current_library_image_preview(request, selected_exercise)
    if selected_exercise:
        initial_prompt = preview["prompt"] if preview else selected_exercise.image_prompt or build_exercise_image_prompt(selected_exercise)
        prompt_form = prompt_form or ExerciseImagePromptForm(
            initial={
                "exercise_id": selected_exercise.id,
                "prompt": initial_prompt,
            }
        )

    context = {
        "filter_form": filter_form,
        "exercise_results": exercise_results,
        "exercise_result_groups": _group_exercise_results_by_category(exercise_results),
        "active_filter_fields": active_filter_fields,
        "selected_exercise": selected_exercise,
        "prompt_form": prompt_form,
        "preview": preview,
        "copy_form": copy_form,
        "copy_candidates": copy_candidates,
    }
    return render(request, "programs/library_admin_images.html", context)


@login_required
def library_admin_view(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff access required.")

    import_form = LibraryImportAdminForm(prefix="import")
    enrich_form = LibraryEnrichAdminForm(prefix="enrich")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "run_import":
            import_form = LibraryImportAdminForm(request.POST, prefix="import")
            if import_form.is_valid():
                result = import_exercise_library(
                    overwrite=import_form.cleaned_data["overwrite"],
                    ai_instructions=import_form.cleaned_data["ai_instructions"],
                )
                messages.success(
                    request,
                    f"Import complete. Created {result['created']}, updated {result['updated']}, scanned {result['total']}.",
                )
                return _library_admin_redirect(request)
        if action == "run_enrich":
            enrich_form = LibraryEnrichAdminForm(request.POST, prefix="enrich")
            if enrich_form.is_valid():
                queryset = Exercise.objects.order_by("name", "external_id")
                if not enrich_form.cleaned_data["overwrite"]:
                    queryset = [exercise for exercise in queryset if exercise_metadata_gaps(exercise)]
                else:
                    queryset = list(queryset)
                exercises = list(queryset[: enrich_form.cleaned_data["limit"]])
                updated = 0
                for exercise in exercises:
                    if enrich_exercise_metadata(
                        exercise,
                        overwrite=enrich_form.cleaned_data["overwrite"],
                        use_ai=enrich_form.cleaned_data["use_ai"],
                    ):
                        updated += 1
                messages.success(request, f"Processed {len(exercises)} exercises and updated {updated}.")
                return _library_admin_redirect(request)
        if action == "apply_suggestions":
            exercise = get_object_or_404(Exercise, pk=request.POST.get("exercise_id"))
            changed_fields = enrich_exercise_metadata(exercise, overwrite=False, use_ai=False)
            if changed_fields:
                messages.success(request, f"Applied suggested updates to {exercise.name}.")
            else:
                messages.info(request, f"No changes were needed for {exercise.name}.")
            return _library_admin_redirect(request)
        if action == "save_review":
            exercise = get_object_or_404(Exercise, pk=request.POST.get("exercise_id"))
            review_form = LibraryExerciseReviewForm(request.POST, prefix=f"review-{exercise.id}")
            if review_form.is_valid():
                exercise.brand = review_form.cleaned_data["brand"]
                exercise.line = review_form.cleaned_data["line"]
                exercise.modality = review_form.cleaned_data["modality"]
                exercise.equipment = review_form.cleaned_data["equipment"]
                exercise.category = review_form.cleaned_data["category"]
                exercise.movement_pattern = review_form.cleaned_data["movement_pattern"]
                exercise.primary_muscles = LibraryExerciseReviewForm.parse_text_list(review_form.cleaned_data["primary_muscles"])
                exercise.secondary_muscles = LibraryExerciseReviewForm.parse_text_list(review_form.cleaned_data["secondary_muscles"])
                exercise.stabilizers = LibraryExerciseReviewForm.parse_text_list(review_form.cleaned_data["stabilizers"])
                exercise.supports_reps = review_form.cleaned_data["supports_reps"]
                exercise.supports_time = review_form.cleaned_data["supports_time"]
                exercise.is_static = review_form.cleaned_data["is_static"]
                exercise.instructions = review_form.cleaned_data["instructions"]
                exercise.save(
                    update_fields=[
                        "brand",
                        "line",
                        "modality",
                        "equipment",
                        "category",
                        "movement_pattern",
                        "primary_muscles",
                        "secondary_muscles",
                        "stabilizers",
                        "supports_reps",
                        "supports_time",
                        "is_static",
                        "instructions",
                        "updated_at",
                    ]
                )
                messages.success(request, f"Saved review changes for {exercise.name}.")
                return _library_admin_redirect(request)
            messages.error(request, f"Please correct the review fields for {exercise.name}.")
        if action == "approve_exercise":
            exercise = get_object_or_404(Exercise, pk=request.POST.get("exercise_id"))
            exercise.verification_status = Exercise.VerificationStatus.APPROVED
            exercise.verified_by = request.user
            exercise.verified_at = timezone.now()
            exercise.save(update_fields=["verification_status", "verified_by", "verified_at", "updated_at"])
            messages.success(request, f"Approved {exercise.name} for the shared library.")
            return _library_admin_redirect(request)
        if action == "reject_exercise":
            exercise = get_object_or_404(Exercise, pk=request.POST.get("exercise_id"))
            exercise.verification_status = Exercise.VerificationStatus.REJECTED
            exercise.verified_by = request.user
            exercise.verified_at = timezone.now()
            exercise.save(update_fields=["verification_status", "verified_by", "verified_at", "updated_at"])
            messages.success(request, f"Rejected {exercise.name}.")
            return _library_admin_redirect(request)
        if action == "merge_duplicates":
            canonical = get_object_or_404(Exercise, pk=request.POST.get("canonical_exercise_id"))
            duplicate_ids = [int(item) for item in request.POST.getlist("duplicate_ids") if item.isdigit()]
            duplicates = list(Exercise.objects.filter(pk__in=duplicate_ids))
            merged_ids = merge_exercise_duplicates(canonical, duplicates)
            messages.success(request, f"Merged {len(merged_ids)} duplicate records into {canonical.name}.")
            return _library_admin_redirect(request)

    brand_choices = list(Exercise.objects.values_list("brand", "brand").distinct().order_by("brand"))
    filter_form = LibraryAdminFilterForm(
        request.GET or None,
        brand_choices=[item for item in brand_choices if item[0]],
    )
    reports = _library_admin_reports(filter_form)
    duplicate_groups = find_duplicate_exercise_groups(list(_library_admin_filtered_queryset(filter_form)))
    if request.method == "POST" and request.POST.get("action") == "save_review":
        failed_exercise = get_object_or_404(Exercise, pk=request.POST.get("exercise_id"))
        failed_prefix = f"review-{failed_exercise.id}"
        failed_form = LibraryExerciseReviewForm(request.POST, prefix=failed_prefix)
        for report in reports:
            if report["exercise"].id == failed_exercise.id:
                report["review_form"] = failed_form
                break
    context = {
        "import_form": import_form,
        "enrich_form": enrich_form,
        "filter_form": filter_form,
        "reports": reports,
        "duplicate_groups": duplicate_groups,
        "total_exercises": Exercise.objects.count(),
        "incomplete_exercises": sum(1 for exercise in Exercise.objects.order_by("id") if exercise_metadata_gaps(exercise)),
        "pending_review_exercises": Exercise.objects.filter(
            verification_status=Exercise.VerificationStatus.PENDING_REVIEW
        ).count(),
        "catalog_backed_exercises": Exercise.objects.exclude(brand="").count(),
        "current_query_string": request.GET.urlencode(),
    }
    return render(request, "programs/library_admin.html", context)
