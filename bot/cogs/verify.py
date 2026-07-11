"""Verification cog — button-based CAPTCHA with purple image.

Commands:
  .verify setup [#channel]   — Set the verification channel & send the panel
  .verify config              — Show current verify settings

The panel has a button. When clicked, the user gets an ephemeral
image with random chars (5-7 digits/letters) in purple. They type
the code to verify. On success, they get a Verified role.
"""

import discord
from discord.ext import commands
import logging
import random
import string
import io
import asyncio
import json
from pathlib import Path

# Pillow (PIL) — required for CAPTCHA images
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    Image = ImageDraw = ImageFont = None
    HAS_PIL = False
    import warnings
    warnings.warn("Pillow not installed — verify CAPTCHA will use text-based codes")

from bot.config import Config

logger = logging.getLogger("kaufy.verify")

# Config storage
_VERIFY_DIR = Config.DATA_DIR / "verify"
_VERIFY_DIR.mkdir(parents=True, exist_ok=True)


def _verify_config_path(guild_id: int) -> Path:
    return _VERIFY_DIR / f"guild_{guild_id}.json"


def _load_config(guild_id: int) -> dict:
    path = _verify_config_path(guild_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {"channel_id": None, "role_id": None, "enabled": True}


def _save_config(guild_id: int, data: dict):
    _verify_config_path(guild_id).write_text(json.dumps(data, indent=2))


# In-memory store: code -> user_id + expiry
_pending: dict[str, int] = {}
_PENDING_LOCK = asyncio.Lock()


def _generate_code(length: int = None) -> str:
    """Generate random alphanumeric code (5-7 chars)."""
    if length is None:
        length = random.randint(5, 7)
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def _generate_captcha_image(code: str) -> io.BytesIO | None:
    """Generate purple CAPTCHA image with the given code.

    Uses a simpler, more reliable approach: draw all chars directly
    on the image instead of rotating individual character images.
    Returns BytesIO with PNG data, or None if PIL is not available.
    """
    if not HAS_PIL:
        return None

    width = 380
    height = 120

    img = Image.new('RGB', (width, height), (15, 15, 35))  # dark background
    draw = ImageDraw.Draw(img)

    # Try to load a monospace font, fallback to default
    _FONT_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/system/fonts/DroidSansMono.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ]
    font = None
    for fp in _FONT_PATHS:
        try:
            font = ImageFont.truetype(fp, 36)
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Background noise: random lines
    for _ in range(8):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(60, 20, 80), width=2)

    # Background noise: random dots
    for _ in range(150):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(40, 10, 60))

    # Draw characters with individual offsets, NO rotation (avoids clipping)
    total_chars = len(code)
    # Calculate spacing so they fit evenly
    char_spacing = width // (total_chars + 1)
    start_x = char_spacing
    y_center = height // 2

    for i, ch in enumerate(code):
        # Random purple shade
        r = random.randint(140, 220)
        g = random.randint(50, 130)
        b = random.randint(190, 255)
        color = (r, g, b)

        # Random vertical offset (-15 to +15 pixels)
        y_offset = random.randint(-15, 15)

        # Random small rotation using individual char image (safe bounding)
        try:
            bbox = draw.textbbox((0, 0), ch, font=font)
            cw = bbox[2] - bbox[0]
            ch_h = bbox[3] - bbox[1]
        except:
            cw = 20
            ch_h = 30

        # Create small char image with plenty of padding
        padding = 10
        char_img = Image.new('RGBA', (cw + padding * 2, ch_h + padding * 2), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((padding, padding), ch, fill=color + (255,), font=font)

        # Subtle rotation (max 15 degrees) to avoid clipping
        angle = random.randint(-15, 15)
        char_img = char_img.rotate(angle, expand=True, fillcolor=(0, 0, 0, 0))

        # Position: center in the image
        x = start_x + i * char_spacing - char_img.width // 2
        y = y_center - char_img.height // 2 + y_offset

        # Ensure within bounds
        x = max(5, min(x, width - char_img.width - 5))
        y = max(5, min(y, height - char_img.height - 5))

        img.paste(char_img, (x, y), char_img)

    # Foreground noise: subtle lines
    for _ in range(4):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(50, 15, 70), width=1)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


# ──────────────────────────────────────────────
# VERIFY VIEW (the button panel)
# ──────────────────────────────────────────────

