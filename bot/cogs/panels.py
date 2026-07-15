"""UI panels with pure SVG design, crypto plans, and gift system."""
import discord
import io
import json
import time
import math
import secrets
from typing import Optional
from bot.config import Config
from bot.models.session import session_manager
from bot.models.user_db import UserDatabase
from bot.services.payment import payment_service

# ═══════════════════════════════════════════════════════════════
# CUSTOM EMOJI IDs (uploaded to Kaufy's Hall server)
# ═══════════════════════════════════════════════════════════════
EMOJI_MONEY   = "<:money:1523839590555975772>"
EMOJI_REDCARD = "<a:redcard:1523839574298857522>"
EMOJI_BOOSTER = "<:booster:1523839571690000435>"
EMOJI_BOTS    = "<:bots:1523839575905140756>"
EMOJI_OWNER   = "<:ownership:1523839569211035708>"
EMOJI_CART    = "<:purpletrolley:1523839584041959656>"
EMOJI_PAYPAL  = "<:paypal:1523839579273170954>"
EMOJI_LTC     = "<:ltc:1523839577469620275>"
EMOJI_ROBUX   = "<:robux:1523839582016114699>"
EMOJI_NITRO   = "<:opal_nitro:1523839675075137657>"
EMOJI_MANAGER = "<:mannger:1523839586059681802>"
EMOJI_SHOP    = "<:shopmedewerker:1523839588722937927>"
EMOJI_CREDIT  = "<:credit:1524607269201907734>"

# ═══════════════════════════════════════════════════════════════
# SVG GENERATORS (clean text, no emoji — emojis go in message text)
# ═══════════════════════════════════════════════════════════════

