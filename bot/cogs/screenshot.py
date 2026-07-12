"""Screenshot cog — tira screenshots de URLs ou gera visuais via AI.

Comandos:
  .screenshot [url]          — Tira screenshot de uma URL (com watermark do servidor)
  .screenshot gen <desc>     — Pede pra AI gerar um screenshot/visual baseado em descrição
  .screenshot info           — Mostra status do sistema de screenshot

O modelo AI também pode GERAR screenshots autônomamente:
- Escrevendo HTML/CSS/SVG em ./output/
- Usando playwright se disponível
- Gerando representações visuais de código, conceitos, etc.

Watermark: Adiciona marca d'água semi-transparente com o link de invite
do servidor em todos os screenshots capturados.
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

# Tentar importar playwright (opcional — usado no CI)
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.info("Playwright não instalado — fallback pra WeasyPrint")

# WeasyPrint + pdf2image como fallback local (Termux-friendly)
try:
    from weasyprint import HTML as WeasyHTML
    from pdf2image import convert_from_bytes
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False
    logger.warning("WeasyPrint/pdf2image não disponível — .gen envia HTML puro")

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logger.info("Pillow not installed — watermark disabled")

# Config watermark
WATERMARK_TEXT = "kaufy.hall"
WATERMARK_OPACITY = 0.20  # 20% opacity — semi-transparent
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

    @commands.group(name="screenshot", invoke_without_command=True)
    async def screenshot_group(self, ctx: commands.Context, *, url: str = None):
        """Tirar screenshot de uma URL ou gerar visual.

        Uso:
          .screenshot <url>     — Captura screenshot de URL (requer playwright)
          .screenshot gen <txt> — AI gera visual baseado em descrição
          .screenshot info      — Status do sistema
        """
        ch_base = self._channel_base(ctx.channel.name)
        if ch_base == Config.CHANNEL_MSG:
            if url:
                await self._capture_url(ctx, url)
            else:
                await ctx.send("📸 Use `.screenshot <url>` ou `.screenshot gen <descrição>`")
        else:
            await ctx.send("Use este comando no seu canal #msg.")

    async def _render_html_to_png(self, html_path: str) -> Optional[str]:
        """Render a local HTML file to PNG.
        
        Tries Playwright first (CI), then WeasyPrint+pdf2image (local).
        Returns path to the PNG file, or None if rendering failed.
        The PNG will have the server watermark added.
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
                        headless=True,
                        args=["--no-sandbox", "--disable-setuid-sandbox"]
                    )
                    page = await browser.new_page(
                        viewport={"width": 1280, "height": 720}
                    )
                    await page.goto(html_file.as_uri(), timeout=15000, wait_until="networkidle")
                    await asyncio.sleep(1)
                    await page.screenshot(path=png_path, full_page=True)
                    await browser.close()
                self._add_watermark(png_path)
                logger.info(f"Playwright rendered PNG: {png_path}")
                return png_path
            except Exception as e:
                logger.warning(f"Playwright render failed ({e}), trying WeasyPrint...")

        # Method 2: WeasyPrint + pdf2image (local/Termux — no JS)
        # NOTA: WeasyPrint usa Pango/Cairo (C extensions) — NÃO roda em
        # thread pool (run_in_executor). Rodamos síncrono direto (rápido).
        if HAS_WEASYPRINT:
            try:
                html_content = html_file.read_text("utf-8", errors="replace")
                pdf_bytes = WeasyHTML(string=html_content).write_pdf()
                images = convert_from_bytes(pdf_bytes, fmt="png", dpi=150)
                images[0].save(png_path)
                self._add_watermark(png_path)
                logger.info(f"WeasyPrint rendered PNG: {png_path}")
                return png_path
            except Exception as e:
                logger.error(f"WeasyPrint render failed: {e}")
                logger.debug("Traceback:", exc_info=True)

        logger.warning("No renderer available — can't convert HTML to PNG")
        return None

    @screenshot_group.command(name="gen")
    async def screenshot_gen(self, ctx: commands.Context, *, description: str):
        """Gerar screenshot/visual via AI baseado em descrição.
        
        Gera HTML via AI, renderiza pra PNG com Playwright (se disponível)
        e envia a imagem com watermark do servidor.
        
        Exemplo: .screenshot gen uma interface de hack com matrix green
        """
        ch_base = self._channel_base(ctx.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            return

        plan = await self._get_user_plan(ctx.author.id)
        plan_config = Config.PLANS.get(plan, Config.PLANS["free"])

        await ctx.send(f"🎨 **Gerando visual:** \"{description}\"...")

        db = UserDatabase(ctx.author.id)
        await db.init()

        is_owner = ctx.author.id in Config.OWNER_IDS

        # Use streaming for the AI generation
        runner = KaufyRunner(ctx.author.id, db)
        full_response = ""
        all_files = []

        async for event in runner.run_stream(
            prompt=(
                f"Gere um screenshot/visual baseado nesta descrição: {description}\n\n"
                f"IMPORTANTE: Crie UM ARQUIVO HTML em ./output/ "
                f"com o visual solicitado. O arquivo DEVE ser HTML puro com CSS inline, "
                f"completo e auto-contido (sem dependências externas). "
                f"O arquivo .html será renderizado para PNG automaticamente.\n\n"
                f"Depois de criar o arquivo, explique brevemente o que foi criado."
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
                f"{full_response[:1900] if full_response else 'Nenhum arquivo foi gerado. Tente uma descrição diferente.'}"
            )
            return

        # Try to render HTML→PNG with Playwright
        png_sent = False
        for fpath in all_files:
            p = Path(fpath)
            if not p.is_file():
                continue
            ext = p.suffix.lower()

            if ext in (".html", ".htm") and (HAS_PLAYWRIGHT or HAS_WEASYPRINT):
                # Render to PNG
                png_path = await self._render_html_to_png(str(p))
                if png_path:
                    file = discord.File(png_path, filename="screenshot.png")
                    await ctx.send(
                        f"📸 **Visual gerado:** \"{description}\"",
                        file=file
                    )
                    png_sent = True
                    # Clean up temp files
                    try:
                        Path(png_path).unlink()
                    except:
                        pass
                    continue

            # Fallback: send original file
            try:
                file = discord.File(str(p))
                await ctx.send(
                    f"📄 **Arquivo gerado:** `{p.name}`\n{full_response[:1000] if not png_sent else ''}",
                    file=file
                )
                png_sent = True
            except Exception as e:
                logger.error(f"Failed to send file {fpath}: {e}")

        if not png_sent:
            await ctx.send(full_response[:1900] if full_response else "✅ Visual gerado!")

    @screenshot_group.command(name="info")
    async def screenshot_info(self, ctx: commands.Context):
        """Mostrar status do sistema de screenshot."""
        ch_base = self._channel_base(ctx.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            return

        lines = [
            "📸 **Screenshot System**\n",
            f"Playwright: {'✅' if HAS_PLAYWRIGHT else '❌'} (CI/browser — JS suportado)",
            f"WeasyPrint: {'✅' if HAS_WEASYPRINT else '❌'} (local/Termux — HTML/CSS estático)",
            f"Watermark: {'✅' if HAS_PILLOW else '❌'} (Pillow)",
            f"AI Screenshot Gen: ✅ Sempre disponível",
            f"Output dir: `./output/` (capturado automaticamente)",
            "",
            "**Como usar:**",
            "`.screenshot <url>` — Captura URL (requer playwright)",
            "`.screenshot gen <desc>` — AI gera visual baseado em descrição",
            "Ou simplesmente PEÇA pra Kaufy criar um screenshot na conversa!",
        ]
        if not HAS_PLAYWRIGHT:
            lines.append("")
            lines.append("💡 Dica: O dono pode instalar playwright com:")
            lines.append("`pip install playwright && playwright install chromium`")

        await ctx.send("\n".join(lines))

    def _add_watermark(self, image_path: str) -> str:
        """Add semi-transparent watermark to a screenshot image.
        
        Returns the path to the watermarked image (same file modified in-place).
        """
        if not HAS_PILLOW:
            logger.warning("Pillow not available — skipping watermark")
            return image_path

        try:
            img = Image.open(image_path).convert("RGBA")
            
            # Create watermark overlay
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            
            # Try to load a font, fall back to default
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
            
            # Watermark text
            watermark_text = f"{WATERMARK_TEXT}  •  {INVITE_LINK}"
            
            # Position: bottom-right corner with padding
            padding = 20
            if font:
                bbox = draw.textbbox((0, 0), watermark_text, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            else:
                tw, th = len(watermark_text) * 8, 16
            
            x = img.width - tw - padding
            y = img.height - th - padding
            
            # Draw shadow/background bar for readability
            bar_height = th + 20
            bar = Image.new("RGBA", (tw + 40, bar_height), (0, 0, 0, 180))
            overlay.paste(bar, (x - 10, y - 10), bar)
            
            # Draw watermark text
            alpha = int(255 * WATERMARK_OPACITY)
            fill_color = (255, 255, 255, alpha)
            if font:
                draw.text((x, y), watermark_text, font=font, fill=fill_color)
            else:
                draw.text((x, y), watermark_text, fill=fill_color)
            
            # Composite
            img = Image.alpha_composite(img, overlay).convert("RGB")
            img.save(image_path, "PNG")
            logger.info(f"Watermark added to {image_path}")
            
        except Exception as e:
            logger.error(f"Watermark failed: {e}")
        
        return image_path

    async def _capture_url(self, ctx: commands.Context, url: str):
        """Capture screenshot of a URL using playwright, with watermark."""
        if not HAS_PLAYWRIGHT:
            return await ctx.send(
                "❌ Playwright não está instalado (indisponível no Termux/Android).\n\n"
                "✅ **`.screenshot gen <desc>` já funciona localmente** "
                "— gera HTML/CSS por AI e renderiza pra PNG "
                "com WeasyPrint + pdf2image.\n\n"
                "Ou peça pro dono rodar no GitHub Actions (Playwright disponível no CI)."
            )

        # Add protocol if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        await ctx.send(f"📸 Capturando `{url}`...")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
                page = await browser.new_page(
                    viewport={"width": 1280, "height": 720}
                )
                await page.goto(url, timeout=30000, wait_until="networkidle")

                # Take screenshot
                screenshot_path = SCREENSHOT_DIR / f"screenshot_{ctx.author.id}_{int(asyncio.get_event_loop().time())}.png"
                await page.screenshot(path=str(screenshot_path), full_page=False)
                await browser.close()

                # Add watermark
                self._add_watermark(str(screenshot_path))

                # Send
                file = discord.File(str(screenshot_path), filename="screenshot.png")
                await ctx.send(f"📸 **Screenshot de:** {url}", file=file)

                # Clean up
                try:
                    screenshot_path.unlink()
                except:
                    pass

        except Exception as e:
            await ctx.send(f"❌ Erro ao capturar screenshot: {str(e)[:200]}")

    @commands.command(name="invite")
    async def server_invite(self, ctx: commands.Context):
        """Create a permanent invite link for this server."""
        # Only allow in the main guild
        if ctx.guild.id != Config.GUILD_ID:
            return await ctx.send("This command only works in Kaufy's Hall.")

        try:
            # Find a good channel for the invite (system channel or first text channel)
            target = ctx.guild.system_channel or ctx.channel
            invite = await target.create_invite(
                max_age=0,       # Never expires
                max_uses=0,      # Unlimited uses
                reason="Permanent invite requested by owner"
            )
            await ctx.send(f"📨 **Permanent invite created:** {invite.url}")
            logger.info(f"Permanent invite created: {invite.url}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to create invites. "
                          "Please give me the `Create Invite` permission.")
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)[:200]}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ScreenshotCog(bot))
