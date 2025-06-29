# handlers.scheduler.py
import asyncio
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import API_TOKEN
from handlers.database import (
    get_all_users_with_subscription,
    get_user_payment_methods,
    update_user_subscription,
    get_admins,
    get_user
)
from handlers.payments import create_auto_payment, check_payment_status
from handlers.utils import send_info_for_admins

logger = logging.getLogger(__name__)

# Удаляем глобальную инициализацию бота
# bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

async def process_auto_payments(bot=None):
    """
    Основная функция для обработки автоматических платежей
    
    Args:
        bot (Bot, optional): Экземпляр бота для отправки уведомлений
    """
    logger.info("Запуск процесса автоматических платежей")
    
    try:
        # Если бот не передан, создаем временный экземпляр
        if bot is None:
            bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            need_to_close = True
        else:
            need_to_close = False
            
        # Получаем всех пользователей с подпиской
        users = await get_all_users_with_subscription()
        
        if not users:
            logger.info("Нет пользователей с подпиской")
            return
        
        logger.info(f"Получено {len(users)} пользователей с подпиской")
        
        # Информируем администраторов о начале процесса
        admins = await get_admins()
        await send_info_for_admins(
            f"🔄 Запущен процесс проверки автоматических платежей\n"
            f"Количество пользователей для проверки: {len(users)}",
            admins,
            bot
        )
        
        # Удаляем неиспользуемую переменную today
        payment_count = 0
        
        # Обрабатываем каждого пользователя
        for user in users:
            # Проверяем, является ли сегодня днем оплаты
            if is_payment_day(user['subscription_end']):
                await process_user_payment(user)
                payment_count += 1
                
                # Небольшая пауза между обработкой пользователей
                await asyncio.sleep(1)
        
        # Информируем администраторов о завершении процесса
        await send_info_for_admins(
            f"✅ Процесс автоматических платежей завершен\n"
            f"Проверено пользователей: {len(users)}\n"
            f"Выполнено платежей: {payment_count}",
            admins,
            bot
        )
        
        # Закрываем сессию бота, если мы его создали
        if need_to_close:
            await bot.session.close()
            
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении автоматических платежей: {str(e)}"
        logger.error(error_msg)
        
        # Информируем администраторов об ошибке
        admins = await get_admins()
        await send_info_for_admins(error_msg, admins, bot)

def is_payment_day(subscription_end):
    """
    Проверяет, является ли сегодня днем оплаты для данной подписки
    
    Args:
        subscription_end (str): Дата окончания подписки в формате ISO
        
    Returns:
        bool: True если сегодня день оплаты, False в противном случае
    """
    if not subscription_end:
        return False
        
    try:
        today = datetime.now().date()
        end_date = datetime.fromisoformat(subscription_end).date()
        
        # Если сегодня день окончания подписки, то это день оплаты
        return today == end_date
    except (ValueError, TypeError):
        logger.error(f"Некорректный формат даты окончания подписки: {subscription_end}")
        return False

async def process_user_payment(user):
    """
    Обрабатывает автоматический платеж для конкретного пользователя,
    пытаясь списать деньги со всех сохраненных методов оплаты
    
    Args:
        user (dict): Информация о пользователе
    """
    user_id = user['user_id']
    subscription_end = user['subscription_end']
    
    logger.info(f"Обработка автоматического платежа для пользователя {user_id}")
    
    try:
        # Получаем все методы оплаты пользователя
        payment_methods = await get_user_payment_methods(user_id)
        
        if not payment_methods:
            logger.info(f"У пользователя {user_id} нет сохраненных методов оплаты")
            # Уведомляем пользователя о необходимости продления вручную
            await send_manual_renewal_notification(user_id, subscription_end)
            return
        
        # Флаг успешного платежа
        payment_success = False
        payment_attempts = 0
        successful_method = None
        successful_amount = 0
        
        # Пробуем списать деньги с каждого метода оплаты
        for payment_method in payment_methods:
            payment_method_id = payment_method['payment_method_id']
            amount = payment_method['amount']
            
            # Формируем описание платежа
            description = f"Автоматическое продление подписки VPN"
            
            try:
                # Создаем автоматический платеж
                payment_id = await create_auto_payment(
                    amount=amount,
                    description=description,
                    payment_method_id=payment_method_id
                )
                
                payment_attempts += 1
                
                # Проверяем статус платежа
                payment_status = await check_payment_status(payment_id, amount)
                
                if payment_status:
                    # Платеж успешен, запоминаем метод и сумму
                    payment_success = True
                    successful_method = payment_method
                    successful_amount = amount
                    
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
            new_end_date = datetime.fromisoformat(subscription_end) + timedelta(days=30)
            await update_user_subscription(user_id, new_end_date.isoformat())
            
            # Уведомляем пользователя об успешном продлении
            await send_success_payment_notification(user_id, successful_amount, new_end_date.isoformat())
            
            # Информируем администраторов
            admins = await get_admins()
            await send_info_for_admins(
                f"✅ Автоматическое продление для пользователя {user_id}\n"
                f"Сумма: {successful_amount}₽\n"
                f"Метод оплаты ID: {successful_method['id']}\n"
                f"Новая дата окончания: {new_end_date.strftime('%d.%m.%Y')}",
                admins,
                bot,
                username=(await get_user(user_id))['username']
            )
        else:
            # Все попытки платежа не удались
            logger.warning(f"Все попытки автоматического платежа для пользователя {user_id} не удались. Попыток: {payment_attempts}")
            
            # Уведомляем пользователя о неудачных попытках
            if payment_methods:
                await send_failed_payment_notification(user_id, payment_methods[0]['amount'], subscription_end, payment_attempts)
            
            # Информируем администраторов
            admins = await get_admins()
            await send_info_for_admins(
                f"❌ Неудачное автоматическое продление для пользователя {user_id}\n"
                f"Количество попыток: {payment_attempts}",
                admins,
                bot,
                username=(await get_user(user_id))['username']
            )
    
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

