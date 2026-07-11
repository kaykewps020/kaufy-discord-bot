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
from PIL import Image, ImageDraw, ImageFont
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


def _generate_captcha_image(code: str) -> io.BytesIO:
    """Generate purple CAPTCHA image with the given code.

    Returns BytesIO with PNG data.
    """
    width = 280
    height = 100

    img = Image.new('RGB', (width, height), (15, 15, 35))  # dark background
    draw = ImageDraw.Draw(img)

    # Try to load a monospace font, fallback to default
    try:
        font = ImageFont.truetype("/system/fonts/DroidSansMono.ttf", 42)
    except:
        try:
            font = ImageFont.truetype("/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSansMono.ttf", 42)
        except:
            font = ImageFont.load_default()

    # Draw random lines (noise)
    for _ in range(6):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(60, 20, 80), width=2)

    # Draw random dots
    for _ in range(100):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(40, 10, 60))

    # Draw each character with slight offset and rotation variation
    total_w = 0
    char_imgs = []
    for ch in code:
        # Random purple shade for each char
        r = random.randint(120, 200)
        g = random.randint(40, 120)
        b = random.randint(180, 255)
        color = (r, g, b)

        # Create temp image for this char
        try:
            bbox = draw.textbbox((0, 0), ch, font=font)
            cw = bbox[2] - bbox[0]
            ch_h = bbox[3] - bbox[1]
        except:
            cw = 30
            ch_h = 40

        char_img = Image.new('RGBA', (cw + 10, ch_h + 10), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((5, 5), ch, fill=color + (255,), font=font)

        # Rotate slightly
        angle = random.randint(-25, 25)
        char_img = char_img.rotate(angle, expand=True, fillcolor=(0, 0, 0, 0))

        char_imgs.append(char_img)
        total_w += char_img.width + 5

    # Composite all chars onto main image
    x_offset = (width - total_w) // 2
    y_offset = (height - 50) // 2
    for cimg in char_imgs:
        img.paste(cimg, (x_offset, y_offset), cimg)
        x_offset += cimg.width + 5

    # Draw more noise lines on top
    for _ in range(3):
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

class VerifyView(discord.ui.View):
    """Verification panel with a single button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="verify_start")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send ephemeral CAPTCHA image + code input modal."""
        code = _generate_code()

        # Store code with expiry (5 min)
        async with _PENDING_LOCK:
            _pending[code] = interaction.user.id

        # Generate image
        buf = _generate_captcha_image(code)
        file = discord.File(buf, filename="captcha.png")

        # Create modal for code input
        modal = VerifyModal(code)
        await interaction.response.send_modal(modal)

        # Also send the image ephemerally
        await interaction.followup.send(
            "**🧩 Verification**\nType the characters from the image below:",
            file=file,
            ephemeral=True,
        )

        # Auto-expire after 5 min
        async def _expire():
            await asyncio.sleep(300)
            async with _PENDING_LOCK:
                _pending.pop(code, None)
        asyncio.ensure_future(_expire())

    @discord.ui.button(label="Resend Code", style=discord.ButtonStyle.secondary, custom_id="verify_resend")
    async def resend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Resend a new CAPTCHA code."""
        code = _generate_code()
        async with _PENDING_LOCK:
            _pending[code] = interaction.user.id

        buf = _generate_captcha_image(code)
        file = discord.File(buf, filename="captcha.png")

        modal = VerifyModal(code)
        await interaction.response.send_modal(modal)

        await interaction.followup.send(
            "**🧩 New Code**\nType the characters from the image:",
            file=file,
            ephemeral=True,
        )

        async def _expire():
            await asyncio.sleep(300)
            async with _PENDING_LOCK:
                _pending.pop(code, None)
        asyncio.ensure_future(_expire())


class VerifyModal(discord.ui.Modal):
    """Modal for entering the CAPTCHA code."""

    def __init__(self, expected_code: str):
        super().__init__(title="Verification")
        self.expected_code = expected_code

        self.code_input = discord.ui.TextInput(
            label="Enter the code from the image",
            placeholder="e.g. A7K3P",
            required=True,
            min_length=4,
            max_length=10,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        entered = self.code_input.value.strip().upper()
        expected = self.expected_code

        async with _PENDING_LOCK:
            stored_user = _pending.get(expected)
            if stored_user is None or stored_user != interaction.user.id:
                return await interaction.response.send_message(
                    "❌ Code expired or invalid. Click 'Resend Code' for a new one.",
                    ephemeral=True
                )
            if entered != expected:
                return await interaction.response.send_message(
                    "❌ Wrong code. Check the image and try again, or click 'Resend Code'.",
                    ephemeral=True
                )
            # Clean up
            _pending.pop(expected, None)

        # Verify succeeded — assign role
        guild_id = interaction.guild.id
        config = _load_config(guild_id)
        role_id = config.get("role_id")
        verified = interaction.guild.get_role(role_id) if role_id else None

        if verified:
            try:
                await interaction.user.add_roles(verified, reason="Verified via CAPTCHA")
                await interaction.response.send_message(
                    "✅ **Verified!** You now have access to the server.",
                    ephemeral=True
                )
                logger.info(f"User {interaction.user.id} verified in guild {guild_id}")
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Verification passed but couldn't assign role: {e}",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "✅ **Verified!** (No verified role configured — ask an admin to set `.verify config`)",
                ephemeral=True
            )


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

    async def _verify_or_owner(ctx: commands.Context) -> bool:
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
