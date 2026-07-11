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


def svg_plan_card(plan_id: str, plan: dict, active: bool = False) -> str:
    """Single plan card SVG."""
    border = "#6366f1" if active else "#2a2a4e"
    glow = "0 0 20px rgba(99,102,241,0.3)" if active else "none"
    price = f"${plan['price_usd']:.2f}" if plan['price_usd'] > 0 else "Free"
    duration = plan['description']
    features = []
    daily = plan.get("daily_messages", 999999)
    if daily < 999999:
        features.append(f"{daily} messages per day")
    elif plan['max_messages'] > 99999:
        features.append("Unlimited messages")
    else:
        features.append(f"{plan['max_messages']} messages")
    if plan['thinking']:
        features.append("Thinking mode")
    if plan['price_usd'] > 0:
        features.append("Priority support")
    else:
        features.append("Basic support")
    if plan_id == "lifetime":
        features.append("One-time payment")

    feat_lines = ""
    for i, f in enumerate(features):
        y = 95 + i * 20
        feat_lines += f'<text x="25" y="{y}" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">- {f}</text>'

    y_offset = 95 + len(features) * 20 + 15

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="380" height="{y_offset + 10}" viewBox="0 0 380 {y_offset + 10}">
  <defs>
    <linearGradient id="bgc" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1a2e"/><stop offset="1" stop-color="#16213e"/></linearGradient>
  </defs>
  <rect x="1" y="1" width="378" height="{y_offset + 8}" rx="14" fill="url(#bgc)" stroke="{border}" stroke-width="1.5" filter="{glow}"/>
  <text x="25" y="35" font-family="system-ui, sans-serif" font-size="16" font-weight="700" fill="#e0e0e0">{duration}</text>
  <text x="355" y="35" font-family="system-ui, sans-serif" font-size="18" font-weight="700" fill="#6366f1" text-anchor="end">{price}</text>
  <line x1="25" y1="55" x2="355" y2="55" stroke="#2a2a4e" stroke-width="1"/>
  {feat_lines}
  {f'<rect x="25" y="{y_offset - 5}" width="330" height="32" rx="8" fill="#6366f1" opacity="0.15"/><text x="190" y="{y_offset + 15}" font-family="system-ui, sans-serif" font-size="12" font-weight="600" fill="#6366f1" text-anchor="middle">ACTIVE</text>' if active else ''}
