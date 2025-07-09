# handlers.scheduler.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import tzlocal
from typing import Dict

from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from config import API_TOKEN, SUPPORT_URI
from handlers.database import (
    update_balance,
    update_key_expriration_date,
    get_all_users_with_subscription,
    get_user_payment_methods,
    update_user_subscription,
    get_admins,
    get_all_keys_to_expire,
    remove_expired_keys,
    check_expiring_subscriptions,
    remove_key,
    get_user,
    check_expiring_in_3_days_subscriptions
)
from handlers.payments import create_auto_payment, check_payment_status, PAYMENT_TYPES
from handlers.utils import send_info_for_admins, unix_to_str

logger = logging.getLogger(__name__)

# Удаляем глобальную инициализацию бота
# bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

active_jobs = []
Scheduler = AsyncIOScheduler()
def remove_job(id: str):
    if id in active_jobs:
        try:
            Scheduler.remove_job(id)
            active_jobs.remove(id)
        except JobLookupError as e:
            logger.warning(f"По какой-то причине не нашли работу: {id}")
        except ValueError:
            logger.warning(f"По какой-то причине не нашли работу в списке active_jobs: {id}")

async def process_auto_payments(bot=None):
    """
    Основная функция для обработки автоматических платежей
    
    Args:
        bot (Bot, optional): Экземпляр бота для отправки уведомлений
    """
    logger.info("Запуск процесса автоматических платежей")

    admins = await get_admins()

    if not Scheduler.running:
        Scheduler.start()
    
    try:
        # Если бот не передан, создаем временный экземпляр
        if bot is None:
            bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            need_to_close = True
        else:
            need_to_close = False
        
        # Получаем всех пользователей с подпиской
        keys = await get_all_keys_to_expire()

        if keys:
            logger.info(f"Получено {len(keys)} ключей, которые истекают сегодня.")
            
            # Информируем администраторов о начале процесса
            await send_info_for_admins(
                f"🔄 Запущен процесс проверки автоматических платежей\n"
                f"Количество ключей для проверки: {len(keys)}",
                admins,
                bot
            )
            
            # Удаляем неиспользуемую переменную today
            payment_count = 0
            
            # Обрабатываем каждого пользователя
            for key in keys:
                success = await process_key_payment(key, bot)
                if success:
                    payment_count += 1

                    key_str = key["key"]
                    job_id = f'remove_{key_str}'
                    
                    remove_job(job_id)
                else:
                    key_str = key["key"]
                    user_id = key["user_id"]

                    run_time = datetime.now(tz=tzlocal.get_localzone()) + timedelta(days=1)
                    
                    job_id = f'remove_{key_str}'
                    job = Scheduler.add_job(
                        remove_key,
                        trigger=DateTrigger(run_date=run_time),
                        id=job_id,
                        name=f'Remove_{key_str}',
                        replace_existing=True,
                        args=[key_str, user_id]
                    )
                    active_jobs.append(job_id)
                        
                # Небольшая пауза между обработкой пользователей
                await asyncio.sleep(1)
            
            # Информируем администраторов о завершении процесса
            await send_info_for_admins(
                f"✅ Процесс автоматических платежей завершен\n"
                f"Проверено ключей: {len(keys)}\n"
                f"Выполнено платежей: {payment_count}",
                admins,
                bot
            )

        await send_info_for_admins(
            "Начинаем рассылку уведомлений о истекающих ключах",
            admins,
            bot
        )

        keys_to_expire_tomorrow = await check_expiring_subscriptions()
        logger.info(f"Найдено {len(keys_to_expire_tomorrow)} ключей, которые истекают завтра")
        for key in keys_to_expire_tomorrow:
            user_id = key["user_id"]
            expiration_date = unix_to_str(key['expiration_date'], include_time=False)
            payment_methods, balance = await get_user_payment_methods(user_id, include_balance=True)
            if (not payment_methods) and ((key["price"] is None) or (balance < key["price"])):
                from handlers.handlers import send_manual_renewal_notification
                await send_manual_renewal_notification(user_id, expiration_date, bot, when="завтра", key=key)
        
        keys_to_expire_in_3_days = await check_expiring_in_3_days_subscriptions()
        logger.info(f"Найдено {len(keys_to_expire_in_3_days)} ключей, которые истекают через 3 дня")
        for key in keys_to_expire_in_3_days:
            user_id = key["user_id"]
            expiration_date = unix_to_str(key['expiration_date'], include_time=False)
            payment_methods, balance = await get_user_payment_methods(user_id, include_balance=True)
            if (not payment_methods) and ((key["price"] is None) or (balance < key["price"])):
                from handlers.handlers import send_manual_renewal_notification
                await send_manual_renewal_notification(user_id, expiration_date, bot, when="через 3 дня", key=key)

        # Закрываем сессию бота, если мы его создали
        if need_to_close:
            await bot.session.close()
            
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении автоматических платежей: {str(e)}"
        logger.error(error_msg)
        
        # Информируем администраторов об ошибке
        admins = await get_admins()
        await send_info_for_admins(error_msg, admins, bot)

