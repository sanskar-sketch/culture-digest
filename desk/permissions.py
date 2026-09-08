"""Who can get into the desk.

Plain session auth, the same one Django ships with - just gated on
`is_staff` rather than routed through `django.contrib.admin`.
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render


def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url="desk:login")
        if not (request.user.is_active and request.user.is_staff):
            # Not redirected to the login page - an already-authenticated,
            # non-staff user would just bounce straight back here, looping.
            return render(request, "desk/forbidden.html", status=403)
        return view(request, *args, **kwargs)

    return wrapped
