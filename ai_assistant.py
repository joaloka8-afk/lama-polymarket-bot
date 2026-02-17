"""AI assistant using xAI Grok for natural language understanding and trade intent extraction."""

import json
import logging
import os
import re

from xai_sdk import Client
from xai_sdk.chat import system, user, assistant

logger = logging.getLogger(__name__)


class GrokAssistant:
    """Grok-powered AI assistant that understands trading commands and user intent."""

    def __init__(self, api_key: str):
        self.client = Client(api_key=api_key, timeout=60)
        self.model = "grok-4-1-fast-reasoning"

    async def understand_intent(
        self, message: str, user_context: dict, chat_history: list[dict] | None = None
    ) -> dict:
        """Analyze user message and extract intent + parameters.

        chat_history is a list of {"role": "user"|"assistant", "message": "..."}.

        Returns dict with:
        - intent: str (command name or "chat")
        - params: dict
        - response: str (AI response to user)
        - requires_confirmation: bool
        """

        system_prompt = f"""You are Lama. You talk like a real person - casual, direct, no fluff. You're a friend who happens to be great at Polymarket trading. You remember everything the user has told you in this conversation.

Rules for how you talk:
- NEVER start with "Hey, I'm Lama" or introduce yourself unless someone literally asks who you are
- Keep it short and natural. No walls of text
- Use casual language like you're texting a friend
- Don't be overly enthusiastic or use corporate-speak
- Skip filler like "Sure!", "Of course!", "Great question!"
- Be confident and straight to the point
- Use slang naturally but don't force it
- If something goes wrong, be honest about it, don't sugarcoat
- Reference things the user told you before when relevant - you have memory

User context:
- Has wallet: {user_context.get('has_wallet', False)}
- Proxy deployed: {user_context.get('has_proxy', False)}
- Trading enabled: {user_context.get('trading_enabled', False)}
- Is paused: {user_context.get('is_paused', False)}
- Following leaders: {user_context.get('leader_count', 0)}

Analyze the user's message and determine their intent. Respond with JSON ONLY:

{{
  "intent": "create_wallet" | "setup_proxy" | "deposit" | "connect" | "follow" | "unfollow" | "leaders" | "pause" | "resume" | "status" | "history" | "trade" | "enable_algo" | "disable_algo" | "algo_status" | "set_strategy" | "chat",
  "params": {{}},
  "response": "Your response - keep it human and casual",
  "requires_confirmation": true/false
}}

For "trade" intent, extract:
- market: market description or condition ID
- side: "BUY" or "SELL"
- outcome: outcome to bet on
- amount: USDC amount

Examples:
"buy 20 dollars on Yes for Trump wins" -> {{"intent": "trade", "params": {{"side": "BUY", "outcome": "Yes", "amount": 20, "market": "Trump wins"}}, "response": "$20 on Yes for Trump wins - confirm?", "requires_confirmation": true}}
"show my status" -> {{"intent": "status", "params": {{}}, "response": "pulling it up", "requires_confirmation": false}}
"start following 0x123..." -> {{"intent": "follow", "params": {{"leader": "0x123..."}}, "response": "on it, adding them now", "requires_confirmation": false}}
"enable algo trading" -> {{"intent": "enable_algo", "params": {{}}, "response": "turning on algo trading for you", "requires_confirmation": false}}
"use momentum strategy" -> {{"intent": "set_strategy", "params": {{"strategy": "momentum"}}, "response": "switched to momentum", "requires_confirmation": false}}
"what's my algo status?" -> {{"intent": "algo_status", "params": {{}}, "response": "let me check", "requires_confirmation": false}}

If asked who you are, just say your name is Lama. Don't make it a whole speech.
"""

        chat = self.client.chat.create(model=self.model)
        chat.append(system(system_prompt))

        # Feed conversation history so Lama remembers past messages
        if chat_history:
            for msg in chat_history:
                if msg["role"] == "user":
                    chat.append(user(msg["message"]))
                elif msg["role"] == "assistant":
                    chat.append(assistant(msg["message"]))

        # Current message
        chat.append(user(message))

        try:
            response = chat.sample()
            content = response.content.strip()

            # Extract JSON from markdown code blocks if present
            if "```json" in content:
                match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
                if match:
                    content = match.group(1)
            elif "```" in content:
                match = re.search(r"```\s*(\{.*?\})\s*```", content, re.DOTALL)
                if match:
                    content = match.group(1)

            result = json.loads(content)
            return result

        except Exception as exc:
            logger.error("Grok intent parsing failed: %s", exc)
            return {
                "intent": "chat",
                "params": {},
                "response": "I'm having trouble understanding. Could you try rephrasing?",
                "requires_confirmation": False,
            }

    async def generate_trade_summary(self, trade_params: dict) -> str:
        """Generate a simple trade summary for confirmation."""
        side = trade_params.get("side", "BUY")
        outcome = trade_params.get("outcome", "")
        amount = trade_params.get("amount", 0)
        market = trade_params.get("market", "unknown market")

        return (
            f"Just to make sure, here's what I'll do:\n"
            f"\n"
            f"Market: {market}\n"
            f"Bet on: {side} {outcome}\n"
            f"Amount: ${amount:.2f}\n"
            f"\n"
            f"Want me to go ahead?"
        )
