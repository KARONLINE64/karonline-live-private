# 🚀 Bascule de l'API centrale vers les comptes KJ (api.karonlinelive.com)

## Constat mesuré au moment de la bascule

Les trois sondes publiques renvoient **HTTP 530** (`/catalogue`, `/auth/me`,
`/session/...`) : l'origin derrière Cloudflare est **éteinte aujourd'hui**.
La mise en ligne consiste donc à démarrer le service avec le nouveau code,
pas à migrer un serveur distant.

## Architecture confirmée dans le dépôt

* L'instance centrale **tourne sur ton PC fixe** : `cloudflared tunnel run
  karonlinelive-lan` expose `https://api.karonlinelive.com → localhost:8765`
  (voir `start_api_tunnel.ps1`).
* Le même fichier `lan_server.py` sert aussi chaque hôte LAN ; les hôtes qui
  font tourner une copie locale récupéreront automatiquement comptes+auth
  après avoir mis à jour ce dossier.
* Aucune dépendance externe : stdlib Python uniquement.

## Procédure de bascule (2 minutes)

```powershell
cd C:\Users\KAR_ONLINE\Documents\AHMED\KARONLINE\KARONLINEKJ_V1.1_BUILD

# (optionnel mais conseillé) mettre à jour depuis git si tu travailles ailleurs
git pull origin karonlinelive-site

powershell -NoProfile -ExecutionPolicy Bypass ^
  -File .\KaronlineKj\restart_api_centrale.ps1
```

Le script fait tout :

1. arrête l'ancien processus `lan_server.py` (8765) si présent ;
2. relance avec `.venv\Scripts\python.exe` (fallback `python`) ;
3. attend que `http://localhost:8765/catalogue` réponde ;
4. relance le tunnel `karonlinelive-lan` s'il manque ;
5. **sonde de signature** : `GET https://api.karonlinelive.com/auth/me` doit
   renvoyer **401 {"error":"TOKEN INVALID"}** — c'est le marqueur de la
   nouvelle version (l'ancienne donnait 404).

Résultat attendu en fin d'exécution :
`[OK] Nouvelle version EN LIGNE (401 TOKEN INVALID).`

## Après la bascule — test utilisateur réel

1. Ouvrir karonlinelive.com → « S'enregistrer » → créer un compte réel.
2. Dans KaronlineBox → bouton **👤 COMPTE** → se connecter.
3. « ▶ DÉMARRER LA SESSION » → partager le nom → demander une chanson depuis
   un téléphone hors du réseau.
4. Vérifier `SESSION REGISTERED = <nom> -> <tunnel> (owner=<email>)` côté PC
   (visible dans les fenêtres de sortie ou via `Get-CimInstance Win32_Process`).

## Fichiers de données & sauvegarde

Par défaut à côté de `lan_server.py` (dossier `KaronlineKj\`) :

| Fichier | Contenu | À sauvegarder |
|---|---|---|
| `accounts.json` | emails, sels, hashes PBKDF2, carte masquée | après chaque inscription |
| `tokens.json` | jetons porteurs (TTL 30 j) | optionnel |

Ces fichiers sont **exclus du dépôt** par `.gitignore` — ne jamais les
committer ni les exposer publiquement.

## Rollback

```powershell
git checkout -- KaronlineKj/lan_server.py
& .\KaronlineKj\restart_api_centrale.ps1 -SkipPublicProbe
```

(les comptes déjà créés restent sur disque et restent compatibles : le schéma
n'a pas changé).

## Prochaines évolutions possibles

Migration vers une petite base SQLite + vérification e-mail à l'inscription,
dès que la phase tests amis/famille se stabilise.