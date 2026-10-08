# Job Fit Bot

A local Telegram bot that analyses job postings against your CV, scores the fit (0 – 100), and — if the score clears your threshold — rewrites your CV and drafts a cover letter using Mistral AI.

---

## How it works

1. You send a job posting URL to the bot on Telegram
2. The bot scrapes the page (or asks you to paste the text if scraping fails)
3. Mistral parses the job posting into structured data
4. The bot researches the company using Mistral + web search (DuckDuckGo)
5. Mistral scores the fit against your CV and preferences (0 – 100)
6. If the score is **≥ 70**, the bot asks for your consent
7. On "yes": Mistral rewrites your CV and drafts a cover letter in the language of the posting
8. All files are saved to `applications/YYYY-MM-DD_Company_Role/`

---

## Prerequisites

- **Python 3.9+**
- A **Telegram bot token** — create one via [@BotFather](https://t.me/botfather) on Telegram
- A **Mistral API key** — get one at [console.mistral.ai](https://console.mistral.ai)
- Your **Telegram user ID** — message [@userinfobot](https://t.me/userinfobot) to find it

---

## Setup

### 1. Clone / download the project

```bash
cd job-fit-bot
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in your three credentials:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
MISTRAL_API_KEY=your_mistral_key_here
AUTHORIZED_USER_ID=123456789
```

### 4. Fill in your profile

Edit both files in the `profile/` folder:

- **`profile/cv.md`** — replace the template with your actual CV
- **`profile/preferences.md`** — fill in what you like, dislike, and want in a role

---

## Running the bot

```bash
source .venv/bin/activate
python main.py
```

The bot runs locally using Telegram polling — no server or webhook needed. Keep the terminal open while using it. Press **Ctrl+C** to stop.

---

## Usage

| You send | Bot does |
|---|---|
| A job posting URL | Fetches, parses, researches, and scores it |
| URL that can't be scraped | Asks you to paste the job text |
| `yes` (after a high score) | Rewrites CV + drafts cover letter |
| Anything else after a high score | Skips rewrite, saves analysis |

---

## Output files

Each application gets its own folder inside `applications/`:

```
applications/
└── 2026-06-15_Acme_Corp_Senior_Engineer/
    ├── job_description.md   ← parsed job + company research
    ├── analysis.md          ← full fit analysis with score
    ├── cv_rewritten.md      ← tailored CV (only if score ≥ 70 and you approved)
    └── cover_letter.md      ← draft cover letter (same condition)
```

---

## Customisation

| Setting | Where |
|---|---|
| Fit threshold (default 70) | `config.py` → `FIT_THRESHOLD` |
| Mistral model | `config.py` → `MISTRAL_MODEL` |
| Max web search results | `agents/analyzer.py` → `max_results` in `web_search()` |
| Max content length sent to Mistral | `agents/analyzer.py` → slice sizes in prompts |

---

## Troubleshooting

**Scraping always fails** — some job boards (Greenhouse, Workday, LinkedIn) block bots or require JS. Paste the text manually when prompted.

**`FileNotFoundError: CV not found`** — make sure `profile/cv.md` exists and contains your actual CV content.

**Telegram says "Unauthorized"** — double-check `TELEGRAM_BOT_TOKEN` in your `.env`.

**Bot ignores your messages** — verify `AUTHORIZED_USER_ID` matches your actual Telegram user ID (message @userinfobot).
