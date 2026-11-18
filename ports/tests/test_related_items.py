"""
Tests for ports.util.get_related_items and ports.util.get_items_to_be_deleted.

The deletion tests compare the result of get_items_to_be_deleted against what the database
really deletes: every row of every concrete model is snapshotted, the instance is deleted
inside a transaction, the rows are snapshotted again and the transaction is rolled back.
"""

from django.apps import apps
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from ports.models import Area
from ports.models import DeletedItem
from ports.models import Generator
from ports.models import Grid
from ports.models import Load
from ports.models import Project
from ports.models import Result
from ports.models import ResultData
from ports.models import Scenario
from ports.models import Setting
from ports.models import Storage
from ports.models import Timeseries
from ports.util import get_items_to_be_deleted
from ports.util import get_related_items

POLY = "SRID=4326;POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"


def keys(objs) -> set[tuple[str, object]]:
    """Comparable (model label, pk) representation of instances"""
    return {(o._meta.concrete_model._meta.label, o.pk) for o in objs}


def snapshot_db() -> set[tuple[str, object]]:
    """(model label, pk) of every row of every concrete model.

    Auto created models (m2m through tables) are skipped, since get_related_items does not
    return through rows.
    """
    rows = set()
    for model in apps.get_models():
        meta = model._meta
        if meta.auto_created or meta.proxy or not meta.managed:
            continue
        rows.update((meta.label, pk) for pk in model._base_manager.values_list("pk", flat=True))
    return rows


def db_deleted_keys(instances) -> set[tuple[str, object]]:
    """Delete instances inside a transaction which is rolled back.
    Returns the (model label, pk) of all rows removed by the database delete."""
    model = type(instances[0])
    before = snapshot_db()
    with transaction.atomic():
        model._base_manager.filter(pk__in=[i.pk for i in instances]).delete()
        after = snapshot_db()
        transaction.set_rollback(True)
    assert snapshot_db() == before, "Rollback failed"
    return before - after


class RelatedItemsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        """
        Scenario
        ├── Area a1 ── Generator g1, Load l1 (t1)
        ├── Area a2 ── Storage s1, Load l2 (t1), Load l3 (t2)
        ├── Area a3   (nothing attached)
        ├── Timeseries t1, t2
        ├── Grid gr1 (areas a1, a2; load=l1 SET_NULL; connected_to=gr2 SET_NULL)
        ├── Grid gr2 (areas a3)
        ├── Setting, DeletedItem (content_type FK CASCADE)
        └── Result ── ResultData (nested non ScenarioItem)
        A second scenario in another project must never be touched.
        """
        cls.user = User.objects.create_user("testuser", password="pass")
        cls.project = Project.objects.create(name="Projekt", manager=cls.user)
        cls.scenario = Scenario.objects.create(
            name="Scenario", project=cls.project, manager=cls.user
        )
        s = cls.scenario
        mk_area = lambda name: Area.objects.create(  # noqa: E731
            scenario=s,
            name=name,
            manager=cls.user,
            area_type=Area.AreaTypeChoices.BUILDING,
            usage=Area.BuildingUsageChoices.OFFICE,
            geom=GEOSGeometry(POLY),
        )
        cls.a1, cls.a2, cls.a3 = mk_area("a1"), mk_area("a2"), mk_area("a3")
        cls.t1 = Timeseries.objects.create(scenario=s, name="t1", timeseries=[1, 2], spec_load=1)
        cls.t2 = Timeseries.objects.create(scenario=s, name="t2", timeseries=[3, 4], spec_load=1)
        cls.l1 = Load.objects.create(scenario=s, name="l1", area=cls.a1, timeseries=cls.t1)
        cls.l2 = Load.objects.create(scenario=s, name="l2", area=cls.a2, timeseries=cls.t1)
        cls.l3 = Load.objects.create(scenario=s, name="l3", area=cls.a2, timeseries=cls.t2)
        cls.g1 = Generator.objects.create(scenario=s, name="g1", area=cls.a1)
        cls.s1 = Storage.objects.create(scenario=s, name="s1", area=cls.a2)
        cls.gr2 = Grid.objects.create(scenario=s, name="gr2", carrier=Grid.CarrierChoices.GAS)
        cls.gr2.areas.set([cls.a3])
        cls.gr1 = Grid.objects.create(scenario=s, name="gr1", load=cls.l1, connected_to=cls.gr2)
        cls.gr1.areas.set([cls.a1, cls.a2])
        cls.setting = Setting.objects.create(scenario=s, name="setting")
        cls.deleted_item = DeletedItem.objects.create(
            scenario=s, name="old", content_type=ContentType.objects.get_for_model(Area)
        )
        cls.result = Result.objects.create(
            scenario=s, started_at=timezone.now(), finished_at=timezone.now()
        )
        cls.result_data = ResultData.objects.create(
            result=cls.result, attribute="costs", value=[1.0]
        )

        # unrelated data which must never be returned
        cls.other_project = Project.objects.create(name="Other")
        cls.other_scenario = Scenario.objects.create(name="Other", project=cls.other_project)
        cls.other_area = Area.objects.create(scenario=cls.other_scenario, name="other")
        cls.other_ts = Timeseries.objects.create(
            scenario=cls.other_scenario, name="other", timeseries=[1], spec_load=1
        )
        cls.other_load = Load.objects.create(
            scenario=cls.other_scenario, area=cls.other_area, timeseries=cls.other_ts
        )


