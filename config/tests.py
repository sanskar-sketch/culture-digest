from unittest import mock

from django.test import TestCase
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_it_reports_ok_when_the_database_answers(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "ok")
        self.assertIsInstance(body["database_ms"], int)

    def test_it_reports_503_when_the_database_is_unreachable(self):
        # The app process being up is not the same as the app working - this
        # is the case that would otherwise look healthy from outside.
        with mock.patch("config.health.connection") as conn:
            conn.cursor.side_effect = RuntimeError("could not connect")
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unhealthy")
        self.assertEqual(response.json()["database"], "error")

    def test_it_never_leaks_why_it_failed(self):
        # A public URL must not echo exception text - it can carry a host
        # name or a connection string.
        with mock.patch("config.health.connection") as conn:
            conn.cursor.side_effect = RuntimeError(
                "connection to postgres://user:secret@db.example.com failed")
            body = self.client.get(reverse("healthz")).content.decode()

        self.assertNotIn("secret", body)
        self.assertNotIn("db.example.com", body)

    def test_it_is_not_cached(self):
        response = self.client.get(reverse("healthz"))
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))

    def test_it_needs_no_login(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
