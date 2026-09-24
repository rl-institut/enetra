# conftest.py
import pytest


# enetra/settings.py
class InvalidVariable(str):
    # cotton's implicit/optional vars, and lucide icon props with defaults
    IGNORED_VARS = {"class", "attrs", "slot", "errors", "success"}

    def __mod__(self, var_name):
        if str(var_name) in self.IGNORED_VARS:
            return ""
        raise Exception(f"Invalid template variable: '{var_name}'")


@pytest.fixture(autouse=True)
def fail_on_invalid_template_vars(settings):
    settings.TEMPLATES[0]["OPTIONS"]["string_if_invalid"] = InvalidVariable("%s")
