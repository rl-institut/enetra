"""
Tests deepcopying

Some tests in DeepcopyGridM2MTest are written test-first and are expected to
fail against the current implementation of ports/deepcopy.py:
- M2M relations (Grid.areas) are not actually copied: `post_copy_m2m` builds an
  invalid queryset lookup and raises FieldError for any M2M field.
- A nullable FK pointing at an already-copied model (e.g. Grid.timeseries) raises
  KeyError in `pre_copy_retarget` when the FK value is None.
These tests document the desired behaviour and should start passing once those
bugs are fixed.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from ports.deepcopy import Deepcopy
from ports.models import Area
from ports.models import Generator
from ports.models import Grid
from ports.models import Load
from ports.models import LoadTemplate
from ports.models import Project
from ports.models import Scenario

DEEPCOPY_LOGGER = "ports.deepcopy"


class DeepcopyProjectTest(TestCase):
    """Deepcopy of a whole Project: FK retargeting, internal_id handling,
    untouched originals and warnings for relations that are intentionally not
    copied (e.g. the User FK on manager/updated_user)."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )
        cls.area2 = Area.objects.create(
            scenario=cls.scenario,
            name="Area 2",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )

        cls.load_template = LoadTemplate.objects.create(
            scenario=cls.scenario,
            name="Template",
            timeseries={},
            spec_load=0.0,
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="Load 1",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )
        cls.load2 = Load.objects.create(
            scenario=cls.scenario,
            name="Load 2",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )

        cls.generator = Generator.objects.create(
            scenario=cls.scenario,
            name="Generator 1",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )
        cls.generator2 = Generator.objects.create(
            scenario=cls.scenario,
            name="Generator 2",
            area=cls.area,
            carrier=Generator.CarrierChoices.DIESEL,
        )

    def deepcopy_project(self, ignore_models=None):
        model_hierarchy = [Project, Scenario, Area, LoadTemplate, Load, Generator]
        if ignore_models is None:
            # User is intentionally never copied; ignore it by default so
            # tests which don't care about warnings stay quiet.
            ignore_models = {User}
        dc = Deepcopy(model_hierarchy, ignore_models=ignore_models)
        return dc.deepcopy(self.project)

    def test_new_project_has_new_pk_and_internal_id_but_same_data(self):
        new_project = self.deepcopy_project()
        assert new_project.id != self.project.id
        assert new_project.internal_id != self.project.internal_id
        assert new_project.name == self.project.name

    def test_scenario_is_copied_with_new_pk_and_internal_id(self):
        new_project = self.deepcopy_project()
        assert new_project.scenario_set.count() == 1
        new_scenario = new_project.scenario_set.get()
        assert new_scenario.id != self.scenario.id
        assert new_scenario.internal_id != self.scenario.internal_id
        assert new_scenario.name == self.scenario.name

    def test_scenario_items_get_new_pk_but_keep_internal_id(self):
        new_project = self.deepcopy_project()
        new_scenario = new_project.scenario_set.get()

        old_areas_by_uuid = {a.internal_id: a for a in Area.objects.filter(scenario=self.scenario)}
        new_areas_by_uuid = {a.internal_id: a for a in Area.objects.filter(scenario=new_scenario)}
        assert set(old_areas_by_uuid) == set(new_areas_by_uuid)
        assert len(old_areas_by_uuid) == 2
        for internal_id, old_area in old_areas_by_uuid.items():
            new_area = new_areas_by_uuid[internal_id]
            assert new_area.id != old_area.id
            assert new_area.name == old_area.name

    def test_load_foreign_keys_are_retargeted_to_copied_instances(self):
        new_project = self.deepcopy_project()
        new_scenario = new_project.scenario_set.get()
        new_area = Area.objects.get(scenario=new_scenario, internal_id=self.area.internal_id)
        new_template = LoadTemplate.objects.get(scenario=new_scenario)

        new_loads = Load.objects.filter(scenario=new_scenario)
        assert new_loads.count() == 2
        for new_load in new_loads:
            assert new_load.area_id == new_area.id
            assert new_load.area_id != self.area.id
            assert new_load.template_id == new_template.id
            assert new_load.template_id != self.load_template.id

    def test_generator_foreign_keys_are_retargeted_to_copied_instances(self):
        new_project = self.deepcopy_project()
        new_scenario = new_project.scenario_set.get()
        new_area = Area.objects.get(scenario=new_scenario, internal_id=self.area.internal_id)

        new_generators = Generator.objects.filter(scenario=new_scenario)
        assert new_generators.count() == 2
        for new_generator in new_generators:
            assert new_generator.area_id == new_area.id
            assert new_generator.area_id != self.area.id

    def test_original_instances_are_left_untouched(self):
        self.deepcopy_project()
        assert Project.objects.filter(id=self.project.id).exists()
        assert Scenario.objects.filter(id=self.scenario.id, project=self.project).exists()
        assert Area.objects.filter(scenario=self.scenario).count() == 2
        assert Load.objects.filter(scenario=self.scenario, area=self.area).count() == 2
        assert Generator.objects.filter(scenario=self.scenario, area=self.area).count() == 2

    def test_warns_about_user_foreign_keys_when_user_is_not_ignored(self):
        """Without ignore_models, User FKs are never copied and must warn."""
        with self.assertLogs(DEEPCOPY_LOGGER, level="WARNING") as cm:
            self.deepcopy_project(ignore_models=set())
        messages = "\n".join(cm.output)
        # manager/updated_user reference User, which is intentionally not part
        # of the model_hierarchy and therefore must never be silently dropped.
        assert "ports.Area.manager" in messages
        assert "ports.Area.updated_user" in messages
        assert "ports.Load.manager" in messages
        assert "ports.Generator.manager" in messages
        assert "django.contrib.auth.models.User" in messages

    def test_ignore_models_suppresses_user_warnings(self):
        """ignore_models={User} (the default used by deepcopy_project) must
        suppress the manager/updated_user warnings entirely."""
        with self.assertNoLogs(DEEPCOPY_LOGGER, level="WARNING"):
            self.deepcopy_project()


