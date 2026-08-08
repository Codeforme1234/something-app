# AWS setup

Auth and the LLM still have mock implementations (`AUTH_MODE=fake`,
`LLM_MODE=fake`). **Storage does not.** DynamoDB Local, MinIO and the
local-filesystem object store have been removed, so there is no configuration
in which the app reads or writes anything but real AWS. This doc is how those
resources are provisioned.

> **Honesty note:** sections 1 and 4 (DynamoDB and S3) have been run against
> the live account `348517220262` in `ap-south-1` — all four resources exist
> and the backend has done real round-trips through `app/repositories/` and
> `app/services/storage/` against them. Sections 2–3 (Cognito, SES) are still
> standard AWS CLI checked against the current CLI's documented arguments but
> **not** executed end to end. Verify region, account ID, and any console
> screen those sections describe before relying on them, and prefer the CLI
> commands over console click-paths where both are given, since the CLI surface
> is far more stable than console UI text.

## 0. Two environments, four resources

| | `.env.dev` (default) | `.env.prod` |
| --- | --- | --- |
| DynamoDB table | `quizdeck-dev` | `quizdeck-prod` |
| S3 bucket | `quizdeck-media-dev` | `quizdeck-media` |
| Cognito pool | *shared* `ap-south-1_FeijV3kgy` | *same* |
| SES identity | *shared* `quizdeck.in` | *same* |
| credentials | `quizdeck` profile in `~/.aws` | ECS task / EC2 instance role |

Cognito and SES are shared deliberately: one user pool and one verified sending
domain, so a teacher's login and the "from" address are the same everywhere.
Only the data stores are split, because those are what you do not want a dev
run to corrupt.

`app/core/config.py` picks the file from `QUIZDECK_ENV_FILE`, defaulting to
`.env.dev` — the safe one is what you get when you forget to choose. Two guards
back that up, both tested in `tests/unit/test_config.py`:

- `APP_ENV=dev` is **refused** with a `*-prod` resource name.
- `APP_ENV=prod` is **refused** with any credential profile set, because a
  container has no `~/.aws`. `.env.prod` therefore cannot boot on a laptop.

The test suite uses neither file: `tests/conftest.py` points
`QUIZDECK_ENV_FILE` at a nonexistent path and wraps the session in `moto`, so
no test can reach an AWS account.

Everything below is in `ap-south-1`, which is where all four resources, the
Cognito pool and the SES identity live. `AWS_REGION` must match.

## 1. DynamoDB

Two tables, same shape: PK + SK only (see `app/repositories/keys.py` — no GSIs,
ever). Substitute `quizdeck-dev` / `quizdeck-prod` for `<table>`:

```bash
aws dynamodb create-table \
  --table-name <table> \
  --attribute-definitions \
      AttributeName=PK,AttributeType=S \
      AttributeName=SK,AttributeType=S \
  --key-schema \
      AttributeName=PK,KeyType=HASH \
      AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1
```

or let the repo's own script do it for whichever env file is active — it reads
`TABLE_NAME` and is idempotent (see its `ClientError` handling):

```bash
make table                                  # .env.dev  -> quizdeck-dev
QUIZDECK_ENV_FILE=.env.prod make table      # .env.prod -> quizdeck-prod
```

> **The table that exists today (verified 2026-08-07):**
> Both tables exist and are ACTIVE — PK + SK, `PAY_PER_REQUEST`, no GSIs, same
> account as Cognito and SES:
> `arn:aws:dynamodb:ap-south-1:348517220262:table/quizdeck-dev` and
> `.../table/quizdeck-prod`. `scripts/create_table.py` reports "already exists"
> against each. There is a third, unrelated `quizdeck` table in the same region
> that the app does not touch.

### 1.1 Credentials for local dev

This machine's *default* AWS profile points at a different account, so an
unqualified boto3 call would sign for the wrong one. Name a profile instead of
pasting keys into an env file — one copy of the secret, in the place the AWS
CLI already reads:

```bash
aws configure set aws_access_key_id     <key-id>   --profile quizdeck
aws configure set aws_secret_access_key <secret>   --profile quizdeck
aws configure set region                ap-south-1 --profile quizdeck
```

Then in `.env.dev`: `DYNAMO_PROFILE=quizdeck` (and `S3_PROFILE`, and
`SES_PROFILE` if you want dev to send mail). All three are **refused** when
`APP_ENV=prod`: a container has no `~/.aws`, and credentials there must come
from the task or instance role.

