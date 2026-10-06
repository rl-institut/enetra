from django.conf import settings


# make the carto_api_token available as context in all templates
def default_ports_context(request):
    return {"CARTO_API_TOKEN": settings.CARTO_API_TOKEN}