class DeepcopyScenarioTest(TestCase):
    """Deepcopy starting at a Scenario (Project excluded from the hierarchy).
    The root instance itself is never retargeted (only descendants are), so the
    copied scenario keeps pointing at the original project and no warning is
    raised for that specific field since ignore_model contains Project"""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )
        cls.load_template = LoadTemplate.objects.create(
            scenario=cls.scenario,
            name="Template",
            timeseries={},
            spec_load=0.0,
        )
        cls.load = Load.objects.create(
            scenario=cls.scenario,
            name="Load 1",
            area=cls.area,
            template=cls.load_template,
            factor=1.0,
        )

    def deepcopy_scenario(self):
        model_hierarchy = [Scenario, Area, LoadTemplate, Load]
        dc = Deepcopy(model_hierarchy, ignore_models={User, Project})
        return dc.deepcopy(self.scenario)

    def test_new_scenario_has_new_pk_and_internal_id(self):
        new_scenario = self.deepcopy_scenario()
        assert new_scenario.id != self.scenario.id
        assert new_scenario.internal_id != self.scenario.internal_id
        assert new_scenario.name == self.scenario.name

    def test_new_scenario_keeps_reference_to_original_project(self):
        """Project is not part of the hierarchy, so it must not be duplicated;
        the copy shares the original project instead."""
        new_scenario = self.deepcopy_scenario()
        assert new_scenario.project_id == self.project.id

    def test_scenario_items_are_still_copied_and_retargeted(self):
        new_scenario = self.deepcopy_scenario()
        new_area = Area.objects.get(scenario=new_scenario, internal_id=self.area.internal_id)
        new_load = Load.objects.get(scenario=new_scenario, internal_id=self.load.internal_id)

        assert new_area.id != self.area.id
        assert new_load.area_id == new_area.id
        assert new_load.area_id != self.area.id

    def test_original_scenario_and_items_untouched(self):
        self.deepcopy_scenario()
        assert Scenario.objects.filter(id=self.scenario.id).exists()
        assert Area.objects.filter(id=self.area.id, scenario=self.scenario).exists()
        assert Load.objects.filter(id=self.load.id, scenario=self.scenario).exists()