class GetItemsToBeDeletedMatchesDbTest(RelatedItemsTestBase):
    """get_items_to_be_deleted must return exactly the rows a database delete removes."""

    def assert_matches_db(self, instances):
        expected = db_deleted_keys(instances)
        # sanity check: the delete actually removed the instances themselves
        self.assertTrue(keys(instances) <= expected)
        got = keys(get_items_to_be_deleted(instances))
        self.assertEqual(
            got,
            expected,
            f"\nreturned but not deleted: {got - expected}\ndeleted but not returned: {expected - got}",
        )

    def test_project(self):
        self.assert_matches_db([self.project])

    def test_scenario(self):
        self.assert_matches_db([self.scenario])

    def test_area_nested(self):
        # a1 -> g1, l1 ; l1 is gr1.load (SET_NULL, gr1 survives)
        self.assert_matches_db([self.a1])

    def test_area_with_multiple_children(self):
        self.assert_matches_db([self.a2])

    def test_multiple_areas(self):
        self.assert_matches_db([self.a1, self.a2, self.a3])

    def test_timeseries_nested(self):
        # t1 -> l1, l2. Areas a1/a2 and gr1 must survive
        self.assert_matches_db([self.t1])

    def test_load(self):
        self.assert_matches_db([self.l1])

    def test_grid_set_null(self):
        # gr2 is connected_to of gr1 with SET_NULL, gr1 survives. m2m rows are not returned
        self.assert_matches_db([self.gr2])

    def test_leaf(self):
        self.assert_matches_db([self.g1])

    def test_result(self):
        self.assert_matches_db([self.result])

    def test_returns_no_duplicates(self):
        items = get_items_to_be_deleted([self.a1, self.a2])
        self.assertEqual(len(items), len(keys(items)))

    def test_other_scenario_untouched(self):
        got = keys(get_items_to_be_deleted([self.scenario]))
        other = keys([self.other_scenario, self.other_area, self.other_ts, self.other_load])
        self.assertFalse(got & other)

    def test_custom_field_filter_is_combined(self):
        # Ignore the Area -> Load relation, cascade via other relations still works
        def no_area_loads(field):
            return field.model is Area and field.related_model is Load

        got = keys(get_items_to_be_deleted([self.a1], field_filter=no_area_loads))
        self.assertNotIn(keys([self.l1]).pop(), got)
        self.assertIn(keys([self.g1]).pop(), got)


class GetRelatedItemsTypeCheckTest(RelatedItemsTestBase):
    def test_mixed_types_raise(self):
        with self.assertRaises(AssertionError):
            get_related_items([self.a1, self.l1])

    def test_mixed_types_raise_not_first(self):
        with self.assertRaises(AssertionError):
            get_related_items([self.a1, self.a2, self.g1])

    def test_mixed_types_raise_for_delete(self):
        with self.assertRaises(AssertionError):
            get_items_to_be_deleted([self.t1, self.l1])

    def test_same_types_ok(self):
        get_related_items([self.a1, self.a2], excluded_models={Scenario, User}, max_depth=1)

    def test_empty(self):
        self.assertEqual(get_related_items([]), [])