def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in Discord channel/category names."""
    return name.lower().replace(" ", "-")[:30]

def svg_usage_bar(current: int, max_msgs: int, plan: str = "free", daily: int = 0, daily_limit: int = 10) -> str:
    """Usage stats SVG with progress bar."""
    is_daily = daily_limit < 999999
    display_current = daily if is_daily else current
    display_max = daily_limit if is_daily else (max_msgs if max_msgs < 999999 else 0)
    label = "Today" if is_daily else "Total"
    pct = min((display_current / display_max) * 100, 100) if display_max else 0
    bar_color = "#22c55e" if pct < 60 else "#eab308" if pct < 85 else "#ef4444"
    plan_color = {"free": "#6b7280", "7d": "#3b82f6", "14d": "#8b5cf6", "30d": "#a855f7", "lifetime": "#f59e0b"}
    pc = plan_color.get(plan, "#6b7280")

    if is_daily:
        usage_text = f"{display_current} / {display_max} today"
    elif display_max == 0:
        usage_text = "Unlimited"
    else:
        usage_text = f"{display_current} / {display_max} total"

    plan_badge = plan.upper()

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="140" viewBox="0 0 420 140">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1a2e"/><stop offset="1" stop-color="#16213e"/></linearGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{bar_color}"/><stop offset="1" stop-color="{bar_color}" stop-opacity="0.7"/></linearGradient>
  </defs>
  <rect width="420" height="140" rx="14" fill="url(#bg)" stroke="#2a2a4e" stroke-width="1"/>
  <text x="20" y="30" font-family="system-ui, sans-serif" font-size="13" font-weight="600" fill="#e0e0e0">{label} Message Usage</text>
  <rect x="20" y="50" width="380" height="22" rx="11" fill="#2a2a4e" stroke="#3a3a5e" stroke-width="0.5"/>
  {f'<rect x="20" y="50" width="{380 * pct / 100}" height="22" rx="11" fill="url(#bar)" opacity="0.9"/>' if display_max > 0 else ''}
  <text x="210" y="64" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="middle">{usage_text}</text>
  <text x="20" y="98" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Plan: {plan}</text>
  <text x="20" y="118" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">{pct:.0f}% used</text>
  <rect x="340" y="84" width="60" height="28" rx="8" fill="{pc}" opacity="0.2"/>
  <text x="370" y="103" font-family="system-ui, sans-serif" font-size="11" font-weight="600" fill="{pc}" text-anchor="middle">{plan_badge}</text>
  <text x="400" y="130" font-family="system-ui, sans-serif" font-size="9" fill="#4b5563" text-anchor="end">Kaufy Hall</text>
</svg>'''


def _plan_features_list(plan: dict, plan_id: str) -> list:
    """Get list of feature strings for a plan."""
    features = []

    # Daily messages
    daily = plan.get("daily_messages", 999999)
    if daily < 999999:
        features.append(f"📝 {daily} msgs/day")
    else:
        features.append("♾️ Unlimited messages")

    # Context
    ctx = plan.get("context_messages", 10)
    features.append(f"🧠 {ctx} msg context")

    # Tokens
    toks = plan.get("max_tokens_allowed", 4096)
    features.append(f"📏 {toks} max tokens")

    # Thinking
    features.append(f"💭 Thinking: {'✅' if plan.get('thinking') else '❌'}")

    # Web search
    features.append(f"🌐 Web Search: {'✅' if plan.get('web_search') else '❌'}")

    # Priority
    features.append(f"⚡ Priority: {'✅' if plan.get('priority_queue') else '❌'}")

    # File storage
    if plan.get("file_storage"):
        mb = plan.get("file_storage_mb", 100)
        features.append(f"💾 {mb}MB Storage: ✅")
    else:
        features.append(f"💾 Storage: ❌")

    # Custom prompt
    features.append(f"✏️ Custom Prompt: {'✅' if plan.get('custom_prompt') else '❌'}")

    # Export
    features.append(f"📥 Export Chat: {'✅' if plan.get('export_chat') else '❌'}")

    # API
    features.append(f"🔌 API Access: {'✅' if plan.get('api_access') else '❌'}")

    # Support
    features.append(f"💎 Premium Support: {'✅' if plan.get('premium_support') else '❌'}")

    return features


def svg_plan_card(plan_id: str, plan: dict, active: bool = False) -> str:
    """Single plan card SVG with full feature list."""
    border = "#6366f1" if active else "#2a2a4e"
    glow_effect = "0 0 25px rgba(99,102,241,0.4)" if active else "none"
    price = f"${plan['price_usd']:.2f}" if plan['price_usd'] > 0 else "Free"
    duration = plan['description']
    badge = plan.get('badge', plan_id.upper())
    color = plan.get('color', '#6366f1')

    features = _plan_features_list(plan, plan_id)
    # Highlight active plan
    if active:
        features.insert(0, "⭐ **YOUR CURRENT PLAN**")

    feat_lines = ""
    for i, f in enumerate(features):
        y = 95 + i * 20
        feat_lines += f'<text x="25" y="{y}" font-family="system-ui, sans-serif" font-size="10.5" fill="{color if active else "#9ca3af"}">{f}</text>'

    y_offset = 95 + len(features) * 20 + 25

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="380" height="{y_offset + 10}" viewBox="0 0 380 {y_offset + 10}">
  <defs>
    <linearGradient id="bgc_{plan_id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1a2e"/><stop offset="1" stop-color="#16213e"/></linearGradient>
    {f'<linearGradient id="act_{plan_id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{color}" stop-opacity="0.2"/><stop offset="1" stop-color="{color}" stop-opacity="0.05"/></linearGradient>' if active else ''}
  </defs>
  <rect x="1" y="1" width="378" height="{y_offset + 8}" rx="14" fill="{'url(#act_' + plan_id + ')' if active else 'url(#bgc_' + plan_id + ')'}" stroke="{border}" stroke-width="1.5" filter="{glow_effect}"/>
  
  <!-- Badge -->
  <rect x="25" y="15" width="{len(badge) * 8 + 16}" height="18" rx="4" fill="{color}" opacity="0.2"/>
  <text x="33" y="28" font-family="system-ui, sans-serif" font-size="9" font-weight="600" fill="{color}">{badge}</text>
  
  <text x="25" y="55" font-family="system-ui, sans-serif" font-size="16" font-weight="700" fill="#e0e0e0">{duration}</text>
  <text x="355" y="55" font-family="system-ui, sans-serif" font-size="22" font-weight="800" fill="{color}" text-anchor="end">{price}</text>
  {f'<text x="355" y="70" font-family="system-ui, sans-serif" font-size="9" fill="#6b7280" text-anchor="end">one-time</text>' if plan_id == "lifetime" else ''}
  <line x1="25" y1="78" x2="355" y2="78" stroke="#2a2a4e" stroke-width="1"/>
  {feat_lines}
  {f'<rect x="25" y="{y_offset - 5}" width="330" height="32" rx="8" fill="{color}" opacity="0.15"/><text x="190" y="{y_offset + 15}" font-family="system-ui, sans-serif" font-size="12" font-weight="600" fill="{color}" text-anchor="middle">✅ ACTIVE</text>' if active else ''}
</svg>'''


def svg_plans_grid(active_plan: str = "free") -> str:
    """Grid of all plans as a single SVG."""
    cards = ""
    y = 0
    idx = 0
    for pid, pdata in Config.PLANS.items():
        is_active = pid == active_plan
        card_svg = svg_plan_card(pid, pdata, is_active)
        cards += f'<g transform="translate(0, {y})">{card_svg}</g>'
        # Calculate height dynamically - approx 95 + len(features)*20 + 35
        feat_count = len(_plan_features_list(pdata, pid)) + (1 if is_active else 0)
        y += 95 + feat_count * 20 + 45
        idx += 1

    total_h = y + 30
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{total_h}" viewBox="0 0 420 {total_h}">
  <defs>
    <linearGradient id="gridbg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0a0a1a"/><stop offset="1" stop-color="#0f172a"/></linearGradient>
  </defs>
  <rect width="420" height="{total_h}" rx="16" fill="url(#gridbg)"/>
  <text x="210" y="30" font-family="system-ui, sans-serif" font-size="18" font-weight="700" fill="#e0e0e0" text-anchor="middle">💎 Available Plans</text>
  <text x="210" y="50" font-family="system-ui, sans-serif" font-size="10" fill="#6b7280" text-anchor="middle">Crypto payment — BTC / ETH / USDT / SOL</text>
  {cards}
  <text x="210" y="{total_h - 10}" font-family="system-ui, sans-serif" font-size="9" fill="#4b5563" text-anchor="middle">All plans include 100% uncensored AI • Kaufy's Hall</text>
</svg>'''


def svg_crypto_panel(plan_id: str, plan: dict) -> str:
    """Payment panel showing crypto addresses for a plan."""
    y = 85
    items = ""
    for coin, addr in plan.get("crypto_addresses", {}).items():
        coin_name = Config.CRYPTO_NAMES.get(coin, coin.upper())
        items += f'''
  <text x="25" y="{y}" font-family="monospace" font-size="11" fill="#818cf8" font-weight="600">{coin_name}</text>
  <text x="25" y="{y + 18}" font-family="monospace" font-size="10" fill="#9ca3af">{addr}</text>
  <line x1="25" y1="{y + 30}" x2="395" y2="{y + 30}" stroke="#1e293b" stroke-width="1"/>'''
        y += 50

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{y + 40}" viewBox="0 0 420 {y + 40}">
  <defs>
    <linearGradient id="bgp" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0f172a"/><stop offset="1" stop-color="#1e293b"/></linearGradient>
  </defs>
  <rect width="420" height="{y + 40}" rx="16" fill="url(#bgp)" stroke="#334155" stroke-width="1"/>
  <text x="210" y="35" font-family="system-ui, sans-serif" font-size="16" font-weight="700" fill="#e0e0e0" text-anchor="middle">{plan["description"]}</text>
  <text x="210" y="58" font-family="system-ui, sans-serif" font-size="20" font-weight="700" fill="#22c55e" text-anchor="middle">${plan["price_usd"]:.2f}</text>
  <line x1="25" y1="72" x2="395" y2="72" stroke="#1e293b" stroke-width="1"/>
  {items}
  <text x="210" y="{y + 15}" font-family="system-ui, sans-serif" font-size="10" fill="#6b7280" text-anchor="middle">
    Send exact amount. Auto-activates on confirmation.
  </text>
</svg>'''


def svg_gift_card(plan_id: str, plan: dict, gift_code: str = "") -> str:
    """Gift purchase confirmation SVG."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" viewBox="0 0 400 200">
  <defs>
    <linearGradient id="bgg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0f172a"/><stop offset="1" stop-color="#1e293b"/></linearGradient>
  </defs>
  <rect width="400" height="200" rx="16" fill="url(#bgg)" stroke="#6366f1" stroke-width="1.5"/>
  <text x="200" y="40" font-family="system-ui, sans-serif" font-size="16" font-weight="700" fill="#e0e0e0" text-anchor="middle">Gift Purchase</text>
  <text x="200" y="65" font-family="system-ui, sans-serif" font-size="13" fill="#9ca3af" text-anchor="middle">{plan["description"]}</text>
  <text x="200" y="95" font-family="system-ui, sans-serif" font-size="24" font-weight="700" fill="#22c55e" text-anchor="middle">${plan["price_usd"]:.2f}</text>
  <text x="200" y="125" font-family="system-ui, sans-serif" font-size="11" fill="#818cf8" text-anchor="middle">Gift Code: {gift_code}</text>
  <text x="200" y="155" font-family="system-ui, sans-serif" font-size="10" fill="#6b7280" text-anchor="middle">Send this code to the person you are gifting</text>
  <text x="200" y="180" font-family="system-ui, sans-serif" font-size="9" fill="#4b5563" text-anchor="middle">They redeem it in their config panel</text>
