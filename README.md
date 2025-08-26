# ITNR – MVP (FastAPI + Amadeus)

Un starter **clé en main** pour tester l'API ITNR en local (FastAPI) et interroger Amadeus.

## 0) Prérequis
- Python 3.10+ (idéalement 3.12)
- Votre **API Key/Secret Amadeus** (Self-Service, environnement test)

## 1) Installation
```bash
# Cloner/extraire le dossier, puis :
cd itnr_api_mvp

# (Optionnel, recommandé) créer un venv
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows (PowerShell)
# .venv\Scripts\Activate.ps1

# Installer les dépendances
pip install --upgrade pip
pip install -r requirements.txt
```

## 2) Configurer vos secrets
```bash
cp .env.example .env
# Éditez .env et renseignez AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET
```

## 3) Lancer l'API en local
```bash
uvicorn app.main:app --reload
# L'API sera disponible sur http://127.0.0.1:8000
# Docs interactives: http://127.0.0.1:8000/docs
```

## 4) Tester l'endpoint /search
### Exemple A/R : Paris → Bangkok pour 2 adultes
```bash
curl -s -X POST http://127.0.0.1:8000/search   -H "Content-Type: application/json"   -d '{
    "originCity":"Paris",
    "destinationCity":"Bangkok",
    "departureDate":"2025-01-20",
    "returnDate":"2025-02-10",
    "passengers":{"adults":2,"children":0,"infants":0},
    "cabin":"ECONOMY",
    "maxStops":1,
    "budgetPerPaxEUR":900
  }' | jq .
```

### Exemple période (~3 semaines)
```bash
curl -s -X POST http://127.0.0.1:8000/search   -H "Content-Type: application/json"   -d '{
    "originCity":"Paris",
    "destinationCity":"Bangkok",
    "period":{"start":"2025-01-15","durationDays":21},
    "passengers":{"adults":1,"children":0,"infants":0},
    "cabin":"ECONOMY",
    "maxStops":1
  }' | jq .
```

## 5) Conseils de dépannage
- **401/403 Amadeus** : vérifiez vos `AMADEUS_CLIENT_ID/SECRET` dans `.env` et utilisez bien l'URL `https://test.api.amadeus.com` (sandbox).
- **Timeout** : réessayez; vérifiez votre connexion internet.
- **0 résultat** : assouplissez `maxStops` ou augmentez `budgetPerPaxEUR`.

## 6) Structure
```
itnr_api_mvp/
├── app/
│   ├── main.py
│   ├── schemas.py
│   └── utils.py
├── tests/
│   └── test_utils.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

## 7) Prochaines étapes (facultatif)
- Dockerfile + déploiement (Fly.io/Render)
- Auth (clé API simple)
- Logs structurés
- Suggestions de dates ±3 jours si “environ X semaines”