async def send_success_payment_notification(user_id, amount, new_end_date):
    """
    Отправляет уведомление пользователю об успешном автоматическом продлении
    """
    try:
        # Форматируем дату для отображения
        formatted_date = datetime.fromisoformat(new_end_date).strftime("%d.%m.%Y")
        
        message = (
            f"✅ <b>Подписка успешно продлена!</b>\n\n"
            f"Ваша подписка VPN была автоматически продлена.\n\n"
            f"└ 💰 Сумма списания: <b>{amount}₽</b>\n"
            f"└ 📅 Новая дата окончания: <b>{formatted_date}</b>\n\n"
            f"Спасибо, что пользуетесь нашим сервисом! Если у вас возникнут вопросы, "
            f"обратитесь в поддержку через бота."
        )
        
        await bot.send_message(user_id, message)
        logger.info(f"Отправлено уведомление об успешном продлении пользователю {user_id}")
    
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {str(e)}")

async def send_failed_payment_notification(user_id, amount, subscription_end, attempts=1):
    """
    Отправляет уведомление пользователю о неудачном автоматическом продлении
    
    Args:
        user_id (int): ID пользователя
        amount (int): Сумма платежа
        subscription_end (str): Дата окончания подписки
        attempts (int): Количество попыток списания
    """
    try:
        # Форматируем дату для отображения
        formatted_date = datetime.fromisoformat(subscription_end).strftime("%d.%m.%Y")
        
        attempts_text = f"Мы попытались списать средства со всех ваших сохраненных методов оплаты ({attempts} попыток)."
        
        message = (
            f"❌ <b>Не удалось продлить подписку</b>\n\n"
            f"{attempts_text}\n\n"
            f"└ 💰 Сумма платежа: <b>{amount}₽</b>\n"
            f"└ 📅 Дата окончания подписки: <b>{formatted_date}</b>\n\n"
            f"Возможные причины:\n"
            f"• Недостаточно средств на картах\n"
            f"• Карты заблокированы или имеют ограничения\n"
            f"• Банк отклонил операции\n\n"
            f"Пожалуйста, продлите подписку вручную через бота или обновите данные карт."
        )
        
        await bot.send_message(user_id, message)
        logger.info(f"Отправлено уведомление о неудачном продлении пользователю {user_id}")
    
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {str(e)}")

async def send_manual_renewal_notification(user_id, subscription_end):
    """
    Отправляет уведомление пользователю о необходимости ручного продления
    """
    try:
        # Форматируем дату для отображения
        formatted_date = datetime.fromisoformat(subscription_end).strftime("%d.%m.%Y")
        
        message = (
            f"⚠️ <b>Срок действия подписки истекает сегодня</b>\n\n"
            f"Ваша подписка VPN истекает <b>сегодня ({formatted_date})</b>.\n\n"
            f"У вас не настроено автоматическое продление. Чтобы продолжить пользоваться "
            f"сервисом без перерывов, пожалуйста, продлите подписку через бота.\n\n"
            f"Для продления нажмите на кнопку «Продлить подписку» в главном меню."
        )
        
        await bot.send_message(user_id, message)
        logger.info(f"Отправлено уведомление о необходимости ручного продления пользователю {user_id}")
    
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {str(e)}")