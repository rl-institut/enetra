from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from django.utils import timezone


class TimezoneMiddleware:
    """Activate the timezone from the 'tz' cookie (set by JS in the base templates)"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tz = request.COOKIES.get("tz")
        if tz:
            try:
                timezone.activate(ZoneInfo(tz))
            except (ZoneInfoNotFoundError, ValueError):
                timezone.deactivate()
        else:
            # Workers reuse threads; without this the previous request's
            # timezone would leak into cookie-less requests
            timezone.deactivate()
        return self.get_response(request)
