"""Screenshot cog — capture URLs or generate visuals via AI.

Commands:
  .screenshot / screenshot <url>          — Screenshot a URL (Playwright)
  .screenshot gen / screenshot gen <desc> — AI generates visual from description
  .screenshot info / screenshot info      — Show screenshot system status

Watermark: Semi-transparent with server invite link on all captured screenshots.
"""
import discord
from discord.ext import commands
import logging
import os
import io
import asyncio
import tempfile
from typing import Optional
from pathlib import Path
from bot.config import Config
from bot.models.user_db import UserDatabase
from bot.services.kaufy_runner import KaufyRunner

logger = logging.getLogger("kaufy.screenshot")

# Optional Playwright (CI only)
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.info("Playwright not installed — WeasyPrint fallback")

# WeasyPrint + pdf2image (local/Termux fallback)
try:
    from weasyprint import HTML as WeasyHTML
    from pdf2image import convert_from_bytes
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False
    logger.warning("WeasyPrint/pdf2image not available — sending raw HTML")

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logger.info("Pillow not installed — watermark disabled")

# Watermark config
WATERMARK_TEXT = "kaufy.hall"
WATERMARK_OPACITY = 0.20
INVITE_LINK = "https://discord.gg/6SN3Fmdvht"

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"


class ScreenshotCog(commands.Cog):
    """Screenshot commands — capture URLs or generate visual content via AI."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def _channel_base(self, name: str) -> str:
        return name.split("-")[0] if "-" in name else name

    async def _get_user_plan(self, user_id: int) -> str:
        db = UserDatabase(user_id)
        await db.init()
        return await db.get_config("plan") or "free"

    @commands.group(name="screenshot", invoke_without_command=True, aliases=["ss"])
    async def screenshot_group(self, ctx: commands.Context, *, url: str = None):
        """Capture a screenshot of a URL or generate a visual.
        
        Usage:
          /screenshot <url>     — Capture URL screenshot (requires Playwright)
          /screenshot gen <txt> — AI generates visual from description
          /screenshot info      — System status
        """
        if isinstance(ctx.channel, discord.DMChannel):
            if url:
                await self._capture_url(ctx, url)
            else:
                await ctx.send("📸 Use `/screenshot <url>` or `/screenshot gen <description>`")
        else:
            ch_base = self._channel_base(ctx.channel.name)
            if ch_base == Config.CHANNEL_MSG:
                if url:
                    await self._capture_url(ctx, url)
                else:
                    await ctx.send("📸 Use `/screenshot <url>` or `/screenshot gen <description>`")
            else:
                await ctx.send("Use this command in your #msg channel.")

    async def _render_html_to_png(self, html_path: str) -> Optional[str]:
        """Render a local HTML file to PNG.
        
        Tries Playwright first (CI), then WeasyPrint+pdf2image (local).
        Returns path to the PNG file, or None if rendering failed.
        The PNG will have the server watermark added on capture.
        """
        html_file = Path(html_path).resolve()
        if not html_file.is_file():
            logger.error(f"HTML file not found: {html_path}")
            return None

        png_path = str(html_path).replace(".html", ".png").replace(".htm", ".png")
        if png_path == html_path:
            png_path = html_path + ".png"

        # Method 1: Playwright (CI — best quality, JS support)
        if HAS_PLAYWRIGHT:
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
                    )
                    page = await browser.new_page(viewport={"width": 1280, "height": 720})
                    await page.goto(html_file.as_uri(), timeout=15000, wait_until="networkidle")
                    await asyncio.sleep(1)
                    await page.screenshot(path=png_path, full_page=True)
                    await browser.close()
                self._add_watermark(png_path)
                logger.info(f"Playwright rendered PNG: {png_path}")
                return png_path
            except Exception as e:
                logger.warning(f"Playwright render failed ({e}), trying WeasyPrint...")

        # Method 2: WeasyPrint + pdf2image (local/Termux — no JS, runs sync)
        if HAS_WEASYPRINT:
            try:
                html_content = html_file.read_text("utf-8", errors="replace")
                logger.info(f"WeasyPrint rendering {len(html_content)}b HTML to PNG...")
                pdf_bytes = WeasyHTML(string=html_content).write_pdf()
                logger.info(f"PDF generated: {len(pdf_bytes)} bytes")
                images = convert_from_bytes(pdf_bytes, fmt="png", dpi=150)
                images[0].save(png_path)
                logger.info(f"PNG saved: {png_path}")
                self._add_watermark(png_path)
                logger.info(f"WeasyPrint rendered PNG with watermark: {png_path}")
                return png_path
            except Exception as e:
                logger.error(f"WeasyPrint render failed: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")

        logger.warning("No renderer available — can't convert HTML to PNG")
        return None

    @screenshot_group.command(name="gen")
    async def screenshot_gen(self, ctx: commands.Context, *, description: str):
        """Generate a screenshot/visual via AI based on description.
        
        AI creates HTML, then renders to PNG (Playwright or WeasyPrint).
        
        Example: /screenshot gen a hacker interface with matrix green
        """
        if isinstance(ctx.channel, discord.DMChannel):
            pass
        else:
            ch_base = self._channel_base(ctx.channel.name)
            if ch_base != Config.CHANNEL_MSG:
                return

        plan = await self._get_user_plan(ctx.author.id)
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])

        await ctx.send(f"🎨 **Generating visual:** \"{description}\"...")

        db = UserDatabase(ctx.author.id)
        await db.init()

        from bot.services.owner_auth import owner_auth
        is_owner = await owner_auth.is_owner(ctx.author.id, via_secret=False)

        runner = KaufyRunner(ctx.author.id, db)
        full_response = ""
        all_files = []

        async for event in runner.run_stream(
            prompt=(
                f"Generate a screenshot/visual based on this description: {description}\n\n"
                f"IMPORTANT: Create ONE HTML file in ./output/ "
                f"with the requested visual. The file MUST be pure HTML with inline CSS, "
                f"self-contained (no external dependencies). "
                f"The .html file will be rendered to PNG automatically.\n\n"
                f"After creating the file, briefly explain what was created."
            ),
            temperature=0.9,
            max_tokens=plan_config.get("max_tokens_allowed", 8192),
            username=str(ctx.author),
            is_owner=is_owner,
            plan=plan,
            context_messages=5,
        ):
            if event["type"] == "chunk":
                full_response += event["text"]
            elif event["type"] == "file":
                all_files.append(event["path"])
            elif event["type"] == "error":
                await ctx.send(event["text"])
                full_response = event["text"]

        await runner.stop()

        if not all_files:
            await ctx.send(
                full_response[:1900] if full_response else 'No file was generated. Try a different description.'
            )
            return

        png_sent = False
        for fpath in all_files:
            p = Path(fpath)
            if not p.is_file():
                continue
            ext = p.suffix.lower()

            if ext in (".html", ".htm") and (HAS_PLAYWRIGHT or HAS_WEASYPRINT):
                png_path = await self._render_html_to_png(str(p))
                if png_path:
                    file = discord.File(png_path, filename="screenshot.png")
                    await ctx.send(f"📸 **Visual generated:** \"{description}\"", file=file)
                    png_sent = True
                    try:
                        Path(png_path).unlink()
                    except:
                        pass
                    continue

            try:
                file = discord.File(str(p))
                await ctx.send(
                    f"📄 **File generated:** `{p.name}`\n{full_response[:1000] if not png_sent else ''}",
                    file=file
                )
                png_sent = True
            except Exception as e:
                logger.error(f"Failed to send file {fpath}: {e}")

        if not png_sent:
            await ctx.send(full_response[:1900] if full_response else "✅ Visual generated!")

    @screenshot_group.command(name="info")
    async def screenshot_info(self, ctx: commands.Context):
        """Show screenshot system status."""
        if isinstance(ctx.channel, discord.DMChannel):
            pass
        else:
            ch_base = self._channel_base(ctx.channel.name)
            if ch_base != Config.CHANNEL_MSG:
                return

        lines = [
            "📸 **Screenshot System**\n",
            f"Playwright: {'✅' if HAS_PLAYWRIGHT else '❌'} (CI/browser — JS supported)",
            f"WeasyPrint: {'✅' if HAS_WEASYPRINT else '❌'} (local/Termux — static HTML/CSS)",
            f"Watermark: {'✅' if HAS_PILLOW else '❌'} (Pillow)",
            f"AI Screenshot Gen: ✅ Always available",
            f"Output dir: `./output/` (auto-captured)",
            "",
            "**How to use:**",
            "`/screenshot <url>` — Capture URL (requires Playwright on CI)",
            "`/screenshot gen <desc>` — AI generates visual from description",
            "Or just ASK Kaufy to create a screenshot in conversation!",
        ]
        if not HAS_PLAYWRIGHT:
            lines.append("")
            lines.append("💡 Owner can install Playwright with:")
            lines.append("`pip install playwright && playwright install chromium`")

        await ctx.send("\n".join(lines))

    def _add_watermark(self, image_path: str) -> str:
        """Add semi-transparent watermark to a screenshot image."""
        if not HAS_PILLOW:
            logger.warning("Pillow not available — skipping watermark")
            return image_path

        try:
            img = Image.open(image_path).convert("RGBA")
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            font = None
            for font_path in [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            ]:
                if Path(font_path).exists():
                    font_size = max(img.width // 25, 20)
                    try:
                        font = ImageFont.truetype(font_path, font_size)
                    except:
                        pass
                    break

            watermark_text = f"{WATERMARK_TEXT}  •  {INVITE_LINK}"

            padding = 20
            if font:
                bbox = draw.textbbox((0, 0), watermark_text, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            else:
                tw, th = len(watermark_text) * 8, 16

            x = img.width - tw - padding
            y = img.height - th - padding

            bar_height = th + 20
            bar = Image.new("RGBA", (tw + 40, bar_height), (0, 0, 0, 180))
            overlay.paste(bar, (x - 10, y - 10), bar)

            alpha = int(255 * WATERMARK_OPACITY)
            fill_color = (255, 255, 255, alpha)
            if font:
                draw.text((x, y), watermark_text, font=font, fill=fill_color)
            else:
                draw.text((x, y), watermark_text, fill=fill_color)

            img = Image.alpha_composite(img, overlay).convert("RGB")
            img.save(image_path, "PNG")
            logger.info(f"Watermark added to {image_path}")
        except Exception as e:
            logger.error(f"Watermark failed: {e}")

        return image_path

    async def _capture_url(self, ctx: commands.Context, url: str):
        """Capture screenshot of a URL using Playwright, with watermark."""
        if not HAS_PLAYWRIGHT:
            return await ctx.send(
                "❌ Playwright is not installed (not available on Termux/Android).\n\n"
                "✅ **`/screenshot gen <desc>` works locally** "
                "— generates HTML/CSS via AI and renders to PNG "
                "with WeasyPrint + pdf2image.\n\n"
                "Or ask the owner to run on GitHub Actions (Playwright available on CI)."
            )

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        await ctx.send(f"📸 Capturing `{url}`...")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
                page = await browser.new_page(viewport={"width": 1280, "height": 720})
                await page.goto(url, timeout=30000, wait_until="networkidle")

                screenshot_path = SCREENSHOT_DIR / f"screenshot_{ctx.author.id}_{int(asyncio.get_event_loop().time())}.png"
                await page.screenshot(path=str(screenshot_path), full_page=False)
                await browser.close()

                self._add_watermark(str(screenshot_path))

                file = discord.File(str(screenshot_path), filename="screenshot.png")
                await ctx.send(f"📸 **Screenshot of:** {url}", file=file)

                try:
                    screenshot_path.unlink()
                except:
                    pass

        except Exception as e:
            await ctx.send(f"❌ Error capturing screenshot: {str(e)[:200]}")

    @commands.hybrid_command(name="invite")
    async def server_invite(self, ctx: commands.Context):
        """Create a permanent invite link for the server."""
        if not ctx.guild or ctx.guild.id != Config.GUILD_ID:
            return await ctx.send("This command only works in Kaufy's Hall.")

        try:
            target = ctx.guild.system_channel or ctx.channel
            invite = await target.create_invite(
                max_age=0, max_uses=0, reason="Permanent invite"
            )
            await ctx.send(f"📨 **Permanent invite:** {invite.url}")
            logger.info(f"Permanent invite created: {invite.url}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to create invites.")
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)[:200]}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ScreenshotCog(bot))
