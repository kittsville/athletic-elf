# Strava Webhooks

A Flask app that receives [Strava webhook events](https://developers.strava.com/docs/webhooks/), fetches activity details via the Strava API, and stores them in a Postgres database.

## How it works

1. Strava sends a webhook event to `POST /webhook` when an activity is created or deleted.
2. For new activities, a task is queued in the `task` table.
3. A separate `POST /cron` endpoint processes up to 10 queued tasks per call — fetching activity details from the Strava API and storing them in the `activity` table.
4. When an activity is deleted, the corresponding record is removed from the `activity` table.

## Setup

### Prerequisites

- Python 3.10+
- Docker (for Postgres)

### Start the database

```bash
docker compose up -d
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Environment variables

| Variable             | Required | Default                                              | Description                        |
|----------------------|----------|------------------------------------------------------|------------------------------------|
| `STRAVA_ACCESS_TOKEN`| Yes      | —                                                    | OAuth2 access token for Strava API |
| `VERIFY_TOKEN`       | No       | `STRAVA`                                             | Token for webhook subscription verification |
| `DATABASE_URL`       | No       | `postgresql://strava:strava@localhost:5432/strava`    | Postgres connection string         |
| `PORT`               | No       | `80`                                                 | Port the server listens on         |

### Run the app

```bash
STRAVA_ACCESS_TOKEN=your_token python app.py
```

## Endpoints

### `GET /webhook`

Handles Strava's [webhook subscription validation](https://developers.strava.com/docs/webhooks/#subscription-validation). Responds with the `hub.challenge` token when the `hub.verify_token` matches.

### `POST /webhook`

Receives webhook events from Strava.

- `aspect_type: create` — queues the activity for processing.
- `aspect_type: delete` — removes the activity from the database.

### `POST /cron`

Processes up to 10 queued tasks. For each task, fetches the activity from the Strava API, saves it to the `activity` table, and deletes the task from the queue. This should be polled every minute when the app is deployed.