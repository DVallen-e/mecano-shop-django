# betatestmecanoshop

## Social sign-in setup

Google sign-in continues to use `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
For Apple sign-in, enable **Sign in with Apple** for a Services ID in Apple
Developer, then set `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, and
`APPLE_PRIVATE_KEY` in the environment. The private key can be provided with
escaped `\n` line breaks. Configure Apple's return URL as
`https://<your-domain>/accounts/apple/login/callback/` (and use the matching
local HTTPS URL during development).

Install the updated Python requirements and run `python manage.py migrate` to
create the allauth and Sites tables.

Sign-in buttons are available at `/login/`. Product pages remain public, but
adding an item to the cart requires an authenticated account.