The settings are deliberately **not** named `AWS_PROFILE` — botocore reads that
name natively, so anything trying to blank it out (as `tests/conftest.py` does)
hits `ProfileNotFound("")` inside boto3 before `Settings` is ever consulted.
For the same reason all three are read as `<value> or None`, so an explicitly
empty profile means "the normal credential chain" rather than a lookup for a
profile literally named `""`.

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
      "Resource": "arn:aws:dynamodb:ap-south-1:348517220262:table/quizdeck-prod"
    }
    // The dev deployment gets the same statement against
    // .../table/quizdeck-dev. Grant one table per role, never both: a prod
    // role that can also write the dev table has no reason to, and a dev role
    // that can write prod is the whole failure this split exists to prevent.
  ]
}
```

No `Scan`, no GSI actions — the app never needs them (CLAUDE.md rule 1).

> **Not what is attached today.** The `personla` IAM user whose keys the
> `quizdeck` profile holds has **`AdministratorAccess`** (plus Amplify and
> Elastic Beanstalk admin). Those keys can therefore delete this table, read
> every other service in account `348517220262`, and create billable
> resources — a leak is an account-level incident, not a table-level one.
> Creating a dedicated `quizdeck-app` user with only the policy above, and
> keeping `personla` as a human console login, is the fix.

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

> **This is the single most common Google-sign-in failure.** If it is missing
> from the Google client's *Authorized redirect URIs*, Cognito redirects to
> Google correctly and **Google** rejects it with `redirect_uri_mismatch` — so
> the error surfaces on accounts.google.com, not in Cognito, and Cognito's own
> config looks perfectly fine. Note it is Cognito's domain, not your app's, no
> trailing slash, and nothing goes under "Authorized JavaScript origins"
> (this is a server-side redirect, not a browser call).
>
> To tell the two sides apart in one command:
> ```bash
> curl -s -o /dev/null -w "%{redirect_url}\n" \
>   "https://<domain>/oauth2/authorize?response_type=code&client_id=<id>&redirect_uri=<app-callback>&scope=openid+email&identity_provider=Google"
> ```
> A 302 to `accounts.google.com` means Cognito is wired correctly and any
> remaining failure is on the Google client.

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
| `NEXT_PUBLIC_COGNITO_SCOPE` | frontend | optional; must be a **subset** of the app client's allowed scopes (default `openid email`) |
| `NEXT_PUBLIC_COGNITO_IDP` | frontend | optional; set to `Google` only once the pool actually has that IdP — it deep-links past the hosted login |

> **The pool that exists today (created 2026-08-07):** `ap-south-1_FeijV3kgy`
> ("User pool - 50unfd", account 348517220262, ap-south-1), app client
> `5svn3uemak6oomm4uf8ufmtqcb` (public, no secret), hosted domain
> `https://ap-south-1feijv3kgy.auth.ap-south-1.amazoncognito.com` (from the
> discovery document). No federated IdP yet — sign-in is Cognito's own
> email/password login, so the steps in 2.2–2.3 above still apply when Google
> gets added. Both repos' env files already carry these values; the remaining
> manual step is registering the callback/sign-out URLs on the app client
> (localhost testing fails with `redirect_mismatch` until then).

## 3. SES

> **What this project actually uses.** A **domain** identity is already
> verified:
>
> ```
> arn:aws:ses:ap-south-1:348517220262:identity/quizdeck.in
> ```
>
> so 3.1 below is done — but note the account, `348517220262`, is not the
> same account as anything else on this machine's AWS profiles, and the
> region, `ap-south-1`, matches Cognito rather than the old `us-east-1`
> default. Both facts are why `SES_REGION` and `SES_PROFILE` exist; see
> "Env vars this section produces". The sending address is
> `admin@quizdeck.in`, which needs no separate verification because the
> whole domain is verified.

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
| `SES_FROM_ADDRESS` | backend | `admin@quizdeck.in` (must match, or sit inside, a verified identity) |
| `SES_REGION` | backend | `ap-south-1` — the identity's region. Unset falls back to `AWS_REGION` |
| `SES_PROFILE` | backend | local dev only: a named profile with credentials in account `348517220262`. Unset in a deployment, and refused outright when `APP_ENV=prod` |
| `SUPPORT_EMAIL` | backend | inbox for in-app support requests. The default `support@quizdeck.local` hard-bounces under real SES |

`SesEmailSender` builds `boto3.Session(profile_name=SES_PROFILE)` —
`profile_name=None` is just boto3's normal credential chain, which is what a
deployed task role uses. The profile knob exists because the machine's
*default* profile points somewhere else entirely, and a silent send through
the wrong account is worse than a startup failure.

To add that profile locally, in `~/.aws/credentials`:

```ini
[quizdeck]
aws_access_key_id = <key for account 348517220262>
aws_secret_access_key = <secret>
```

then uncomment `SES_PROFILE=quizdeck` in `.env`. Confirm it points where you
think it does before sending anything:

```bash
aws sts get-caller-identity --profile quizdeck          # expect Account 348517220262
aws sesv2 get-email-identity --email-identity quizdeck.in \
    --profile quizdeck --region ap-south-1               # expect VerifiedForSendingStatus: true
aws sesv2 get-account --profile quizdeck --region ap-south-1 \
    --query 'ProductionAccessEnabled'                    # false == still sandboxed
```

## 4. S3 (question images)

Question images and, later, transient PDF uploads live in one bucket behind the
`ObjectStore` Protocol (`app/services/storage/`). Only the object **key** is ever
stored in DynamoDB — never a URL — so everything below is a config change with no
data migration.

### 4.1 Create the bucket

```bash
aws s3api create-bucket --bucket <your-bucket> --region <region> \
  --create-bucket-configuration LocationConstraint=<region>
```

(Omit `--create-bucket-configuration` entirely if `<region>` is `us-east-1` — the
API rejects it there.)

Key layout, built only by `app/services/storage/keys.py`:

```
tests/<test-ulid>/q/<image-ulid>.png|jpg|webp        question images
kb/<sha256(sub)[:32]>/<ulid>.pdf|png|jpg|webp|txt|md source documents
```

Question images are scoped by **test**, because they are uploaded into one that
already exists. Source documents are scoped by **teacher**, because the upload
happens before any test does. The teacher segment is a *hash* of the Cognito
sub, not the sub: that keeps the key path-safe whatever a sub contains, keeps
identities out of S3 access logs, and still lets ownership be checked with a
string compare and zero lookups (CLAUDE.md rule 3).

Both shapes are validated by allowlist regex, so traversal, absolute paths,
query strings and double extensions are impossible by construction rather than
by enumerating bad characters.

> **The buckets that exist today (verified 2026-08-07):** `quizdeck-media`
> (prod) and `quizdeck-media-dev`, both in `ap-south-1`, account 348517220262.
> Identical configuration: all four public-access blocks on, SSE-S3 default
> encryption with a bucket key, and the lifecycle rules in 4.4. Verified end to
> end against `quizdeck-media`: upload → object in S3 with the pinned content
> type and immutable cache → byte-identical read back through the proxy route →
> anonymous direct GET returns 403 → deleting the test sweeps the image.

### 4.2 Decide how images reach a student's browser

This is the one real decision, and it is a single env var. **Option A is what is
configured today** (`IMAGE_PUBLIC_BASE_URL` unset).

Note this decision covers question images only. Knowledge-base documents are
always proxied through the token-checked `GET /api/v1/knowledge-base/{key}`
route and are never addressed via `IMAGE_PUBLIC_BASE_URL`, because a CDN origin
carries no bearer token and would bypass the `kb_belongs_to_teacher` ownership
check — see the `kb/` carve-out in `app/services/storage/urls.py`.

**Option A — proxied through the API (default; leave `IMAGE_PUBLIC_BASE_URL` unset).**
The bucket stays **completely private**: no bucket policy, no public access, no
CloudFront. `GET /api/v1/images/{key}` reads from S3 and streams the bytes back.

- Simplest and secure by default. Nothing about the bucket is public.
- Costs API bandwidth and CPU for every image a student loads.
- Start here. Switching later needs no code change and no data migration.

**Option B — served directly from CloudFront (set `IMAGE_PUBLIC_BASE_URL`).**
`public_url()` returns `<base-url>/<key>` and the browser fetches it without
touching the API.

- Keeps student image traffic off your servers, and the objects are cached at the
  edge (`Cache-Control: public, max-age=31536000, immutable` is already set on
  every upload, because keys are immutable).
- Requires the `tests/` prefix to be readable by anyone holding the key. Keys are
  26-character ULIDs and the bucket must never grant `s3:ListBucket` publicly, so
  they are not enumerable — but a leaked URL is readable forever, with no
  revocation short of deleting the object.
