from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=User)
def create_user_project(sender, instance: User, created: bool, raw: bool = False, **kwargs):
    """Give every new user an own example project with a filled example scenario."""
    # raw: loaded from fixtures, the data user only provides template data
    # skipped in tests, since every created user would get a full scenario
    if not created or raw or settings.TESTING or instance.username == settings.DATA_USER:
        return

    # lazy import to avoid circular imports between core and ports
    from ports.create_placeholder_scenario import _create_placeholder_scenario

    # keep the example project consistent: either created completely or not at all
    with transaction.atomic():
        _create_placeholder_scenario(instance)