def _generate_wrong_codes(correct: str, count: int = 4) -> list[str]:
    """Generate wrong codes that look similar to the correct one."""
    wrong = set()
    chars = string.ascii_uppercase + string.digits
    max_attempts = 50
    while len(wrong) < count and max_attempts > 0:
        max_attempts -= 1
        # Copy correct code and mutate 1-2 chars
        candidate = list(correct)
        mutations = random.randint(1, min(2, len(candidate)))
        for _ in range(mutations):
            idx = random.randrange(len(candidate))
            candidate[idx] = random.choice(chars)
        c = "".join(candidate)
        if c != correct:
            wrong.add(c)
    # Fill remaining with random codes
    while len(wrong) < count:
        c = _generate_code(len(correct))
        if c != correct:
            wrong.add(c)
    return list(wrong)[:count]


class VerifyChoiceView(discord.ui.View):
    """Multiple choice buttons under the CAPTCHA image."""
    def __init__(self, correct_code: str, options: list[str]):
        super().__init__(timeout=300)
        self.correct_code = correct_code
        for opt in options:
            self.add_item(VerifyChoiceButton(opt, correct_code))


class VerifyChoiceButton(discord.ui.Button):
    """Single choice button."""
    def __init__(self, code: str, correct: str):
        label = code
        style = discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, custom_id=f"verify_opt_{code}")
        self.code = code
        self.correct = correct

    async def callback(self, interaction: discord.Interaction):
        if self.code == self.correct:
            # Verify succeeded
            async with _PENDING_LOCK:
                _pending.pop(self.correct, None)

            guild_id = interaction.guild.id
            config = _load_config(guild_id)
            role_id = config.get("role_id")
            verified = interaction.guild.get_role(role_id) if role_id else None

            if verified:
                try:
                    await interaction.user.add_roles(verified, reason="Verified via CAPTCHA")
                    await interaction.response.edit_message(
                        content="✅ **Verified!** You now have access to the server.",
                        view=None,
                        attachments=[],  # Remove the image
                    )
                    logger.info(f"User {interaction.user.id} verified in guild {guild_id}")
                except Exception as e:
                    await interaction.response.edit_message(
                        content=f"❌ Verification passed but couldn't assign role: {e}",
                        view=None,
                    )
            else:
                await interaction.response.edit_message(
                    content="✅ **Verified!** (No verified role configured)",
                    view=None,
                )
        else:
            await interaction.response.send_message(
                "❌ Wrong code. Click 'Resend Code' to try again.",
                ephemeral=True
            )


class VerifyView(discord.ui.View):
    """Verification panel with Verify + Resend buttons."""

    def __init__(self):
        super().__init__(timeout=None)
        # The panel shows Verify + Resend
        # When Verify is clicked, we send ephemeral CAPTCHA with choice buttons

    @discord.ui.button(label="🔓 Verify", style=discord.ButtonStyle.success, custom_id="verify_start")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send ephemeral CAPTCHA image + multiple choice buttons."""
        code = _generate_code()

        # Store code
        async with _PENDING_LOCK:
            _pending[code] = interaction.user.id

        buf = _generate_captcha_image(code)
        options = [code] + _generate_wrong_codes(code)
        random.shuffle(options)

        if buf:
            await interaction.response.send_message(
                "**🧩 Verification**\nSelect the code from the image below:",
                file=discord.File(buf, filename="captcha.png"),
                view=VerifyChoiceView(code, options),
                ephemeral=True,
            )
        else:
            # Fallback: show code as text + options
            await interaction.response.send_message(
                f"**🧩 Verification**\nSelect the code below:\n```\n{code}\n```",
                view=VerifyChoiceView(code, options),
                ephemeral=True,
            )

        # Auto-expire after 5 min
        async def _expire():
            await asyncio.sleep(300)
            async with _PENDING_LOCK:
                _pending.pop(code, None)
        asyncio.ensure_future(_expire())

    @discord.ui.button(label="🔄 Resend Code", style=discord.ButtonStyle.secondary, custom_id="verify_resend")
    async def resend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Resend a new CAPTCHA with fresh choices."""
        code = _generate_code()
        async with _PENDING_LOCK:
            _pending[code] = interaction.user.id

        buf = _generate_captcha_image(code)
        options = [code] + _generate_wrong_codes(code)
        random.shuffle(options)

        if buf:
            await interaction.response.send_message(
                "**🧩 New Code**\nSelect the code from the image below:",
                file=discord.File(buf, filename="captcha.png"),
                view=VerifyChoiceView(code, options),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"**🧩 New Code**\nSelect the code below:\n```\n{code}\n```",
                view=VerifyChoiceView(code, options),
                ephemeral=True,
            )

        async def _expire():
            await asyncio.sleep(300)
            async with _PENDING_LOCK:
                _pending.pop(code, None)
        asyncio.ensure_future(_expire())


