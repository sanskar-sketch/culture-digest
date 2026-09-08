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

from campaigns.models import Campaign
from desk.permissions import staff_required, superuser_required
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from siteconfig.emails import EmailTemplate

# kind -> how to find it, what to call it, and where to go afterwards.
DELETABLE = {
    "listings": {
        "model": Opportunity, "label": "listing", "list_url": "desk:listings_list",
        "name": lambda o: o.title,
        "instead": "Archiving it keeps the listing and everything it was part of, "
                   "but takes it out of matching so it is never recommended again.",
    },
    "interests": {
        "model": Tag, "label": "interest", "list_url": "desk:interests_list",
        "name": lambda o: o.name,
        "instead": None,
    },
    "readers": {
        "model": Reader, "label": "reader", "list_url": "desk:readers_list",
        "name": lambda o: o.email,
        "instead": "Unticking “Is active” stops every email without erasing what "
                   "they were sent or what they told you. Delete is for an actual "
                   "erasure request.",
    },
    "campaigns": {
        "model": Campaign, "label": "campaign", "list_url": "desk:campaigns_list",
        "name": lambda o: o.name,
        "instead": "Cancelling it stops anything not yet sent while keeping the "
                   "record of what was.",
    },
    "templates": {
        "model": EmailTemplate, "label": "email template", "list_url": "desk:templates_list",
        "name": lambda o: o.name,
        "instead": None,
    },
    "users": {
        "model": User, "label": "user", "list_url": "desk:users_list",
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
    "opportunity": ("listing", "listings"),
    "tag": ("interest", "interests"),
    "reader": ("reader", "readers"),
    "newsletterissue": ("newsletter issue", "newsletter issues"),
    "recommendation": ("recommendation and the reader's feedback on it",
                       "recommendations and the reader feedback on them"),
    "campaign": ("campaign", "campaigns"),
    "campaigndelivery": ("delivery record", "delivery records"),
    "emailtemplate": ("email template", "email templates"),
    "user": ("user", "users"),
    "group": ("group", "groups"),
}


def _collateral(obj):
    """Everything that would go with it, counted per model.

    Excludes the object itself and the pure link rows of a many-to-many,
    which are an implementation detail - unlinking an interest from a
    listing is not something to warn anyone about.
    """
    collector = Collector(using=obj._state.db)
    collector.collect([obj])

    counts = {}
    for model, instances in collector.data.items():
        n = len(instances)
        if model is type(obj):
            n -= 1  # the object itself
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


def _view(request, kind, pk):
    spec = DELETABLE.get(kind)
    if spec is None:
        raise Http404(f"Nothing called {kind!r} can be deleted here.")
    obj = get_object_or_404(spec["model"], pk=pk)

    if spec["model"] is User and obj.pk == request.user.pk:
        messages.error(request, "You can't delete the account you're signed in with.")
        return redirect("desk:users_change", pk=obj.pk)

    name = spec["name"](obj)
    if request.method == "POST":
        obj.delete()
        messages.success(request, f"Deleted {spec['label']} “{name}”.")
        return redirect(spec["list_url"])

    context = {
        "page_title": f"Delete {spec['label']}?",
        "breadcrumbs": [(spec["label"].title() + "s", reverse(spec["list_url"])),
                        (str(name), None), ("Delete", None)],
        "object_name": name,
        "label": spec["label"],
        "collateral": _collateral(obj),
        "instead": spec.get("instead"),
        "back_url": request.META.get("HTTP_REFERER") or reverse(spec["list_url"]),
    }
    return render(request, "desk/confirm_delete.html", context)


@staff_required
def delete(request, kind, pk):
    spec = DELETABLE.get(kind)
    if spec and spec.get("superuser_only") and not request.user.is_superuser:
        return render(request, "desk/forbidden.html", status=403)
    return _view(request, kind, pk)