class DeepcopyGridM2MTest(TestCase):
    """Grid exercises the M2M relation (areas), a nullable self-referential FK
    (connected_to) and a nullable FK to an already-copied model (timeseries).
    These pin down how Deepcopy is expected to handle M2M rows and
    warn/skip relations that were not (or could not yet be) copied.
    """

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name="Test Projekt")
        cls.scenario = Scenario.objects.create(name="Test Scenario", project=cls.project)
        cls.area = Area.objects.create(
            scenario=cls.scenario,
            name="Area 1",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )
        cls.area2 = Area.objects.create(
            scenario=cls.scenario,
            name="Area 2",
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
        )
        cls.grid = Grid.objects.create(
            scenario=cls.scenario,
            name="Grid 1",
            carrier=Grid.CarrierChoices.ELECTRICITY,
        )

        cls.grid2 = Grid.objects.create(
            scenario=cls.scenario,
            name="Grid 2",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            connected_to=cls.grid,
        )
        cls.grid.areas.set([cls.area, cls.area2])

    def test_m2m_relations_are_retargeted_to_copied_instances(self):
        """Expected: the M2M rows on the copy should point
        at the newly copied Area instances, with the same count as the
        original, and never at the originals."""
        model_hierarchy = [Project, Scenario, Area, Grid]
        dc = Deepcopy(model_hierarchy, ignore_models={User})
        new_project = dc.deepcopy(self.project)
        new_scenario = new_project.scenario_set.get()
        new_grid = Grid.objects.get(scenario=new_scenario, internal_id=self.grid.internal_id)
        new_area_ids = set(Area.objects.filter(scenario=new_scenario).values_list("id", flat=True))
        copied_area_ids = set(new_grid.areas.values_list("id", flat=True))

        assert new_grid.areas.count() == self.grid.areas.count() == 2
        assert copied_area_ids == new_area_ids
        assert not copied_area_ids & {self.area.id, self.area2.id}

    def test_original_m2m_relations_are_left_untouched(self):
        model_hierarchy = [Project, Scenario, Area, Grid]
        dc = Deepcopy(model_hierarchy, ignore_models={User})
        dc.deepcopy(self.project)
        assert set(self.grid.areas.values_list("id", flat=True)) == {self.area.id, self.area2.id}

    def test_self_referential_fk_is_changed(self):
        """connected_to points at Grid itself. Make sure its"""
        model_hierarchy = [Project, Scenario, Area, Grid]
        dc = Deepcopy(model_hierarchy, ignore_models={User})
        with self.assertLogs(DEEPCOPY_LOGGER, level="WARNING") as _:
            new_project = dc.deepcopy(self.project)
        new_scenario = new_project.scenario_set.get()
        old_connected_grid = self.grid2
        new_grid = Grid.objects.get(
            scenario=new_scenario, internal_id=old_connected_grid.internal_id
        )

        assert new_grid.connected_to_id is not None

    def test_nullable_fk_to_already_copied_model_does_not_crash(self):
        """Expected (currently failing): Grid.timeseries is a nullable FK to
        Load. When Load has already been copied but a given Grid's timeseries
        is unset (None), the copy should keep it None rather than raising."""
        load_template = LoadTemplate.objects.create(
            scenario=self.scenario, name="Template", timeseries={}, spec_load=0.0
        )
        Load.objects.create(
            scenario=self.scenario,
            name="Load 1",
            area=self.area,
            template=load_template,
            factor=1.0,
        )
        grid_without_timeseries = Grid.objects.create(
            scenario=self.scenario,
            name="Grid without timeseries",
            carrier=Grid.CarrierChoices.ELECTRICITY,
            timeseries=None,
        )

        model_hierarchy = [Project, Scenario, Area, LoadTemplate, Load, Grid]
        dc = Deepcopy(model_hierarchy, ignore_models={User})
        new_project = dc.deepcopy(self.project)
        new_scenario = new_project.scenario_set.get()

        new_grid = Grid.objects.get(
            scenario=new_scenario, internal_id=grid_without_timeseries.internal_id
        )
        assert new_grid.timeseries_id is None

    def test_warns_and_skips_m2m_when_related_model_not_in_hierarchy(self):
        """If Area is not part of the copied hierarchy, the M2M rows cannot be
        retargeted. Deepcopy must warn and simply skip copying them, instead of
        silently dropping them without any signal or crashing."""
        model_hierarchy = [Project, Scenario, Grid]
        dc = Deepcopy(model_hierarchy, ignore_models={User})
        with self.assertLogs(DEEPCOPY_LOGGER, level="WARNING") as cm:
            new_project = dc.deepcopy(self.project)
        new_scenario = new_project.scenario_set.get()
        new_grid = Grid.objects.get(scenario=new_scenario, internal_id=self.grid.internal_id)

        assert new_grid.areas.count() == 0
        messages = "\n".join(cm.output)
        assert "ports.Grid.areas" in messages
        assert "ports.models.Area" in messages
