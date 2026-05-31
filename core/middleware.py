import re
from django.utils.deprecation import MiddlewareMixin

class NoCacheForAuthMiddleware(MiddlewareMixin):
    """Add no-cache headers to any response when the user is authenticated.

    This complements the @never_cache decorator for views that might miss it and
    ensures that browsers cannot serve a cached page after logout via the Back
    button. It sets the standard Django cache‑control headers:
        Cache-Control: no-cache, no-store, must-revalidate, max-age=0
        Pragma: no-cache
        Expires: 0
    """

    def process_response(self, request, response):
        # If the user is authenticated, enforce strict no‑cache headers.
        if getattr(request, "user", None) and request.user.is_authenticated:
            response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