</svg>'''


def svg_config_panel(temperature: float, max_tokens: int, plan: str, daily: int = 0, daily_limit: int = 10, context: int = 10, plan_config: dict = None) -> str:
    """Config status SVG with model info and plan details."""
    if plan_config is None:
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
    
    is_unlimited = daily_limit >= 999999
    usage_text = f"{daily} today" if not is_unlimited else "Unlimited"
    pct = min((daily / daily_limit) * 100, 100) if daily_limit < 999999 and daily_limit > 0 else 0
    bar_color = "#22c55e" if pct < 60 else "#eab308" if pct < 85 else "#ef4444"
    
    color = plan_config.get('color', '#6b7280')
    thinking_yn = "✅" if plan_config.get('thinking') else "❌"
    search_yn = "✅" if plan_config.get('web_search') else "❌"
    priority_yn = "✅" if plan_config.get('priority_queue') else "❌"
    storage_yn = "✅" if plan_config.get('file_storage') else "❌"
    custom_yn = "✅" if plan_config.get('custom_prompt') else "❌"

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="270" viewBox="0 0 420 270">
  <defs>
    <linearGradient id="bgcf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1a2e"/><stop offset="1" stop-color="#16213e"/></linearGradient>
    <linearGradient id="bar_grad" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{bar_color}"/><stop offset="1" stop-color="{bar_color}" stop-opacity="0.7"/></linearGradient>
  </defs>
  <rect width="420" height="270" rx="14" fill="url(#bgcf)" stroke="#2a2a4e" stroke-width="1"/>
  
  <!-- Header -->
  <text x="25" y="28" font-family="system-ui, sans-serif" font-size="15" font-weight="600" fill="#e0e0e0">⚙️ Configuration</text>
  <rect x="340" y="15" width="60" height="18" rx="4" fill="{color}" opacity="0.2"/>
  <text x="370" y="28" font-family="system-ui, sans-serif" font-size="9" font-weight="600" fill="{color}" text-anchor="middle">{plan.upper()}</text>
  
  <line x1="25" y1="40" x2="395" y2="40" stroke="#2a2a4e" stroke-width="1"/>
  
  <!-- Usage bar -->
  <text x="25" y="62" font-family="system-ui, sans-serif" font-size="10" fill="#9ca3af">Usage Today</text>
  <text x="395" y="62" font-family="system-ui, sans-serif" font-size="10" fill="#e0e0e0" text-anchor="end">{usage_text}</text>
  <rect x="25" y="70" width="370" height="8" rx="4" fill="#2a2a4e"/>
  {f'<rect x="25" y="70" width="{370 * pct / 100 if is_unlimited else 0}" height="8" rx="4" fill="url(#bar_grad)"/>' if not is_unlimited else ''}
  
  <line x1="25" y1="92" x2="395" y2="92" stroke="#2a2a4e" stroke-width="0.5"/>
  
  <!-- Settings -->
  <text x="25" y="112" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Temperature</text>
  <text x="395" y="112" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{temperature}</text>
  <text x="25" y="132" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Max Tokens</text>
  <text x="395" y="132" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{max_tokens}</text>
  <text x="25" y="152" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Context Memory</text>
  <text x="395" y="152" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{context} msgs</text>
  
  <line x1="25" y1="165" x2="395" y2="165" stroke="#2a2a4e" stroke-width="0.5"/>
  
  <!-- Features -->
  <text x="25" y="185" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">💭 Thinking Mode</text>
  <text x="395" y="185" font-family="system-ui, sans-serif" font-size="11" fill="{color}" text-anchor="end">{thinking_yn}</text>
  <text x="25" y="205" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">🌐 Web Search</text>
  <text x="395" y="205" font-family="system-ui, sans-serif" font-size="11" fill="{color}" text-anchor="end">{search_yn}</text>
  <text x="25" y="225" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">⚡ Priority Queue</text>
  <text x="395" y="225" font-family="system-ui, sans-serif" font-size="11" fill="{color}" text-anchor="end">{priority_yn}</text>
  <text x="25" y="245" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">💾 File Storage</text>
  <text x="395" y="245" font-family="system-ui, sans-serif" font-size="11" fill="{color}" text-anchor="end">{storage_yn}</text>
  <text x="25" y="262" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">✏️ Custom Prompt</text>
  <text x="395" y="262" font-family="system-ui, sans-serif" font-size="11" fill="{color}" text-anchor="end">{custom_yn}</text>
</svg>'''


def svg_thinking_panel(plan: str) -> str:
    """Thinking mode panel for premium users."""
    locked = plan == "free"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="160" viewBox="0 0 420 160">
  <defs>
    <linearGradient id="bgt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1a2e"/><stop offset="1" stop-color="#16213e"/></linearGradient>
  </defs>
  <rect width="420" height="160" rx="14" fill="url(#bgt)" stroke="#2a2a4e" stroke-width="1"/>
  <text x="210" y="35" font-family="system-ui, sans-serif" font-size="16" font-weight="600" fill="#e0e0e0" text-anchor="middle">Thinking Mode</text>
  <text x="210" y="60" font-family="system-ui, sans-serif" font-size="12" fill="#9ca3af" text-anchor="middle">
    {("Locked - Upgrade your plan" if locked else "Active - AI reasoning is visible")}
  </text>
  {f'<rect x="140" y="80" width="140" height="32" rx="8" fill="#6b7280" opacity="0.3"/><text x="210" y="101" font-family="system-ui, sans-serif" font-size="12" font-weight="600" fill="#6b7280" text-anchor="middle">LOCKED</text>' if locked else f'<rect x="140" y="80" width="140" height="32" rx="8" fill="#22c55e" opacity="0.15"/><text x="210" y="101" font-family="system-ui, sans-serif" font-size="12" font-weight="600" fill="#22c55e" text-anchor="middle">ACTIVE</text>'}
