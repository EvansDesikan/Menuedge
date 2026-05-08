# MenuIQ — Project Instructions

## LOCKED FILES — Do NOT modify without explicit user instruction

The following files are considered stable and must not be changed unless the user explicitly says "change X in [file]":

- `app.py`
- `dashboard.html`
- `index.html`
- `auth.html`
- `report.py`
- `competitor.py`
- `static/food-bg.jpg`

## What you CAN freely work on
- `landing.html` (new home/marketing page)
- New routes added to `app.py` only if the user explicitly requests them
- The `static/` folder for new assets

## Project context
MenuIQ is a Flask + Claude AI webapp that analyses restaurant menu photos and generates PDF reports with dish performance insights (Star/Plowhorse/Puzzle/Dog classification), underperformance diagnostics, and trend suggestions.

Key routes: `/` (landing), `/analyse` (menu upload), `/dashboard`, `/login`, `/signup`
