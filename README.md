# Lama

Lama is a Telegram bot that trades on Polymarket for you. You just chat with it like a normal person and it does the rest.

## What is it

Lama is your trading buddy on Telegram. You talk to it in plain English and it understands what you want. Want to place a bet? Just say "buy 20 dollars on Yes for Trump wins" and it will ask you to confirm before doing anything. It is powered by Grok AI from xAI so it actually understands what you mean.

It can do three things for you. First it can copy other traders. You give it the wallet address of someone who is good at trading and Lama watches them and copies their trades into your account automatically. Second it can trade on its own using built in strategies like momentum trading. Third you can just tell it what to trade by chatting with it.

It has safety limits built in so you dont lose more than you are comfortable with. It tracks how much you lose per day, how many trades you have open, and how much you have in each market. If something hits a limit it stops trading.

Lama remembers your conversations. If you told it something earlier it knows about it next time you message it.

Every user gets their own wallet. Your private keys are encrypted and never shown to anyone. Not even in the chat.

## How it works

When you first start you go through these steps one by one

1. Create a wallet. Lama makes you an Ethereum wallet
2. Set up your Polymarket proxy wallet. This is where your money lives
3. Deposit money into your proxy wallet using the Polymarket bridge
4. Connect to the trading system so Lama can place trades for you

After that you can start copy trading or algo trading or just chat with Lama to place trades yourself.

## Files in this project

- main.py is where the bot starts
- config.py loads your settings
- database.py stores all user data, trades, and chat history
- wallet_manager.py creates wallets and encrypts your keys
- polymarket_api.py talks to Polymarket to place trades and get data
- copy_engine.py runs in the background watching and copying leader trades
- algo_engine.py runs in the background doing automated trading strategies
- algo_strategies.py has the actual trading strategies like momentum and mean reversion
- ai_assistant.py connects to Grok AI so Lama can understand what you say
- handlers.py handles all the slash commands like /start and /status
- ai_handlers.py handles when you just type a message instead of a command
- algo_handlers.py handles the algo trading commands

## What you need before you start

- Python 3.10 or newer from python.org
- A Telegram bot token from BotFather on Telegram
- An xAI API key from console.x.ai so the AI chat works
- Polymarket builder credentials from their developer docs
- Some USDC to fund your trading wallet

## How to run it

Open a terminal in the project folder and run these commands one at a time

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Copy the example env file and fill in your keys

    copy .env.example .env

Open the .env file and put in your Telegram bot token, encryption key, xAI key, and Polymarket credentials. Then run it

    python main.py

To generate an encryption key run this

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

## Commands you can use in Telegram

- /start shows the welcome message and what to do
- /create_wallet makes you a new wallet
- /setup_proxy sets up your Polymarket trading wallet
- /deposit shows you where to send money
- /connect hooks up the trading system
- /follow and then a wallet address to copy someone
- /unfollow and then a wallet address to stop copying someone
- /leaders shows who you are copying
- /pause stops all trading
- /resume starts trading again
- /status shows your account info
- /history shows your recent trades
- /enable_algo turns on automatic trading
- /disable_algo turns off automatic trading
- /algo_status shows your algo trading settings
- /set_strategy and then a name to change your trading strategy

Or you can just type whatever you want in plain English and Lama will figure it out.

## Cloud deployment

This bot is set up to run on Railway. Push the code to GitHub and connect it to Railway. Add your environment variables in the Railway dashboard. Attach a volume mounted at /app/data and set DATABASE_PATH to /app/data/polybot.db so your data sticks around between deploys.

There is also a Dockerfile if you want to run it on any server with Docker.

## Security

Your private keys and API credentials are encrypted before they are stored. They are never shown in Telegram messages or written to log files. The .env file and database file are excluded from git so they dont get uploaded anywhere.

## License

MIT
