from django.apps import AppConfig


class PortsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ports"

    def ready(self):
        # import models to ensure they are loaded
        from .models import ScenarioItem

        # connect pre_delete for every subclass of BaseModel
        for subclass in ScenarioItem.__subclasses__():
            subclass._connect_signals()
