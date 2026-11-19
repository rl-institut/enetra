from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=User)
def create_user_project(sender, instance: User, created: bool, raw: bool = False, **kwargs):
    """Give every new user an own project with an empty scenario."""
    # raw: loaded from fixtures, the data user only provides template data
    if not created or raw or instance.username == settings.DATA_USER:
        return

    # lazy import to avoid circular imports between core and ports
    from ports.create_placeholder_scenario import _create_placeholder_scenario

    # savepoint, so the user creation is rolled back as well if this fails
    with transaction.atomic():
        _create_placeholder_scenario(instance, instance)
