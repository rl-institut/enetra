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


@register.simple_tag
def truthy_attrs(*pairs) -> dict[str, str]:
    """Build an attrs dict containing only the attributes whose value is truthy.

    Takes alternating name/value pairs:
        {% truthy_attrs 'required' field.field.required 'readonly' readonly as extra %}
        <c-foo :attrs="extra" />
    Each kept attribute is rendered with an empty value, so this only suits HTML boolean
    attributes (disabled, required, readonly, checked, open, hidden, ...). Not for aria-*
    or enumerated attributes, which need an explicit "true"/"false" value.
    """
    if len(pairs) % 2:
        raise template.TemplateSyntaxError("truthy_attrs expects name/value pairs")
    names, values = pairs[::2], pairs[1::2]
    return {str(name): "" for name, value in zip(names, values, strict=True) if value}


@register.filter
def negate(value) -> bool:
    """`x|negate` -> `not x`. Useful in tag arguments, which don't support `not`."""
    return not value
