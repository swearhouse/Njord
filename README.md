# Njord

<img src="static/njord.png" alt="Njord logo" width="96">

A small, self-hosted Flask app for tracking your monthly budget and loans.

## Features

- **Monthly overview** – income, expenses and what's left, with pie charts by asset and category
- **Items** – edit recurring income and expenses with categories and assets
- **Assets** – group costs by things like a house or a car
- **Loans** – track balance, rate, payment and fee, with payoff estimates
- **Payoff schedules** – per-loan and combined schedules with yearly and monthly breakdowns
- **Backup** – download or upload `data.json` from the Options page
- **Currency** – configurable, shown alongside amounts

## Running with Docker

```sh
docker compose up -d --build
```

Then open <http://localhost:8000>. Data is stored in the `njord-data` Docker volume (`/var/lib/njord` in the container).

## Running locally

Requires Python 3.10+.

```sh
pip install -e .
python -m njord
```

The app listens on port 8000 and is served by waitress. Run a single process, since file access is guarded by an in-process lock.

To run the tests:

```sh
pip install ".[test]"
pytest
```

## Login

On first start, a default user is created: **admin** / **admin**. Change it right away by editing `users.json` in the data directory:

```json
{"admin": "your-new-password"}
```

Passwords are stored in plain text, so only run Njord on a trusted network or behind a reverse proxy with HTTPS.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `NJORD_DATA_DIR` | Directory for stored data | `./instance` (`/var/lib/njord` in Docker) |
| `NJORD_SECRET_KEY` | Flask session secret | Generated into `.secret_key` in the data directory |

## Data

Everything is stored as JSON in the data directory:

- `data.json` – currency, items, assets and loans
- `users.json` – usernames and passwords
- `.secret_key` – generated session secret

If `data.json` is missing, an empty one is created on startup.
