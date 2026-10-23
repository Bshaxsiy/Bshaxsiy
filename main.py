import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from handlers import router
from guard_middleware import GuardMiddleware

import os
from config import BASE_DIR

# Agar pythonw (orqa fon / GUI) rejimida ishlayotgan bo'lsa, stderr va stdout ni xavfsiz yo'naltirish
is_gui = "pythonw" in sys.executable.lower() or sys.stdout is None
if is_gui:
    try:
        sys.stderr = open(BASE_DIR / "bot_error.log", "a", encoding="utf-8")
    except Exception:
        pass

log_handlers = [
    logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8")
]
if not is_gui and sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=log_handlers
)
logger = logging.getLogger(__name__)


async def start_health_server():
    """
    Render (Web Service) uchun port tinglovchi sodda HTTP server.
    Faqat PORT muhit o'zgaruvchisi berilganda ishga tushadi (noutbukda ishlamaydi).
    """
    port = os.getenv("PORT")
    if not port:
        return None
    from aiohttp import web

    async def health(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(port))
    await site.start()
    logger.info(f"Salomatlik serveri {port}-portda ishga tushdi.")
    return runner


async def keep_alive_loop():
    """
    Render bepul tarifida xizmat uxlab qolmasligi uchun o'z /healthz manzilini
    har 10 daqiqada chaqirib turadi. Foydalanuvchilarga ko'rinmaydi.
    Faqat Render (RENDER_EXTERNAL_URL mavjud bo'lsa) ishlaydi.
    """
    base_url = os.getenv("RENDER_EXTERNAL_URL")
    if not base_url:
        return
    import aiohttp

    target = base_url.rstrip("/") + "/healthz"
    await asyncio.sleep(60)
    while True:
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(target) as resp:
                    await resp.read()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Keep-alive so'rovi bajarilmadi: {e}")
        await asyncio.sleep(600)


async def main():
    """
    Botni ishga tushiruvchi asosiy funksiya
    """
    logger.info("Bot ishga tushirilmoqda...")

    # Bot va Dispatcher yaratish
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Qo'riqchi (Access Guard) middleware-ni ro'yxatdan o'tkazish
    dp.message.outer_middleware(GuardMiddleware())
    dp.callback_query.outer_middleware(GuardMiddleware())

    # Routerlarni ulash
    dp.include_router(router)

    health_runner = await start_health_server()
    keep_alive_task = asyncio.create_task(keep_alive_loop())

    # Oldingi kutilayotgan yangilanishlarni tozalash (webhook o'chirish)
    await bot.delete_webhook(drop_pending_updates=True)

    bot_info = await bot.get_me()
    logger.info(f"Bot muvaffaqiyatli ishga tushdi: @{bot_info.username} ({bot_info.full_name})")

    try:
        # Polling rejimida ishlash
        await dp.start_polling(bot)
    finally:
        # Bot sessiyasini yopish
        keep_alive_task.cancel()
        if health_runner is not None:
            await health_runner.cleanup()
        await bot.session.close()
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot foydalanuvchi tomonidan to'xtatildi.")
    except Exception:
        logger.exception("Botda kutilmagan xatolik yuz berdi:")
