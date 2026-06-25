from uuid import uuid4

from django.contrib.auth.models import User

from .db_deepcopy import deepcopy
from .models import Project
from .models import Scenario


def duplicate_project(project: Project):
    new_project, _ = deepcopy(project, exclude_models={User}, max_depth=2)
    return new_project


def duplicate_scenario(scenario: Scenario, user: User, suffix=" (Dupliziert)"):
    # Scenario internal_id must be unique. by changing the in memory internal_id
    # the deepcopy does not create a collision
    scenario.internal_id = uuid4()
    new_scenario, _ = deepcopy(scenario, exclude_models={User, Project}, max_depth=1)
    new_scenario.name += suffix
    new_scenario.manager = user
    new_scenario.save()
    return new_scenario


def get_user_projects(user: User):
    # TODO: Add all scenarios with permission for the user not just managed
    if user.is_superuser:
        return Project.objects.all().prefetch_related("scenario_set")
    return Project.objects.filter(manager=user).prefetch_related("scenario_set")


def get_template_scenarios(user: User):
    # TODO: template user, e.g. add data as user TEMPLATE or smth?
    template_user = User.objects.get(username="admin")
    if user.is_superuser:
        return Scenario.objects.all()
    return Scenario.objects.filter(manager__in=[user, template_user])
