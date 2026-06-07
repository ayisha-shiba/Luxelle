import re
from django.utils.deprecation import MiddlewareMixin

class NoCacheForAuthMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        if getattr(request, "user", None) and request.user.is_authenticated:
            response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
