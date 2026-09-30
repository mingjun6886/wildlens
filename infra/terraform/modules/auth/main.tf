data "aws_region" "current" {}

# --- User pool ---------------------------------------------------------------
#
# The directory of users. It issues the JWT that every API route requires, and
# the `email` claim inside that JWT is the only trustworthy source for the
# uploadedBy field on a record: the upload function reads it from a token Cognito
# signed, rather than from anything the client supplied.

resource "aws_cognito_user_pool" "users" {
  name = "${var.name_prefix}-users"

  # Email is the username. There is no separate handle to remember, and no
  # second identifier to keep in step with it.
  #
  # This cannot be changed later. Switching to alias_attributes, or adding a
  # username, requires destroying the pool and every account in it — so it is
  # worth being sure now rather than discovering it in Phase 7.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    # AWS defaults, kept as they are. Weakening them for the convenience of a
    # demo account would be the wrong trade in a repository meant to be read.
    minimum_length                   = 8
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # Note: prevent_user_existence_errors is a client setting, not a pool setting,
  # and is configured on the app client below. It is what stops the sign-in page
  # from distinguishing a wrong password on a registered address from a wrong
  # password on an unregistered one — a difference that turns the login form into
  # a way to test whether an email has an account here.

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Cognito's own email sender, capped at 50 messages a day. Enough for a
  # personal project and it needs no verified SES identity; production would use
  # SES and say so here.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # OFF, and this is a deliberate limitation rather than an oversight.
  #
  # The IAM user that administers this account has MFA. End users of the
  # application do not, because enabling it here forces a software-token
  # enrolment step into the middle of any demo and this pool holds no data worth
  # a second factor. A production pool would set OPTIONAL at minimum.
  mfa_configuration = "OFF"

  # Cognito threat protection (compromised-credential detection, adaptive auth)
  # is a paid feature and is left off for cost. Named here so that its absence
  # reads as a decision.
  # user_pool_add_ons { advanced_security_mode = "AUDIT" }

  tags = { Component = "auth" }
}

# --- Hosted UI domain --------------------------------------------------------
#
# Cognito hosts the sign-in page, so this project ships no login form, stores no
# passwords and runs no password-reset flow. That is the main reason to use
# Cognito at all rather than a JWT library.
#
# The prefix is unique across all of AWS, which is why it carries the same random
# suffix as the buckets. Without the suffix, `terraform apply` fails on a name
# somebody else in another account already took.
resource "aws_cognito_user_pool_domain" "hosted_ui" {
  domain       = "${var.name_prefix}-${var.suffix}"
  user_pool_id = aws_cognito_user_pool.users.id
}

# --- App client --------------------------------------------------------------

resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.name_prefix}-web"
  user_pool_id = aws_cognito_user_pool.users.id

  # No client secret. A single-page application cannot keep one: whatever ships
  # to the browser is readable by anyone who opens the developer tools, so a
  # secret there is a secret only in name. Generating one also breaks the
  # authorization-code flow from a browser in a way whose error message points at
  # the redirect rather than at the secret.
  generate_secret = false

  # Authorization code flow, not implicit.
  #
  # Implicit returns the tokens in the URL fragment, where they reach browser
  # history, the referrer header and any logging in between. The code flow
  # returns a one-time code instead and exchanges it for tokens in a POST. It is
  # also the only flow that supports PKCE, which is what makes a public client
  # without a secret safe.
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true

  # openid is required for an ID token at all. email is required because the
  # upload function reads the email claim to fill uploadedBy — drop it and that
  # field silently becomes unavailable.
  allowed_oauth_scopes         = ["openid", "email", "profile"]
  supported_identity_providers = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  # Enabled so that integration tests can obtain a token without driving a
  # browser. Without this the only way to a token is the Hosted UI, and the
  # post-deploy test the design calls for cannot be automated.
  #
  # ADMIN_USER_PASSWORD_AUTH, deliberately, and not USER_PASSWORD_AUTH. The admin
  # variant is only callable with IAM credentials that hold
  # cognito-idp:AdminInitiateAuth, so the attack surface it opens is bounded by
  # IAM rather than exposed to the internet. USER_PASSWORD_AUTH would accept a
  # username and password from anyone who knows the client id, which is public —
  # that would trade the whole benefit of the Hosted UI for test convenience.
  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # See the note on the pool above: this is what stops the sign-in page from
  # revealing whether an address is registered.
  prevent_user_existence_errors = "ENABLED"

  id_token_validity      = var.token_validity_hours
  access_token_validity  = var.token_validity_hours
  refresh_token_validity = var.refresh_validity_days

  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }

  # Refresh tokens are rotated on use, so a leaked one stops working as soon as
  # the legitimate client refreshes.
  enable_token_revocation = true
}
