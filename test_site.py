import importlib
import os
import unittest


# Prevent background refresh threads and network churn while importing the app in tests.
os.environ["DISABLE_BACKGROUND_THREADS"] = "1"

website_app = importlib.import_module("website_app")


class SiteRoutesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        website_app.app.config.update(TESTING=True)

    def setUp(self):
        self.client = website_app.app.test_client()

    def test_homepage_loads(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<title>Home</title>", response.data)
        self.assertIn(b"Welcome to Somewheria, LLC.", response.data)

    def test_missing_page_returns_404(self):
        response = self.client.get("/this-page-does-not-exist")

        self.assertEqual(response.status_code, 404)

    def test_manage_listings_redirects_when_logged_out(self):
        response = self.client.get("/manage-listings", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_admin_route_forbids_non_admin_user(self):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": "user-1",
                "email": "renter@example.com",
                "name": "Renter User",
                "role": "renter",
            }

        response = self.client.get("/admin/users")

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Front Door Locked", response.data)

    def test_admin_status_redirects_when_logged_out(self):
        response = self.client.get("/admin/status", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_admin_status_forbids_renter(self):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": "user-2",
                "email": "renter@example.com",
                "name": "Renter User",
                "role": "renter",
            }

        response = self.client.get("/admin/status")

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Front Door Locked", response.data)

    def test_admin_status_loads_for_admin(self):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": "admin-1",
                "email": "admin@example.com",
                "name": "Admin User",
                "role": "high_admin",
            }

        response = self.client.get("/admin/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"System Status", response.data)
        self.assertIn(b"Website Sections", response.data)
        self.assertIn(b"Public Pages", response.data)
        self.assertIn(b"Admin Tools", response.data)

    def test_google_login_shows_error_screen_when_oauth_is_not_configured(self):
        services = website_app.app.extensions["somewheria_services"]
        original_client_id = services.config.google_client_id
        original_client_secret = services.config.google_client_secret

        try:
            services.config.google_client_id = ""
            services.config.google_client_secret = ""

            response = self.client.get("/google/login")

            self.assertEqual(response.status_code, 503)
            self.assertIn(b"Google Sign-In Isn&#39;t Ready Yet", response.data)
            self.assertIn(b"Google OAuth is not configured on this server right now", response.data)
        finally:
            services.config.google_client_id = original_client_id
            services.config.google_client_secret = original_client_secret


if __name__ == "__main__":
    unittest.main()
