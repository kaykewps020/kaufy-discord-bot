"""Screenshot cog — tira screenshots de URLs ou gera visuais via AI.

Comandos:
  .screenshot [url]          — Tira screenshot de uma URL (se playwright disponível)
  .screenshot gen <desc>     — Pede pra AI gerar um screenshot/visual baseado em descrição
  .screenshot info           — Mostra status do sistema de screenshot

O modelo AI também pode GERAR screenshots autônomamente:
- Escrevendo HTML/CSS/SVG em ./output/
- Usando playwright se disponível
- Gerando representações visuais de código, conceitos, etc.
"""
import discord
from discord.ext import commands
import logging
import os
import io
import asyncio
import tempfile
from pathlib import Path
from bot.config import Config
from bot.models.user_db import UserDatabase
from bot.services.kaufy_runner import KaufyRunner

logger = logging.getLogger("kaufy.screenshot")

# Tentar importar playwright (opcional)
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.info("Playwright not installed — screenshot will use AI generation")

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

    @screenshot_group.command(name="gen")
    async def screenshot_gen(self, ctx: commands.Context, *, description: str):
        """Gerar screenshot/visual via AI baseado em descrição.
        
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

        runner = KaufyRunner(ctx.author.id, db)
        response, files = await runner.run(
            prompt=(
                f"Gere um screenshot/visual baseado nesta descrição: {description}\n\n"
                f"IMPORTANTE: Crie UM ARQUIVO HTML ou SVG em ./output/ "
                f"com o visual solicitado. O arquivo DEVE ser completo e auto-contido "
                f"(sem dependências externas). Use CSS inline. O arquivo será "
                f"automaticamente capturado e enviado ao usuário.\n\n"
                f"Depois de criar o arquivo, explique brevemente o que foi criado."
            ),
            temperature=0.9,
            max_tokens=plan_config.get("max_tokens_allowed", 8192),
            username=str(ctx.author),
            is_owner=is_owner,
            plan=plan,
            context_messages=5,
        )
        await runner.stop()

        # Send response + any generated files
        clean_response = response
        file_objs = []
        for fpath in files or []:
            p = Path(fpath)
            if p.is_file():
                try:
                    file_objs.append(discord.File(str(p)))
                except Exception as e:
                    logger.error(f"Failed to attach file {fpath}: {e}")

        if file_objs:
            await ctx.send(
                f"📸 **Visual gerado:**\n{clean_response[:1500] if clean_response else 'Arquivo gerado!'}",
                files=file_objs
            )
        else:
            await ctx.send(
                f"{clean_response[:1900] if clean_response else 'Nenhum arquivo foi gerado. Tente uma descrição diferente.'}"
            )

    @screenshot_group.command(name="info")
    async def screenshot_info(self, ctx: commands.Context):
        """Mostrar status do sistema de screenshot."""
        ch_base = self._channel_base(ctx.channel.name)
        if ch_base != Config.CHANNEL_MSG:
            return

        lines = [
            "📸 **Screenshot System**\n",
            f"Playwright: {'✅ Instalado' if HAS_PLAYWRIGHT else '❌ Não instalado'}",
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

    async def _capture_url(self, ctx: commands.Context, url: str):
        """Capture screenshot of a URL using playwright."""
        if not HAS_PLAYWRIGHT:
            return await ctx.send(
                "❌ Playwright não está instalado. Use `.screenshot gen <desc>` "
                "para gerar visuais via AI, ou peça pro dono instalar playwright."
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


async def setup(bot: commands.Bot):
    await bot.add_cog(ScreenshotCog(bot))
