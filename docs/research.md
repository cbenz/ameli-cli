# ameli-cli — Exploration journal (supplementary notes)

> **Role of this document**: record **what does not fit** in
> [docs/functional-specs.md](functional-specs.md) and
> [docs/technical-specs.md](technical-specs.md) — raw observations, portal
> footprints, items **to be confirmed by a live capture** and still-open
> decisions. Everything stable has been moved into the two specs; this file
> stays a working journal.
>
> Last updated: 2026-09-09.

## Table of contents

1. [Observed technical footprints](#1-observed-technical-footprints)
2. [Discovery of the initial scope: the monthly-statements archive](#2-discovery-of-the-initial-scope-the-monthly-statements-archive)
3. [To be confirmed by capture](#3-to-be-confirmed-by-capture)
4. [Open decisions](#4-open-decisions)
5. [Supplementary risks](#5-supplementary-risks)
6. [Next steps: capture plan](#6-next-steps-capture-plan)
7. [Sources](#7-sources)

---

## 1. Observed technical footprints

Factual observations gathered on the public pages and the portal HTML
(2026-09-08). These are **raw clues**, not yet a specification.

- The personal "compte ameli" space is served on **`https://assure.ameli.fr`**.
- Signature of a **J2EE portal** (server-side session state, server-rendered
  HTML) in the URLs:

```text
  https://assure.ameli.fr/PortailAS/appmanager/PortailAS/assure?_nfpb=true&_pageLabel=…
  ```

  Here `appmanager` + `_nfpb` + `_pageLabel` are the signature of an Oracle
  WebCenter / ADF-style portal.

- Portal version seen in the page footer: **`25.29.00`** (to track: the portal
  changes, so the integration is fragile).
- Page labels observed in URLs:
  - `as_accueil_page` — home;
  - `as_login_page` — login;
  - `as_creation_immediate_page` — account creation;
  - `as_paiements_page` — "Mes paiements et remboursements" (with parameters
    like `paiements_1flagAccueil=…`);
  - `as_accessibilite_page` — accessibility.
- Services promoted on the public account page:
  "Attestations de droits · Paiements et remboursements · Carte Vitale ·
  Carte Européenne d'Assurance Maladie · Changement de situation".
- Account creation: "You already have an ameli account (or a code given by your
  health fund)" — two entry points.
- Mobile app "Compte ameli": Android package **`fr.cnamts.it.activity`** (iOS
  too); used as a login-validation channel.
- `login.ameli.fr` exists (credential gateway?) — heavy page, not
  automatically extractable here; to observe during the capture.
- Authentication goes through **ameli credentials + password** with a **double
  validation** (SMS code or validation in the app), or through **FranceConnect**
  (external identity provider — in that case the CLI cannot pre-fill
  credentials).

## 2. Discovery of the initial scope: the monthly-statements archive

Scope retained for v1 (see the functional spec): **the monthly reimbursement
statements from the archive**.

- Direct archive URL: **`https://assure.ameli.fr/compte/aspm/releves-mensuels`**.
- "Human" access: from the "Mes paiements et remboursements" screen
  (`_pageLabel=as_paiements_page`), the link **"Accéder à mon historique depuis
  plus de 6 mois"**.
- The current screen only shows the **last 6 months**; the archive gives the
  history **beyond 6 months** (that is the point of the scope).
- Clue: the path `/compte/aspm/releves-mensuels` suggests an **"aspm"
  sub-application** of the portal, possibly technically different from the rest
  of the portal (more modern interface, maybe JSON) — to confirm by capture:
  this might be where the simplest exploitable route lives.

## 3. To be confirmed by capture

What **still blocks the implementation** of `api.py`/`auth.py` and must be
resolved during a real, logged-in session (results to report in `docs/API.md`):

1. **Real archive routes**: when opening `releves-mensuels`, which requests go
   out (HTML page to parse? internal JSON calls?) — method, URL, headers,
   required cookies.
2. **Entry format**: how each month is represented (label, period,
   download link/action); is there a **real PDF** per month or an **on-demand
   generation**?
3. **Behavior of the "beyond 6 months" link**: does it open a dedicated page,
   is there pagination / progressive loading to handle?
4. **Login form** (`as_login_page`): stable selectors (field ids, French
   labels), the **double-validation** screens (SMS / app), presence of a
   cookie-consent banner to close, timings.
5. **Real session lifetime** of the portal (to calibrate the cache TTL) and the
   behavior when the session expires mid-`sync`.
6. **Possible anti-automation** and **cookie compatibility outside the
   browser**: does a plain HTTP client (requests) with the harvested cookies
   work, or must we stay inside the browser context (Playwright
   `context.request`) to remain credible?
7. **Page details**: exact month labels, the file name actually served for the
   PDF (useful for sanitization and the incremental skip).

## 4. Open decisions

Not yet settled (outside the specs; to confirm before or during the
implementation):

- **Name** of the package/binary (`ameli-cli`?) and canonical collection
  identifiers.
- **Language** of the CLI messages and the repository — decided in AGENTS.md:
  **English everywhere**.
- **Future collections** (attestations, payment details, messaging) and their
  canonical name — v1 only covers `RELEVES_MENSUELS`.
- **Strategy if a month has no PDF**: store the raw data (JSON) instead, or
  report it?
- **Recommended frequency** of `sync` runs (reasonable use).

## 5. Supplementary risks

Complement to the specs:

- **Undocumented, versioned portal**: the integration relies on reverse
  engineering; any portal change can break the tool (track version `25.29.00`,
  date the observations).
- **Controlled automation**: the health-insurance body may monitor accesses;
  keep the tool a personal, low-frequency, read-only backup of one's own data.
- **Double validation**: makes any re-login (after expiry) interactive —
  provide clear messages inviting to run `login` with a window.

## 6. Next steps: capture plan

1. Open the ameli account in the **dedicated Chrome** (CDP), complete the
   **full login** (credentials + double validation SMS/app).
2. **Record the traffic** (HAR via CDP / local proxy) over the journeys:
   - "Mes paiements et remboursements" screen (`as_paiements_page`);
   - click on "Accéder à mon historique depuis plus de 6 mois";
   - **archive** `releves-mensuels`: list of months, then download at least one
     statement (and one remote month, > 6 months).
3. Note: hosts, routes, methods, headers, cookies, **response shapes**, session
   duration.
4. Report the mapping in **`docs/API.md`**, then freeze `api.py` and calibrate
   the session TTL in `auth.py`.

## 7. Sources

- Public ameli.fr pages (consulted on 2026-09-08):
  - <https://www.ameli.fr/assure> — insured portal;
  - <https://www.ameli.fr/assure/remboursements/suivre-remboursements/compte-ameli-connexion-services> — compte ameli services;
  - <https://www.ameli.fr/assure/remboursements/suivre-remboursements/se-connecter-compte-ameli> — account creation/login;
  - <https://assure.ameli.fr/> — public compte ameli page (`PortailAS/appmanager/PortailAS/assure` structure, version `25.29.00`, page labels, promoted services);
  - <https://assure.ameli.fr/compte/aspm/releves-mensuels> — monthly-statements archive (v1 scope, reported by the user);
  - <https://login.ameli.fr/> — gateway (content not automatically extractable).
- Mobile apps: "Compte ameli" (Android `fr.cnamts.it.activity`).
- FranceConnect: <https://www.franceconnect.gouv.fr/comptes-disponibles/ameli/>

## 8. Capture outcome (2026-09-09)

A live capture with a real login resolved the open items (details in
`docs/API.md` and `docs/AUTH.md`):

- The `/compte` sub‑apps (incl. the archive) are **OAuth2/OIDC-protected**
  (`ameliconnect.ameli.fr/oauth2/authorize`, `client_id=compte_AS`, PKCE);
  the session is **cookie-only** (`mod_auth_openidc_session` + F5 cookies), no
  token in `localStorage`.
- **WAF**: plain HTTP and headless Chrome are rejected ("Request Rejected");
  only a **visible** Chrome passes. Cookies are **re-injectable** into a fresh
  dedicated Chrome (verified) — hence `session.json` and the visible window on
  each run.
- **Archive API** (JSON): list
  `/compte/aspmm/rest/releves-mensuels/home?debutPeriode&finPeriode` (27-month
  retention, last *closed* month max), pdf
  `/compte/aspmm/rest/releves-mensuels/pdf/<identifiant%23->` returning
  base64 in `contenu`. Custom headers required: `canal`, `x-app-name`,
  `x-app-version`, `x-request-engine: Axios`, `x-correlation-id`.
- `ameli-cli list`/`sync` validated end‑to‑end on the real account (10 PDFs
  downloaded, filename `ReleveMensuel<Mois><Année>.pdf`, incremental skip by
  name).
- **Login form**: single-step J2EE form with stable selectors (`#userfield` =
  numéro de sécurité sociale, `#passwordfield`, submit `#id_r_cnx_btn_submit`
  kept disabled until both fields are valid, cookie banner `#accepteCookie`).
  `ameli-cli` pre-fills the configured credentials and submits; the double
  validation (OTP SMS/app) stays manual.

Remaining to confirm: real session lifetime (cache TTL), and the `x-app-version`
drift.
