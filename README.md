# 🧱 LEGO® Insiders Rewards Telegram Bot (`en-PL`)

Monitors [https://www.lego.com/en-pl/insiders/rewards?r-sort=newest](https://www.lego.com/en-pl/insiders/rewards?r-sort=newest) via LEGO's GraphQL Gateway (`rewardListingV2` & `rewardV4`) and sends rich notifications (with product photos, points required, availability/release dates, descriptions, SKU, and direct links) to your Telegram account.

---

## 📅 When & How Often Does LEGO Add New Insiders Rewards?

Based on LEGO's official release patterns and empirical analysis of the `en-PL` GraphQL catalog timestamps:

1. **Major Drop Dates (Frequency)**:
   - **1st of the Month**: Major waves (especially **January 1, March 1, June 1, August 1, October 1**) almost always bring new physical Insiders rewards, collectible posters, or sets alongside main set launches.
   - **Special Promotional Events**: **LEGO Insiders Weekend** (the weekend before Black Friday in November), **Black Friday / Cyber Monday**, **May the 4th (Star Wars Day)**, and **LEGO Insiders Days** (July/August).
   - **Mid-month & "Coming Soon" Pre-listings**: LEGO frequently uploads new rewards to the catalog **days or weeks in advance** during European working hours (`10:00–16:00 CET/CEST`) with a future `startDate` (`Coming Soon`). For example, the *LEGO® Ideas Godzilla Poster* (3,000 points) is pre-listed weeks ahead with `startDate: 2026-11-26T23:00:00Z` (`2026-11-27 00:00 CET`).

2. **Exact Release Times (`Europe/Warsaw` — CET/CEST)**:
   - **Physical / Promo Code Rewards (`type: CODE`)**: Go live at **00:00 (Midnight) CET/CEST** (`23:00 UTC` in winter / `22:00 UTC` in summer), and occasionally at `01:00 CEST` (`00:00 BST/UTC` during summer). High-demand limited collectibles can sell out within minutes of midnight.
   - **Digital Rewards (`type: DOWNLOAD`)**: Usually go live immediately when uploaded during daytime business hours (`10:00–16:00 CET/CEST`).

### ⚡ How the Bot's Smart Adaptive Schedule Works
Because of these two distinct patterns, the bot uses a **3-tier adaptive polling schedule**:
1. **Standard Daytime/Nighttime Polling (every 15 minutes)**: Catches newly uploaded rewards (including "Coming Soon" pre-listings and daytime digital rewards) without spamming LEGO's servers.
2. **Midnight Drop Window Boost (every 90 seconds)**: Automatically accelerates checks during `23:55–00:30` and `00:55–01:15` (`Europe/Warsaw` time) when physical rewards drop at midnight.
3. **Exact `startDate` Countdown**: When the bot sees a `Coming Soon` reward with an upcoming `startDate`, it automatically schedules a check **5 seconds after its exact `startDate`** so you get notified the moment it becomes redeemable.

---

## 🚀 Quick Start

### 1. Preview Live Rewards in Your Terminal (No Telegram Token Needed)
You can test the scraper and preview the exact Telegram notification card immediately:
```bash
python3 bot.py --preview
```

### 2. Configure Your Telegram Bot
1. Open Telegram, message **[@BotFather](https://t.me/BotFather)**, and send `/newbot` to get your `TELEGRAM_BOT_TOKEN`.
2. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
3. Paste your `TELEGRAM_BOT_TOKEN` into `.env`.
4. *(Optional)* Set `TELEGRAM_CHAT_ID` in `.env`, **or** leave it blank, start the bot, and send `/start` to your bot in Telegram — it will automatically reply with your Chat ID and subscribe your chat!

### 3. Run Locally or via GitHub Actions (Free 24/7 Cloud Schedule)

#### Option A: Run 24/7 in the Cloud via GitHub Actions (No Server Needed)
A GitHub Actions workflow is already configured in [`.github/workflows/check_rewards.yml`](.github/workflows/check_rewards.yml). It runs every 15 minutes (plus extra checks right after midnight CET/CEST) and commits catalog state updates to `data/rewards_state.json`.

1. **Create a new repository on GitHub** (e.g. `lego-insider-rewards-telegram-chatbot` — **Public** repositories get unlimited free GitHub Actions minutes; Private repositories get 2,000 free minutes/month).
2. **Push this project to your GitHub repository:**
   ```bash
   git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/lego-insider-rewards-telegram-chatbot.git
   git branch -M main
   git push -u origin main
   ```
3. **Add your Telegram Secret(s) on GitHub:**
   - Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
     - `TELEGRAM_BOT_TOKEN`: Your bot token from [@BotFather](https://t.me/BotFather).
     - `TELEGRAM_CHAT_ID` *(optional)*: Your Telegram Chat ID. If you leave this out, just send `/start` to your bot in Telegram — the next GitHub Actions run will automatically subscribe your chat and save it!
4. **Test it immediately:**
   - Go to the **Actions** tab in your GitHub repository → select **Check LEGO Insiders Rewards** → click **Run workflow**.
   - Within ~15 seconds, you will receive the initialization message and the latest reward card on Telegram!

#### Option B: Run Locally on Your Mac
```bash
python3 bot.py
```
Or run a single check:
```bash
python3 bot.py --once
```

---

## 💬 Telegram Bot Commands

- `/start` or `/help` — Subscribes your chat, displays your `Chat ID`, and lists commands.
- `/latest [N]` — Lists the newest `N` rewards (default 8) with points and availability.
- `/upcoming` — Lists all rewards currently marked as **Coming Soon** with their exact release date/time and countdown.
- `/detail <N>` — Sends the full notification card (photo, description, points, availability, SKU, limits, and link) for the `#N` newest reward (e.g. `/detail 1`).
- `/check` — Forces an immediate check of LEGO.com right now.
- `/status` — Displays bot health, total tracked rewards, next scheduled drop, and active polling mode.
