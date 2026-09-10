"""Deleting things, with the consequences shown first.

One view for every model rather than a per-section copy, because the
interesting part is identical everywhere: work out what else goes, say so
plainly, then do it.

What "else goes" is not guessed. Django's own Collector walks the real
foreign-key graph, which matters here because the cascades are not
obvious: Recommendation.opportunity is CASCADE, so deleting a listing
also erases every record of it having been recommended, and with it the
reader feedback the matching learns from. That is worth a sentence in
front of someone before they press the button, not a surprise after.

Where a softer option exists it is offered alongside - archiving a
listing or deactivating a reader keeps the history that deleting burns.
"""

from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.db.models.deletion import Collector
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.permissions import staff_required
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from siteconfig.emails import EmailTemplate

# kind -> how to find it, what to call it, and where to go afterwards.
DELETABLE = {
    "listings": {
        "model": Opportunity, "label": "event", "list_url": "desk:listings_list",
        "name": lambda o: o.title,
        "instead": "Archiving keeps everything an event was part of, but takes it "
                   "out of matching so it is never recommended again.",
    },
    "interests": {
        "model": Tag, "label": "interest", "list_url": "desk:interests_list",
        "name": lambda o: o.name,
        "instead": None,
    },
    "readers": {
        "model": Reader, "label": "reader", "section": "Users",
        "list_url": "desk:readers_list",
        "name": lambda o: o.email,
        "instead": "Unticking “Is active” stops every email without erasing what "
                   "they were sent or what they told you. Delete is for an actual "
                   "erasure request.",
    },
    "templates": {
        "model": EmailTemplate, "label": "email design", "section": "Email designs",
        "list_url": "desk:templates_list",
        "name": lambda o: o.name,
        "instead": None,
    },
    "users": {
        "model": User, "label": "user", "section": "Editor accounts",
        "list_url": "desk:users_list",
        "name": lambda o: o.username, "superuser_only": True,
        "instead": "Unticking “Active” blocks sign-in without deleting the account "
                   "or anything they made.",
    },
    "groups": {
        "model": Group, "label": "group", "list_url": "desk:groups_list",
        "name": lambda o: o.name, "superuser_only": True,
        "instead": None,
    },
}

# Said in the editor's words, not the database's.
MODEL_WORDING = {
    "opportunity": ("event", "events"),
    "tag": ("interest", "interests"),
    "reader": ("reader", "readers"),
    "newsletterissue": ("newsletter issue", "newsletter issues"),
    "recommendation": ("recommendation and the reader's feedback on it",
                       "recommendations and the reader feedback on them"),
    "campaign": ("campaign", "campaigns"),
    "campaigndelivery": ("delivery record", "delivery records"),
    "emailtemplate": ("email design", "email designs"),
    "user": ("user", "users"),
    "group": ("group", "groups"),
}


def _collateral(objects):
    """Everything that would go with them, counted per model.

    Takes the whole selection at once rather than one at a time, so two
    listings that share a recommendation report it once, not twice.

    Excludes the objects themselves and the pure link rows of a
    many-to-many, which are an implementation detail - unlinking an
    interest from a listing is not something to warn anyone about.
    """
    objects = list(objects)
    if not objects:
        return []
    collector = Collector(using=objects[0]._state.db)
    collector.collect(objects)

    counts = {}
    for model, instances in collector.data.items():
        n = len(instances)
        if model is type(objects[0]):
            n -= len(objects)  # the objects themselves
        if n > 0 and not model._meta.auto_created:
            counts[model] = counts.get(model, 0) + n
    for queryset in collector.fast_deletes:
        model = queryset.model
        if model._meta.auto_created:
            continue
        n = queryset.count()
        if n:
            counts[model] = counts.get(model, 0) + n

    lines = []
    for model, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        singular, plural = MODEL_WORDING.get(
            model._meta.model_name,
            (model._meta.verbose_name, model._meta.verbose_name_plural))
        lines.append(f"{n} {singular if n == 1 else plural}")
    return lines


def _spec_or_404(kind):
    spec = DELETABLE.get(kind)
    if spec is None:
        raise Http404(f"Nothing called {kind!r} can be deleted here.")
    return spec


def _plural(spec, n):
    return spec["label"] if n == 1 else spec["label"] + "s"


def _confirm_page(request, kind, spec, objects, selected_ids=None):
    """The page that stands between a Delete button and the deletion."""
    names = [str(spec["name"](obj)) for obj in objects]
    n = len(objects)
    return render(request, "desk/confirm_delete.html", {
        "page_title": f"Delete {_plural(spec, n)}?",
        "breadcrumbs": [(spec.get("section") or spec["label"].title() + "s",
                         reverse(spec["list_url"])),
                        ("Delete", None)],
        "object_name": names[0] if n == 1 else f"{n} {_plural(spec, n)}",
        "object_names": names if n > 1 else [],
        "count": n,
        "label": spec["label"],
        "label_plural": _plural(spec, n),
        "collateral": _collateral(objects),
        "instead": spec.get("instead"),
        "selected_ids": selected_ids or [],
        "back_url": request.META.get("HTTP_REFERER") or reverse(spec["list_url"]),
    })


def _delete_all(request, spec, objects):
    """Delete one by one so each model's own delete() still runs."""
    for obj in objects:
        obj.delete()
    n = len(objects)
    messages.success(request, f"Deleted {n} {_plural(spec, n)}.")
    return redirect(spec["list_url"])


@staff_required
def delete(request, kind, pk):
    """Delete one row, named, with its consequences shown first."""
    spec = _spec_or_404(kind)
    if spec.get("superuser_only") and not request.user.is_superuser:
        return render(request, "desk/forbidden.html", status=403)
    obj = get_object_or_404(spec["model"], pk=pk)

    if spec["model"] is User and obj.pk == request.user.pk:
        messages.error(request, "You can't delete the account you're signed in with.")
        return redirect("desk:users_change", pk=obj.pk)

    if request.method == "POST":
        name = spec["name"](obj)
        obj.delete()
        messages.success(request, f"Deleted {spec['label']} “{name}”.")
        return redirect(spec["list_url"])
    return _confirm_page(request, kind, spec, [obj])


@staff_required
def delete_selected(request, kind):
    """Delete the ticked rows - same confirmation, several objects.

    The bulk bar's Delete button posts the list form straight here with
    formaction, so the section's own view never has to know about it.
    """
    spec = _spec_or_404(kind)
    if spec.get("superuser_only") and not request.user.is_superuser:
        return render(request, "desk/forbidden.html", status=403)
    list_url = reverse(spec["list_url"])
    if request.method != "POST":
        return redirect(list_url)

    ids = request.POST.getlist("selected")
    objects = list(spec["model"].objects.filter(pk__in=ids))
    if spec["model"] is User:
        kept = [o for o in objects if o.pk != request.user.pk]
        if len(kept) != len(objects):
            messages.warning(request, "Left out the account you're signed in with.")
        objects = kept
    if not objects:
        messages.warning(request, "Nothing selected.")
        return redirect(list_url)

    if request.POST.get("confirm"):
        return _delete_all(request, spec, objects)
    return _confirm_page(request, kind, spec, objects,
                         selected_ids=[obj.pk for obj in objects])