# ──────────────────────────────────────────────
# VERIFY COG
# ──────────────────────────────────────────────

class Verification(commands.Cog):
    """Verification system with CAPTCHA image."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pending_cleanup_task = None

    async def cog_load(self):
        self._pending_cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def cog_unload(self):
        if self._pending_cleanup_task:
            self._pending_cleanup_task.cancel()

    async def _cleanup_loop(self):
        """Periodically clean expired pending codes."""
        while True:
            await asyncio.sleep(60)
            now = asyncio.get_event_loop().time()
            # Codes expire after 5 min — handled per-code with asyncio.ensure_future

    async def _verify_or_owner(self, ctx: commands.Context) -> bool:
        """Allow if admin OR owner (no secret needed for verify)."""
        if ctx.author.id in Config.OWNER_IDS:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        await ctx.reply("⛔ You need `Administrator` permission or be the owner.", delete_after=10)
        return False

    @commands.group(name="verify", invoke_without_command=True)
    async def verify_group(self, ctx: commands.Context):
        """Verification system management.

        Subcommands:
          setup [#channel] [@role]  — Place verification panel in a channel
          config                    — Show current verification settings
        """
        if not await self._verify_or_owner(ctx):
            return
        await ctx.send_help(ctx.command)

    @verify_group.command(name="setup")
    async def verify_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel = None,
        role: discord.Role = None,
    ):
        """Place the verification panel in a channel.

        Usage: .verify setup [#channel] [@role]
        If no channel is given, uses the current channel.
        If no role is given, uses "Verified" or creates it.

        Example: .verify setup #welcome @Verified
        """
        if not await self._verify_or_owner(ctx):
            return
        target = channel or ctx.channel
        guild_id = ctx.guild.id

        # Resolve role
        verified_role = role or discord.utils.get(ctx.guild.roles, name="Verified")
        if not verified_role:
            try:
                verified_role = await ctx.guild.create_role(
                    name="Verified",
                    reason="Verification system setup",
                    color=discord.Color.purple(),
                )
                await ctx.send(f"✅ Created `Verified` role.")
            except Exception as e:
                return await ctx.reply(f"❌ Could not create Verified role: {e}")

        # Save config
        config = {
            "channel_id": target.id,
            "role_id": verified_role.id,
            "enabled": True,
        }
        _save_config(guild_id, config)

        # Send panel
        embed = discord.Embed(
            title="🧩 Verification",
            description=(
                "Click **Verify** below to prove you're human.\n"
                "You'll receive a code image — type the characters shown."
            ),
            color=0x9B59B6  # purple
        )
        embed.set_footer(text="Kaufy's Hall • Verification")

        await target.send(embed=embed, view=VerifyView())
        await ctx.reply(f"✅ Verification panel placed in {target.mention} with role {verified_role.mention}")

        # Set channel slowmode to avoid spam
        try:
            await target.edit(slowmode_delay=5)
        except:
            pass

    @verify_group.command(name="config")
    async def verify_config(self, ctx: commands.Context):
        """Show current verification settings."""
        if not await self._verify_or_owner(ctx):
            return
        guild_id = ctx.guild.id
        config = _load_config(guild_id)

        ch = ctx.guild.get_channel(config.get("channel_id", 0))
        role = ctx.guild.get_role(config.get("role_id", 0))

        lines = [
            "**🧩 Verification Config**",
            f"Channel: {ch.mention if ch else 'Not set'}",
            f"Role: {role.mention if role else 'Not set'}",
            f"Enabled: {'✅' if config.get('enabled') else '❌'}",
        ]
        await ctx.reply("\n".join(lines))

    @verify_group.command(name="enable")
    async def verify_enable(self, ctx: commands.Context):
        """Enable verification."""
        if not await self._verify_or_owner(ctx):
            return
        guild_id = ctx.guild.id
        config = _load_config(guild_id)
        config["enabled"] = True
        _save_config(guild_id, config)
        await ctx.reply("✅ Verification enabled.")

    @verify_group.command(name="disable")
    async def verify_disable(self, ctx: commands.Context):
        """Disable verification."""
        if not await self._verify_or_owner(ctx):
            return
        guild_id = ctx.guild.id
        config = _load_config(guild_id)
        config["enabled"] = False
        _save_config(guild_id, config)
        await ctx.reply("⏸️ Verification disabled.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