- This is the same posture the platform already takes with
  `app.core.ids.new_link_token`, a never-expiring token that alone authorizes a
  student's whole attempt. It is not a new weakening, but it *is* a choice.

If you pick B, put CloudFront in front with Origin Access Control rather than
making the bucket itself public, and set `IMAGE_PUBLIC_BASE_URL` to the
distribution domain. Keep `BlockPublicAcls` and `IgnorePublicAcls` on either way.

> **No bucket CORS is needed in either option.** Nothing in the browser `fetch`es
> an image or uploads directly to S3 — uploads go through the API as multipart,
> and images are rendered with a plain `<img src>`, which is not subject to CORS.
> This is a real advantage of routing uploads through the API rather than using
> presigned POSTs.

### 4.3 IAM policy for the app

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "QuizdeckObjectStore",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::quizdeck-media/*"
    }
  ]
}
```

`s3:GetObject` is required because the API reads the bytes itself — both to serve
them in option A and to read an uploaded PDF in the extraction pipeline. **No
`s3:ListBucket`**: nothing enumerates the bucket, and withholding it means a
future orphan-reaper has to arrive as a visible, reviewable IAM change.

Same caveat as section 1: this is the policy the app *should* run under, not
what is attached today. The `personla` user backing the `quizdeck` profile has
`AdministratorAccess`, so its keys can empty or delete this bucket outright.

### 4.4 Lifecycle: expire source documents

Nothing in the code deletes a `kb/` object. `delete_many` is called in exactly
one place — `test_service.delete_test`, for that test's question images — so
without a lifecycle rule every document a teacher ever uploads is kept forever.
The extracted text is already persisted in DynamoDB, so expiring the file only
costs the ability to re-download the original.

The rules applied to **both** buckets — substitute `quizdeck-media-dev`:

```bash
aws s3api put-bucket-lifecycle-configuration --bucket quizdeck-media \
  --lifecycle-configuration '{"Rules":[
    {"ID":"expire-kb-source-documents","Status":"Enabled",
     "Filter":{"Prefix":"kb/"},"Expiration":{"Days":30}},
    {"ID":"abort-incomplete-multipart-uploads","Status":"Enabled",
     "Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}
  ]}'
```

> **Scope the expiry to `kb/` and nothing else.** An age-based rule covering
> `tests/` would delete **live** images out of older tests — S3 has no
> expression that distinguishes a referenced object from an unreferenced one.
> Earlier revisions of this doc described a `pdf/` prefix with a 1-day expiry;
> no such prefix has ever existed in `app/services/storage/keys.py`, so that
> rule matched nothing, and "fixing" it by repointing it at `kb/` would have
> silently destroyed teachers' uploads after a day.

**Known gap — orphaned question images.** An image uploaded to a test that is
then abandoned without saving questions is never referenced and never swept:
`delete_test` only collects `image_key`s from *stored questions*. There is no
lifecycle rule that can catch this, since an orphan is indistinguishable by age
from a live image. Cleaning it up needs a reaper that lists `tests/<id>/q/` and
diffs against the question rows — deliberately not written yet, because it
would need `s3:ListBucket`, which section 4.3 withholds on purpose.

### 4.5 Testing the storage code path

There is no MinIO profile and no local-filesystem store any more. The test
suite exercises the real `S3ObjectStore` against `moto`, which intercepts
boto3 in-process (`tests/conftest.py`), so `make test` needs no bucket, no
container and no network.

To exercise it against real S3, run the app on `.env.dev` and upload an image
in the editor: that writes to `quizdeck-media-dev`, which is what the dev
bucket is for.

### Env vars this section produces

| Var | Repo | Value |
| --- | --- | --- |
| `S3_BUCKET` | backend | `quizdeck-media-dev` in `.env.dev`; `quizdeck-media` in `.env.prod` |
| `S3_PROFILE` | backend | local dev only: `quizdeck`. Refused when `APP_ENV=prod` |
| `IMAGE_PUBLIC_BASE_URL` | backend | *unset* for option A; the CloudFront domain for option B |
| `API_PUBLIC_ORIGIN` | backend | `https://<your-deployed-backend-origin>` — used to build proxied image URLs, so it must be what the browser can actually reach |

## 5. Going to production

`.env.prod` is already written; this is what it says and why. Run it with
`make prod`, or `QUIZDECK_ENV_FILE=.env.prod` on any command.

```dotenv
APP_ENV=prod
FRONTEND_ORIGIN=https://<your-deployed-frontend-origin>

AUTH_MODE=cognito                # fake is refused
EMAIL_MODE=ses                   # outbox is refused
LLM_MODE=openai                  # fake is refused

TABLE_NAME=quizdeck-prod
AWS_REGION=ap-south-1
S3_BUCKET=quizdeck-media
API_PUBLIC_ORIGIN=https://<your-deployed-backend-origin>
# DYNAMO_PROFILE / S3_PROFILE / SES_PROFILE all absent -- refused in prod, so
#   credentials come from the ECS task role or EC2 instance profile.
# IMAGE_PUBLIC_BASE_URL absent -> images proxied through the API (option A).

# Identical to .env.dev: one pool, one verified domain, both environments.
COGNITO_USER_POOL_ID=<pool-id>
COGNITO_CLIENT_ID=<app-client-id>
COGNITO_REGION=ap-south-1
SES_FROM_ADDRESS=admin@quizdeck.in
SES_REGION=ap-south-1
SUPPORT_EMAIL=admin@quizdeck.in

# OPENAI_API_KEY absent on purpose -- injected as a task-definition secret, so
#   a copy of this file is not a copy of the key. LLM_MODE=openai refuses to
#   start without it, which is the intended alarm.
# Both models are REQUIRED under LLM_MODE=openai and have no code default, so
#   the model being billed is always the one named here.
OPENAI_MODEL=gpt-4o-mini
OPENAI_EXTRACTION_MODEL=gpt-5.6-terra
OPENAI_TIMEOUT_SECONDS=60
OPENAI_EXTRACTION_TIMEOUT_SECONDS=600
OPENAI_MAX_RETRIES=1

STARTING_CREDITS=20
STARTING_AI_CREDITS=20
AI_CREDIT_COST_PROMPT=1
AI_CREDIT_COST_PDF=2

MAX_IMAGE_BYTES=2000000
MAX_UPLOAD_BYTES=20000000
MAX_PDF_BYTES=20000000
MAX_PDF_PAGES=60

SUBMIT_GRACE_SECONDS=30
```

**Nothing operational is a Python constant any more.** Model names, request
timeouts, retry count, credit pricing and upload limits all come from the env
file, so repricing a credit or switching model is a config change rather than a
deploy. The two OpenAI model names deliberately have *no* default: a default is
a model compiled into the image, and an env file that omits one would quietly
bill something other than what it names.

Because the profiles are refused and the OpenAI key is absent, **`.env.prod`
will not boot on a laptop.** That is the design: the only place it starts is
somewhere with a task role and injected secrets.

**Frontend `.env.local`:**

```dotenv
NEXT_PUBLIC_API_URL=https://<your-deployed-backend-origin>
NEXT_PUBLIC_AUTH_MODE=cognito
NEXT_PUBLIC_COGNITO_DOMAIN=https://<your-chosen-domain-prefix>.auth.<region>.amazoncognito.com
NEXT_PUBLIC_COGNITO_CLIENT_ID=<app-client-id>
NEXT_PUBLIC_COGNITO_REDIRECT_URI=https://<your-deployed-frontend-origin>/auth/callback
```

**The built-in safety net.** `Settings` (`app/core/config.py`) refuses to
construct at all, so the backend won't start rather than silently serve a "prod"
that is half-wired to mocks or a "dev" that is writing to production. All of
these are covered by tests in `tests/unit/test_config.py`:

| Refused | When | Why |
| --- | --- | --- |
| `AUTH_MODE=fake`, `EMAIL_MODE=outbox`, `LLM_MODE=fake` | `APP_ENV=prod` | a mock reached production |
| `SES_PROFILE`, `DYNAMO_PROFILE`, `S3_PROFILE` | `APP_ENV=prod` | a container has no `~/.aws`; credentials must come from the task role |
| a `*-prod` `TABLE_NAME` or `S3_BUCKET` | `APP_ENV=dev` | a dev run addressing production data — silent, since the writes succeed |
| missing `TABLE_NAME` or `S3_BUCKET` | always | no defaults, so an incomplete env file cannot inherit another environment's store |

The last two are the ones that matter most now that there is no local mode.
Before, a misconfigured dev box failed loudly by pointing at a localhost port
that wasn't listening; now it would succeed against real AWS, so the guard has
to be explicit.
