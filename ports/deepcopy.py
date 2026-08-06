"""Deepcopy an object based on a given table hierarchy
The instances are copied in the order of the hierarchy.
It is assumed creation in the order is possible.
Each Model can perform a pre_copy mutation, e.g. change its internal_id to fulfill
uniqueness.
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
        query: dict | None = None,
    ):
        # list of models in the order they are deepcopied
        self._model_hierarchy = model_hierarchy
        self.model_hierarchy = iter(model_hierarchy)
        self.query: dict = query or dict()
        self.old_new = dict()

    def pre_copy_mutate(self, instances: list[models.Model]):
        """Adjust the instances in place so they can be copied"""
        match instances[0]:
            case Project() | Scenario():
                for instance in instances:
                    instance.id = None
                    instance.internal_id = uuid4()
            case _:
                for instance in instances:
                    instance.id = None

    def pre_copy_retarget(self, instances: list[models.Model]):
        """Adjust the foreign fields according to already copied instances"""
        instance = instances[0]
        for field in instance._meta.fields:
            if not field.is_relation:
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
                logger.warning(
                    f"{type(instance)} has a field {field} with a related object of type {field.related_model} which was not found in the copied instances. This might be desired if some references should not be copied"
                )

    def post_copy_m2m(self, instances: list[models.Model]):
        """Create m2m relationship after the new instances were generated"""
        instance = instances[0]
        model = instance._meta.model
        for field in instance._meta.many_to_many:
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
                query_dict = {f"{instance_fk.name}__in": model.objects.filter(**self.query)}
                m2m_instances = through.objects.filter(**query_dict)
                if m2m_instances.exists():
                    # to create them we adjust them and retarget them to the new instances
                    self.pre_copy_mutate(m2m_instances)
                    self.pre_copy_retarget(m2m_instances)
            else:
                logger.warning(
                    f"{type(instance)} has a field {field} with a related object of type {field.related_model} which was not found in the copied instances. This might be desired if some references should not be copied"
                )

    def deepcopy(self, instance: models.Model):
        # decouple instance from copy
        instance = instance._meta.model.objects.get(id=instance.pk)
        # cast instance to list, since the class methods are intended for lists
        original_instances = [instance]
        # Copies the given instance and sets the query to find related objects
        new_org_instances = self.first_copy(original_instances)
        for model in self.model_hierarchy:
            instances = list(model.objects.filter(**self.query))
            old_ids = [x.id for x in instances]
            # Make the objects db writeable by enforcing db requirements
            self.pre_copy_mutate(instances)
            # check fields of table. if a model references a already copied model
            # the old_new dict is checked and the reference is targeted to the new instance
            self.pre_copy_retarget(instances)
            # The instances can be created now
            new_instances = model.objects.bulk_create(instances)
            new_ids = [x.id for x in new_instances]
            self.old_new[model] = dict(zip(old_ids, new_ids, strict=False))
            # if the model has many to many relationships they can now be created
            self.post_copy_m2m(instances)

            # prepare the query for 'lower' models
            if model == Project:
                # project is part of the first copy
                raise Exception("Project should not be copied here")
            elif model == Scenario:
                self.query = {"scenario_id__in": Scenario.objects.filter(**self.query)}
            else:
                # all other objects should be found via scenario_id
                pass
        return new_org_instances[0]

    def first_copy(self, instances: list[models.Model]):
        copy_model = instances[0]._meta.model
        for model in self.model_hierarchy:
            if copy_model == model:
                break
        else:
            raise Exception(
                f"Instance of type {copy_model} to deepcopy is not part of the model_hierarchy"
            )
        old_ids = [x.id for x in instances]
        self.pre_copy_mutate(instances)
        new_instances = copy_model.objects.bulk_create(instances)
        new_ids = [x.id for x in new_instances]
        self.old_new[copy_model] = dict(zip(old_ids, new_ids, strict=False))
        match instances:
            # pattern match a list with first element of type Project
            case [Project(), *_]:
                # Query to find related objects
                self.query = {"project_id__in": old_ids}
            case [Scenario(), *_]:
                self.query = {"scenario_id__in": old_ids}
            case _:
                raise NotImplementedError(f"Deepcopying {copy_model} is not implemented")
        return new_instances
