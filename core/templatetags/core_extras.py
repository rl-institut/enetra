import uuid

from django import template
from django_cotton import templatetags
from widget_tweaks.templatetags.widget_tweaks import set_attr

register = template.Library()


@register.filter
def add_attrs_without_class(field, attrs: templatetags.Attrs):
    filtered_attrs = {
        key: val
        for key, val in attrs.items()
        if key != "class" and key not in attrs._exclude_from_str
    }
    for key, val in filtered_attrs.items():
        field = set_attr(field, ":".join((str(key), str(val))))
    return field


@register.simple_tag
def gen_uuid():
    return uuid.uuid4().hex