async def process_key_payment(key: Dict, bot: Bot) -> bool:
    """
    Обрабатывает автоматический платеж для конкретного пользователя,
    пытаясь списать деньги со всех сохраненных методов оплаты
    
    Args:
        user (dict): Информация о пользователе
    """
    user_id = key['user_id']

    subscription_end = unix_to_str(key['expiration_date'], include_time=True)
    subscription_end_no_time = unix_to_str(key['expiration_date'], include_time=False)
    
    logger.info(f"Обработка автоматического платежа для пользователя {user_id}")
    
    try:
        # 1. Попытаемся списать с счета в боте
        user_info = await get_user(user_id)

        payment_attempts = 0
        payment_success = False

        if key["price"] is None:
            from handlers.handlers import ask_for_key_period
            await ask_for_key_period(key, user_id, bot)
            return False
        
        key_price = int(key["price"])
        days = int(key["days"])

        if int(user_info["balance"]) >= key_price:
            payment_attempts += 1
            await pay_with_int_balance(user_id, int(user_info["balance"]), key_price)
            successful_method = { "id": "Внутренний баланс" }
            successful_type = "Внутренний баланс"
            payment_success = True
        else:
            # Получаем все методы оплаты пользователя
            payment_methods = await get_user_payment_methods(user_id)
            
            if not payment_methods:
                logger.info(f"У пользователя {user_id} нет сохраненных методов оплаты")
                # Уведомляем пользователя о необходимости продления вручную
                from handlers.handlers import send_manual_renewal_notification
                await send_manual_renewal_notification(user_id, subscription_end, bot, key=key)
                return False
            
            # Флаг успешного платежа
            successful_method = None
            
            # Пробуем списать деньги с каждого метода оплаты
            for payment_method in payment_methods:
                payment_method_id = payment_method['payment_method_id']
                
                # Формируем описание платежа
                description = f"Автоматическое продление подписки VPN"
                
                try:
                    # Создаем автоматический платеж
                    payment_id = await create_auto_payment(
                        amount=key_price,
                        description=description,
                        saved_method_id=payment_method_id
                    )
                    
                    payment_attempts += 1
                    
                    # Проверяем статус платежа
                    payment_success, saved_payment_method_type, payment = await check_payment_status(payment_id, key_price, second_arg="type")
                    
                    if payment_success:
                        # Платеж успешен, запоминаем метод и сумму
                        successful_method = payment_method
                        successful_type = saved_payment_method_type
                        
                        # Логируем успешный платеж
                        logger.info(f"Успешное списание с метода оплаты ID: {payment_method['id']} для пользователя {user_id}")
                        
                        # Прекращаем перебор методов оплаты
                        break
                    else:
                        # Логируем неудачную попытку
                        logger.warning(f"Неудачное списание с метода оплаты ID: {payment_method['id']} для пользователя {user_id}")
                
                except Exception as e:
                    logger.error(f"Ошибка при попытке списания с метода оплаты ID: {payment_method['id']}: {str(e)}")
                    continue
            
        # Обрабатываем результат попыток списания
        if payment_success:
            # Платеж успешен, продлеваем подписку
            expiration_date_milliseconds = key['expiration_date']
            timestamp_s = int(expiration_date_milliseconds) / 1000  # делим на 1000 — получаем секунды
            dt = datetime.fromtimestamp(timestamp_s)
            new_dt = dt + timedelta(days=days)
            new_end_date_ms = int(new_dt.timestamp() * 1000)
            dt = datetime.strptime(subscription_end, "%d.%m.%Y %H:%M")
            new_end_date = dt + timedelta(days=int(days))
            await update_user_subscription(user_id, str(new_end_date.isoformat()))
            
            # Уведомляем пользователя об успешном продлении
            from handlers.handlers import send_success_payment_notification
            await send_success_payment_notification(user_id,
                                                    key_price,
                                                    new_end_date.isoformat(),
                                                    days,
                                                    successful_type,
                                                    bot)
            
            await update_key_expriration_date(key=key["key"], new_end_date=new_end_date_ms)

            # Информируем администраторов
            admins = await get_admins()
            await send_info_for_admins(
                f"✅ Автоматическое продление для пользователя {user_id}\n"
                f"Сумма: {key_price}₽\n"
                f"Метод оплаты ID: {successful_method['id']}\n"
                f"Новая дата окончания: {new_end_date.strftime('%d.%m.%Y')}",
                admins,
                bot,
                username=(await get_user(user_id))['username']
            )

            return True
        else:
            # Все попытки платежа не удались
            logger.warning(f"Все попытки автоматического платежа для пользователя {user_id} не удались. Попыток: {payment_attempts}")
            
            # Уведомляем пользователя о неудачных попытках
            if payment_methods:
                from handlers.handlers import send_failed_payment_notification
                await send_failed_payment_notification(user_id, key_price, subscription_end_no_time, attempts=payment_attempts, bot=bot, key=key)
            
            # Информируем администраторов
            admins = await get_admins()
            await send_info_for_admins(
                f"❌ Неудачное автоматическое продление для пользователя {user_id}\n"
                f"Количество попыток: {payment_attempts}",
                admins,
                bot,
                username=(await get_user(user_id))['username']
            )

            return False
        
    except Exception as e:
        error_msg = f"Ошибка при обработке автоматического платежа для пользователя {user_id}: {str(e)}"
        logger.error(error_msg)
    
        # Информируем администраторов об ошибке
        admins = await get_admins()
        await send_info_for_admins(
            f"❌ {error_msg}",
            admins,
            bot,
            username=(await get_user(user_id))['username'] if await get_user(user_id) else None
        )
    
        return False

async def pay_with_int_balance(user_id, balance, price):
    """Проводит оплату у пользователя засчет его баланса"""
    new_balance = balance - price
    if new_balance < 0:
        raise ValueError("Указанная цена оплаты превышает баланс пользователя!!")
    
    await update_balance(user_id, new_balance)
