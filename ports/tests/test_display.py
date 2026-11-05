"""
Tests for ItemTemplate.display_value() and the {{ instance|display:"field" }}
template filter (ports/templatetags/display.py).
"""

from django.template import Context
from django.template import Template
from django.test import TestCase

from ports.models import GeneratorTemplate


class DisplayValueTest(TestCase):
    """Unit tests directly against ItemTemplate.display_value()."""

    def test_default_formatting_uses_units_suffix(self):
        instance = GeneratorTemplate(power_kw=10.0)
        result = instance.display_value("power_kw")
        self.assertIn("10", result)
        self.assertIn("kW", result)

    def test_default_formatting_without_units_entry_returns_plain_value(self):
        instance = GeneratorTemplate(name="My Generator")
        self.assertIn("My Generator", instance.display_value("name"))

    def test_default_formatting_of_empty_value_does_not_raise(self):
        instance = GeneratorTemplate(power_kw=None)
        result = instance.display_value("power_kw")
        self.assertIsInstance(result, str)
        self.assertNotEqual(result, "None")

    def test_formatting_with_custom_formatter(self):
        # ElectricComponentTemplate.display_formatters converts the stored fraction
        # to a percentage for "efficiency" instead of using a UNITS suffix.
        instance = GeneratorTemplate(efficiency=0.5)
        result = instance.display_value("efficiency")
        self.assertIn("50", result)
        self.assertIn("%", result)

    def test_error_on_unknown_field_name(self):
        instance = GeneratorTemplate(power_kw=10.0)
        with self.assertRaises(AttributeError):
            instance.display_value("not_a_real_field")


class DisplayFilterTest(TestCase):
    """Integration test: the template filter delegates to display_value()."""

    def render(self, instance, field_name):
        template = Template('{% load display %}{{ instance|display:"' + field_name + '" }}')
        return template.render(Context({"instance": instance}))

    def test_filter_renders_formatted_value(self):
        instance = GeneratorTemplate(efficiency=0.5)
        result = self.render(instance, "efficiency")
        self.assertIn("50", result)
        self.assertIn("%", result)

    def test_filter_renders_empty_value_without_raising(self):
        instance = GeneratorTemplate(power_kw=None)
        result = self.render(instance, "power_kw")
        self.assertNotEqual(result, "None")

    def test_filter_propagates_error_on_unknown_field_name(self):
        instance = GeneratorTemplate(power_kw=10.0)
        with self.assertRaises(AttributeError):
            self.render(instance, "not_a_real_field")
