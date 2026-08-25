# 📦 Déploiement KaronlineLive - karonlinelive.com

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│        Site Public (GitHub Pages)                       │
│        https://karonlinelive.com                        │
│  ✅ Catalogue (statique)                                │
│  ✅ Demandes de chansons                                │
│  ✅ Téléchargement KaronlineBox                         │
└─────────────────────────────────────────────────────────┘
                        ↓ CORS + HTTPS
┌─────────────────────────────────────────────────────────┐
│        Serveur LAN Local (PC Fixe - Port 8765)          │
│        http://192.168.x.x:8765 (réseau privé)          │
│  ✅ Catalogue (depuis SERVER/ - fichiers MP4)          │
│  ✅ Réception demandes (relay vers port 8766)          │
│  ✅ Téléchargement KaronlineBox (depuis setup.exe)     │
└─────────────────────────────────────────────────────────┘
```

---

## 📋 Checklist Déploiement

### 1️⃣ Préparer le Domaine (OVH, Gandi, etc.)

**DNS Configuration**:
```
Type       Name                     Value
A          karonlinelive.com        185.199.108.153
A          www.karonlinelive.com    185.199.108.153
```

Ces IP sont les serveurs GitHub Pages (fixes).

**Chez votre registrar**:
- Connexion → DNS
- Modifier les enregistrements A
- Pointer vers les IP ci-dessus
- ⏳ Attendre 5-30 min (propagation DNS)

### 2️⃣ Configurer GitHub Pages

**Sur GitHub**:
1. Aller sur: `https://github.com/KARONLINE64/karonline-live-private`
2. ⚠️ **RENDRE LE REPO PUBLIC** (Settings → Visibility → Change visibility)
   - Seulement le frontend KaronlineLive/ sera exposé
   - Les fichiers backend restent en local
3. Settings → Pages
4. Source: `main` / root folder
5. Custom domain: `karonlinelive.com`
6. ✅ "Enforce HTTPS" (sera automatique après quelques minutes)

### 3️⃣ Lancer le Serveur LAN Local

Sur votre PC fixe (ne jamais arrêter):

```powershell
cd C:\Users\KAR_ONLINE\Documents\AHMED\KARONLINE\KARONLINEKJ_V1.1_BUILD\KaronlineKj
python lan_server.py --port 8765
```

**Logs attendus**:
```
SERVER STARTED
SERVER IP = 192.168.129.0
SERVER PORT = 8765
MUSIC FOLDER = C:\Users\...\SERVER
```

### 4️⃣ Configurer l'API LAN (Port Forwarding optionnel)

Si vous voulez que le site public accède à l'API LAN depuis Internet (⚠️ avancé):

**Option A (Recommandée - Pas de setup)**:
- ✅ Site fonctionne en réseau privé uniquement
- ❌ Utilisateurs Internet voient "Serveur LAN indisponible"
- Votre PC n'a pas besoin d'être accessible

**Option B (Reverse Proxy - Complexe)**:
- Port forwarding sur votre routeur: 8765 → PC fixe:8765
- DynDNS si IP publique dynamique
- ⚠️ Risque de sécurité (serveur exposé)
- ✅ Utilisateurs Internet peuvent accéder au catalogue

→ **Laissez Option A pour commencer** (Option B demande config avancée)

### 5️⃣ Préparer KaronlineBox pour Téléchargement

Deux approches:

**Approche 1 (Recommandée - Rapide)**:
Placer `KaronlineBox_V90_Setup.exe` dans le dossier PC local:
```
C:\Users\KAR_ONLINE\Documents\AHMED\KARONLINE\KARONLINEKJ_V1.1_BUILD\KaronlineKj\setup.exe
```
ou
```
C:\Users\KAR_ONLINE\Documents\AHMED\KARONLINE\KARONLINEKJ_V1.1_BUILD\setup.exe
```

Le serveur cherchera automatiquement dans ces emplacements.

