# conftest.py
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--check-template-vars",
        action="store_true",
        default=False,
        help="Check if template vars are undefined / missing. Some Variables are exluded from being checked",
    )


# enetra/settings.py
class InvalidVariable(str):
    # cotton's implicit/optional vars, and lucide icon props with defaults
    IGNORED_VARS = {"class", "attrs", "slot", "errors", "success"}

    def __mod__(self, var_name):
        if str(var_name) in self.IGNORED_VARS:
            return ""
        raise Exception(f"Invalid template variable: '{var_name}'")


@pytest.fixture(autouse=True)
def fail_on_invalid_template_vars(settings, request):
    if request.config.getoption("--check-template-vars"):
        settings.TEMPLATES[0]["OPTIONS"]["string_if_invalid"] = InvalidVariable("%s")
