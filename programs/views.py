from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    AddExerciseToDayForm,
    ExerciseLibraryFilterForm,
    ManualExerciseConfigForm,
    ManualProgramDayForm,
    ManualProgramDraftForm,
    ProgramGenerateForm,
)
from .manual_services import DAY_ORDER, create_manual_exercise_for_day, publish_manual_program
from .models import Exercise, ManualProgramDay, ManualProgramDraft, ManualProgramExercise, TrainingProgram
from .prompt_examples import load_program_prompt_examples
from .services import build_program_profile_context, generate_program_for_user, restore_program_for_user
from .structure import get_day_blocks


def _exercise_filter_choices():
    modalities = list(Exercise.objects.filter(is_active=True).values_list("modality", "modality").distinct().order_by("modality"))
    categories = list(Exercise.objects.filter(is_active=True).values_list("category", "category").distinct().order_by("category"))
    return modalities, [item for item in categories if item[0]]


FILTER_FIELDS = ("query", "modality", "category", "library_role", "supports_time")


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


def _entry_summary(entry: ManualProgramExercise) -> str:
    if entry.prescription_type == ManualProgramExercise.PrescriptionType.TIME:
        return f"{entry.sets_count} x {entry.target_seconds or '-'} sec"
    return f"{entry.sets_count} x {entry.target_reps or '-'}"


def _manual_day_workspace_context(request, day, expanded_entry_id=None, invalid_entry_forms=None):
    invalid_entry_forms = invalid_entry_forms or {}
    filter_data = _get_manual_day_filter_data(request)
    modalities, categories = _exercise_filter_choices()
    filter_form = ExerciseLibraryFilterForm(filter_data or None, modality_choices=modalities, category_choices=categories)
    selected_exercise_ids = list(day.manual_exercises.values_list("exercise_id", flat=True))
    exercise_queryset = Exercise.objects.filter(is_active=True).exclude(id__in=selected_exercise_ids).order_by("name")
    if filter_form.is_valid():
        query = filter_form.cleaned_data.get("query")
        if query:
            exercise_queryset = exercise_queryset.filter(
                Q(name__icontains=query)
                | Q(category__icontains=query)
                | Q(movement_pattern__icontains=query)
                | Q(equipment__icontains=query)
            )
        modality = filter_form.cleaned_data.get("modality")
        if modality:
            exercise_queryset = exercise_queryset.filter(modality=modality)
        category = filter_form.cleaned_data.get("category")
        if category:
            exercise_queryset = exercise_queryset.filter(category=category)
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

    return {
        "filter_form": filter_form,
        "exercise_results": list(exercise_queryset[:40]),
        "warmup_entries": warmup_entries,
        "main_entries": main_entries,
        "active_filter_fields": _build_filter_hidden_fields(filter_form),
        "expanded_entry_id": expanded_entry_id,
    }


def _manual_day_render(request, draft, day, day_form, workspace_context):
    context = {"draft": draft, "day": day, "day_form": day_form, **workspace_context}
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
    day_form = ManualProgramDayForm(prefix="day")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_draft":
            form = ManualProgramDraftForm(request.POST, instance=draft, prefix="draft")
            if form.is_valid():
                form.save()
                messages.success(request, "Draft details updated.")
                return redirect("manual_program_detail", draft_id=draft.id)
        if action == "add_day":
            day_form = ManualProgramDayForm(request.POST, prefix="day")
            if day_form.is_valid():
                day = day_form.save(commit=False)
                day.draft = draft
                day.save()
                messages.success(request, f"{day.day_label} added to the manual plan.")
                return redirect("manual_program_day_detail", draft_id=draft.id, day_id=day.id)
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

    days = sorted(draft.days.all(), key=lambda day: DAY_ORDER.get(day.day_key, 99))
    return render(
        request,
        "programs/manual_program_detail.html",
        {"draft": draft, "form": form, "day_form": day_form, "days": days},
    )


@login_required
def manual_program_day_detail_view(request, draft_id, day_id):
    draft = get_object_or_404(ManualProgramDraft, pk=draft_id, user=request.user)
    day = get_object_or_404(ManualProgramDay, pk=day_id, draft=draft)
    day_form = ManualProgramDayForm(instance=day, prefix="day")
    invalid_entry_forms = {}
    expanded_entry_id = request.GET.get("expanded")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_day":
            day_form = ManualProgramDayForm(request.POST, instance=day, prefix="day")
            if day_form.is_valid():
                day_form.save()
                messages.success(request, "Day details updated.")
                return redirect("manual_program_day_detail", draft_id=draft.id, day_id=day.id)
        if action == "add_exercise":
            add_form = AddExerciseToDayForm(request.POST, prefix="add")
            if add_form.is_valid():
                exercise = get_object_or_404(Exercise, pk=add_form.cleaned_data["exercise_id"], is_active=True)
                entry = create_manual_exercise_for_day(day, exercise, add_form.cleaned_data["block_type"])
                messages.success(request, f"{exercise.name} added to {day.day_label}.")
                workspace_context = _manual_day_workspace_context(request, day, expanded_entry_id=entry.id)
                if getattr(request, "htmx", False):
                    return _manual_day_render(request, draft, day, day_form, workspace_context)
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
                    return _manual_day_render(request, draft, day, day_form, workspace_context)
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
                return _manual_day_render(request, draft, day, day_form, workspace_context)
            return _manual_day_redirect(draft, day, workspace_context["active_filter_fields"])

    workspace_context = _manual_day_workspace_context(
        request,
        day,
        expanded_entry_id=expanded_entry_id,
        invalid_entry_forms=invalid_entry_forms,
    )
    return _manual_day_render(request, draft, day, day_form, workspace_context)
