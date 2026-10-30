import logging

from django import template
from django.conf import settings

register = template.Library()

logger = logging.getLogger(__name__)


@register.filter(name="display")
def display(instance, field_name):
    """Format a model field's value for display: {% load display %}{{ instance|display:"power_kw" }}

    Delegates formatting to instance.display_value() (ItemTemplate), so this filter
    only handles the template-rendering concern: a dev-mode warning when accessing
    an uncached relation would trigger an extra query.
    """
    if settings.DEBUG:
        field = instance._meta.get_field(field_name)
        if (
            field.many_to_one or field.one_to_one
        ) and field_name not in instance._state.fields_cache:
            logger.warning(
                "display filter: %s.%s is not prefetched/cached, accessing it "
                "triggers an extra query. Add select_related('%s') to the queryset.",
                type(instance).__name__,
                field_name,
                field_name,
            )
    return instance.display_value(field_name)
