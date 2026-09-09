# ameli account portal — API documentation

Mapping of the personal account portal (`assure.ameli.fr`), discovered by a
live capture on 2026-09-09. There is **no public API**: these routes are
deduced from the browser traffic of the real, logged-in session.

## 1. Structure of the portal

- Base host: `https://assure.ameli.fr`
- Legacy pages: J2EE portal
  `https://assure.ameli.fr/PortailAS/appmanager/PortailAS/assure?_nfpb=true&_pageLabel=<label>`
  (`as_accueil_page`, `as_login_page`, `as_creation_immediate_page`,
  `as_paiements_page`, …).
- The account sub-applications live under **`/compte/…`** (e.g. the
  monthly-statements app `/compte/aspm/releves-mensuels`). They are served by a
  modern Vue/Vuetify SPA whose REST API lives under `/compte/aspmm/rest/…`.

## 2. Authentication & anti-bot

| Item | Value |
| --- | --- |
| Login | ameli credentials (`login` = numéro de sécurité sociale) + password + **double validation** (SMS / "Compte ameli" app) on `ameliconnect.ameli.fr` |
| OAuth | The `/compte` sub-apps are protected by **OAuth2/OIDC**: `ameliconnect.ameli.fr/oauth2/authorize` (`client_id=compte_AS`, PKCE), then a redirect back to `assure.ameli.fr/compte/redirect_uri` |
| Session | **Cookies only** (no token in `localStorage`): `mod_auth_openidc_session` on `assure.ameli.fr` (OIDC session) + `AMWebSSOv2` on `.ameliconnect.ameli.fr` + F5 cookies (`BIGipServer*`, `TS*`) |
| Anti-bot | **F5/Imperva WAF** ("Request Rejected", "Your support ID is …"): rejects plain HTTP clients **and headless Chrome** — only a real **visible** Chrome passes |
| Cookie reuse | The session cookies are **session cookies** (not persisted by Chrome), but they can be **harvested and re-injected** into a fresh dedicated Chrome (verified: works headful) |
| API auth | The REST calls use the **session cookies** — **no `Authorization` header** |

## 3. REST API (monthly statements)

Base: `https://assure.ameli.fr/compte/aspmm/rest/releves-mensuels`

### 3.1 List

```text
GET …/home?debutPeriode=YYYYMM&finPeriode=YYYYMM
```

- `finPeriode` must be the **last closed month** (the current month is rejected
  with HTTP 400).
- The archive keeps a **27-month retention** (see `infosStatiques`, entry
  `portailAs.relevesMensuels.rsij.retention`). The SPA loads it in sliding
  windows of ~6 months ("Afficher plus de relevés").

Response:

```json
{
  "rubriquesMensuelles": [
    { "moisAnnee": "202606", "releves": [
        { "objectType": "ReleveSoinsIJ", "identifiant": "SOINS_IJ#-#<token>" }
    ]},
    { "moisAnnee": "202608", "releves": [] }
  ],
  "infosStatiques": [ ]
}
```

A month with `releves: []` had no statement ("Aucun paiement").

### 3.2 PDF download

```text
GET …/pdf/<identifiant url-encoded>
```

The `identifiant` contains a `#` (`SOINS_IJ#-#<token>`) which **must be
URL-encoded** (`%23`). Response is JSON (not a raw PDF):

```json
{
  "nom": "ReleveMensuelJuin2026.pdf",
  "nomAffichage": "ReleveMensuelJuin2026.pdf",
  "taille": 171928,
  "typeMime": "application/pdf",
  "contenu": "<base64 PDF>"
}
```

`contenu` is the base64-encoded PDF. `nom`/`nomAffichage` is the display name
suggested by the portal — the CLI does **not** use it for the on-disk file
name. Files are named from the configurable `[download] file_mask` (default
`Relevé Mensuel {period}.pdf`) so alphabetical order is chronological; files
created by older versions under the `nomAffichage` naming are renamed on the
next `sync`.

### 3.3 Required request headers

The SPA sends these custom headers on every REST call — the server answers
**HTTP 400** without them:

```text
canal:            {"canal":"PORTAIL","reduction":"ASDS_X"}
x-app-name:       ASDS_X
x-app-version:    25.40.0
x-request-engine: Axios
x-correlation-id: <uuid>
Accept:           application/json, text/plain, */*
```

> ⚠️ `x-app-version` is the SPA version (observed `25.40.0`) and may change
> with the portal — track it here when it drifts.

## 4. How the CLI uses it

Because of the WAF, all REST calls run **inside the authenticated page**
(a same-origin `fetch` through `AmeliBrowser.fetch_json`, see `docs/AUTH.md`),
never through a plain HTTP client. `api.py` implements the listing (walking the
27-month retention in windows) and the download (base64 decode), and documents
the whole flow in this file.
