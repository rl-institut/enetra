"""Deepcopy an object based on a given table hierarchy
The instances are copied in the order of the hierarchy.
It is assumed creation in the order is possible.
Each Instance/Model may perform a pre_copy mutation, e.g. change its internal_id to preserve uniqueness.
Before creation foreign fields are changed to copied instances.
If these instances are missing, this value adjustment is pushed to a backlog
After object creation m2m fields are searched on the object and the m2m table is created.
When all objects from the model hierarchy are copied/created the backlog will be checked
and the values will be adjusted to the now (hopefully) existing new objects.
If the objects are still missing, a warning will be given.
If some models should not be copied/adjusted (e.g. User) these should not be part of the model
hierarchy. To turn of warnings that these instances are not found, add the Model to ignore_models.
Copies will reference generated copies instead of the original instances
`
    b = Bar()
    foo = Foo(bar=b)
    foo2 = deepcopy(foo)
    foo2.bar =! foo.bar
`
"""

import logging
from uuid import uuid4

from django.db import models

from ports.models import Project
from ports.models import Scenario

logger = logging.getLogger(__name__)


class Deepcopy:
    def __init__(
        self,
        model_hierarchy: list[type[models.Model]],
        ignore_models: set[type[models.Model]] | None = None,
    ):
        # list of models in the order they are deepcopied
        self.model_hierarchy = model_hierarchy
        # Suppress warnings for missing related objects of a Model type
        self.ignore_models: set[type[models.Model]] = ignore_models or set()

        # nested lookup table for new IDs of already copied instances.
        # First key is the Model, second key is the id of the instance before it was copied
        # The value is the id of the instance after it was copied
        self.old_new: dict[models.Model, dict[int, int]] = dict()
        # Fields which can not be set before bulk creation, e.g. for Models referencing themselves
        self.backlog: dict[type[models.Model], dict] = dict()

    def pre_copy_mutate(self, instances: list[models.Model]):
        """Adjust the instances in place so they can be copied"""
        # Saving the instance will create a new one with a new id
        for instance in instances:
            instance.id = None

        # Projects and Scenarios must have unique internal_ids since they are used for lookup.
        # ScenarioItems dont have this requirement as long as they belong to different Scenarios
        reset_uuid = type(instances[0]) in [Project, Scenario]
        if reset_uuid:
            for instance in instances:
                instance.internal_id = uuid4()

    def pre_copy_retarget(self, instances: list[models.Model]):
        """Adjust the foreign fields according to already copied instances"""
        instance = instances[0]
        for field in instance._meta.fields:
            if not field.is_relation:
                continue
            if field.related_model in self.ignore_models:
                continue
            if field.related_model in self.old_new:
                # the model has a field with a related model which was already copied
                # retarget the instance to the copied instance instead
                for instance in instances:
                    # the attibute name which is transfered (id of referenced instance)
                    lookup = field.name + "_id"
                    old_related_id = getattr(instance, lookup)
                    # Null Values don't need copying
                    if old_related_id is None:
                        continue
                    new_id = self.old_new[field.related_model][old_related_id]
                    setattr(instance, lookup, new_id)
            else:
                # these fields might have to be updated after creation
                self.backlog.setdefault(instance._meta.model, {})
                self.backlog[instance._meta.model].setdefault("fields", [])
                self.backlog[instance._meta.model]["instances"] = instances
                self.backlog[instance._meta.model]["fields"].append(field)

    def update_backlog_fields(self):
        """Repeat the retarget with fields which were not found"""
        for model, val in self.backlog.items():
            instances = val["instances"]
            instance = instances[0]
            found_fields = []
            for field in val["fields"]:
                if field.related_model in self.old_new:
                    # the attribute name which is transferred (id of referenced instance)
                    lookup = field.name + "_id"
                    for instance in instances:
                        old_related_id = getattr(instance, lookup)
                        # Null Values don't need copying
                        if old_related_id is None:
                            continue
                        new_id = self.old_new[field.related_model][old_related_id]
                        setattr(instance, lookup, new_id)
                        found_fields.append(field.name)
                else:
                    logger.warning(
                        f"{type(instance)} has a field {field} with a related object of type "
                        f"{field.related_model} which was not found in the copied instances. "
                        "This might be desired if some references should not be copied."
                    )
            if found_fields:
                model.objects.bulk_update(instances, fields=found_fields)

    def post_copy_m2m(self, instances: list[models.Model], query: dict):
        """Create m2m relationship after the new instances were generated"""
        instance = instances[0]
        model = instance._meta.model
        for field in instance._meta.many_to_many:
            if field.related_model in self.ignore_models:
                continue
            if field.related_model in self.old_new:
                # the model has m2m relationship with already copied instances
                # through is the model/table with the m2m relationship
                through = field.remote_field.through
                # find the foreign key of the m2m table which points to the model of the instances
                instance_fk = next(
                    f
                    for f in through._meta.fields
                    if f.remote_field and f.remote_field.model is model
                )
                # use a subquery to fetch all m2m rows which are related to our instances
                # the subquery avoids sending huge IN statements to the db
                query_dict = {f"{instance_fk.name}__in": model.objects.filter(**query)}
                m2m_instances = through.objects.filter(**query_dict)
                if m2m_instances.exists():
                    instances = list(m2m_instances)
                    old_ids = [x.id for x in instances]
                    # to create them we adjust them and retarget them to the new instances
                    self.pre_copy_mutate(instances)
                    self.pre_copy_retarget(instances)
                    new_instances = through.objects.bulk_create(instances)
                    new_ids = [x.id for x in new_instances]
                    self.old_new[through] = dict(zip(old_ids, new_ids, strict=True))
                else:
                    self.old_new[through] = {}
            else:
                logger.warning(
                    f"{type(instance)} has a field {field} with a related object of type {field.related_model} which was not found in the copied instances. This might be desired if some references should not be copied."
                )

    def deepcopy(self, instance: models.Model):
        try:
            model_index = self.model_hierarchy.index(instance._meta.model)
        except ValueError as err:
            raise Exception(f"{instance._meta.model} not part of hierarchy") from err

        # Query to find related objects
        query = {"id": instance.id}

        # Return the copies of the first instance
        new_org_instances = None
        for model in self.model_hierarchy[model_index:]:
            instances = list(model.objects.filter(**query))
            if not new_org_instances:
                new_org_instances = instances
            if not instances:
                self.old_new[model] = {}
                continue
            old_ids = [x.id for x in instances]
            # Make the objects db writeable by enforcing db requirements
            self.pre_copy_mutate(instances)
            # check fields of table. if a model references a already copied model
            # the old_new dict is checked and the reference is targeted to the new instance
            self.pre_copy_retarget(instances)
            # The instances can be created now
            new_instances = model.objects.bulk_create(instances)
            new_ids = [x.id for x in new_instances]
            self.old_new[model] = dict(zip(old_ids, new_ids, strict=True))
            # if the model has many to many relationships they can now be created
            self.post_copy_m2m(instances, query)

            # prepare the query for 'lower' models
            if model == Project:
                # project is part of the first copy
                query = {"project_id__in": old_ids}
            elif model == Scenario:
                query = {"scenario_id__in": Scenario.objects.filter(**query)}
            else:
                # all other objects should be found via scenario_id
                pass
        self.update_backlog_fields()
        return new_org_instances[0]
