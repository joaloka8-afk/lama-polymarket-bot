# 🦙 Lama – Polymarket Trading Bot

Meet **Lama**, your AI-powered Polymarket trading assistant with:

- 🤖 **Natural language chat** powered by xAI Grok
- 👥 **Copy-trading** – automatically mirror successful traders
- 📊 **Algo-trading** – automated strategies (momentum, mean reversion, value)
- 💬 **Chat-to-trade** – place orders just by messaging
- 🔒 **Self-custodial** – you own your keys (encrypted at rest)

Each user gets their own Polymarket wallet (EOA + Gnosis Safe proxy) and can chat naturally 
with Lama to trade, follow leaders, or enable algorithmic strategies.

## Architecture

```
main.py                  Entry point
config.py                Env-var loader
database.py              Async SQLite (users, leaders, trade log, dedup)
wallet_manager.py        EOA generation + Fernet encryption + proxy derivation
polymarket_api.py        Relayer · CLOB · Bridge · Data API façade
copy_engine.py           Copy-trading background loop
algo_engine.py           Algo-trading background loop
algo_strategies.py       Strategy implementations (momentum, mean reversion, value)
ai_assistant.py          xAI Grok integration for natural language
handlers.py              Command handlers
ai_handlers.py           AI message + trade confirmation handlers
algo_handlers.py         Algo trading command handlers
```

### Data flow

**Copy-trading:**
```
Leader trades (Data API)  ──►  CopyEngine  ──►  Risk Checks  ──►  CLOB order
```

**Algo-trading:**
```
Market Data  ──►  Strategy.analyze()  ──►  Signal  ──►  Risk Checks  ──►  CLOB order
```

**Risk checks (both engines):**
- Daily loss limit
- Max open positions
- Max per market
- Slippage cap
- Pause state
```

## Prerequisites

| Requirement | Where to get it |
|---|---|
| Python 3.10+ | python.org |
| Telegram Bot Token | [@BotFather](https://t.me/BotFather) |
| **xAI API Key** (for AI chat) | [xAI Console](https://console.x.ai/team/default/api-keys) |
| Polymarket Builder creds | [Polymarket Builder docs](https://docs.polymarket.com/developers/builders/relayer-client) |
| USDC on any supported chain | For funding proxy wallets |

## Quick start

```bash
# 1. Clone / enter the project
cd "telegram bot"

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux

# Then edit .env:
#   TELEGRAM_BOT_TOKEN=<from BotFather>
#   ENCRYPTION_KEY=<run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
#   XAI_API_KEY=<from https://console.x.ai/team/default/api-keys>  (for AI chat features)
#   BUILDER_API_KEY / BUILDER_SECRET / BUILDER_PASS_PHRASE  (from Polymarket)

# 5. Run
python main.py

# Or deploy to the cloud (see DEPLOYMENT.md)
```

## ☁️ Cloud Deployment

Deploy Lama to run 24/7 in the cloud:

```bash
# Railway (easiest, free tier)
railway up

# Docker (any VPS)
docker-compose up -d

# Render.com (1-click)
# See DEPLOYMENT.md for full guide
```

**See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed cloud deployment instructions.**
```

## How to use

### 🦙 Talk to Lama (Natural Language)

Just **chat naturally**! Lama understands what you want:

- "show me my status"
- "buy 20 dollars on Yes for Trump wins"
- "start following 0x1234..."
- "enable algo trading"
- "use momentum strategy"
- "pause trading"

All manual trades require confirmation with ✅/❌ buttons.

### 📱 Telegram commands

| Command | Description |
|---|---|
| `/start` | Show welcome + setup steps |
| `/create_wallet` | Generate a new owner EOA |
| `/setup_proxy` | Deploy Gnosis Safe proxy via Polymarket relayer |
| `/set_proxy <addr>` | Manually set proxy address (fallback) |
| `/deposit` | Get bridge deposit addresses for proxy wallet |
| `/connect` | Derive CLOB L2 API credentials |
| `/follow <addr>` | Start copying a leader's trades |
| `/unfollow <addr>` | Stop copying a leader |
| `/leaders` | List followed leaders |
| `/pause` | Pause all trading (copy + algo) |
| `/resume` | Resume all trading |
| `/status` | Account overview + risk limits + algo status |
| `/history` | Recent trade log |
| `/enable_algo` | Enable algorithmic trading |
| `/disable_algo` | Disable algorithmic trading |
| `/algo_status` | Show algo settings and strategy |
| `/set_strategy <name>` | Change algo strategy (momentum/mean_reversion/value) |

## User flow

```
/create_wallet  →  EOA generated (private key encrypted at rest)
       │
/setup_proxy    →  Gnosis Safe deployed via relayer (gas-free)
       │
/deposit        →  Bridge deposit addresses shown; user sends USDC
       │
/connect        →  L2 API creds derived from L1 signer
       │
       ├──→ /follow <addr>    → Copy engine mirrors leader trades
       │
       └──→ /enable_algo      → Algo engine runs automated strategies
```

## Configuration reference

All values live in `.env`. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *required* | Bot token from BotFather |
| `ENCRYPTION_KEY` | *required* | Fernet key for encrypting secrets at rest |
| `XAI_API_KEY` | | xAI Grok API key (enables AI chat features) |
| `BUILDER_API_KEY` | | Polymarket builder API key |
| `BUILDER_SECRET` | | Polymarket builder secret |
| `BUILDER_PASS_PHRASE` | | Polymarket builder passphrase |
| `CHAIN_ID` | 137 | Polygon mainnet |
| `RELAYER_URL` | https://relayer-v2.polymarket.com | Relayer endpoint |
| `CLOB_URL` | https://clob.polymarket.com | CLOB endpoint |
| `DATA_API_URL` | https://data-api.polymarket.com | Data API endpoint |
| `BRIDGE_URL` | https://bridge.polymarket.com | Bridge endpoint |
| `POLL_INTERVAL_SECONDS` | 30 | Seconds between leader-trade polls |
| `DEFAULT_ORDER_SIZE_USDC` | 10.0 | Default fixed order size |
| `DEFAULT_MAX_SLIPPAGE` | 0.02 | 2 % max slippage |
| `DEFAULT_MAX_DAILY_LOSS` | 100.0 | USD daily loss ceiling |
| `DEFAULT_MAX_PER_MARKET` | 50.0 | USD max exposure per market |
| `DEFAULT_MAX_OPEN_POSITIONS` | 10 | Max concurrent markets |

## Security

* Private keys and API credentials are **Fernet-encrypted** before being
  written to SQLite.  The encryption key itself must be kept safe in `.env`.
* Keys are **never** echoed in Telegram messages or written to log files.
* `.env` and `*.db` are `.gitignore`-d by default.

## Logging

* `bot.log` – rotating file log (INFO level)
* `trade_log` table in `polybot.db` – every copy-trade attempt with full
  context (timestamp, user, leader, condition, side, size, price, order id,
  status, error)

## License

MIT
