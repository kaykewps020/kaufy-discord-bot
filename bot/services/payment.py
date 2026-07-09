"""Payment service - automatic crypto payment verification and plan activation."""
import asyncio
import json
import logging
import time
import secrets
import hmac
import hashlib
from typing import Optional, Dict, List
from bot.config import Config
from bot.models.user_db import UserDatabase

logger = logging.getLogger("kaufy.payment")

class PaymentService:
    """Auto-verifies crypto payments and activates plans without manual approval.

    Flow:
    1. User clicks plan → payment_id + addresses generated
    2. User sends crypto with payment_id in memo
    3. Background poll checks for confirmations every 60s
    4. On confirmation → plan auto-activated, channels renamed
    5. User notified via DM and #plans channel

    Production: plug in Blockchair/Etherscan/Solscan API keys.
    Demo mode: auto-activates after 120s for testing.
    """

    def __init__(self, demo_mode: bool = True, demo_delay: int = 120):
        self._poll_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, dict] = {}
        self.demo_mode = demo_mode
        self.demo_delay = demo_delay  # seconds until auto-activate in demo
        self._on_plan_activated = None  # callback: async def(user_id, plan_id) -> None
        self._on_role_update = None     # callback: async def(user_id) -> None

    def set_plan_activated_callback(self, callback):
        """Register a callback for when a plan is activated.
        
        callback signature: async def callback(user_id: int, plan_id: str) -> None
        """
        self._on_plan_activated = callback

    def set_role_update_callback(self, callback):
        """Register a callback for when roles should be re-evaluated.
        
        callback signature: async def callback(user_id: int) -> None
        """
        self._on_role_update = callback

    async def create_payment(self, user_id: int, plan_id: str, channel_id: int = 0) -> dict:
        """Create a payment record. Returns payment info with addresses."""
        plan = Config.PLANS.get(plan_id)
        if not plan:
            return {"error": "Invalid plan"}

        payment_id = secrets.token_hex(12).upper()
        db = UserDatabase(user_id)
        await db.init()

        now = int(time.time())
        info = {
            "payment_id": payment_id,
            "user_id": user_id,
            "plan_id": plan_id,
            "amount_usd": plan["price_usd"],
            "amount_crypto": {},  # filled per-currency below
            "status": "pending",
            "created_at": now,
            "expires_at": now + 86400,
            "channel_id": channel_id,
            "crypto_addresses": plan.get("crypto_addresses", {}),
            "confirmations": 0,
            "tx_hash": "",
        }

        await db.set_config(f"payment_{payment_id}", json.dumps(info))
        self._pending[payment_id] = info

        logger.info(f"Payment created: {payment_id} | user={user_id} | plan={plan_id} | ${plan['price_usd']}")
        return info

    async def simulate_confirmation(self, payment_id: str) -> bool:
        """Simulate blockchain confirmation (demo mode).

        In production, this would call:
        - Blockchair API: https://api.blockchair.com/{coin}/dashboards/transaction/{tx_hash}
        - Etherscan API: https://api.etherscan.io/api?module=transaction&action=gettxreceiptstatus
        - Solscan API: https://api.solscan.io/transaction/{tx_hash}
        """
        info = self._pending.get(payment_id)
        if not info or info["status"] != "pending":
            return False

        age = time.time() - info["created_at"]
        if age < self.demo_delay:
            return False  # Not enough time simulated

        # Auto-confirm the payment
        info["status"] = "confirmed"
        info["confirmations"] = 6
        info["tx_hash"] = f"demo_{secrets.token_hex(16)}"
        self._pending[payment_id] = info

        # Activate the plan
        await self._activate_plan(info)
        return True

    async def _activate_plan(self, info: dict):
        """Activate plan, rename channels, notify user."""
        user_id = info["user_id"]
        plan_id = info["plan_id"]
        plan = Config.PLANS.get(plan_id)
        if not plan:
            return

        db = UserDatabase(user_id)
        await db.init()

        expires = int(time.time()) + (plan["duration_days"] * 86400)
        await db.set_config("plan", plan_id)
        await db.set_config("plan_expires", str(expires))
        await db.set_config(f"payment_{info['payment_id']}_status", "confirmed")

        # Save as token record
        await db.save_token("crypto_payment", json.dumps({
            "payment_id": info["payment_id"],
            "plan": plan_id,
            "amount": plan["price_usd"],
            "time": int(time.time()),
        }))

        logger.info(f"Plan auto-activated: user={user_id} plan={plan_id} expires={expires}")

        # Notify channel manager to hide #plans channel
        if self._on_plan_activated:
            try:
                await self._on_plan_activated(user_id, plan_id)
            except Exception as e:
                logger.warning(f"Plan activated callback error: {e}")

        # Assign Discord roles
        if self._on_role_update:
            try:
                await self._on_role_update(user_id)
            except Exception as e:
                logger.warning(f"Role update callback error: {e}")

    async def poll_pending(self) -> int:
        """Check all pending payments. Returns newly confirmed count."""
        confirmed = 0
        expired = []

        for pid, info in list(self._pending.items()):
            if info["status"] != "pending":
                continue

            if time.time() > info["expires_at"]:
                expired.append(pid)
                continue

            if self.demo_mode:
                ok = await self.simulate_confirmation(pid)
                if ok:
                    confirmed += 1

        for pid in expired:
            info = self._pending.pop(pid, None)
            if info:
                db = UserDatabase(info["user_id"])
                await db.init()
                await db.set_config(f"payment_{pid}_status", "expired")
                logger.info(f"Payment expired: {pid}")

        return confirmed

    async def get_payment(self, payment_id: str) -> Optional[dict]:
        """Get payment info."""
        return self._pending.get(payment_id)

    async def get_user_plan(self, user_id: int) -> str:
        """Get current plan for user. Detects expiry and reverts to free."""
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        expires = await db.get_config("plan_expires") or "0"
        exp_int = int(expires)

        # Check if plan expired
        if exp_int > 0 and time.time() > exp_int:
            if plan != "free":
                await db.set_config("plan", "free")
                await db.set_config("plan_expires", "0")
                logger.info(f"Plan expired for user {user_id}: was {plan}")
                # Notify channel manager to show #plans again
                if self._on_plan_activated:
                    try:
                        await self._on_plan_activated(user_id, "free")
                    except Exception as e:
                        logger.warning(f"Plan expired callback error: {e}")
                return "free"

        return plan

    async def auto_poll_loop(self, interval: int = 60):
        """Background poll every 60s for pending payments."""
        logger.info(f"Payment poll started (demo={self.demo_mode}, delay={self.demo_delay}s)")
        while True:
            await asyncio.sleep(interval)
            try:
                count = await self.poll_pending()
                if count:
                    logger.info(f"Auto-confirmed {count} new payment(s)")
            except Exception as e:
                logger.error(f"Payment poll error: {e}")

    def start_background_polling(self, interval: int = 60):
        loop = asyncio.get_event_loop()
        self._poll_task = loop.create_task(self.auto_poll_loop(interval))

    def stop_background_polling(self):
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None


# Singleton
payment_service = PaymentService(demo_mode=True, demo_delay=120)
