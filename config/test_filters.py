"""The changelist filters: the custom ones filter correctly, and the panel
renders as chips."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from opportunities.models import Opportunity
from readers.models import Reader
from recommendations.models import NewsletterIssue, Recommendation

# The two taste dials are required on an opportunity.
DIALS = {"mainstream_to_unusual": 3, "intimate_to_large_scale": 3}

plain_static = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


@plain_static
class FilterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(
            get_user_model().objects.create_superuser("f", "f@example.com", "pw"))
        self.ada = Reader.objects.create(email="ada@example.com", name="Ada",
                                         interest_categories=["music", "film"])
        self.bob = Reader.objects.create(email="bob@example.com", name="Bob",
                                         interest_categories=[])
        self.issue = NewsletterIssue.objects.create(reader=self.ada, sent_at=timezone.now())

    def emails(self, response):
        return [r.email for r in response.context["cl"].result_list]

    def test_readers_can_be_filtered_by_what_they_follow(self):
        url = reverse("admin:readers_reader_changelist")
        self.assertEqual(self.emails(self.client.get(url, {"follows": "music"})),
                         ["ada@example.com"])
        self.assertEqual(self.emails(self.client.get(url, {"follows": "none"})),
                         ["bob@example.com"])

    def test_readers_can_be_filtered_by_whether_they_were_ever_sent_anything(self):
        url = reverse("admin:readers_reader_changelist")
        self.assertEqual(self.emails(self.client.get(url, {"sent": "never"})),
                         ["bob@example.com"])
        self.assertEqual(self.emails(self.client.get(url, {"sent": "sent"})),
                         ["ada@example.com"])
        self.assertEqual(self.emails(self.client.get(url, {"sent": "replied"})), [])
        opp = Opportunity.objects.create(title="A show", category="theatre", **DIALS)
        Recommendation.objects.create(issue=self.issue, opportunity=opp, rationale="x",
                                      feedback=Recommendation.Feedback.BOOKED)
        self.assertEqual(self.emails(self.client.get(url, {"sent": "replied"})),
                         ["ada@example.com"])

    def test_opportunities_can_be_filtered_by_whether_they_are_still_on(self):
        today = timezone.localdate()
        on = Opportunity.objects.create(title="Still on", category="music",
                                        end_date=today + timedelta(days=3), **DIALS)
        over = Opportunity.objects.create(title="Over", category="music",
                                          end_date=today - timedelta(days=1), **DIALS)
        undated = Opportunity.objects.create(title="Undated", category="music", **DIALS)
        url = reverse("admin:opportunities_opportunity_changelist")

        def titles(params):
            return {o.title for o in self.client.get(url, params).context["cl"].result_list
                    if o.pk in {on.pk, over.pk, undated.pk}}

        self.assertEqual(titles({"live": "live"}), {"Still on", "Undated"})
        self.assertEqual(titles({"live": "ended"}), {"Over"})
        self.assertEqual(titles({"live": "undated"}), {"Undated"})

    def test_issues_can_be_filtered_by_delivery(self):
        NewsletterIssue.objects.create(reader=self.bob)  # never sent
        url = reverse("admin:recommendations_newsletterissue_changelist")
        sent = self.client.get(url, {"delivery": "sent"}).context["cl"].result_list
        unsent = self.client.get(url, {"delivery": "unsent"}).context["cl"].result_list
        self.assertEqual([i.reader.email for i in sent], ["ada@example.com"])
        self.assertEqual([i.reader.email for i in unsent], ["bob@example.com"])

    def test_the_panel_renders_as_chips_with_the_picked_value_marked(self):
        url = reverse("admin:opportunities_opportunity_changelist")
        response = self.client.get(url, {"status__exact": "draft"})
        self.assertContains(response, 'class="e-chips"')
        self.assertContains(response, 'details class="e-filter"')
        self.assertContains(response, "Clear all filters")
        # The picked chip is marked, and the "All" chip carries data-all so
        # the script can tell a real selection from the default.
        self.assertContains(response, '<li class="selected">')
        self.assertContains(response, 'data-all="1"')