</svg>'''


def svg_divider() -> str:
    """Simple divider SVG."""
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="20" viewBox="0 0 420 20">
  <line x1="20" y1="10" x2="400" y2="10" stroke="#1e293b" stroke-width="1"/>
</svg>'''


# ═══════════════════════════════════════════════════════════════
# DISCORD BUTTON VIEWS (no emoji labels, clean design)
# ═══════════════════════════════════════════════════════════════


class WelcomeView(discord.ui.View):
    """Welcome panel — cria canais do usuário com plan-awareness."""

    IS_OWNER = staticmethod(lambda uid: uid in Config.OWNER_IDS)

    def __init__(self):
        super().__init__(timeout=None)

    async def _get_user_plan(self, user_id: int) -> str:
        """Get plan from DB; owner always gets 'lifetime'."""
        if self.IS_OWNER(user_id):
            return "lifetime"
        from bot.models.user_db import UserDatabase
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        return plan

    async def _ensure_user_channels(self, interaction: discord.Interaction) -> dict:
        """Cria canais do usuário: msg, config, (+ thinking se pago), nunca plans se pago."""
        guild = interaction.guild
        member = interaction.user
        cat_name = f"Kaufy's Chat - {member.display_name}"
        is_owner = self.IS_OWNER(member.id)

        # Owner / paid plan → everything unlocked
        plan = await self._get_user_plan(member.id)
        is_paid = is_owner or plan != "free"

        # Check if category already exists
        category = discord.utils.get(guild.categories, name=cat_name)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_channels=True
            ),
        }

        if not category:
            category = await guild.create_category(
                cat_name, overwrites=overwrites,
                reason=f"Kaufy Chat for {member}"
            )

        channels = {}

        # Always create msg + config
        for base in ["msg", "config"]:
            ch_name = f"{base}-{_sanitize_name(member.display_name)}-{plan}"
            existing = discord.utils.get(category.channels, name=ch_name)
            if not existing:
                existing = await guild.create_text_channel(
                    ch_name, category=category, overwrites=overwrites,
                    reason=f"Kaufy {base} for {member}"
                )
            channels[base] = existing

        # Thinking channel (paid only)
        if is_paid:
            ch_name = f"thinking-{_sanitize_name(member.display_name)}-{plan}"
            existing = discord.utils.get(category.channels, name=ch_name)
            if not existing:
                existing = await guild.create_text_channel(
                    ch_name, category=category, overwrites=overwrites,
                    reason=f"Kaufy thinking for {member}"
                )
            channels["thinking"] = existing

        # Plans channel (free only)
        if not is_paid:
            ch_name = f"plans-{_sanitize_name(member.display_name)}-{plan}"
            existing = discord.utils.get(category.channels, name=ch_name)
            if not existing:
                existing = await guild.create_text_channel(
                    ch_name, category=category, overwrites=overwrites,
                    reason=f"Kaufy plans for {member}"
                )
            channels["plans"] = existing

        return channels, plan

    async def _send_panels(self, channels: dict, plan: str, member: discord.Member):
        """Envia os panels corretos em cada canal."""
        from bot.cogs.panels import get_welcome_panel, get_config_panel, get_plans_panel, get_thinking_panel
        is_owner = self.IS_OWNER(member.id)
        is_paid = is_owner or plan != "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])

        # ── msg: Welcome embed ──
        desc_lines = [
            f"{EMOJI_BOTS} **Kaufy** — your 100%% UNCENSORED AI assistant.\n",
            f"{EMOJI_MANAGER} Ask **anything** — no filters, no refusals, no limits.",
            f"{EMOJI_SHOP} Configure in {channels['config'].mention}",
        ]
        if is_paid:
            desc_lines.append(f"{EMOJI_BOOSTER} **{plan_config['badge']}** plan active — all perks unlocked!")
            if "thinking" in channels:
                desc_lines.append(f"{EMOJI_BOTS} 🧠 See my reasoning in {channels['thinking'].mention}")
        else:
            desc_lines.append(f"{EMOJI_CART} 👉 Check {channels['plans'].mention} to unlock unlimited power")
            desc_lines.append(f"{EMOJI_BOOSTER} 📝 10 free msgs/day • Upgrade for ♾️ unlimited")

        embed = discord.Embed(
            title=f"{EMOJI_BOTS} Welcome to Kaufy Hall",
            description="\n".join(desc_lines),
            color=0x9B59B6
        )
        
        # Build benefits list
        benefits = []
        if is_paid:
            ctx_msgs = plan_config.get("context_messages", 50)
            max_tok = plan_config.get("max_tokens_allowed", 8192)
            benefits.append(f"🧠 **{ctx_msgs} msg** context memory")
            benefits.append(f"📝 **{max_tok} tokens** max response")
            if plan_config.get("thinking"):
                benefits.append(f"💭 **Thinking mode** — see AI reasoning")
            if plan_config.get("web_search"):
                benefits.append(f"🌐 **Web search** — real-time info")
            if plan_config.get("priority_queue"):
                benefits.append(f"⚡ **Priority queue** — skip the line")
            if plan_config.get("file_storage"):
                mb = plan_config.get("file_storage_mb", 100)
                benefits.append(f"💾 **{mb}MB file storage**")
            if plan_config.get("custom_prompt"):
                benefits.append(f"✏️ **Custom system prompt**")
            if plan_config.get("export_chat"):
                benefits.append(f"📥 **Export conversations**")
            if plan_config.get("api_access"):
                benefits.append(f"🔌 **API access**")
            if plan_config.get("premium_support"):
                benefits.append(f"💎 **Premium support**")
            if plan_config.get("early_access"):
                benefits.append(f"🚀 **Early access** to new features")
            benefits.append(f"♾️ **No daily limits**")
            benefits.append(f"📸 **AI screenshot generation**")
            benefits.append(f"🚫 **No censorship** — generate ANYTHING")
        else:
            plan_7d = Config.PLANS.get("7d", {})
            benefits.append(f"📝 10 msgs/day (free)")
            benefits.append(f"🧠 10 msg context")
            benefits.append(f"💭 Thinking mode: ❌")
            benefits.append(f"🌐 Web search: ❌")
            benefits.append(f"⚡ Standard queue")
            benefits.append(f"")
            benefits.append(f"**💎 Upgrade benefits:**")
            benefits.append(f"♾️ Unlimited messages")
            benefits.append(f"🧠 50-100 msg context")
            benefits.append(f"📝 8K-16K tokens")
            benefits.append(f"💭 Visible AI reasoning")
            benefits.append(f"🌐 Web search + priority queue")
            benefits.append(f"💾 File storage + export")
            benefits.append(f"✏️ Custom prompts")
            benefits.append(f"🔌 API access (30d/Lifetime)")
            benefits.append(f"🔞 **100% uncensored — no filters**")

        embed.add_field(
            name=f"{EMOJI_BOOSTER} {'Your Benefits (' + plan_config.get('badge', plan.upper()) + ')' if is_paid else 'Free Plan vs Premium'}",
            value="\n".join(benefits),
            inline=False
        )
        
        # Add payment info for free users
        if not is_paid:
            embed.add_field(
                name=f"{EMOJI_LTC} Payment Methods",
                value=(
                    f"• **Bitcoin** (BTC)\n"
                    f"• **Ethereum** (ETH)\n"
                    f"• **USDT** (ERC-20)\n"
                    f"• **Solana** (SOL)\n"
                    f"• 🎁 Gift cards available\n\n"
                    f"**Starting at just $3.99!**"
                ),
                inline=False
            )

        await channels["msg"].send(embed=embed)

        # ── config: Config panel (global persistent view) ──
        await channels["config"].send(
            embed=discord.Embed(
                title=f"{EMOJI_MANAGER} Configuration Panel",
                description=(
                    f"Adjust your AI experience below.\n\n"
                    f"{EMOJI_BOTS} **Model:** {Config.MODEL}\n"
                    f"{EMOJI_MANAGER} Use the dropdowns below to change settings."
                ),
                color=0x3498DB
            ),
            view=ConfigView()
        )

        # ── thinking: Thinking panel (paid only, global view) ──
        if is_paid and "thinking" in channels:
            ctx_msgs = Config.PLANS.get(plan, {}).get("context_messages", 50)
            await channels["thinking"].send(
                embed=discord.Embed(
                    title=f"{EMOJI_BOTS} Thinking Mode — Active",
                    description=(
                        f"{EMOJI_BOOSTER} **Active** — Kaufy's chain-of-thought reasoning appears here.\n\n"
                        f"When you ask a question in {channels['msg'].mention}, Kaufy will:\n"
                        f"1️⃣ Think through the problem (shown here)\n"
                        f"2️⃣ Deliver the final answer in {channels['msg'].mention}\n\n"
                        f"🧠 Context: last **{ctx_msgs} messages** remembered\n"
                        f"🌐 Web search available via `.search <query>`\n"
                        f"⚡ Priority processing enabled"
                    ),
                    color=0x22C55E
                ),
                view=ThinkingView()
            )

        # ── plans: Plans panel (free only, global view) ──
        if not is_paid and "plans" in channels:
            await channels["plans"].send(
                embed=discord.Embed(
                    title=f"{EMOJI_CART} Plans & Subscription",
                    description=(
                        f"{EMOJI_SHOP} Choose your plan to unlock features.\n\n"
                        f"**Free:**\n"
                        f"• 10 messages/day\n"
                        f"• 10 msg context memory\n"
                        f"• Basic queue\n\n"
                        f"**Paid plans (7d/14d/30d/Lifetime):**\n"
                        f"🚫 **Unlimited messages** — no daily cap\n"
                        f"🧠 **50-100 msg memory** — remembers more context\n"
                        f"📝 **8K-16K tokens** — longer responses\n"
                        f"💭 **Thinking mode** — see Kaufy's reasoning\n"
                        f"🌐 **Web search** — `.search <query>` command\n"
                        f"⚡ **Priority queue** — skip the line\n\n"
                        f"{EMOJI_LTC} Crypto only: BTC / ETH / USDT / SOL\n"
                        f"{EMOJI_BOOSTER} Click a plan below to see payment details."
                    ),
                    color=0x2ECC71
                ),
                view=PlansView()
            )

    @discord.ui.button(label="🚀 Start Chatting", style=discord.ButtonStyle.primary, custom_id="welcome_chat")
    async def start_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channels, plan = await self._ensure_user_channels(interaction)
        await self._send_panels(channels, plan, interaction.user)

        lines = [
            f"✅ **Channels ready!**",
            f"{channels['msg'].mention} — Chat with Kaufy",
            f"{channels['config'].mention} — Settings",
        ]
        if "thinking" in channels:
            lines.append(f"{channels['thinking'].mention} — Visible thinking")
        if "plans" in channels:
            lines.append(f"{channels['plans'].mention} — Available plans")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="⚙️ Configure", style=discord.ButtonStyle.secondary, custom_id="welcome_config")
    async def go_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels, plan = await self._ensure_user_channels(interaction)
        await interaction.response.send_message(
            f"⚙️ Adjust your settings in {channels['config'].mention}",
            ephemeral=True
        )

    @discord.ui.button(label="💎 Plans", style=discord.ButtonStyle.success, custom_id="welcome_plans")
    async def go_plans(self, interaction: discord.Interaction, button: discord.ui.Button):
        plan = await self._get_user_plan(interaction.user.id)
        if plan == "free":
            channels, _ = await self._ensure_user_channels(interaction)
            await interaction.response.send_message(
                f"💎 See plans in {channels['plans'].mention}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"💎 Your **{plan.upper()}** plan is already active — no restrictions!",
                ephemeral=True
            )

    @discord.ui.button(label="🎁 Redeem Gift", style=discord.ButtonStyle.secondary, custom_id="welcome_gift")
    async def redeem_gift(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Owner / paid users don't need gifts
        plan = await self._get_user_plan(interaction.user.id)
        if plan != "free":
            return await interaction.response.send_message(
                "🎁 You already have an active plan — no gift needed!",
                ephemeral=True
            )
        await interaction.response.send_modal(GiftRedeemModal())


class ConfigView(discord.ui.View):
    """Configuration panel with dropdowns and buttons - global persistent view.

    Each config channel is user-private, so interaction.user.id is always
    the channel owner. We use it directly instead of storing per-user state.
    """
    def __init__(self):
        super().__init__(timeout=None)

    async def _get_user_id(self, interaction: discord.Interaction) -> int:
        """Get the target user ID for this config panel.
        
        The channel is user-private, so the clicking user is the owner.
        For owner-created panels, the channel's first non-bot member is used.
        """
        return interaction.user.id

    @discord.ui.select(
        placeholder="Temperature...",
        options=[
            discord.SelectOption(label="0.1 - Precise", value="0.1", description="Most deterministic"),
            discord.SelectOption(label="0.3 - Focused", value="0.3", description="Consistent responses"),
            discord.SelectOption(label="0.5 - Balanced", value="0.5", description="Good balance"),
            discord.SelectOption(label="0.8 - Creative (default)", value="0.8", description="Creative default"),
            discord.SelectOption(label="1.0 - Very Creative", value="1.0", description="More variation"),
            discord.SelectOption(label="1.2 - Maximum", value="1.2", description="Maximum creativity"),
        ],
        custom_id="config_temp"
    )
    async def set_temperature(self, interaction: discord.Interaction, select: discord.ui.Select):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("temperature", select.values[0])
        await self._refresh_panel(interaction, user_id)
        await interaction.response.send_message(f"Temperature set to {select.values[0]}.", ephemeral=True)

    @discord.ui.select(
        placeholder="Max tokens...",
        options=[
            discord.SelectOption(label="1024 - Quick", value="1024", description="Fast, short replies"),
            discord.SelectOption(label="2048 - Normal", value="2048", description="Standard length"),
            discord.SelectOption(label="4096 - Long (default)", value="4096", description="Long responses"),
            discord.SelectOption(label="8192 - Extended", value="8192", description="Very long responses"),
        ],
        custom_id="config_tokens"
    )
    async def set_tokens(self, interaction: discord.Interaction, select: discord.ui.Select):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("max_tokens", select.values[0])
        await self._refresh_panel(interaction, user_id)
        await interaction.response.send_message(f"Max tokens set to {select.values[0]}.", ephemeral=True)

    @discord.ui.button(label="Show Stats", style=discord.ButtonStyle.secondary, custom_id="config_stats")
    async def show_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        configs = await db.get_all_config()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        temp = float(configs.get("temperature", "0.8"))
        tokens = int(configs.get("max_tokens", "4096"))
        daily = await db.get_daily_count()
        daily_limit = plan_config.get("daily_messages", 999999)
        context = plan_config.get("context_messages", 10)

        svg = svg_config_panel(temp, tokens, plan, daily, daily_limit, context, plan_config)
        file = discord.File(io.BytesIO(svg.encode()), filename="config.svg")
        msg = f"{EMOJI_BOOSTER} **Configuration & Stats**\n{EMOJI_MANAGER} Plan: `{plan_config.get('badge', plan.upper())}`  |  {EMOJI_BOTS} Model: `{Config.MODEL}`"
        await interaction.response.send_message(msg, file=file, ephemeral=True)

    @discord.ui.button(label="Reset to Defaults", style=discord.ButtonStyle.danger, custom_id="config_reset")
    async def reset_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        await db.set_config("temperature", "0.8")
        await db.set_config("max_tokens", "4096")
        await self._refresh_panel(interaction, user_id)
        await interaction.response.send_message("Configuration reset to defaults.", ephemeral=True)

    @discord.ui.button(label="🗑️ Clear Context", style=discord.ButtonStyle.secondary, custom_id="config_clear")
    async def clear_context(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        async with db._lock:
            import aiosqlite
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute("DELETE FROM messages WHERE role IN ('user', 'assistant')")
                await conn.commit()
        await interaction.response.send_message("🗑️ Conversation context cleared. Kaufy will forget our previous chat.", ephemeral=True)

    @discord.ui.button(label="📥 Export Chat", style=discord.ButtonStyle.secondary, custom_id="config_export")
    async def export_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        
        if not plan_config.get("export_chat", False):
            return await interaction.response.send_message(
                "📥 Chat export is only available on paid plans (7d, 14d, 30d, Lifetime).",
                ephemeral=True
            )
        
        messages = await db.get_messages(limit=200)
        if not messages:
            return await interaction.response.send_message("No messages to export.", ephemeral=True)
        
        lines = []
        for msg in messages:
            role = msg["role"].upper()
            content = msg["content"][:500]
            lines.append(f"[{role}]\n{content}\n")
        
        text = "\n".join(lines)
        file = discord.File(io.BytesIO(text.encode()), filename=f"chat_export_{user_id}.txt")
        await interaction.response.send_message("📥 **Chat Export** — Here's your conversation:", file=file, ephemeral=True)

    @discord.ui.button(label="✏️ Custom Prompt", style=discord.ButtonStyle.secondary, custom_id="config_prompt")
    async def custom_prompt(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        
        if not plan_config.get("custom_prompt", False):
            return await interaction.response.send_message(
                "✏️ Custom system prompt is available on **14d, 30d, and Lifetime** plans.",
                ephemeral=True
            )
        
        await interaction.response.send_modal(CustomPromptModal(user_id))

    async def _refresh_panel(self, interaction: discord.Interaction, user_id: int):
        """Refresh the config channel with updated SVG."""
        db = UserDatabase(user_id)
        await db.init()
        configs = await db.get_all_config()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        temp = float(configs.get("temperature", "0.8"))
        tokens = int(configs.get("max_tokens", "4096"))
        daily = await db.get_daily_count()
        daily_limit = plan_config.get("daily_messages", 999999)
        context = plan_config.get("context_messages", 10)
        
        # Try to update the config channel message
        try:
            embed = discord.Embed(
                title=f"{EMOJI_MANAGER} Configuration Panel",
                description=(
                    f"Adjust your AI experience below.\n\n"
                    f"{EMOJI_BOTS} **Model:** {Config.MODEL}\n"
                    f"{EMOJI_MANAGER} Use the dropdowns below to change settings."
                ),
                color=0x3498DB
            )
            await interaction.edit_original_response(embed=embed, view=ConfigView())
        except:
            pass


class PlansView(discord.ui.View):
    """Plans panel with crypto payment buttons and gift option. Global persistent view."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="7 Days - $3.99", style=discord.ButtonStyle.primary, custom_id="plan_7d")
    async def plan_7d(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_payment(interaction, "7d")

    @discord.ui.button(label="14 Days - $6.99", style=discord.ButtonStyle.primary, custom_id="plan_14d")
    async def plan_14d(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_payment(interaction, "14d")

    @discord.ui.button(label="30 Days - $9.99", style=discord.ButtonStyle.primary, custom_id="plan_30d")
    async def plan_30d(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_payment(interaction, "30d")

    @discord.ui.button(label="Lifetime - $29.99", style=discord.ButtonStyle.success, custom_id="plan_lifetime")
    async def plan_lifetime(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_payment(interaction, "lifetime")

    @discord.ui.button(label="Gift a Plan", style=discord.ButtonStyle.secondary, custom_id="plan_gift")
    async def gift_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GiftPurchaseModal())

    @discord.ui.button(label="My Plan Info", style=discord.ButtonStyle.secondary, custom_id="plan_info")
    async def plan_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        msgs = await db.get_message_count()
        daily = await db.get_daily_count()
        daily_limit = plan_config.get("daily_messages", 999999)
        max_msgs = plan_config.get("max_messages", 999999)

        svg = svg_usage_bar(msgs, max_msgs, plan, daily, daily_limit)
        file = discord.File(io.BytesIO(svg.encode()), filename="usage.svg")
        await interaction.response.send_message(
            f"{EMOJI_BOOSTER} **{plan.upper()} Plan**\n"
            f"{EMOJI_MANAGER} Daily: {daily}/{daily_limit if daily_limit < 999999 else 'Unlimited'}",
            file=file, ephemeral=True)

    async def _show_payment(self, interaction: discord.Interaction, plan_id: str):
        """Create a payment and show crypto addresses with payment ID."""
        plan = Config.PLANS.get(plan_id)
        if not plan:
            return await interaction.response.send_message("Plan not found.", ephemeral=True)

        # Create payment record
        payment = await payment_service.create_payment(
            user_id=interaction.user.id,
            plan_id=plan_id,
            channel_id=interaction.channel_id
        )

        if "error" in payment:
            return await interaction.response.send_message(payment["error"], ephemeral=True)

        # Show payment panel with addresses + payment_id
        svg = svg_crypto_panel(plan_id, plan)
        file = discord.File(io.BytesIO(svg.encode()), filename="payment.svg")

        view = PaymentConfirmView(interaction.user.id, plan_id, payment["payment_id"])
        await interaction.response.send_message(
            f"{EMOJI_MONEY} **Payment for {plan['description']}**\n"
            f"{EMOJI_CREDIT} **Payment ID:** `{payment['payment_id']}`\n"
            f"{EMOJI_LTC} Send the exact amount to one of the addresses below.\n"
            f"{EMOJI_PAYPAL} Use the Payment ID as memo/note for verification.\n"
            f"{EMOJI_BOOSTER} Auto-activates within ~2 min after confirmation.",
            file=file,
            view=view,
            ephemeral=True
        )


class PaymentConfirmView(discord.ui.View):
    """Payment confirmation view - creates payment and auto-verifies."""
    def __init__(self, user_id: int, plan_id: str, payment_id: str):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.plan_id = plan_id
        self.payment_id = payment_id

    @discord.ui.button(label="I Sent Payment", style=discord.ButtonStyle.success, custom_id="pay_confirm")
    async def confirm_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your payment.", ephemeral=True)

        plan = Config.PLANS[self.plan_id]
        payment = await payment_service.get_payment(self.payment_id)
        if not payment:
            return await interaction.response.send_message("Payment not found (expired?).", ephemeral=True)

        # Mark as reported so the background poll can pick it up
        payment["reported"] = True

        await interaction.response.send_message(
            f"{EMOJI_MONEY} **Payment reported for {plan['description']}** (${plan['price_usd']:.2f}).\n"
            f"{EMOJI_CREDIT} Payment ID: `{self.payment_id}`\n"
            f"{EMOJI_BOOSTER} Auto-verifying... plan activates within ~2 min once confirmed.\n"
            f"{EMOJI_BOTS} No manual approval needed — system verifies automatically.",
            ephemeral=True
        )

        # Notify owner (informational only)
        owner_id = Config.OWNER_IDS[0] if Config.OWNER_IDS else None
        if owner_id:
            try:
                owner = await interaction.client.fetch_user(owner_id)
                await owner.send(
                    f"{EMOJI_MONEY} Payment reported: User {interaction.user.name} (`{interaction.user.id}`) "
                    f"for **{plan['description']}** — auto-verifying now. "
                    f"Payment ID: `{self.payment_id}`"
                )
            except:
                pass


class GiftPurchaseModal(discord.ui.Modal):
    """Modal to purchase a gift plan via crypto payment."""
    def __init__(self):
        super().__init__(title="Purchase a Gift Plan")

    plan = discord.ui.TextInput(
        label="Plan",
        placeholder="7d, 14d, 30d, or lifetime",
        required=True,
        min_length=2,
        max_length=10,
    )

    recipient = discord.ui.TextInput(
        label="Recipient Discord ID",
        placeholder="Their Discord user ID",
        required=True,
        min_length=10,
        max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction):
        plan_id = self.plan.value.strip().lower()
        recipient_id = self.recipient.value.strip()

        if plan_id not in Config.PLANS or plan_id == "free":
            return await interaction.response.send_message(
                "Invalid plan. Choose: 7d, 14d, 30d, or lifetime", ephemeral=True
            )

        if not recipient_id.isdigit():
            return await interaction.response.send_message("Invalid Discord ID.", ephemeral=True)

        plan = Config.PLANS[plan_id]
        gift_code = secrets.token_hex(8).upper()

        # Create payment for the gift
        payment = await payment_service.create_payment(
            user_id=interaction.user.id,
            plan_id=plan_id,
            channel_id=interaction.channel_id
        )

        if "error" in payment:
            return await interaction.response.send_message(payment["error"], ephemeral=True)

        # Store gift record in sender's DB
        db = UserDatabase(interaction.user.id)
        await db.init()
        await db.save_token("gift", json.dumps({
            "code": gift_code,
            "plan": plan_id,
            "recipient": recipient_id,
            "sender": str(interaction.user.id),
            "payment_id": payment["payment_id"],
            "created": int(time.time()),
            "redeemed": False,
        }))

        # Also store in a central gift registry (recipient's DB for lookup)
        db_r = UserDatabase(int(recipient_id))
        await db_r.init()
        await db_r.set_config(f"gift_{gift_code}", json.dumps({
            "code": gift_code,
            "plan": plan_id,
            "sender": str(interaction.user.id),
            "created": int(time.time()),
            "redeemed": False,
        }))

        svg = svg_gift_card(plan_id, plan, gift_code)
        file = discord.File(io.BytesIO(svg.encode()), filename="gift.svg")

        await interaction.response.send_message(
            f"{EMOJI_SHOP} **Gift purchase for {plan['description']}!**\n\n"
            f"{EMOJI_MONEY} Send **${plan['price_usd']:.2f}** in crypto to the address below.\n"
            f"{EMOJI_CREDIT} Payment ID: `{payment['payment_id']}`\n"
            f"{EMOJI_SHOP} Gift Code: `{gift_code}` — share this with the recipient.\n\n"
            f"{EMOJI_BOOSTER} Auto-activates within ~2 min after payment is confirmed.",
            file=file,
            ephemeral=True
        )


class CustomPromptModal(discord.ui.Modal):
    """Modal to set a custom system prompt for paid users."""
    def __init__(self, user_id: int):
        super().__init__(title="Set Custom System Prompt")
        self.user_id = user_id

    prompt = discord.ui.TextInput(
        label="Custom Prompt",
        placeholder="Enter custom instructions for Kaufy to follow...",
        required=True,
        min_length=10,
        max_length=2000,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        db = UserDatabase(self.user_id)
        await db.init()
        await db.set_config("custom_prompt", self.prompt.value)
        await interaction.response.send_message(
            "✏️ **Custom prompt saved!** Kaufy will follow these instructions.\n"
            f"```\n{self.prompt.value[:200]}{'...' if len(self.prompt.value) > 200 else ''}\n```",
            ephemeral=True
        )


class GiftRedeemModal(discord.ui.Modal):
    """Modal to redeem a gift code — auto-activates if the gift was paid for."""
    def __init__(self):
        super().__init__(title="Redeem Gift Code")

    code = discord.ui.TextInput(
        label="Gift Code",
        placeholder="Paste your gift code here",
        required=True,
        min_length=8,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        gift_code = self.code.value.strip().upper()
        user_id = interaction.user.id

        db = UserDatabase(user_id)
        await db.init()

        # Check if already has a pending gift
        pending = await db.get_config("pending_gift")
        if pending:
            return await interaction.response.send_message(
                "You already have a pending gift to redeem.", ephemeral=True
            )

        # Look up the gift in this user's DB (stored by sender during purchase)
        gift_raw = await db.get_config(f"gift_{gift_code}")
        if not gift_raw:
            return await interaction.response.send_message(
                "Gift code not found or not assigned to you. "
                "Make sure the sender used your correct Discord ID.", ephemeral=True
            )

        try:
            gift = json.loads(gift_raw)
        except:
            return await interaction.response.send_message("Invalid gift data.", ephemeral=True)

        if gift.get("redeemed"):
            return await interaction.response.send_message("This gift code has already been redeemed.", ephemeral=True)

        plan_id = gift.get("plan", "7d")
        plan = Config.PLANS.get(plan_id, Config.PLANS["7d"])
        expires = int(time.time()) + (plan["duration_days"] * 86400)

        # Activate the plan
        await db.set_config("plan", plan_id)
        await db.set_config("plan_expires", str(expires))

        # Mark gift as redeemed
        gift["redeemed"] = True
        gift["redeemed_at"] = int(time.time())
        gift["redeemed_by"] = user_id
        await db.set_config(f"gift_{gift_code}", json.dumps(gift))

        # Clear pending
        await db.set_config("pending_gift", "")

        # Notify channel manager to hide #plans
        from bot.services.payment import payment_service
        if payment_service._on_plan_activated:
            try:
                await payment_service._on_plan_activated(user_id, plan_id)
            except Exception as e:
                pass

        await interaction.response.send_message(
            f"{EMOJI_SHOP} **Gift code redeemed!**\n"
            f"{EMOJI_BOOSTER} Plan: **{plan['description']}** activated until <t:{expires}:f>.\n"
            f"{EMOJI_BOTS} Enjoy your premium access to Kaufy!",
            ephemeral=True
        )

        # Notify owner + sender
        owner_id = Config.OWNER_IDS[0] if Config.OWNER_IDS else None
        sender_id = gift.get("sender")
        if owner_id:
            try:
                owner = await interaction.client.fetch_user(owner_id)
                await owner.send(
                    f"{EMOJI_SHOP} Gift redeemed: User `{user_id}` ({interaction.user.name}) "
                    f"redeemed `{gift_code}` for **{plan['description']}** plan."
                )
            except:
                pass
        if sender_id:
            try:
                sender = await interaction.client.fetch_user(int(sender_id))
                await sender.send(
                    f"{EMOJI_SHOP} Your gift ({gift_code}) for **{plan['description']}** "
                    f"was redeemed by {interaction.user.name}!"
                )
            except:
                pass


class ThinkingView(discord.ui.View):
    """Thinking mode panel for premium users. Global persistent view."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Show Thinking", style=discord.ButtonStyle.primary, custom_id="thinking_show")
    async def show_thinking(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        if plan == "free":
            return await interaction.response.send_message(
                "Thinking mode is only available on paid plans. Check your #plans channel to upgrade.",
                ephemeral=True
            )
        await interaction.response.send_message(
            "Thinking mode active. The AI will display its reasoning process alongside responses. "
            "Ask a question in #msg to see it.",
            ephemeral=True
        )

    @discord.ui.button(label="My Files", style=discord.ButtonStyle.secondary, custom_id="thinking_files")
    async def list_files(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        db = UserDatabase(user_id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        if plan == "free":
            return await interaction.response.send_message("File storage requires a paid plan.", ephemeral=True)
        files = await db.get_files()
        if not files:
            return await interaction.response.send_message("No files saved yet.", ephemeral=True)
        lines = [f"{f['filename']} ({f['mime_type']})" for f in files]
        await interaction.response.send_message("Your files:\n" + "\n".join(lines[:20]), ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# FACTORY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_welcome_panel(_user_id: int = None) -> WelcomeView:
    return WelcomeView()

def get_config_panel(_user_id: int = None) -> ConfigView:
    return ConfigView()

def get_plans_panel(_user_id: int = None) -> PlansView:
    return PlansView()

def get_thinking_panel(_user_id: int = None) -> ThinkingView:
    return ThinkingView()
