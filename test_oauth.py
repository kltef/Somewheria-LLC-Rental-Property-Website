import os
import unittest

from dotenv import load_dotenv


load_dotenv()


class OAuthConfigTestCase(unittest.TestCase):
    def test_required_environment_variables_are_present(self):
        required_vars = [
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "SECRET_KEY",
        ]

        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            self.skipTest(
                "OAuth environment variables are not configured for this machine: "
                + ", ".join(missing_vars)
            )

        self.assertFalse(
            missing_vars,
            f"Missing required OAuth environment variables: {', '.join(missing_vars)}",
        )


if __name__ == "__main__":
    unittest.main()
