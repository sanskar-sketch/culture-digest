"""Editor accounts and permission groups.

The last part of the desk that still pointed at Django's own admin. It
does now what the rest of the desk does - its own views, its own
templates - with two differences that matter, because this is the one
section where a mistake is a security mistake rather than an editorial
one:

* every view is superuser-only, not merely staff (see
  desk.permissions.superuser_required);
* a password is never rendered, only set, and always through Django's
  own form so it is hashed and validated the same way as anywhere else.
"""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import Group, User
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.forms import GroupForm, UserCreateForm, UserEditForm, UserPasswordForm
from desk.permissions import superuser_required
from desk.utils import filter_options, paginate, search


@superuser_required
def user_list(request):
    qs = User.objects.all().prefetch_related("groups")
    qs = search(qs, request, ["username", "first_name", "last_name", "email"])
    role = request.GET.get("role")
    if role == "superuser":
        qs = qs.filter(is_superuser=True)
    elif role == "staff":
        qs = qs.filter(is_staff=True, is_superuser=False)
    elif role == "none":
        qs = qs.filter(is_staff=False)
    active = request.GET.get("active")
    if active in ("1", "0"):
        qs = qs.filter(is_active=(active == "1"))

    context = {
        "page_title": "Users",
        "page_blurb": "Editor accounts: who can sign in, and what they're allowed to "
                      "do once they're here.",
        "breadcrumbs": [("Users", None)],
        "page_obj": paginate(request, qs.order_by("username")),
        "result_count": qs.count(),
        "search_placeholder": "Search users…",
        "filter_groups": [
            {"title": "Role", "param": "role", "options": filter_options(request, "role", [
                ("superuser", "Superuser"), ("staff", "Editor"), ("none", "No desk access")])},
            {"title": "Active", "param": "active",
             "options": filter_options(request, "active", [("1", "Active"), ("0", "Disabled")])},
        ],
        "has_active_filters": bool(request.GET.get("role") or request.GET.get("active")),
        "add_url": reverse("desk:users_add"),
        "current_user_id": request.user.pk,
    }
    return render(request, "desk/user_list.html", context)


@superuser_required
def user_form(request, pk=None):
    instance = get_object_or_404(User, pk=pk) if pk else None
    form_class = UserEditForm if instance else UserCreateForm

    if request.method == "POST":
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"Saved {user.username}.")
            return redirect("desk:users_change", pk=user.pk)
    else:
        form = form_class(instance=instance)

    context = {
        "page_title": "Add user" if not instance else instance.username,
        "breadcrumbs": [("Users", reverse("desk:users_list")),
                        ("Add" if not instance else instance.username, None)],
        "form": form,
        "instance": instance,
        "is_self": instance is not None and instance.pk == request.user.pk,
    }
    return render(request, "desk/user_form.html", context)


@superuser_required
def user_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            # Changing your own password rotates the session hash, which
            # would otherwise sign you straight out.
            if user.pk == request.user.pk:
                update_session_auth_hash(request, user)
            messages.success(request, f"Password set for {user.username}.")
            return redirect("desk:users_change", pk=user.pk)
    else:
        form = UserPasswordForm(user)

    context = {
        "page_title": f"Set password for {user.username}",
        "breadcrumbs": [("Users", reverse("desk:users_list")),
                        (user.username, reverse("desk:users_change", args=[user.pk])),
                        ("Password", None)],
        "form": form,
        "instance": user,
    }
    return render(request, "desk/user_password.html", context)


@superuser_required
def group_list(request):
    qs = Group.objects.annotate(members=Count("user", distinct=True),
                                permissions_count=Count("permissions", distinct=True))
    qs = search(qs, request, ["name"])
    context = {
        "page_title": "Groups",
        "page_blurb": "Bundles of permissions, if you want more than one kind of editor "
                      "rather than granting everything to everyone.",
        "breadcrumbs": [("Groups", None)],
        "page_obj": paginate(request, qs.order_by("name")),
        "result_count": qs.count(),
        "search_placeholder": "Search groups…",
        "add_url": reverse("desk:groups_add"),
    }
    return render(request, "desk/group_list.html", context)


@superuser_required
def group_form(request, pk=None):
    instance = get_object_or_404(Group, pk=pk) if pk else None
    if request.method == "POST":
        form = GroupForm(request.POST, instance=instance)
        if form.is_valid():
            group = form.save()
            messages.success(request, f"Saved “{group.name}”.")
            return redirect("desk:groups_list")
    else:
        form = GroupForm(instance=instance)

    context = {
        "page_title": "Add group" if not instance else instance.name,
        "breadcrumbs": [("Groups", reverse("desk:groups_list")),
                        ("Add" if not instance else instance.name, None)],
        "form": form,
        "instance": instance,
    }
    return render(request, "desk/group_form.html", context)
