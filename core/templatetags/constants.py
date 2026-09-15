from difflib import get_close_matches

from django import template
from django.conf import settings

register = template.Library()


class EventsAccessor:
    """Proxy object returned by {% events %} — supports {{ EVENTS.KEY }} syntax."""

    def __getattr__(self, key: str) -> str:
        # Check if requested key is in events dict. Ignore private variables
        if key[0] != "_" and key not in settings.EVENTS_DICT:
            raise template.TemplateSyntaxError(
                f"EVENTS has no key '{key}'. "
                f"Closest match: {get_close_matches(key, settings.EVENTS_DICT.keys(), n=1, cutoff=0.0)[0]}: \n"
                f"Available keys: {sorted(settings.EVENTS_DICT.keys())}"
            )
        return settings.EVENTS_DICT.get(key, "")


@register.simple_tag
def events():
    return EventsAccessor()


@register.simple_tag
def events_data():
    return settings.EVENTS_DICT


@register.filter
def assertValue(val):
    if val is None or val == "" or val == "None":
        print("Value is none or empty string")
        raise AssertionError("AssertedValue is None")
    return val


@register.filter
def get_value(d: dict, name):
    return d[name]


@register.filter
def get_value_or_none(d: dict, name):
    return d.get(name)


@register.filter
def widget_type(field):
    return field.field.widget.__class__.__name__