class GetRelatedItemsExcludedModelsTest(RelatedItemsTestBase):
    def test_excluded_model_not_returned(self):
        got = keys(get_related_items([self.scenario], excluded_models={Load, User}))
        self.assertFalse({k for k in got if k[0] == "ports.Load"})
        # other models are still found
        self.assertTrue(keys([self.a1, self.t1, self.g1]) <= got)

    def test_excluded_model_not_traversed(self):
        # From a1, t1 is only reachable via Load (or via the Scenario).
        got = keys(get_related_items([self.a1], excluded_models={Scenario, User, Load}))
        self.assertNotIn(keys([self.t1]).pop(), got)
        self.assertNotIn(keys([self.l1]).pop(), got)
        self.assertNotIn(keys([self.scenario]).pop(), got)
        self.assertIn(keys([self.g1]).pop(), got)

    def test_excluded_model_traversal_when_allowed(self):
        # control for test above: without excluding Load, t1 is reachable
        got = keys(get_related_items([self.a1], excluded_models={Scenario, User}))
        self.assertIn(keys([self.t1]).pop(), got)

    def test_excluded_queries(self):
        # excluded models must not even be queried
        with self.assertNumQueries(0):
            get_related_items(
                [self.g1],
                excluded_models={Scenario, User, Area},
            )


class GetRelatedItemsFieldFilterTest(RelatedItemsTestBase):
    def test_field_filter_receives_fields(self):
        seen = []

        def record(field):
            seen.append(field)
            return False

        get_related_items([self.a1], excluded_models={Scenario, User}, field_filter=record)
        self.assertTrue(seen)
        self.assertTrue(all(f.is_relation for f in seen))
        # excluded related models are skipped before the filter is consulted
        self.assertFalse([f for f in seen if f.related_model in (Scenario, User)])

    def test_field_filter_blocks_relation(self):
        def no_area_loads(field):
            return field.model is Area and field.related_model is Load

        got = keys(
            get_related_items(
                [self.a1], excluded_models={Scenario, User}, field_filter=no_area_loads
            )
        )
        # l1 is still reachable indirectly (a1 -> gr1 -> l1 via Grid.load), so check depth 1:
        # the direct Area -> Load relation must not be used
        got_depth1 = keys(
            get_related_items(
                [self.a1],
                excluded_models={Scenario, User},
                field_filter=no_area_loads,
                max_depth=1,
            )
        )
        self.assertNotIn(keys([self.l1]).pop(), got_depth1)
        self.assertIn(keys([self.g1]).pop(), got_depth1)
        self.assertIn(keys([self.gr1]).pop(), got)

    def test_field_filter_blocks_everything(self):
        got = get_related_items([self.a1], field_filter=lambda _: True)
        self.assertEqual(keys(got), keys([self.a1]))

    def test_field_filter_blocks_everything_no_queries(self):
        with self.assertNumQueries(0):
            get_related_items([self.scenario], field_filter=lambda _: True)


class GetRelatedItemsMaxDepthTest(RelatedItemsTestBase):
    """Traversal from t1 without Scenario/User:
    depth 1: l1, l2 (Load.timeseries)
    depth 2: a1, a2 (Load.area), gr1 (Grid.load)
    depth 3: g1, s1, l3 (via areas), gr2 (gr1.connected_to)
    depth 4: t2 (via l3), a3 (via gr2.areas)
    """

    excluded = {Scenario, User}

    def related(self, max_depth):
        return keys(
            get_related_items([self.t1], excluded_models=self.excluded, max_depth=max_depth)
        )

    def test_depth_1(self):
        self.assertEqual(self.related(1), keys([self.t1, self.l1, self.l2]))

    def test_depth_2(self):
        self.assertEqual(
            self.related(2), keys([self.t1, self.l1, self.l2, self.a1, self.a2, self.gr1])
        )

    def test_depth_3(self):
        self.assertEqual(
            self.related(3),
            keys(
                [self.t1, self.l1, self.l2, self.a1, self.a2, self.gr1]
                + [self.g1, self.s1, self.l3, self.gr2]
            ),
        )

    def test_unlimited(self):
        everything = keys(
            [self.t1, self.l1, self.l2, self.a1, self.a2, self.gr1]
            + [self.g1, self.s1, self.l3, self.gr2]
            + [self.t2, self.a3]
        )
        self.assertEqual(self.related(None), everything)
        self.assertEqual(self.related(4), everything)
        self.assertEqual(self.related(100), everything)

    def test_depth_monotonic(self):
        prev = set()
        for depth in range(1, 6):
            cur = self.related(depth)
            self.assertTrue(prev <= cur)
            prev = cur

    def test_depth_limits_queries(self):
        # depth 1 only queries the relations of the starting model
        with self.assertNumQueries(len(self._relations(Timeseries))):
            self.related(1)

    def _relations(self, model):
        return [
            f
            for f in model._meta.get_fields()
            if f.is_relation and f.remote_field and f.related_model not in self.excluded
        ]
