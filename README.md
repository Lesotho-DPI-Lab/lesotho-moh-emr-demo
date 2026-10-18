# MoH Child EMR — X-Road demonstrator

A lightweight electronic child health record for the Ministry of Health. It
demonstrates identity verification with the Ministry of Home Affairs civil
registry over the Lesotho Exchange (X-Road) pilot, scenario A.
**Demonstration only: every record is synthetic.**

| | |
|---|---|
| UI and REST API | `https://10.255.100.183/` (moh-mis) |
| X-Road client | `LS-DEMO/GOV/MOH/CHILD-EMR` on moh-ss `10.255.100.184` |
| Service consumed | `LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/child-verification`, `POST /verifications` ([civil registry](https://github.com/Lesotho-DPI-Lab/lesotho-mohap-cr-demo)) |
| Contract | `GET /api/v1/openapi.json` |

## What it does

**Web UI** (roles: nurse, administrator)

- Sign in. Passwords are hashed with scrypt, logins lock after 5 failures,
  and forms carry CSRF tokens.
- **Register a child.** A Home Affairs identifier (birth registration number
  or national ID) is optional.
  - With an identifier and the guardian's consent, the EMR verifies the child
    with the civil registry over X-Road straight away.
  - On success the record is marked **✔ Verified with Home Affairs**, and the
    registry's names, date of birth and sex replace what was typed. Those
    fields are then locked.
- **Link to civil registry.** Verify an existing record later, for example
  when the guardian brings the birth certificate on the next visit.
- **Visits.** Well-child, growth monitoring, immunisation, sick child and
  follow-up visits record:
  - observations: weight, length/height, MUAC, head circumference,
    temperature, respiratory rate, feeding;
  - IMCI general danger signs (any one marks the visit for urgent referral);
  - vitamin A, deworming, assessment, plan and next visit date.
  - Flags are shown for MUAC (SAM/MAM), fever and fast breathing for age.
- **Immunisations.** An EPI schedule shows each vaccine as given, due, overdue
  or upcoming. Vaccines can be recorded during a visit or directly from the
  schedule.
- **Growth.** A weight-for-age curve from birth weight and visit weights.
- **Verification history** on each child, with the X-Road message id. The id
  matches the moh-ss message log and the registry's "who accessed this
  record".
- **Dashboard.** Counts, recent registrations, return visits this week, and
  pending verifications with *Retry now*.
- **Administrator only.**
  - Verification log, searchable by X-Road message id, consent reference or
    nurse.
  - X-Road page with the client configuration and a *Check connection*
    button, which calls the `allowedMethods` metaservice and sends no child
    data.

### Verification over X-Road

```http
POST https://10.255.100.184:8443/r1/LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/child-verification/verifications
X-Road-Client: LS-DEMO/GOV/MOH/CHILD-EMR
X-Road-UserId: nurse.mpho                      (the signed-in nurse)
X-Road-Issue:  CONSENT-CH-2026-000014-20261020093012   (guardian consent reference)

{"purpose": "CHILD_HEALTH_SERVICE", "registrationNumber": "B-MSU-2025-000001",
 "firstName": "Lerato", "surname": "Mokoena", "dateOfBirth": "2025-08-24"}
```

- The guardian's consent is required before anything is sent. The nurse may
  type a consent reference, for example from a paper form; otherwise the EMR
  generates one.
- Identifiers travel only in the body. The EMR's own URLs use its EMR number
  (`CH-2026-000014`), never a Home Affairs identifier.
- Every answer becomes one of these states on the child record:

| State | When | Shown to the nurse |
|---|---|---|
| UNVERIFIED | No identifier, or consent not recorded | *Link to civil registry* form |
| VERIFIED | Registry confirmed the identity | ✔ badge; identity attributes locked |
| DECLINED | RC-01 not found, RC-02 partial match, RC-05 restricted | Reason, plus the remedy in English and Sesotho |
| PENDING | RC-06, moh-ss unreachable, timeout, X-Road network fault | Care continues; retried every 5 minutes and on *Retry now* |
| ERROR | RC-03, RC-04, invalid request, X-Road access denied or other configuration fault | The X-Road fault, for the administrator |

The integration lives entirely in `app/emr/xroad.py`. The planned SOAP
variant of the EMR (through the SOAP→REST adapter) only has to replace that
module.

**REST API** (`/api/v1`, bearer token from `POST /auth/token`)

| Method | Path | |
|---|---|---|
| POST | `/auth/token` | `{"username","password"}` → `accessToken` (8 h) |
| GET, POST | `/children` | Search (`q`, `status`) or register; registration verifies when `guardianConsent` is true and an identifier is given |
| GET | `/children/{emrNumber}` | Record with verification status and latest attempt |
| POST | `/children/{emrNumber}/verification` | Link or verify later; 409 if already verified |
| GET | `/children/{emrNumber}/verifications` | Verification history with X-Road message ids |
| GET, POST | `/children/{emrNumber}/visits` | Visits with observations; `vaccines` records immunisations given at the visit |
| GET, POST | `/children/{emrNumber}/immunisations` | EPI schedule with states; record a vaccine |

## Demo script

Use the civil registry's demo cases (`data/demo-cases.json` on moha-mis). Its
dates of birth depend on the day the registry was seeded.

1. **Happy path.** Register *Lerato Mokoena* with `B-MSU-2025-000001`, her
   date of birth from the cases file, and consent ticked. The record becomes
   ✔ Verified, the first name becomes *Lerato Palesa*, and an X-Road message
   id appears in the history.
2. **Link later.** Open a seeded child, record a visit, then link a
   registration number from the profile.
3. **Declines.**
   - *Nthabiseng Letsie* `B-MSU-2025-999999` returns RC-01.
   - *Palesa Ramakatsa* with her date of birth plus one day returns RC-02.
   - *Tumelo Sekhesa* `B-MFT-2025-000001` returns RC-05.

   Each shows the remedy in English and Sesotho.
4. **Resilience.** With the registry stopped, registration still succeeds
   with *Verification pending*. Restart the registry and press *Retry now*.
5. **Audit trail.** Copy an X-Road message id from the verification log.
   - Find it in the moh-ss message log and in the registry's audit log.
   - The registry's person profile lists the access under "who accessed this
     record".

## X-Road configuration on moh-ss

- Client subsystem `LS-DEMO:GOV:MOH:CHILD-EMR` is registered. Its
  connection type is **HTTPS**, and the EMR's client certificate
  (`certs/xroad/client.crt`, CN=CHILD-EMR) is one of its internal TLS
  certificates. `scripts/register-on-ss.sh` sets both through the security
  server REST API.
- The EMR pins moh-ss's internal TLS certificate (`certs/xroad/moh-ss.crt`).
  Only that server is trusted to carry the call.
- Access rights to the service are granted on moha-ss: `CHILD-EMR` only.

## Running it

**Local (any machine with Docker)**

```bash
scripts/init-env.sh                                  # creates .env, prints initial passwords once
openssl s_client -connect <moh-ss>:8443 </dev/null | openssl x509 > ss.crt
scripts/gen-certs.sh 127.0.0.1 ss.crt
docker compose up -d --build
docker compose exec -T app python -m emr.seed        # 12 synthetic children with visits
docker compose run --rm -T --no-deps app pytest -q -p no:cacheprovider tests
```

**Deploy to moh-mis (from moh-ss)**

```bash
scripts/deploy.sh                                    # default target administrator@10.255.100.183
scripts/register-on-ss.sh deploy-out/child-emr-client.crt   # once, or after the client cert changes
```

`deploy.sh` does the following:

1. Copies the code to `~/child-emr` on the target.
2. Pins moh-ss's internal TLS certificate.
3. Keeps the existing `.env` and certificates.
4. Builds, runs the test suite, and restarts the stack.
5. Seeds demo data on the first run.
6. Brings back the EMR's X-Road client certificate.

## Layout

```
app/emr/       FastAPI app: xroad.py (civil registry client), services.py (record rules),
               api.py (REST API), ui.py (web UI), clinical.py (EPI schedule, IMCI flags),
               auth.py, models.py, seed.py, reason_codes.py
app/tests/     X-Road client, UI and API tests against a fake registry behind a fake moh-ss
nginx/         TLS termination
scripts/       init-env, gen-certs, deploy, register-on-ss
```

## Known limits (demo scope)

- The EPI schedule is illustrative. Confirm it against the current MoH
  schedule.
- There are no WHO z-scores: the growth curve plots weight only.
- Sesotho remedy texts come from the registry's catalogue, which marks them as
  drafts. The EMR's own Sesotho labels are drafts too.
- Initial accounts are created from `.env`; there is no user administration
  screen.
- The schema is created on start-up (no migrations), and records are never
  deleted.
- Scenario B (OpenHIM with eReg, eLMIS and BKM) and the SOAP variant are later
  phases.
