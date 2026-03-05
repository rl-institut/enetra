from django.contrib import admin
from guardian.admin import GuardedModelAdmin
from unfold.admin import ModelAdmin

from .models import ElectricComponent
from .models import Scenario
from .models import ScenarioItem


# Register your models here.
class ScenarioAdmin(ModelAdmin, GuardedModelAdmin):
    pass


admin.site.register(Scenario, ScenarioAdmin)


# TODO: Implement more advances permissions using django-guardian
def get_queryset(self, request):
    qs = super(self.__class__, self).get_queryset(request)
    if request.user.is_superuser:
        return qs
    # Only show user's own articles for now
    return qs.filter(manager=request.user)


def has_change_permission(self, request, obj=None):
    if request.user.is_superuser:
        return True
    if obj and obj.manager != request.user:
        return False
    return super(self.__class__, self).has_change_permission(request, obj)


methods = {
    "get_queryset": get_queryset,
    "has_change_permission": has_change_permission,
}
# Add all scenario items to the admin panel
for subclass in ScenarioItem.__subclasses__():
    if subclass == ElectricComponent:
        continue  # abstract subclass
    DynamicClass = type(str(subclass) + "Admin", (ModelAdmin,), methods)
    admin.site.register(subclass, DynamicClass)
for subclass in ElectricComponent.__subclasses__():
    DynamicClass = type(str(subclass) + "Admin", (ModelAdmin,), methods)
    admin.site.register(subclass, DynamicClass)
