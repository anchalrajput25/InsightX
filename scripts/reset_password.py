"""Reset a forgotten InsightX password (run on the server).

    python scripts/reset_password.py someone@example.com

Asks for the new password twice and signs that account out everywhere.
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.auth.service import AuthError, set_password  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    email = sys.argv[1]
    new = getpass.getpass("New password: ")
    if new != getpass.getpass("Repeat it: "):
        sys.exit("The two passwords are different. Nothing was changed.")
    try:
        user = set_password(email, new)
    except AuthError as error:
        sys.exit(error.message)
    print(f"Password changed for {user['email']}. They are signed out everywhere.")


if __name__ == "__main__":
    main()
