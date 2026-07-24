from django.test import SimpleTestCase


class HomeViewTest(SimpleTestCase):
    def test_home_renders(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Case Law Extraction")
