# 🔐 Authentification KaronlineLive — comptes KJ (phase tests amis/famille)

## Règles métier adoptées

| Élément | Comportement |
|---|---|
| Inscription | Site web → **« S'enregistrer »** (ex-onglet « S'abonner », désormais fonctionnel) : email + mot de passe ≥ 8 car., carte *optionnelle* |
| Carte bancaire | Optionnelle pendant les tests : **seuls marque + 4 derniers chiffres** transitent/sont stockés. Aucun prélèvement. La facturation réelle des sessions arrivera plus tard sur ce même compte |
| Catalogue | Consultable dans KaronlineBox **uniquement connecté** au compte (API centrale). Impossible hors connexion |
| Favoris & réglages | Locaux (QSettings) → pleinement utilisables **sans** connexion |
| Sessions publiques | Créées depuis KaronlineBox **par un compte connecté** ; le nom de session appartient à ce compte (`owner`). Toute personne possédant le nom participe avec demandes distantes — les futurs frais sont portés par le compte hôte |

## Endpoints API centrale (api.karonlinelive.com)

```
POST /auth/register   {email, password, card_brand?, card_last4?} → 201 {token,email,card_label}
POST /auth/login      {email, password}                        → 200 {token,email,card_label}
GET  /auth/me         Authorization: Bearer                    → 200 {email,card_label}
POST /auth/logout     Authorization: Bearer                    → 200 {}
POST /session/register{...} **Bearer requis**                 → owner=account     (sinon 401 AUTH REQUIRED)
GET  /session/<nom>   public                                   → {host_url}        (inchangé)
```

Codes applicatifs : `EMAIL TAKEN`, `INVALID EMAIL`, `WEAK PASSWORD`,
`WRONG CREDENTIALS`, `TOKEN INVALID`, `CARD INVALID`, `AUTH REQUIRED`.

## Stockage serveur

* `accounts.json` — email, sel aléatoire 16 o, hash **PBKDF2-HMAC-SHA256**
  (120 000 itérations), carte masquée (jamais de numéro complet), date.
* `tokens.json` — jetons porteurs `token_urlsafe(32)` persistés, TTL 30 jours.
* Emplacement modifiable pour les tests via `KL_DATA_DIR=<dossier>`.

## Côté clients

* **Site (js/auth.js v1)** : état en `localStorage` (`kl_auth_token/email/card`),
  dialogs `#register-dialog` / `#login-dialog`, chip « Compte : … · Déconnexion » ;
  inclure après `app.js` sur toutes les pages (fait sur index.html + catalogue.html).
* **KaronlineBox (core/central_auth.py + ui/auth_dialog.py)** :
  `CentralAuthClient(QSettings)` garde le jeton (clés `auth/token`, `auth/email`,
  `auth/card`) ; le bouton **COMPTE** permet connexion/enregistrement ou
  déconnection ; verrous posés sur la consultation de la bibliothèque
  (`scan_video_library`) et sur **DÉMARRER LA SESSION** (jeton envoyé en Bearer).

## Limites assumées en phase de test

Pas de vérification d'email, pas de récupération de mot de passe, HTTPS de
GitHub Pages vers tunnel requis (les invités invités passent par les noms de
session, jamais en HTTP traversant Internet). Prochaines étapes paiement :
rattacher un PSP (stripe-like) puis remplacer `card_last4` par token PSP.

## Vérification automatisée

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File KaronlineKj\tests\run_checks.ps1
```

Compilation des modules (serveur, client central, dialogues Qt, app,
fenêtre principale) + scénario complet register/login/me/session/logout
sur un serveur isolé aléatoire (11 assertions). Sortie attendue :
`11/11 tests passés` puis `SMOKE_EXIT=0`.