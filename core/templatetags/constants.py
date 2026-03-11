import json
from difflib import get_close_matches

from django import template
from django.conf import settings

register = template.Library()

events_dict = dict()
with open("ports/static/ports/events.json") as f:
    events_dict = json.load(f)


class EventsAccessor:
    """Proxy object returned by {% events %} — supports {{ EVENTS.KEY }} syntax."""

    def __getattr__(self, key: str) -> str:
        global events_dict
        if settings.DEBUG:
            # Keep the file fresh during development
            with open("ports/static/ports/events.json") as f:
                events_dict = json.load(f)
        if key not in events_dict:
            raise template.TemplateSyntaxError(
                f"EVENTS has no key '{key}'. "
                f"Closest match: {get_close_matches(key, events_dict.keys(), n=1, cutoff=0.0)[0]}: \n"
                f"Available keys: {sorted(events_dict.keys())}"
            )
        return events_dict[key]


@register.simple_tag
def events():
    return EventsAccessor()


@register.simple_tag
def events_data():
    return events_dict


@register.filter
def assertValue(val):
    print(f"asserting: {val}")
    if val is None or val == "" or val == "None":
        print("Value is none or empty string")
        raise AssertionError("AssertedValue is None")
    return val
