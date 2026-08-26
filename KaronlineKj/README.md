# Karonline KJ — prototype v0.1

Prototype Windows de l’interface validée. Cette version contient la coque UI, ACTUELLEMENT, SUIVANT, FILE D’ATTENTE, Key Change -6/+6, volumes et fenêtre écran public. Le lecteur est encore simulé.

## Lancer

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

## Suite
1. intégrer libmpv/mpv ;
2. tester les vrais fichiers fournisseur ;
3. implémenter le Key Change temps réel sans modifier la vitesse ;
4. brancher demandes/paiement ;
5. rotation ;
6. break M3U + AUTO ;
7. mémoire locale FAVORIS ;
8. écran externe.
