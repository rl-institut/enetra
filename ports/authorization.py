import logging
import uuid
from typing import Literal

from django.contrib.auth.models import User
from django.db.models import ForeignKey
from django.db.models import ManyToManyField
from django.db.models import QuerySet
from guardian.shortcuts import get_objects_for_user

from .models import Area
from .models import Project
from .models import Scenario
from .models import ScenarioItem

logger = logging.getLogger(__name__)


def get_related_model_values(
    instances: QuerySet[ScenarioItem], model: type[ScenarioItem], field_name: str
):
    """Find the values of field_name on all related instances of type model for the given queryset

    Used to find all area_internal_ids for a given QuerySet of ScenarioItems, so the permission
    can be checked on those areas
    """
    all_fields = instances.model._meta.get_fields()
    fk_fields = [
        f for f in all_fields if isinstance(f, ForeignKey) and issubclass(f.related_model, model)
    ]
    m2m_fields = [
        f
        for f in all_fields
        if isinstance(f, ManyToManyField) and issubclass(f.related_model, model)
    ]
    if not fk_fields and not m2m_fields:
        return None

    if fk_fields:
        instances = instances.select_related(*[f.name for f in fk_fields])
    if m2m_fields:
        instances = instances.prefetch_related(*[f.name for f in m2m_fields])

    values = []
    for instance in instances:
        for field in fk_fields:
            related_instance = getattr(instance, field.name)
            if related_instance is not None:
                values.append(getattr(related_instance, field_name))
        for field in m2m_fields:
            values.extend(getattr(instance, field.name).values_list(field_name, flat=True))
    return values


def has_area_authorization_from_uuids(
    uuids: list[uuid.UUID | str],
    user: User,
    crud: Literal["view", "create", "details", "change", "delete"],
    scenario: Scenario,
    model: type[ScenarioItem],
):
    if model is Area:
        area_uuids = uuids
    else:
        instances = model.objects.filter(scenario=scenario, internal_id__in=uuids)
        area_uuids = get_related_model_values(instances, Area, "internal_id")
    areas = Area.objects.filter(scenario=scenario, internal_id__in=area_uuids).exclude(manager=user)
    non_managed_areas_internal_ids = areas.values_list("internal_id", flat=True)
    allowed_internal_ids = get_objects_for_user(user, crud, areas).values_list(
        "internal_id", flat=True
    )
    missing_permissions = len(set(non_managed_areas_internal_ids).difference(allowed_internal_ids))
    return missing_permissions == 0


def has_authorization(
    item: Project | ScenarioItem,
    user: User,
    crud: Literal["view", "create", "details", "change", "delete"],
) -> bool:
    """
    View: See an item with limited set of attributes
    details: See an item with all its attributes
    Change: Change an item
    Delete: Delete an item
    Create: Create an item
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user == item.manager:
        return True

    # For now short the authorization for ScenarioItems, since their authorization is
    # based only on areas for now
    if isinstance(item, ScenarioItem):
        return has_area_authorization_from_uuids(
            [item.internal_id], user, crud, item.scenario, item.model()
        )

    has_perm = False
    match crud:
        case "delete" | "details":
            has_perm = user.has_perm("details", item)
        case "view":
            has_perm = user.has_perm("view", item)
        case _:
            logger.warning("no matching crud found for " + crud)
    return has_perm
