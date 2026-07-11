# Blox Fruits Stock Telegram Bot

Python 3.12 bot for GitHub Actions. It checks Normal Stock and Mirage Stock every five minutes and posts only a new stock state to Telegram. No VPS or always-on server is needed.

The project uses public JSON APIs only; it does not scrape web pages. The default primary and fallback endpoints are in `src/bot.py`. If a provider changes an address or response schema, set the optional URL secrets below instead of changing the workflow.

## GitHub setup

1. Create a new GitHub repository from the contents of this directory and push it.
2. In **Settings → Secrets and variables → Actions**, add these repository secrets:

   | Secret | Value |
   | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | Telegram bot token |
   | `TELEGRAM_CHAT_ID` | Target chat ID |
   | `TOPIC_ID` | Forum topic ID; omit it to post to the main chat |

3. Optional public API overrides:

   | Secret | Purpose |
   | --- | --- |
   | `STOCK_API_PRIMARY_URL` | Primary JSON API URL |
   | `STOCK_API_FALLBACK_URL` | Fallback JSON API URL |

The bot needs permission to post in the chat/topic. GitHub scheduled workflows are best-effort: GitHub can occasionally delay a cron start, but the workflow schedule is configured as `*/5 * * * *`.

## State and duplicate protection

The last successfully prepared stock is stored in GitHub Actions Cache, not committed to the repository. The cache is written before Telegram delivery, which ensures that an interrupted workflow will not publish the same stock twice. A failed Telegram request can therefore skip that particular update rather than risk a duplicate notification.

## Local checks

No third-party Python packages are required.

```powershell
python -m unittest discover -s tests -v
```

To call the bot locally, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and optionally `TOPIC_ID`, then run `python src/bot.py --prepare` followed by `python src/bot.py --send`.