**Approche 2 (GitHub Releases - Public)**:
```powershell
# Compiler KaronlineBox
cd KaronlineKj
pyinstaller KaronlineBox_V90.spec

# Upload setup.exe sur GitHub Releases
# Modifier js/config.js pour télécharger depuis GitHub
```

---

## 🧪 Test Local (Avant déploiement)

### 1. Site Statique Localhost
```powershell
cd KaronlineLive
python -m http.server 8000
# Ouvrir http://localhost:8000/
```

### 2. Serveur LAN
```powershell
cd KaronlineKj
python lan_server.py --port 8765
```

### 3. Tester
- `http://localhost:8000/index.html`
- Bouton "Catalogue" → charge les chansons depuis `http://localhost:8765/catalogue`
- Soumettre une demande → POST vers `http://localhost:8765/request-demand`
- Bouton "Télécharger" → GET `http://localhost:8765/download/karonlinebox`

---

## 📤 Push Vers GitHub

```powershell
cd C:\Users\KAR_ONLINE\Documents\AHMED\KARONLINE\KARONLINEKJ_V1.1_BUILD

# Ajouter les fichiers modifiés
git add -A

# Commit
git commit -m "Deploy KaronlineLive frontend to GitHub Pages with LAN API support

- Modified config.js: Support both LAN and cloud API endpoints
- Updated app.js: Added LAN availability check and fallback messages
- Modified lan_server.py: Accept CORS from karonlinelive.com domain
- Added /download/karonlinebox endpoint for setup file
- Added CNAME file for custom domain
- Download status UI with error handling"

# Push
git push origin main
```

---

## ✅ Vérification Déploiement

1. **GitHub Pages Live**:
   ```
   https://karonlinelive.com → Site affiche ACCUEIL_00.png
   ```

2. **Catalogue (LAN required)**:
   ```
   Bouton "Catalogue" → Connecte à http://192.168.x.x:8765/catalogue
   (Affiche: "Serveur LAN indisponible" si PC éteint)
   ```

3. **Demande (LAN required)**:
   ```
   Remplir formulaire → POST http://192.168.x.x:8765/request-demand
   → Signal Qt émis dans KaronlineBox (port 8766)
   ```

4. **Téléchargement (LAN required)**:
   ```
   Bouton "Télécharger KaronlineBox" → GET http://192.168.x.x:8765/download/karonlinebox
   → Télécharge setup.exe
   ```

---

## 🔐 Sécurité

| Aspect | Statut | Détail |
|--------|--------|--------|
| HTTPS | ✅ | GitHub Pages fournit SSL gratuit (auto-renew) |
| CORS | ✅ | Accepte domaine karonlinelive.com |
| API LAN | ⚠️ | HTTP uniquement (réseau privé) |
| Authentification | ❌ | Aucune (accès libre pour réseau privé) |
| IP Validation | ✅ | Accepte IP privée uniquement |

---

## 🐛 Troubleshooting

### "Serveur LAN indisponible" sur karonlinelive.com
**Cause**: Le serveur `python lan_server.py` n'est pas lancé.
**Solution**: Lancer le serveur sur votre PC fixe.

### DNS ne fonctionne pas
**Cause**: Propagation incomplète.
**Solution**: Attendre 30 min, puis tester avec `nslookup karonlinelive.com`.

### HTTPS non disponible
**Cause**: GitHub Pages génération du certificat.
**Solution**: Attendre 5-10 min après la première visite.

### KaronlineBox téléchargement échoue
**Cause**: Fichier setup.exe introuvable.
**Solution**: Placer `KaronlineBox_V90_Setup.exe` dans les chemins configurés.

---

## 📞 Support

**Commandes utiles**:

```powershell
# Vérifier DNS
nslookup karonlinelive.com

# Vérifier serveur LAN
curl http://192.168.129.0:8765/catalogue

# Logs GitHub Pages
# Voir: https://github.com/KARONLINE64/karonline-live-private/deployments
```

---

**Date**: 25 août 2026  
**Status**: ✅ Prêt pour déploiement  
**Prochaines étapes**: Configurer DNS + Lancer serveur LAN
