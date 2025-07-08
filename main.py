# main.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import signal
import platform
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import List

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from config import API_TOKEN
from handlers.database import (
    init_db,
    setup_scheduler,
    delete_all_payment_methods,
    get_users_without_payment_methods,
    get_user_transactions,
)
from handlers.handlers import (
    router,
    setup_notification_scheduler,
    auto_payments_agreement,
    setup_dp_instance
)
from handlers.database import set_bot_instance
from handlers.payments import get_payment_info, create_auto_payment
from handlers.utils import once_per_string

from handlers.scheduler import process_auto_payments
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

payScheduler = AsyncIOScheduler()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

class BotFactory:
    """
    Фабрика для создания экземпляров бота с предустановленными параметрами.
    
    Complexity: O(1)
    """
    @staticmethod
    def create_bot(token: str) -> Bot:
        """
        Создает экземпляр бота с настроенными параметрами.
        
        Args:
            token (str): API токен бота
            
        Returns:
            Bot: Настроенный экземпляр бота
        """
        return Bot(
            token=token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
            )
        )

@lru_cache(maxsize=1)
def get_bot_commands() -> List[BotCommand]:
    """
    Получение списка команд бота с кэшированием.
    
    Returns:
        List[BotCommand]: Список команд бота
    
    Complexity: O(1) благодаря кэшированию
    """
    return [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="connect", description="Подключиться"),
    ]

class RetryStrategy:
    """
    Стратегия повторных попыток для операций с возможными сбоями.
    """
    def __init__(self, max_retries: int = 3, delay: float = 1.0):
        self.max_retries = max_retries
        self.delay = delay

    async def execute(self, operation, *args, **kwargs):
        """
        Выполняет операцию с повторными попытками в случае сбоя.
        
        Args:
            operation: Асинхронная функция для выполнения
            *args: Позиционные аргументы для операции
            **kwargs: Именованные аргументы для операции
            
        Raises:
            Exception: Если все попытки завершились неудачно
        """
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(f"Попытка {attempt + 1} не удалась: {e}")
                    await asyncio.sleep(self.delay)
                else:
                    logger.error(f"Все попытки исчерпаны. Последняя ошибка: {e}")
                    raise last_error

async def set_commands(bot: Bot) -> None:
    """
    Установка команд бота с механизмом повторных попыток.
    
    Args:
        bot (Bot): Экземпляр бота
        
    Raises:
        TelegramAPIError: При неудачной попытке установки команд
    """
    retry_strategy = RetryStrategy()
    async def set_commands_operation():
        commands = get_bot_commands()
        await bot.set_my_commands(commands=commands, scope=BotCommandScopeDefault())
        logger.info("Команды бота успешно установлены")
    
    await retry_strategy.execute(set_commands_operation)

@asynccontextmanager
async def bot_lifecycle(bot: Bot, dp: Dispatcher):
    """
    Контекстный менеджер для управления жизненным циклом бота.
    
    Args:
        bot (Bot): Экземпляр бота
        dp (Dispatcher): Экземпляр диспетчера
    """
    await process_auto_payments(bot)

    payScheduler.add_job(
        lambda: process_auto_payments(bot),
        trigger=CronTrigger(hour=10, minute=0),
        id='auto_payments',
        replace_existing=True
    )

    # Запуск планировщика
    payScheduler.start()

    scheduler = setup_scheduler()
    notification_scheduler = setup_notification_scheduler(bot)
    
    try:
        yield
    finally:
        logger.info("Graceful shutdown...")
        await notification_scheduler.shutdown()
        scheduler.shutdown()
        await bot.session.close()

def setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """
    Настройка обработчиков сигналов для graceful shutdown.
    
    Args:
        loop (asyncio.AbstractEventLoop): Event loop
    """
    if platform.system() != 'Windows':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(loop, sig)))

async def shutdown(loop: asyncio.AbstractEventLoop, signal: signal.Signals) -> None:
    """
    Корректное завершение работы приложения.
    
    Args:
        loop (asyncio.AbstractEventLoop): Event loop
        signal (signal.Signals): Полученный сигнал
    """
    logger.info(f"Получен сигнал {signal.name}, начинаю graceful shutdown...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

class DatabaseConnection:
    """
    Управление подключением к базе данных.
    """
    @staticmethod
    async def initialize() -> None:
        """
        Инициализация подключения к базе данных с обработкой ошибок.
        
        Raises:
            Exception: При ошибке инициализации базы данных
        """
        try:
            logger.info("Подключаюсь к базе данных...")
            await init_db()
            logger.info("Подключение к базе данных успешно установлено")
        except Exception as e:
            logger.error(f"Ошибка при инициализации базы данных: {e}")
            raise

async def payment_method_migration(bot: Bot):
    """
    1. Удаляет всем пользователям способ оплаты
    2. Находит пользователей, у которых нет способов оплаты (всех)
    3. Для каджого такого пользователя проверяет, был ли способ оплаты сохранён хоть раз
    3. Рассылает каждому такому пользователю сообщение с согласием на автооплаты
    4. В сообщении кнопка - автоматически мигрирует его платежные данные
    """
    await delete_all_payment_methods()

    users = await get_users_without_payment_methods()

    for user in users:
        user_id = user["user_id"]
        transactions = await get_user_transactions(user_id)

        payment_methods = set()
        for t in transactions:
            payment_info = await get_payment_info(t["transaction_id"])

            if payment_info.payment_method.saved:
                payment_methods.add(payment_info.payment_method)
        payment_methods = list(payment_methods)

        if payment_methods:
            await auto_payments_agreement(
                bot=bot,
                user_id=user_id,
                payment_methods=payment_methods
            )

async def start_bot(bot: Bot, dp: Dispatcher) -> None:
    """
    Запуск бота с предварительной настройкой.
    
    Args:
        bot (Bot): Экземпляр бота
        dp (Dispatcher): Экземпляр диспетчера
    """
    try:
        await set_commands(bot)
        set_bot_instance(bot)
        logger.info("Бот запущен и готов к работе")

        update_string = "Миграция платежных данных"
        async for _ in once_per_string(update_string):
            await payment_method_migration(bot)

        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка при работе бота: {e}")
        raise

async def main() -> None:
    """
    Основная функция запуска бота.
    
    Complexity: O(1) для инициализации
    Dependencies: aiogram, asyncio
    """
    bot = BotFactory.create_bot(API_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    loop = asyncio.get_event_loop()
    setup_signal_handlers(loop)
    setup_dp_instance(dp)

    await DatabaseConnection.initialize()

    async with bot_lifecycle(bot, dp):
        await start_bot(bot, dp)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise