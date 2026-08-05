# AWS setup for real mode

QuizDeck runs fully on mocks today (`AUTH_MODE=fake`, `EMAIL_MODE=outbox`,
`LLM_MODE=fake`, DynamoDB Local). This doc is the step-by-step for standing
up the real AWS side and flipping each mode switch in `app/core/config.py`.

> **Honesty note:** the commands below are standard AWS CLI (`aws dynamodb`,
> `aws cognito-idp`, `aws sesv2`) and were checked against the current CLI's
> documented arguments, but nothing here has been run against a live AWS
> account — there is none in this environment. Verify region, account ID,
> and any console screen this doc describes before relying on it, and prefer
> the CLI commands over console click-paths where both are given, since the
> CLI surface is far more stable than console UI text.

Do all of this in a region you're comfortable naming explicitly — every
command below takes `--region`, and `AWS_REGION` in the backend `.env` must
match wherever you actually created these resources.

## 1. DynamoDB

One table, `quizdeck-main`, PK + SK only (see `app/repositories/keys.py` —
no GSIs, ever).

**Create it** — either the AWS CLI directly:

```bash
aws dynamodb create-table \
  --table-name quizdeck-main \
  --attribute-definitions \
      AttributeName=PK,AttributeType=S \
      AttributeName=SK,AttributeType=S \
  --key-schema \
      AttributeName=PK,KeyType=HASH \
      AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region <region>
```

or point the repo's own script at real AWS instead of DynamoDB Local, by
unsetting the local-only vars so boto3 falls back to your normal credential
chain (`aws configure`, an SSO profile, or an instance/task role):

```bash
# In .env: comment out DYNAMO_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
poetry run python scripts/create_table.py   # idempotent -- see its ClientError handling
```

**Minimal IAM policy for the app** (attach to whatever role/user the
backend runs as — an ECS task role, an EC2 instance profile, or a CI/local
IAM user for testing):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "QuizdeckTableAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:BatchWriteItem",
        "dynamodb:TransactWriteItems",
        "dynamodb:DeleteItem"
      ],
      "Resource": "arn:aws:dynamodb:<region>:<account-id>:table/quizdeck-main"
    }
  ]
}
```

No `Scan`, no GSI actions — the app never needs them (CLAUDE.md rule 1).

## 2. Cognito (Google sign-in)

### 2.1 Create the user pool

```bash
aws cognito-idp create-user-pool \
  --pool-name quizdeck \
  --auto-verified-attributes email \
  --username-attributes email \
  --region <region>
```

Note the returned `Id` (the user pool ID) — everything below refers to it as
`<pool-id>`.

### 2.2 Register a Google OAuth client

In Google Cloud Console, under **APIs & Services → Credentials**, create an
**OAuth 2.0 Client ID** of type "Web application". You'll need its client
ID and client secret in the next step, and you'll need to come back here
after step 2.4 to add the authorized redirect URI once you know your
Cognito domain — it's a chicken-and-egg dependency, not an ordering mistake.

The authorized redirect URI is always:

```
https://<your-cognito-domain>.auth.<region>.amazoncognito.com/oauth2/idpresponse
```

### 2.3 Add Google as a federated identity provider

```bash
aws cognito-idp create-identity-provider \
  --user-pool-id <pool-id> \
  --provider-name Google \
  --provider-type Google \
  --provider-details client_id=<google-client-id>,client_secret=<google-client-secret>,authorize_scopes="openid email profile" \
  --attribute-mapping email=email,name=name,username=sub \
  --region <region>
```

This is the email/name attribute mapping the task calls for: Google's
`email` and `name` claims land on the Cognito user as `email` and `name`,
which is exactly what `app/auth/cognito.py::CognitoJwtVerifier.verify`
reads out of the ID token.

### 2.4 Create the hosted UI domain

```bash
aws cognito-idp create-user-pool-domain \
  --domain <your-chosen-domain-prefix> \
  --user-pool-id <pool-id> \
  --region <region>
```

This is what makes `https://<your-chosen-domain-prefix>.auth.<region>.amazoncognito.com`
resolve. Go back to step 2.2 now and add
`https://<your-chosen-domain-prefix>.auth.<region>.amazoncognito.com/oauth2/idpresponse`
as the Google client's authorized redirect URI.

### 2.5 Create the app client

No secret (the frontend is a public SPA client), authorization-code grant
with PKCE. Cognito adds PKCE support automatically for a public
(no-secret) client using the code grant — `src/lib/auth/cognito.ts` already
sends `code_challenge`/`code_challenge_method=S256` on `/oauth2/authorize`,
there's nothing extra to enable on the Cognito side for that part.

```bash
aws cognito-idp create-user-pool-client \
  --user-pool-id <pool-id> \
  --client-name quizdeck-web \
  --no-generate-secret \
  --allowed-o-auth-flows code \
  --allowed-o-auth-flows-user-pool-client \
  --allowed-o-auth-scopes openid email profile \
  --supported-identity-providers Google \
  --callback-urls "http://localhost:3000/auth/callback" "https://<your-deployed-frontend-origin>/auth/callback" \
  --logout-urls "http://localhost:3000/login" "https://<your-deployed-frontend-origin>/login" \
  --prevent-user-existence-errors ENABLED \
  --region <region>
```

Replace `<your-deployed-frontend-origin>` with a real value once you have
one; until then the localhost callback/logout URL alone is enough for local
testing against real Cognito. Note the returned `ClientId`.

The sign-out URL above is `/login` per the task spec — `CognitoAuthProvider.signOut`
in `src/lib/auth/cognito.ts` builds `logout_uri` from
`NEXT_PUBLIC_COGNITO_REDIRECT_URI`'s origin plus `/login`, so it must be one
of the app client's registered logout URLs or Cognito's `/logout` endpoint
will reject the redirect.

### Env vars this section produces

| Var | Repo | Value |
| --- | --- | --- |
| `COGNITO_USER_POOL_ID` | backend | `<pool-id>` |
| `COGNITO_CLIENT_ID` | backend | `<ClientId>` from 2.5 |
| `COGNITO_REGION` | backend | `<region>` |
| `NEXT_PUBLIC_COGNITO_DOMAIN` | frontend | `https://<your-chosen-domain-prefix>.auth.<region>.amazoncognito.com` |
| `NEXT_PUBLIC_COGNITO_CLIENT_ID` | frontend | same `<ClientId>` |
| `NEXT_PUBLIC_COGNITO_REDIRECT_URI` | frontend | `http://localhost:3000/auth/callback` (or the deployed origin's `/auth/callback`) |

## 3. SES

### 3.1 Verify a sender identity

Simplest path — verify a single "from" address:

```bash
aws sesv2 create-email-identity --email-identity noreply@yourdomain.com --region <region>
```

This sends a verification email to that address; click the link in it.
(Verifying the whole domain via DKIM instead lets you send from *any*
address `@yourdomain.com` — see `--dkim-signing-attributes` on the same
command — but it needs DNS access to add the CNAME records AWS returns, so
start with the single address unless you already need the domain-wide
version.)

Check status any time with:

```bash
aws sesv2 get-email-identity --email-identity noreply@yourdomain.com --region <region>
```

### 3.2 Sandbox mode

New SES accounts start in the **sandbox**: you can only send *to* addresses
or domains that are themselves verified, and there's a low daily send quota.
Since QuizDeck invites arbitrary student email addresses, you need
**production access** before this is usable for real students — request it
from the SES console ("Account dashboard" → "Request production access") or
via `aws sesv2 put-account-details`. Until that's granted, verify each test
recipient the same way as step 3.1 to be able to send to them at all.

### 3.3 Minimal IAM policy for the app

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "QuizdeckSesSend",
      "Effect": "Allow",
      "Action": "ses:SendEmail",
      "Resource": "arn:aws:ses:<region>:<account-id>:identity/yourdomain.com"
    }
  ]
}
```

(`app/services/email/ses.py` calls the `sesv2` client's `send_email`, which
is still authorized under the `ses:SendEmail` IAM action — SESv1 and SESv2
share the same action namespace.) Scope `Resource` to the identity ARN you
verified in 3.1; use `"Resource": "*"` only if you verified multiple
identities and don't want to enumerate them all.

### Env vars this section produces

| Var | Repo | Value |
| --- | --- | --- |
| `SES_FROM_ADDRESS` | backend | `noreply@yourdomain.com` (must match a verified identity) |

## 4. Flip the switches

Once the above exists, this is the entire diff between dev and real mode.

**Backend `.env`:**

```dotenv
APP_ENV=prod
FRONTEND_ORIGIN=https://<your-deployed-frontend-origin>

AUTH_MODE=cognito
EMAIL_MODE=ses
LLM_MODE=openai

TABLE_NAME=quizdeck-main
AWS_REGION=<region>
# DYNAMO_ENDPOINT_URL left unset -> real DynamoDB via the normal AWS credential chain
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY left unset for the same reason
#   (use an instance/task role in AWS, or `aws configure` locally against real AWS)

COGNITO_USER_POOL_ID=<pool-id>
COGNITO_CLIENT_ID=<app-client-id>
COGNITO_REGION=<region>

SES_FROM_ADDRESS=noreply@yourdomain.com

OPENAI_API_KEY=<your-openai-key>
OPENAI_MODEL=gpt-4o-mini

SUBMIT_GRACE_SECONDS=30
```

**Frontend `.env.local`:**

```dotenv
NEXT_PUBLIC_API_URL=https://<your-deployed-backend-origin>
NEXT_PUBLIC_AUTH_MODE=cognito
NEXT_PUBLIC_COGNITO_DOMAIN=https://<your-chosen-domain-prefix>.auth.<region>.amazoncognito.com
NEXT_PUBLIC_COGNITO_CLIENT_ID=<app-client-id>
NEXT_PUBLIC_COGNITO_REDIRECT_URI=https://<your-deployed-frontend-origin>/auth/callback
```

**The built-in safety net:** `Settings` (`app/core/config.py`) refuses to
construct at all when `APP_ENV=prod` and any of `AUTH_MODE=fake`,
`EMAIL_MODE=outbox`, `LLM_MODE=fake`, or a set `DYNAMO_ENDPOINT_URL` are
still present — see `test_prod_refuses_fake_auth` /
`test_prod_refuses_local_dynamo_endpoint` in `tests/unit/test_config.py`.
The backend simply won't start rather than silently serve a "prod" that's
still half-wired to mocks.