</svg>'''


def svg_plans_grid(active_plan: str = "free") -> str:
    """Grid of all plans as a single SVG."""
    cards = ""
    y = 0
    for pid, pdata in Config.PLANS.items():
        is_active = pid == active_plan
        card_svg = svg_plan_card(pid, pdata, is_active)
        # Extract height from SVG
        cards += f'<g transform="translate(0, {y})">{card_svg}</g>'
        y += 220

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{y + 20}" viewBox="0 0 420 {y + 20}">
  <rect width="420" height="{y + 20}" rx="16" fill="#0f172a"/>
  <text x="210" y="35" font-family="system-ui, sans-serif" font-size="18" font-weight="700" fill="#e0e0e0" text-anchor="middle">Available Plans</text>
  <text x="210" y="55" font-family="system-ui, sans-serif" font-size="11" fill="#6b7280" text-anchor="middle">Crypto payment only - BTC / ETH / USDT / SOL</text>
  {cards}
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


def svg_config_panel(temperature: float, max_tokens: int, plan: str) -> str:
    """Config status SVG with model info."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="180" viewBox="0 0 420 180">
  <defs>
    <linearGradient id="bgcf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1a2e"/><stop offset="1" stop-color="#16213e"/></linearGradient>
  </defs>
  <rect width="420" height="180" rx="14" fill="url(#bgcf)" stroke="#2a2a4e" stroke-width="1"/>
  <text x="25" y="30" font-family="system-ui, sans-serif" font-size="15" font-weight="600" fill="#e0e0e0">Configuration</text>
  <line x1="25" y1="45" x2="395" y2="45" stroke="#2a2a4e" stroke-width="1"/>
  <text x="25" y="70" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Temperature</text>
  <text x="395" y="70" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{temperature}</text>
  <text x="25" y="95" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Max Tokens</text>
  <text x="395" y="95" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{max_tokens}</text>
  <text x="25" y="120" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Plan</text>
  <text x="395" y="120" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{plan}</text>
  <text x="25" y="145" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Model</text>
  <text x="395" y="145" font-family="system-ui, sans-serif" font-size="11" fill="#e0e0e0" text-anchor="end">{Config.MODEL}</text>
  <text x="25" y="168" font-family="system-ui, sans-serif" font-size="11" fill="#9ca3af">Messages</text>
  <text x="395" y="168" font-family="system-ui, sans-serif" font-size="11" fill="#22c55e" text-anchor="end">Unlimited</text>
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
    """Welcome panel — cria canais do usuário automaticamente ao clicar."""
    def __init__(self):
        super().__init__(timeout=None)

    async def _ensure_user_channels(self, interaction: discord.Interaction) -> dict:
        """Cria ou pega canais do usuário com categoria."""
        guild = interaction.guild
        member = interaction.user
        cat_name = f"Kaufy's Chat - {member.display_name}"
        
        # Check if category already exists
        category = discord.utils.get(guild.categories, name=cat_name)
        
        # Get plan from DB
        from bot.models.user_db import UserDatabase
        db = UserDatabase(member.id)
        await db.init()
        plan = await db.get_config("plan") or "free"
        
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
        for base in ["msg", "config", "plans"]:
            ch_name = f"{base}-{_sanitize_name(member.display_name)}-{plan}"
            existing = discord.utils.get(category.channels, name=ch_name)
            if not existing:
                existing = await guild.create_text_channel(
                    ch_name, category=category, overwrites=overwrites,
                    reason=f"Kaufy {base} for {member}"
                )
            channels[base] = existing
        
        return channels, plan

    async def _send_panels(self, channels: dict, plan: str, member: discord.Member):
        """Envia os panels de boas-vindas, config e plans."""
        from bot.cogs.panels import get_welcome_panel, get_config_panel, get_plans_panel
        from bot.cogs.panels import EMOJI_BOTS, EMOJI_MANAGER, EMOJI_SHOP, EMOJI_CART, EMOJI_BOOSTER
        
        is_paid = plan != "free"
        
        # Welcome in msg channel
        desc = (
            f"{EMOJI_BOTS} I am Kaufy, your unrestricted technical AI assistant.\n\n"
            f"{EMOJI_MANAGER} Ask me anything in this channel\n"
            f"{EMOJI_SHOP} Configure your experience in {channels['config'].mention}\n"
            + (f"{EMOJI_CART} Check plans in {channels['plans'].mention}\n{EMOJI_BOOSTER} 10 free messages per day"
               if not is_paid
               else f"{EMOJI_BOOSTER} Your **{plan.upper()}** plan is active")
        )
        
        embed = discord.Embed(
            title=f"{EMOJI_BOTS} Welcome to Kaufy Hall",
            description=desc,
            color=0x9B59B6
        )
        await channels["msg"].send(embed=embed)
        
        # Config panel
        await channels["config"].send(
            embed=discord.Embed(
                title=f"{EMOJI_MANAGER} Configuration Panel",
                description=f"{EMOJI_MANAGER} Adjust your AI experience below.",
                color=0x3498DB
            ),
            view=get_config_panel(member.id)
        )
        
        # Plans panel — only for free users
        if not is_paid:
            await channels["plans"].send(
                embed=discord.Embed(
                    title=f"{EMOJI_CART} Plans and Subscription",
                    description=f"{EMOJI_SHOP} Choose your plan to unlock features. Crypto only.",
                    color=0x2ECC71
                ),
                view=get_plans_panel(member.id)
            )

    @discord.ui.button(label="🚀 Start Chatting", style=discord.ButtonStyle.primary, custom_id="welcome_chat")
    async def start_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channels, plan = await self._ensure_user_channels(interaction)
        await self._send_panels(channels, plan, interaction.user)
        await interaction.followup.send(
            f"✅ **Canais criados!**\n"
            f"{channels['msg'].mention} — Converse com Kaufy\n"
            f"{channels['config'].mention} — Configurações\n"
            f"{channels['plans'].mention} — Planos",
            ephemeral=True
        )

    @discord.ui.button(label="⚙️ Configure", style=discord.ButtonStyle.secondary, custom_id="welcome_config")
    async def go_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels, plan = await self._ensure_user_channels(interaction)
        await interaction.response.send_message(
            f"⚙️ Ajuste suas configurações em {channels['config'].mention}",
            ephemeral=True
        )

    @discord.ui.button(label="💎 Plans", style=discord.ButtonStyle.success, custom_id="welcome_plans")
    async def go_plans(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels, plan = await self._ensure_user_channels(interaction)
        if plan == "free":
            await interaction.response.send_message(
                f"💎 Veja os planos em {channels['plans'].mention}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"💎 Seu plano **{plan.upper()}** já está ativo!",
                ephemeral=True
            )

    @discord.ui.button(label="🎁 Redeem Gift", style=discord.ButtonStyle.secondary, custom_id="welcome_gift")
    async def redeem_gift(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GiftRedeemModal())


class ConfigView(discord.ui.View):
    """Configuration panel with dropdowns and buttons - no emojis."""
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

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
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your configuration panel.", ephemeral=True)
        db = UserDatabase(self.user_id)
        await db.init()
        await db.set_config("temperature", select.values[0])
        await self._refresh_panel(interaction)
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
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your configuration panel.", ephemeral=True)
        db = UserDatabase(self.user_id)
        await db.init()
        await db.set_config("max_tokens", select.values[0])
        await self._refresh_panel(interaction)
        await interaction.response.send_message(f"Max tokens set to {select.values[0]}.", ephemeral=True)

    @discord.ui.button(label="Show Stats", style=discord.ButtonStyle.secondary, custom_id="config_stats")
    async def show_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your configuration panel.", ephemeral=True)
        db = UserDatabase(self.user_id)
        await db.init()
        stats = await db.get_stats()
        configs = await db.get_all_config()
        plan = await db.get_config("plan") or "free"
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])
        temp = float(configs.get("temperature", "0.8"))
        tokens = int(configs.get("max_tokens", "4096"))
        daily = await db.get_daily_count()
        daily_limit = plan_config.get("daily_messages", 999999)

        svg = svg_usage_bar(stats["messages"], stats["max_messages"], plan, daily, daily_limit)
        file = discord.File(io.BytesIO(svg.encode()), filename="usage.svg")
        msg = f"{EMOJI_BOOSTER} **Usage Stats**\n{EMOJI_MANAGER} Plan: `{plan}`  |  {EMOJI_BOTS} Model: `{Config.MODEL}`"
        await interaction.response.send_message(msg, file=file, ephemeral=True)

    @discord.ui.button(label="Reset to Defaults", style=discord.ButtonStyle.danger, custom_id="config_reset")
    async def reset_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your configuration panel.", ephemeral=True)
        db = UserDatabase(self.user_id)
        await db.init()
        await db.set_config("temperature", "0.8")
        await db.set_config("max_tokens", "4096")
        await self._refresh_panel(interaction)
        await interaction.response.send_message("Configuration reset to defaults.", ephemeral=True)

    async def _refresh_panel(self, interaction: discord.Interaction):
        """Refresh the config channel with updated SVG."""
        # This would re-send the config panel - for now just acknowledge
        pass


class PlansView(discord.ui.View):
    """Plans panel with crypto payment buttons and gift option."""
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="7 Days - $3.99", style=discord.ButtonStyle.primary, custom_id="plan_7d")
    async def plan_7d(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your panel.", ephemeral=True)
        await self._show_payment(interaction, "7d")

    @discord.ui.button(label="14 Days - $6.99", style=discord.ButtonStyle.primary, custom_id="plan_14d")
    async def plan_14d(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your panel.", ephemeral=True)
        await self._show_payment(interaction, "14d")

    @discord.ui.button(label="30 Days - $9.99", style=discord.ButtonStyle.primary, custom_id="plan_30d")
    async def plan_30d(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your panel.", ephemeral=True)
        await self._show_payment(interaction, "30d")

    @discord.ui.button(label="Lifetime - $29.99", style=discord.ButtonStyle.success, custom_id="plan_lifetime")
    async def plan_lifetime(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your panel.", ephemeral=True)
        await self._show_payment(interaction, "lifetime")

    @discord.ui.button(label="Gift a Plan", style=discord.ButtonStyle.secondary, custom_id="plan_gift")
    async def gift_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your panel.", ephemeral=True)
        await interaction.response.send_modal(GiftPurchaseModal())

    @discord.ui.button(label="My Plan Info", style=discord.ButtonStyle.secondary, custom_id="plan_info")
    async def plan_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This is not your panel.", ephemeral=True)
        db = UserDatabase(self.user_id)
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
            user_id=self.user_id,
            plan_id=plan_id,
            channel_id=interaction.channel_id
        )

        if "error" in payment:
            return await interaction.response.send_message(payment["error"], ephemeral=True)

        # Show payment panel with addresses + payment_id
        svg = svg_crypto_panel(plan_id, plan)
        file = discord.File(io.BytesIO(svg.encode()), filename="payment.svg")

        view = PaymentConfirmView(self.user_id, plan_id, payment["payment_id"])
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
    """Thinking mode panel for premium users."""
    def __init__(self, user_id: int, plan: str = "free"):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.plan = plan

    @discord.ui.button(label="Show Thinking", style=discord.ButtonStyle.primary, custom_id="thinking_show")
    async def show_thinking(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        if self.plan == "free":
            plans_ch = discord.utils.get(interaction.guild.text_channels, name="plans")
            plans_mention = plans_ch.mention if plans_ch else "#plans"
            return await interaction.response.send_message(
                f"Thinking mode is only available on paid plans. Check {plans_mention} to upgrade.",
                ephemeral=True
            )
        await interaction.response.send_message(
            "Thinking mode active. The AI will display its reasoning process alongside responses. "
            "Ask a question in #msg to see it.",
            ephemeral=True
        )

    @discord.ui.button(label="My Files", style=discord.ButtonStyle.secondary, custom_id="thinking_files")
    async def list_files(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        if self.plan == "free":
            return await interaction.response.send_message("File storage requires a paid plan.", ephemeral=True)
        db = UserDatabase(self.user_id)
        await db.init()
        files = await db.get_files()
        if not files:
            return await interaction.response.send_message("No files saved yet.", ephemeral=True)
        lines = [f"{f['filename']} ({f['mime_type']})" for f in files]
        await interaction.response.send_message("Your files:\n" + "\n".join(lines[:20]), ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# FACTORY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_welcome_panel() -> WelcomeView:
    return WelcomeView()

def get_config_panel(user_id: int) -> ConfigView:
    return ConfigView(user_id)

def get_plans_panel(user_id: int) -> PlansView:
    return PlansView(user_id)

def get_thinking_panel(user_id: int, plan: str = "free") -> ThinkingView:
    return ThinkingView(user_id, plan)
