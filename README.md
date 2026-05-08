# MenuEdge — AI Menu Optimiser

MenuEdge analyses restaurant menu photos using Claude AI and generates PDF reports that classify every dish as a **Star, Plowhorse, Puzzle, or Dog**, flag pricing gaps, and suggest trends to boost revenue.

---

## Prerequisites

- Python 3.10 or higher
- An [Anthropic API key](https://console.anthropic.com/) (required for AI analysis)
- A Stripe account (optional — only needed for payments/subscriptions)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/EvansDesikan/Menuedge.git
cd Menuedge
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

- **Windows:** `venv\Scripts\activate`
- **Mac/Linux:** `source venv/bin/activate`

### 3. Install dependencies

```bash
pip install flask flask-limiter anthropic stripe bcrypt reportlab requests beautifulsoup4 pillow openpyxl
```

### 4. Set up your environment variables

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_anthropic_api_key_here
SECRET_KEY=any_long_random_string_here

# Optional — only needed if using Stripe payments
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_PRICE_ID_STARTER=price_...
STRIPE_PRICE_ID_STARTER_ANNUAL=price_...
STRIPE_PRICE_ID_PRO=price_...
STRIPE_PRICE_ID_PRO_ANNUAL=price_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

> The app will run without Stripe keys — payment features will simply be inactive.

---

## Running the app

```bash
python app.py
```

Then open your browser and go to:

```
http://localhost:5002
```

---

## Key pages

| URL | Description |
|-----|-------------|
| `/` | Landing page |
| `/signup` | Create an account |
| `/login` | Sign in |
| `/analyse` | Upload a menu photo for analysis |
| `/dashboard` | View past analyses and reports |

---

## How it works

1. **Sign up / log in** to your account
2. Go to **Analyse** and upload a photo of any menu (printed, laminated, chalkboard, or PDF)
3. MenuEdge extracts all dishes using Claude AI
4. A full report is generated classifying each dish and identifying revenue opportunities
5. Download the PDF report from your dashboard

---

## Notes

- The SQLite database (`menuiq.db`) and uploaded files are created automatically on first run
- Max upload size is 20 MB
- Reports are saved to the `reports/` folder
