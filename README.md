# Somali Help App

Professional emergency help platform for Somalia. Citizens report emergencies with live GPS, track response teams in real time, and reach a Call Center for operator-led multi-service dispatch.

**Repository:** [https://github.com/axmed-8/Somali_help_app](https://github.com/axmed-8/Somali_help_app)

---

## Description

Somali Help App connects citizens with hospitals, police, and fire services. Emergencies can be started from a mobile SOS form or by calling the Call Center (phone + silent GPS). An **AI Assistance** engine analyzes incidents and recommends responders — it never dispatches automatically. Final approval always belongs to a human operator (Call Center) or the existing SOS auto-routing rules for life-safety Method 1.

---

## Features

- **Two emergency methods**
  - Method 1: Report Emergency (SOS) with GPS and auto-routing
  - Method 2: Call Emergency Center (`tel:` + silent location push)
- **Role-based dashboards** — Citizen, Hospital, Police, Fire, Call Center, Admin
- **Smart hospital routing** — nearest open facility, capacity-aware escalation
- **Police & fire dispatch** — type-based assignment to response stations
- **Call Center multi-dispatch** — Medical + Police + Fire from one call
- **Live GPS tracking & ETA** — map view with Google Maps or Leaflet fallback
- **Chat & notifications** — in-app messaging and role-targeted alerts
- **AI Emergency Engine** — category, priority, risk, recommended responders, confidence & reason (rule-based by default)
- **MySQL or JSON storage** — JSON for local/dev/tests; MySQL for production

---

## Technologies Used

| Layer | Stack |
|-------|--------|
| Backend | Python 3, Flask, Werkzeug |
| Frontend | HTML5, CSS3, JavaScript (mobile-first) |
| Database | JSON files (default/dev) or MySQL (PyMySQL) |
| Maps | Google Maps JavaScript API + OpenStreetMap / Leaflet fallback |
| AI | Provider-independent `ai_engine` (default: offline rule-based) |
| Tests | pytest |

---

## Installation

### Prerequisites

- Python 3.10+ recommended
- (Optional) MySQL 8+ for production mode
- (Optional) Google Maps API key for full map features

### 1. Clone the repository

```bash
git clone https://github.com/axmed-8/Somali_help_app.git
cd Somali_help_app
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run in JSON mode (quickest)

```bash
python app.py
```

Open [http://127.0.0.1:5000/login](http://127.0.0.1:5000/login)

### 5. MySQL mode (production)

```bash
# Copy example config and edit credentials
copy database\db_config.env.example database\db_config.env   # Windows
# cp database/db_config.env.example database/db_config.env   # macOS/Linux

# Or set environment variables:
set GURMADNET_DB=mysql
set MYSQL_HOST=127.0.0.1
set MYSQL_USER=root
set MYSQL_PASSWORD=yourpassword
set MYSQL_DATABASE=gurmad
set GOOGLE_MAPS_API_KEY=your_key
set SECRET_KEY=change-me-in-production

python scripts/init_mysql.py
python app.py
```

> **Security:** Never commit `database/db_config.env`. It is listed in `.gitignore`.

---

## Usage

Register with a real email address on `/signup`, then sign in on `/login`.
Call Center operators are created by an administrator (role: `call_center`).

### Important URLs

| URL | Purpose |
|-----|---------|
| `/login` | Sign in |
| `/` | Citizen SOS / Call Center entry |
| `/dashboard` | Citizen live tracking |
| `/call-center/login` | Call Center operator login |
| `/admin` | Admin dashboard |

### Tests

```bash
# Prefer JSON mode for pytest
set GURMADNET_DB=json
python -m pytest tests/ -v
```

---

## Folder Structure

```
Somali_help_app/
├── app.py                      # Flask application & routes
├── hospital_logic.py           # Hospital routing, notifications, helpers
├── hospital_service.py         # Hospital service helpers
├── call_center_logic.py        # Call Center session & dispatch helpers
├── requirements.txt
├── LICENSE
├── README.md
├── ai_engine/                  # AI Emergency Decision Support Engine
│   ├── service.py
│   ├── providers/              # rule_based (+ optional LLM stubs)
│   └── ...
├── database/
│   ├── schema.sql
│   ├── mysql_store.py
│   ├── connection.py
│   ├── db_config.env.example   # Template only (commit this)
│   ├── *.json                  # Seed / fallback JSON stores
│   └── *.sql                   # Schema & patches
├── templates/                  # Jinja HTML pages
├── static/                     # CSS & JavaScript
├── scripts/                    # MySQL init, migrate, patch utilities
├── tests/                      # pytest suite
└── docs/                       # Technical & feature reports
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GURMADNET_DB` | `mysql` for MySQL; omit or `json` for JSON stores |
| `MYSQL_HOST` / `DB_HOST` | MySQL host |
| `MYSQL_USER` / `DB_USER` | MySQL user |
| `MYSQL_PASSWORD` / `DB_PASSWORD` | MySQL password |
| `MYSQL_DATABASE` / `DB_NAME` | Database name (default: `gurmad`) |
| `GOOGLE_MAPS_API_KEY` | Google Maps JavaScript API key |
| `SECRET_KEY` | Flask session secret |
| `AI_PROVIDER` | `rule_based` (default), `openai`, `gemini`, `claude`, `deepseek`, `local` |

---

## AI Emergency Engine

The AI layer (`ai_engine/`) is a **decision support engine**, not a chatbot.

- Analyzes emergency category, priority, risk, and required services
- Recommends hospital / police / fire with ETA and reasons
- Stores analysis for learning/memory
- **Never auto-dispatches** — humans remain in control
- SOS Method 1 keeps existing auto-routing; AI runs in parallel without blocking life-safety response

---

## Future Improvements

- Full Call Center AI Recommendation Panel (Approve / Reject / Manual) end-to-end UX
- Admin “AI Intelligence” analytics page
- Live unit inventory (ambulances, police cars, fire trucks) instead of station-only assignment
- Traffic-aware ETA and speech transcript support for Call Center
- Optional cloud LLM providers behind the same `ai_engine` interface
- Hardened production deployment (gunicorn/waitress, HTTPS, secrets manager)
- CI pipeline (GitHub Actions) for pytest on every push

---

## License

This project is released under the [MIT License](LICENSE).
