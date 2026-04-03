import json

from django import template


register = template.Library()


@register.filter
def field_by_name(form, name):
    return form[name]


@register.filter
def pretty_json(value):
    return json.dumps(value, indent=2, ensure_ascii=False)
