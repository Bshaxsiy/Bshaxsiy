import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from handlers import router
from guard_middleware import GuardMiddleware

# Logging sozlamalari
log_handlers = []
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))
else:
    from config import BASE_DIR
    log_handlers.append(logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=log_handlers
)
logger = logging.getLogger(__name__)


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

    # Oldingi kutilayotgan yangilanishlarni tozalash (webhook o'chirish)
    await bot.delete_webhook(drop_pending_updates=True)

    bot_info = await bot.get_me()
    logger.info(f"Bot muvaffaqiyatli ishga tushdi: @{bot_info.username} ({bot_info.full_name})")

    try:
        # Polling rejimida ishlash
        await dp.start_polling(bot)
    finally:
        # Bot sessiyasini yopish
        await bot.session.close()
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot foydalanuvchi tomonidan to'xtatildi.")
