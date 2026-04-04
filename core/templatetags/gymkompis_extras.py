import json

from django import template


register = template.Library()


@register.filter
def field_by_name(form, name):
    return form[name]


@register.filter
def pretty_json(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


@register.filter
def set_target_display(set_item):
    if set_item.get("prescription_type") == "time" or set_item.get("target_seconds") is not None:
        seconds = set_item.get("target_seconds")
        return f"{seconds} sec" if seconds is not None else "-"
    reps = set_item.get("target_reps")
    return f"{reps} reps" if reps else "-"


@register.filter
def actual_set_display(set_item):
    if set_item.get("seconds") is not None:
        return f"{set_item['seconds']} sec"
    if set_item.get("reps") is not None:
        return f"{set_item['reps']} reps"
    return "-"
