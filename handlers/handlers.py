# handlers.handlers.py
import base64
import logging
import os
import random
import re
import uuid
from datetime import datetime, timezone, timedelta

import pandas as pd
from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, Message, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from py3xui import AsyncApi, Client
from apscheduler.triggers.cron import CronTrigger
import string
import hashlib
from config import CHANNEL, BOT, SUPPORT, CHANNEL_LINK, REDIRECT_URI, SUPPORT_URI
from handlers.classes import (
    AdminBroadcastStates,
    AdminKeyRemovalStates,
    AdminStates,
    BalanceForm,
    PromoCodeAdminStates,
    PromoCodeState,
    ReplaceKeyForm,
    SubscriptionStates,
    AccountStates,
    KeyNameStates
)
from handlers.database import (
    add_active_key,
    add_or_update_user,
    add_promocode,
    add_promocode_days,
    add_referral_bonus,
    add_server,
    add_used_promocode,
    check_expiring_subscriptions,
    check_unused_free_keys,
    check_user_used_promocode,
    delete_server,
    get_admins,
    get_all_promocodes,
    get_all_servers,
    get_all_users,
    get_api_instance,
    get_available_countries,
    get_free_days,
    get_free_keys_count,
    get_key_by_uniquie_id,
    get_key_expiry_date,
    get_keys_count,
    get_promocode,
    get_referral_count,
    get_server_by_address,
    get_server_by_id,
    get_server_count_by_address,
    get_user,
    get_user_by_username,
    get_user_email,
    get_user_keys,
    remove_key_bd,
    remove_promocode,
    save_or_update_email,
    set_free_keys_count,
    update_balance,
    update_free_keys_count,
    update_key_expiry_date,
    update_keys_count,
    update_promocode_amount,
    update_referral_count,
    update_server_clients_count,
    update_server_info,
    update_subscription,
    get_user_transactions,
    add_transaction,
    update_transaction_status,
    get_transaction_by_id,
    set_is_first_payment_done,
    get_is_first_payment_done,
    get_user_info,
    update_user_channel,
    update_user_pay_count,
    get_system_statistics,
    get_channel_statistics,
    get_user_segments,
    get_users_with_unused_free_keys,
    add_payment_method,
    get_user_payment_methods,
    delete_payment_method,
    get_payment_method_by_id,
    get_users_by_server_address,
    sync_payment_id_for_all_keys,
    get_payment_id_for_key,
    set_payment_id_for_key,
    get_all_keys_with_payment_method,
    get_user_id_by_key,
    update_key_expiration,
    update_key_name,
)
from handlers.db_utils.server_utils import (
    add_inbound,
    get_server_inbounds,
    get_servers_with_total_clients,
    remove_inbound,
    update_inbound_id,
    update_inbound_max_clients,
    update_inbound_pbk,
    update_inbound_port,
    update_inbound_protocol,
    update_inbound_sid,
    update_inbound_sni,
    update_inbound_utls,
)
from handlers.payments import check_payment_status, create_payment, check_transaction_status
from handlers.utils import (
    extract_key_data,
    generate_random_string,
    send_info_for_admins,
    send_channel_log
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


async def send_unused_keys_notification(bot: Bot):
    """
    Отправляет уведомления пользователям с неиспользованными бесплатными ключами
    """
    try:
        users = await check_unused_free_keys()
        kb = InlineKeyboardBuilder()
        kb.button(text="🌐 Подключить VPN", callback_data="connection")
        kb.button(text="◀️ Открыть меню", callback_data="back_to_menu")
        kb.adjust(1)

        for user_id in users:
            user = await get_user(user_id=user_id)
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🎁 <b>У вас есть неактивированный бесплатный VPN!</b>\n\n"
                        "Не упустите возможность попробовать наш сервис бесплатно.\n"
                        "Подключитесь прямо сейчас и оцените качество работы VPN!\n\n"
                        "• Высокая скорость\n"
                        "• Надёжная защита\n"
                        "• Простое подключение"
                    ),
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
                await send_info_for_admins(f"Отправлено уведомление о неиспользованном ключе пользователю {user_id}", await get_admins(), bot, username=user.get("username"))
                logger.info(f"Отправлено уведомление о неиспользованном ключе пользователю {user_id}")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка в send_unused_keys_notification: {e}")

async def send_expiring_subscription_notification(bot: Bot):
    """
    Отправляет уведомления пользователям об истекающих подписках
    """
    try:
        # Создаем storage и state для каждого уведомления
        storage = MemoryStorage()
        state = FSMContext(storage=storage, key='notification_state')
        
        expiring_subs = await check_expiring_subscriptions()
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Продлить подписку", callback_data="extend_subscription")
        kb.button(text="◀️ Открыть меню", callback_data="back_to_menu")
        kb.adjust(1)

        for user_id, sub_end, key in expiring_subs:
            try:
                user = await get_user(user_id=user_id)
                end_date = datetime.fromisoformat(sub_end).strftime("%d.%m.%Y")
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⚠️ <b>Ваша подписка скоро закончится!</b>\n\n"
                        f"📅 Дата окончания: {end_date}\n"
                        f"🔑 Ключ: <code>{key}</code>\n\n"
                        "Чтобы продолжить пользоваться сервисом без перерывов,\n"
                        "рекомендуем продлить подписку заранее.\n\n"
                        "💡 Преимущества продления:\n"
                        "• Непрерывный доступ к VPN\n"
                        "• Сохранение всех настроек\n"
                        "• Специальные условия для постоянных клиентов"
                    ),
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
                
                # Сохраняем данные в state для конкретного пользователя
                await state.set_data({
                    f"user_{user_id}": {
                        "key_to_connect": key,
                        "user_id": user_id,
                        "expiration_date": sub_end
                    }
                })
                await send_info_for_admins(f"Отправлено уведомление об истекающей подписке пользователю {user_id}", await get_admins(), bot, username=user.get("username"))
                logger.info(f"Отправлено уведомление об истекающей подписке пользователю {user_id}")
            except Exception as e:
                await send_info_for_admins(f"Ошибка при отправке уведомления пользователю об истекающей подписке {user_id}: {e}", await get_admins(), bot, username=user.get("username"))
                logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка в send_expiring_subscription_notification: {e}")



def setup_notification_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    Настраивает планировщик для отправки уведомлений
    
    Args:
        bot (Bot): Экземпляр бота
        
    Returns:
        AsyncIOScheduler: Настроенный планировщик уведомлений
    """
    try:
        scheduler = AsyncIOScheduler()
        
        
        async def send_expiring_subs_wrapper(bot):
            """Обертка для отправки уведомлений об истекающих подписках"""
            try:
                await send_expiring_subscription_notification(bot)
            except Exception as e:
                logger.error(f"Error in send_expiring_subs_wrapper: {e}")
        
        
        # Уведомления об истекающих подписках (ежедневно в 12:00)
        scheduler.add_job(
            send_expiring_subs_wrapper,
            trigger='cron',
            hour=12,
            args=[bot],
            id='expiring_subs_notifications',
            name='Send expiring subscription notifications',
            replace_existing=True,
            misfire_grace_time=None  # Разрешаем выполнение пропущенных задач
        )
                  

        scheduler.start()
        logger.info("Планировщик уведомлений успешно запущен")
        return scheduler
        
    except Exception as e:
        logger.error(f"Error setting up notification scheduler: {e}")
        # Создаем новый планировщик в случае ошибки
        fallback_scheduler = AsyncIOScheduler()
        fallback_scheduler.start()
        return fallback_scheduler

@router.message(CommandStart())
async def start_command(message: types.Message, bot: Bot, state: FSMContext):
    """
    Обработчик команды /start
    Отображает приветственное сообщение и основное меню
    """
    args = message.text.split()
    referrer_id = None

    # Создаем reply-клавиатуру с кнопкой "Открыть меню"
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="◀️ Открыть меню")]],
        resize_keyboard=True,
        persistent=True
    )

    channel_id = CHANNEL

    if len(args) > 1 and args[1].startswith("ref_"):
        referrer_id = int(args[1].replace("ref_", ""))
        await state.update_data(referrer_id=referrer_id)

    if len(args) > 1 and args[1].startswith("channel_"):
        channel_from = args[1].replace("channel_", "")
        await send_channel_log(
            bot=message.bot,
            channel_from=channel_from,
            username=message.from_user.username
        ) 
        await state.update_data(channel_from=channel_from)

    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=message.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            # Устанавливаем состояние ожидания подписки
            await state.set_state(SubscriptionStates.waiting_for_subscription)
            
            await message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку».\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await message.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
              

    if referrer_id != message.from_user.id if message.from_user else None:
        existing_user = await get_user(user_id=message.from_user.id if message.from_user else None)
        if not existing_user:

                await add_or_update_user(
                    user_id=message.from_user.id,
                    username=message.from_user.username or f"None{random.randint(10, 999)}",  # Используем f-string
                    subscription_type="Без подписки",
                    is_admin=False,
                    balance=0,
                    subscription_end=None,
                    referrer_id=referrer_id
                )
                await add_referral_bonus(referrer_id, 50)
                await update_referral_count(referrer_id)

                referrer = await get_user(user_id=referrer_id)
                if referrer:
                    kb = InlineKeyboardBuilder()
                    kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                    kb.adjust(1, 1)
                    await bot.send_message(
                        referrer_id,
                        f"🎉 <b>Поздравляем!</b>\n\n"
                        f"👤 Пользователь @{message.from_user.username or 'Неизвестный'} "
                        f"зарегистрировался по вашей реферальной ссылке!\n\n"
                        f"💰 Вам начислено: <b>50₽</b>\n"
                        f"💎 Вы также будете получать 30% от его пополнений!",
                        parse_mode="HTML",
                        reply_markup=kb.as_markup()
                    )                
                await message.answer(
                    "🎁 <b>Добро пожаловать в нашу реферальную программу!</b>\n\n"
                    "Вы присоединились по приглашению другого пользователя.\n"
                    "Вы тоже можете приглашать друзей и получать бонусы:\n"
                    "└ 50₽ за каждого приглашенного пользователя\n"
                    "└ 30% от всех их пополнений\n\n",
                    parse_mode="HTML",
                    reply_markup=reply_kb
                )

    kb = InlineKeyboardBuilder()

    user = await get_user(user_id=message.from_user.id)
    if not user:
        await add_or_update_user(
            user_id=message.from_user.id, 
            username=message.from_user.username or f"None{random.randint(10, 999)}", # Используем f-string
            subscription_type="Без подписки", 
            is_admin=False, 
            balance=0, 
            subscription_end=None
        )
        kb.button(text="ПОЛУЧИТЬ", callback_data="connection")
        kb.adjust(1)        
        welcome_text = (
            f"👋 <b>Привет, {message.from_user.username or 'Неизвестный(У вас нет username)'}!</b>\n\n"
            "🎁 <b>Подключись к VPN бесплатно!</b>\n"
            "└ Дарим тебе <b>3 дня</b> премиум доступа\n\n"
            "✨ <b>Преимущества нашего VPN:</b>\n"
            "├ 🚀 Молниеносная скорость\n"
            "├ 🛡 Полная защита данных\n"
            "├ 🔓 Отсутствие блокировок\n"
            "├ 💳 Удобная оплата картами РФ\n"
            "└ 💰 Лучшая цена на рынке\n\n"
            "🤝 <b>Реферальная программа:</b>\n"
            "└ Приглашай друзей и получай:\n"
            "   • 50₽ за каждого пользователя\n"
            "   • 30% со всех пополнений\n\n"
            "📱 <b>Поддерживаемые устройства:</b>\n"
            "└ iOS, Android, MacOS и Windows\n\n"
            "⬇️ <b>Жми кнопку для подключения!</b> ⬇️"
        )
        await message.answer_photo(
            photo=types.FSInputFile("handlers/images/07.jpg"),
            caption=welcome_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        if len(args) > 1 and args[1].startswith("channel_"):
            channel_from = args[1].replace("channel_", "")   
            await update_user_channel(
                user_id=message.from_user.id,
                from_channel=channel_from
            )
        # Отправляем reply-клавиатуру отдельным сообщением
        await message.answer("Используйте кнопку ниже для быстрого доступа к меню:", reply_markup=reply_kb)
    else:
        await state.clear()
        kb.button(text="🌐 Купить VPN", callback_data="connection")
        kb.button(text="🔄 Продлить подписку", callback_data="extend_subscription")
        kb.button(text="👤 Мой профиль", callback_data="profile")
        kb.button(text="📖 Как подключить VPN", callback_data="instruction")
        kb.button(text="🤝 Пригласить друзей", callback_data="invite")
        kb.button(text="🆘 Не работает VPN?", callback_data="troubleshoot")
        kb.adjust(1, 1, 2, 1, 1)
        welcome_text = (
            "👋 <b>Добро пожаловать в главное меню!</b>\n\n"
            "🚀 Обходите блокировки, получайте доступ к любимому контенту и наслаждайтесь быстрой скоростью соединения.\n\n"
            "📖️ Выберите действие:"
        )
        await message.answer_photo(
            photo=types.FSInputFile("handlers/images/04.jpg"),
            caption=welcome_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        # Отправляем reply-клавиатуру отдельным сообщением
        await message.answer("Используйте кнопку ниже для быстрого доступа к меню:", reply_markup=reply_kb)

@router.callback_query(F.data == "troubleshoot")
async def troubleshoot_vpn(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """
    Отправляет пользователю пошаговую инструкцию по устранению проблем с VPN
    """
    try:
        channel_id = CHANNEL
        member = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            # Устанавливаем состояние ожидания подписки
            await state.set_state(SubscriptionStates.waiting_for_subscription)
            
            await callback.message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку».\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await callback.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Заменить ключ", callback_data="replace_key")
    kb.button(text="🌍 Сменить страну", callback_data="change_key_country")
    kb.button(text="🆘 Поддержка", url=SUPPORT_URI)
    kb.button(text="◀️ Назад", callback_data="back_to_menu")
    kb.adjust(1)

    troubleshoot_text = (
        "🛠 <b>Решение проблем с VPN</b>\n\n"
        "<b>Быстрые решения:</b>\n"
        "1. 🔄 Перезапустите приложение\n"
        "2. 📶 Проверьте интернет-соединение\n"
        "3. ⚡️ Отключите энергосбережение\n\n"
        "<b>Если проблема осталась:</b>\n"
        "4. 🌍 Попробуйте другую страну\n"
        "5. 🔑 Замените ключ на новый\n\n"
        "<b>Дополнительные шаги:</b>\n"
        "6. 🔄 Обновите приложение\n"
        "7. 🧹 Очистите кэш\n\n"
        "❓ Если ничего не помогло, обратитесь в поддержку"
    )

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/8banner.png"),
            caption=troubleshoot_text,
        )
    )

    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())


@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """
    Проверка подписки с сохранением состояния
    """
    channel_id = CHANNEL
    
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            await callback.message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку»\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return
        
        existing_user = await get_user(user_id=callback.from_user.id)
        if not existing_user:
            data = await state.get_data()
            referrer_id = data.get('referrer_id')
            random_username = f"user{str(uuid.uuid4())[:8]}" if not callback.from_user.username else callback.from_user.username
            channel_from = data.get('channel_from')
            await update_user_channel(
                user_id=callback.from_user.id,
                from_channel=channel_from
            )
            # Проверяем реферальную систему только для новых пользователей
            if referrer_id and referrer_id != callback.from_user.id:
                referrer = await get_user(user_id=referrer_id)
                if referrer:
                    # Добавляем пользователя с реферером
                    await add_or_update_user(
                        user_id=callback.from_user.id,
                        username=random_username,
                        subscription_type="Без подписки",
                        is_admin=False,
                        balance=0,
                        subscription_end=None,
                        referrer_id=referrer_id
                    )
                    
                    # Начисляем бонус рефереру
                    await add_referral_bonus(referrer_id, 50)
                    await update_referral_count(referrer_id)
                    
                    # Уведомляем реферера
                    kb_ref = InlineKeyboardBuilder()
                    kb_ref.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                    kb_ref.adjust(1)
                    
                    try:
                        await bot.send_message(
                            referrer_id,
                            "🎉 <b>Поздравляем!</b>\n\n"
                            "👤 Новый пользователь присоединился по вашей ссылке\n\n"
                            "💰 Вам начислено: <b>50₽</b>\n"
                            "💎 Вы также будете получать 30% от его пополнений!",
                            parse_mode="HTML",
                            reply_markup=kb_ref.as_markup()
                        )
                    except Exception as e:
                        logger.error(f"Error sending referral notification: {e}")

                    # Уведомляем нового пользователя
                    await callback.message.answer(
                        "🎁 <b>Добро пожаловать в нашу реферальную программу!</b>\n\n"
                        "Вы присоединились по приглашению другого пользователя.\n"
                        "Вы тоже можете приглашать друзей и получать бонусы:\n"
                        "└ 50₽ за каждого приглашенного пользователя\n"
                        "└ 30% от всех его пополнений\n\n",
                        parse_mode="HTML"
                    )
            else:
                # Добавляем пользователя без реферера
                await add_or_update_user(
                    user_id=callback.from_user.id,
                    username=random_username,
                    subscription_type="Без подписки",
                    is_admin=False,
                    balance=0,
                    subscription_end=None
                )

            # Отправляем приветственные сообщения
            kb = InlineKeyboardBuilder()
            kb.button(text="🌐 Купить VPN", callback_data="connection")
            kb.button(text="👤 Мой профиль", callback_data="profile")
            kb.button(text="📖 Как подключить VPN", callback_data="instruction")
            kb.button(text="🤝 Пригласить друзей", callback_data="invite")
            kb.button(text="🆘 Не работает VPN?", callback_data="troubleshoot")
            kb.adjust(1, 2, 1, 1)
            
            try:
                await callback.message.answer_photo(
                    photo=types.FSInputFile("handlers/images/07.jpg"),
                    caption="👋 <b>Добро пожаловать в главное меню!</b>\n\n"
                            "Выберите действие:",
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )

                kb_2 = InlineKeyboardBuilder()
                kb_2.button(text="ПОЛУЧИТЬ", callback_data="connection")
                kb_2.adjust(1)        
                welcome_text = (
                    f"👋 <b>Привет, {callback.from_user.username or 'Без username'}!</b>\n\n"
                    "🎁 <b>Подключись к VPN бесплатно!</b>\n"
                    "└ Дарим тебе <b>3 дня</b> премиум доступа\n\n"
                    "✨ <b>Преимущества нашего VPN:</b>\n"
                    "├ 🚀 Молниеносная скорость\n"
                    "├ 🛡 Полная защита данных\n"
                    "├ 🔓 Отсутствие блокировок\n"
                    "├ 💳 Удобная оплата картами РФ\n"
                    "└ 💰 Лучшая цена на рынке\n\n"
                    "🤝 <b>Реферальная программа:</b>\n"
                    "└ Приглашай друзей и получай:\n"
                    "   • 50₽ за каждого пользователя\n"
                    "   • 30% со всех пополнений\n\n"
                    "📱 <b>Поддерживаемые устройства:</b>\n"
                    "└ iOS, Android, MacOS и Windows\n\n"
                    "⬇️ <b>Жми кнопку для подключения!</b> ⬇️"
                )
                await callback.message.answer_photo(
                    photo=types.FSInputFile("handlers/images/04.jpg"),
                    caption=welcome_text,
                    reply_markup=kb_2.as_markup(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error sending welcome messages: {e}")
        else:
            # Для существующих пользователей просто очищаем состояние
            kb = InlineKeyboardBuilder()
            kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
            kb.adjust(1)
            await callback.message.answer(
                "✅ <b>Подписка проверена</b>\n\n"
                "Вы уже зарегистрированы в системе.",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )

        await state.clear()

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await callback.message.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    """
    Возвращает пользователя в главное меню
    """
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 Купить VPN", callback_data="connection")
    kb.button(text="🔄 Продлить подписку", callback_data="extend_subscription")
    kb.button(text="👤 Мой профиль", callback_data="profile")
    kb.button(text="📖 Как подключить VPN", callback_data="instruction")
    kb.button(text="🤝 Пригласить друзей", callback_data="invite")
    kb.button(text="🆘 Не работает VPN?", callback_data="troubleshoot")
    kb.adjust(1, 1, 2, 1, 1)

    menu_text = (
        "👋 Добро пожаловать в главное меню!\n\n"
        "🚀 Обходите блокировки, получайте доступ к любимому контенту и наслаждайтесь быстрой скоростью соединения.\n\n"
        "📖️ Выберите действие:"
    )

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/07.jpg"),
            caption=menu_text
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await callback.answer()

@router.message(F.text == "◀️ Открыть меню")
async def open_menu_command(message: Message, state: FSMContext):
    """
    Обработчик текстовой команды "◀️ Открыть меню"
    Возвращает пользователя в главное меню
    """
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 Купить VPN", callback_data="connection")
    kb.button(text="🔄 Продлить подписку", callback_data="extend_subscription")
    kb.button(text="👤 Мой профиль", callback_data="profile")
    kb.button(text="📖 Как подключить VPN", callback_data="instruction")
    kb.button(text="🤝 Пригласить друзей", callback_data="invite")
    kb.button(text="🆘 Не работает VPN?", callback_data="troubleshoot")
    kb.adjust(1, 1, 2, 1, 1)

    menu_text = (
        "👋 Добро пожаловать в главное меню!\n\n"
        "🚀 Обходите блокировки, получайте доступ к любимому контенту и наслаждайтесь быстрой скоростью соединения.\n\n"
        "📖️ Выберите действие:"
    )
    await message.answer_photo(
        photo=types.FSInputFile("handlers/images/07.jpg"),
        caption=menu_text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.message(Command("profile"))
async def profile_command_handler(message: Message, bot: Bot, state: FSMContext):
    """
    Обработчик команды /profile
    """
    await state.clear()
    channel_id = CHANNEL
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=message.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            await message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку»\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await message.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
    
    user = await get_user(user_id=message.from_user.id)
    keys_count = await get_keys_count(message.from_user.id)
    
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 Купить VPN", callback_data="connection")
    kb.button(text="🔄 Продлить подписку", callback_data="extend_subscription")
    kb.button(text="💰 Пополнить баланс", callback_data="add_balance")
    kb.button(text="🎁 Активировать промокод", callback_data="promocode")
    kb.button(text="🔑 Мои ключи", callback_data="active_keys")
    kb.button(text="◀️ Главное меню", callback_data="back_to_menu")
    kb.adjust(1, 1, 1)
    
    profile_text = (
        "👤 <b>Мой профиль</b>\n\n"
        f"👥 Пользователь: @{message.from_user.username or 'Без username'}\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"💰 Баланс: <b>{user['balance']:,}₽</b>\n"
        f"🤝 Приглашено друзей: <b>{user.get('referral_count', 0)}</b>\n"
        f"🔑 Активных ключей: <b>{keys_count}</b>\n\n"
    )
    
    await message.answer_photo(
        photo=types.FSInputFile("handlers/images/02.jpg"),
        caption=profile_text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "promocode")
async def promocode_command(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработчик команды активации промокода
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="profile")
    kb.adjust(1)
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/11.jpg"),
            caption="🎁 <b>Активация промокода</b>\n\n"
                "Введите промокод для активации:\n\n"
                "<i>Для отмены нажмите кнопку ниже</i>",
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await state.set_state(PromoCodeState.waiting_for_promocode)

@router.message(PromoCodeState.waiting_for_promocode)
async def process_promocode(message: Message, state: FSMContext):
    """
    Обработка введенного промокода с учетом количества использований
    """
    promocode = message.text.strip().upper()

    if await check_user_used_promocode(message.from_user.id, promocode):
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Попробовать ещё раз", callback_data="promocode")
        kb.button(text="◀️ Вернуться в профиль", callback_data="profile")
        kb.adjust(1)
        
        await message.answer(
            "❌ <b>Промокод уже использован</b>\n\n"
            "Вы ранее активировали этот промокод.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await state.clear()
        return
        
    # Проверяем промокод в базе данных
    promo_data = await get_promocode(promocode)
    
    if promo_data:
        # Распаковываем данные промокода
        promo_id, promo_code, promo_user_id, promo_amount, gift_balance, gift_days, expiration_date = promo_data
        
        # Проверяем срок действия промокода
        current_time = datetime.now()
        expiration_date = datetime.fromisoformat(expiration_date)
        
        if current_time <= expiration_date:
            # Проверяем, можно ли еще использовать промокод
            if promo_amount > 0:
                user = await get_user(user_id=message.from_user.id)
                
                kb = InlineKeyboardBuilder()

                
                # Начисляем бонусы
                new_balance = user['balance'] + gift_balance
                await update_balance(message.from_user.id, new_balance)
                
                # Уменьшаем количество использований промокода
                await update_promocode_amount(promo_id)
                # Проверяем, есть ли баланс и дни
                if gift_balance > 0 and gift_days > 0:
                    kb.button(text="🌐 Получить VPN", callback_data="connection")
                    await set_free_keys_count(message.from_user.id, 1)
                    await add_promocode_days(message.from_user.id, gift_days)
                    
                    await message.answer(
                        "✅ <b>Промокод успешно активирован!</b>\n\n"
                        f"💰 Начислено баланса: {gift_balance}₽\n"
                        f"💎 Новый баланс: {new_balance}₽\n"
                        f"🕰 Дней VPN: {gift_days}\n\n"
                        "Нажмите кнопку, чтобы выбрать устройство для подключения!",
                        reply_markup=kb.as_markup(),
                        parse_mode="HTML"
                    )
                elif gift_balance > 0:
                    kb.button(text="◀️ Открыть профиль", callback_data="profile")
                    await message.answer(
                        "✅ <b>Промокод успешно активирован!</b>\n\n"
                        f"💰 Начислено баланса: {gift_balance}₽\n"
                        f"💎 Новый баланс: {new_balance}₽",
                        reply_markup=kb.as_markup(),
                        parse_mode="HTML"
                    )
                elif gift_days > 0:
                    kb.button(text="🌐 Купить VPN", callback_data="connection")
                    await set_free_keys_count(message.from_user.id, 1)
                    await add_promocode_days(message.from_user.id, gift_days)
                    
                    await message.answer(
                        "✅ <b>Промокод успешно активирован!</b>\n\n"
                        f"🕰 Дней VPN: {gift_days}\n\n"
                        "Нажмите кнопку, чтобы выбрать устройство для подключения!",
                        reply_markup=kb.as_markup(),
                        parse_mode="HTML"
                    )
                await add_used_promocode(message.from_user.id, promocode)
            else:
                kb = InlineKeyboardBuilder()
                kb.button(text="🔄 Попробовать ещё раз", callback_data="promocode")
                kb.button(text="◀️ Вернуться в профиль", callback_data="profile")
                kb.adjust(1)
                
                await message.answer(
                    "❌ <b>Промокод исчерпан</b>\n\n"
                    "Количество использований этого промокода закончилось.\n"
                    "Попробуйте другой промокод или вернитесь в профиль.",
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
        else:
            kb = InlineKeyboardBuilder()
            kb.button(text="🔄 Попробовать ещё раз", callback_data="promocode")
            kb.button(text="◀️ Вернуться в профиль", callback_data="profile")
            kb.adjust(1)
            
            await message.answer(
                "❌ <b>Промокод просрочен</b>\n\n"
                "Срок действия этого промокода истек.\n"
                "Попробуйте другой промокод или вернитесь в профиль.",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
    else:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Попробовать ещё раз", callback_data="promocode")
        kb.button(text="◀️ Вернуться в профиль", callback_data="profile")
        kb.adjust(1)
        
        await message.answer(
            "❌ <b>Неверный промокод</b>\n\n"
            "Возможные причины:\n"
            "• Промокод недействителен\n"
            "• Промокод уже использован\n\n"
            "Попробуйте другой промокод или вернитесь в профиль",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    
    await state.clear()

@router.message(Command("help"))
async def help_command_handler(message: Message, bot: Bot):
    """
    Обработчик команды /help
    """
    channel_id = CHANNEL
    try:
        member = await bot.get_chat_member(
            chat_id=channel_id, user_id=message.from_user.id
        )
        is_subscribed = member.status not in ["left", "kicked", "banned"]

        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)

            await message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку»\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML",
            )
            return
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await message.answer(
            "Произошла ошибка при проверке подписки. Попробуйте позже."
        )
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="🆘 Не работает VPN?", callback_data="troubleshoot")
    kb.adjust(1, 1)

    await message.answer(
        "💡 <b>Помощь</b>\n\n", reply_markup=kb.as_markup(), parse_mode="HTML"
    )


@router.message(Command("connect"))
async def connect_command_handler(message: Message, bot: Bot):
    """
    Обработчик команды /connect
    """
    channel_id = CHANNEL
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=message.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            await message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку»\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return
        else:
            await message.answer("🔓 <b>Доступ открыт</b>\n\n"
                                 "Вы успешно подписались на наш канал и можете пользоваться ботом!\n"
                                 "Напишите /start, если у вас есть реферальная ссылка, перейдите по ней ещё раз")
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await message.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📱 iOS", callback_data="device_ios")
    kb.button(text="🤖 Android", callback_data="device_android")
    kb.button(text="📺 Android TV", callback_data="device_androidtv")
    kb.button(text="🖥 Windows", callback_data="device_windows")
    kb.button(text="🍎 macOS", callback_data="device_mac")
    kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
    kb.adjust(2, 2, 1)

    connection_text = (
        "🌐 <b>Выберите устройство для подключения:</b>\n\n"
        "Мы поддерживаем все основные платформы и операционные системы.\n"
        "Выберите ваше устройство для получения подробной инструкции."
    )

    await message.answer_photo(
        photo=types.FSInputFile("handlers/images/08.jpg"),
        caption=connection_text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "instruction")
async def instruction_command(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """
    Обработчик команды /instruction
    Отображает общую инструкцию и кнопки для выбора устройства
    """
    try:
        channel_id = CHANNEL
        member = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            # Устанавливаем состояние ожидания подписки
            await state.set_state(SubscriptionStates.waiting_for_subscription)
            
            await callback.message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку».\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await callback.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="📱 iOS", callback_data="guide_ios")
    kb.button(text="🤖 Android", callback_data="guide_android")
    kb.button(text="📺 Android TV", callback_data="guide_androidtv")
    kb.button(text="🖥 Windows", callback_data="guide_windows")
    kb.button(text="🍎 macOS", callback_data="guide_mac")
    kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
    kb.adjust(2, 2, 1)

    instruction_text = (
        "📖 <b>Инструкция по подключению VPN</b>\n\n"
        "🔹 <b>Общие рекомендации:</b>\n"
        "• Используйте официальные приложения\n"
        "• Следуйте инструкции для вашего устройства\n"
        "• Проверьте подключение к интернету\n\n"
        "🔸 <b>Порядок подключения:</b>\n"
        "1. Выберите тип вашего устройства\n"
        "2. Установите необходимое приложение (v2raytun для iOS и macOS, V2Ray для Android, Android TV и Windows)\n"
        "3. Импортируйте полученный ключ\n"
        "4. Подключитесь к серверу\n\n"
        "❓ Выберите ваше устройство для получения подробной инструкции:"
    )

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/12.jpg"),
            caption=instruction_text
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("guide_"))
async def device_guide(callback: types.CallbackQuery):
    """
    Обработчик выбора устройства для инструкции
    """
    device = callback.data.split("_")[1]
    
    # Улучшенная нормализация устройств
    device_mapping = {
        'and': 'android',
        'andtv': 'androidtv',
        'tv': 'androidtv',
        'win': 'windows'
    }
    
    # Нормализация устройства
    normalized_device = device_mapping.get(device.lower(), device.lower())

    kb = InlineKeyboardBuilder()
    
    # Получаем ключи пользователя
    user_keys = await get_user_keys(callback.from_user.id)
    
    if not user_keys:
        kb.button(text="💫 Купить подписку", callback_data="connection")

        await callback.message.edit_caption(
            caption="🔑 <b>У вас пока нет активных ключей.</b>\n\n"
            "Приобретите подписку в разделе 'Подключение', чтобы получить доступ к VPN.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Фильтруем ключи для выбранного устройства с учетом нормализации
    device_keys = [
        key for key in user_keys 
        if (key[1].lower() == normalized_device or 
            any(key[1].lower().startswith(alias) for alias in device_mapping.keys()))
    ]
    
    if not device_keys:
        kb.button(text="💫 Купить подписку", callback_data="connection")

        await callback.message.edit_caption(
            caption="🔑 <b>У вас нет активных ключей для {normalized_device.upper()}.</b>\n\n"
            "Приобретите подписку для этого устройства в разделе 'Подключение'.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Добавляем кнопки скачивания для разных устройств
    download_links = {
        "ios": {
            "vless": "https://apps.apple.com/ru/app/v2raytun/id6476628951",
            "shadowsocks": "https://goo.su/rfYop"
        },
        "mac": {
            "vless": "https://apps.apple.com/ru/app/v2raytun/id6476628951",
            "shadowsocks": "https://goo.su/ztVi"
        },
        "windows": {
            "vless": "https://github.com/hiddify/hiddify-next/releases/latest/download/Hiddify-Windows-Setup-x64.exe",
            "shadowsocks": "https://goo.su/jzloMN"
        },
        "android": {
            "vless": "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru",
            "shadowsocks": "https://goo.su/P5f7"
        },
        "androidtv": {
            "vless": "https://play.google.com/store/apps/details?id=tech.simha.androidtvremote",
            "shadowsocks": "https://goo.su/P5f7"
        }
    }
    
    kb.button(text="📲 Скачать V2rayTun/Hiddify", url=download_links[device]["vless"])
    kb.adjust(1)
    kb.button(text="📲 Скачать Outline", url=download_links[device]["shadowsocks"])
    
    # Если есть ключи, добавляем их
    if user_keys:
        if device in ["ios", "mac"]:
            kb.button(
                text=f"🔑 Мои ключи для {device.upper()}", 
                callback_data=f"show_keys_{device}"
            )
        elif device in ["android"]:
            kb.button(
                text=f"🔑 Мои ключи для {device.upper()}", 
                callback_data=f"show_keys_{device}"
            )
        elif device in ["androidtv"]:
            kb.button(
                text=f"🔑 Мои ключи для {device.upper()}", 
                callback_data=f"show_keys_{device}"
            )
        elif device in ["windows"]:
            kb.button(
                text=f"🔑 Мои ключи для {device.upper()}", 
                callback_data=f"show_keys_{device}"
                )
    kb.button(text="◀️ Назад к инструкции", callback_data="instruction")
    kb.adjust(1)  

    guides = {
        "ios": (
            "📱 <b>Инструкция для iOS:</b>\n\n"
            "<b>Для VLESS:</b>\n"
            "1️⃣ Установите приложение V2rayTun из App Store\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на значок '+' вверху экрана\n"
            "4️⃣ Выберите 'Добавить из буфера'\n"
            "5️⃣ Вставьте скопированный ключ\n"
            "6️⃣ Включите VPN\n\n"
            "<b>Для Shadowsocks:</b>\n"
            "1️⃣ Установите приложение Outline\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на плюсик в правом верхнем углу\n"
            "4️⃣ Вставьте скопированный ключ и добавьте сервер\n"
            "5️⃣ Нажмите 'Подключиться'\n\n"
            "✅ Готово! VPN подключен\n\n"
        ),
        "android": (
            "🤖 <b>Инструкция для Android:</b>\n\n"
            "<b>Для VLESS:</b>\n"
            "1️⃣ Установите приложение V2rayTun\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на значок '+' внизу экрана\n"
            "4️⃣ Выберите 'Добавить из буфера'\n"
            "5️⃣ Вставьте скопированный ключ\n"
            "6️⃣ Нажмите на кнопку подключения\n\n"
            "<b>Для Shadowsocks:</b>\n"
            "1️⃣ Установите приложение Outline\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на плюсик в правом верхнем углу\n"
            "4️⃣ Вставьте скопированный ключ и добавьте сервер\n"
            "5️⃣ Нажмите 'Подключиться'\n\n"
            "✅ Готово! VPN подключен"
        ),
        "androidtv": (
            "🤖 <b>Инструкция для Android TV:</b>\n\n"
            "<b>Для VLESS:</b>\n"
            "1️⃣ Установите v2raytun на Андроид TV\n"
            "2️⃣ Установите приложение Remote ATV на ваш телефон Андроид:\nhttps://play.google.com/store/apps/details?id=tech.simha.androidtvremote\n"
            "3️⃣ Айфон:\nhttps://apps.apple.com/ru/app/remote-for-android-tv/id1668755298?l=en-GB\n"
            "4️⃣ Подключите телефон к телевизору через Remote ATV'\n"
            "5️⃣ Скопируйте ваш ключ из бота\n"
            "6️⃣ Зайдите в приложение Remote ATV и не закрывайте его\n"
            "7️⃣ Зайдите в приложение v2raytun на Андроид TV\n"
            "8️⃣ Кликните на управление -> ручной ввод\n"
            "9️⃣ В телефоне появляется графа для ввода данных, вставляем в нее ключ и нажимаем окей\n"
            "🔟 На телевизоре возвращаемся назад и нажимаем кнопку подключить\n\n"
            "<b>Для Shadowsocks:</b>\n"
            "1️⃣ Установите приложение Outline\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на плюсик в правом верхнем углу\n"
            "4️⃣ Вставьте скопированный ключ и добавьте сервер\n"
            "5️⃣ Нажмите 'Подключиться'\n\n"
            "✅ Готово! VPN подключен"
        ),
        "windows": (
            "🖥 <b>Инструкция для Windows:</b>\n\n"
            "<b>Для VLESS:</b>\n"
            "1️⃣ Скачайте и установите Hiddify Next\n"
            "2️⃣ Запустите программу\n"
            "3️⃣ Нажмите на значок '+' в верхнем меню\n"
            "4️⃣ Выберите 'Добавить из буфера'\n"
            "5️⃣ Вставьте скопированный ключ\n"
            "6️⃣ Нажмите на кнопку подключения\n\n"
            "<b>Для Shadowsocks:</b>\n"
            "1️⃣ Установите приложение Outline\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на плюсик в правом верхнем углу\n"
            "4️⃣ Вставьте скопированный ключ и добавьте сервер\n"
            "5️⃣ Нажмите 'Подключиться'\n\n"
            "✅ Готово! VPN подключен"
        ),
        "mac": (
            "🍎 <b>Инструкция для macOS:</b>\n\n"
            "<b>Для VLESS:</b>\n"
            "1️⃣ Установите приложение V2rayTun\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на значок '+' вверху экрана\n"
            "4️⃣ Выберите 'Добавить из буфера'\n"
            "5️⃣ Вставьте скопированный ключ\n"
            "6️⃣ Включите VPN\n\n"
            "<b>Для Shadowsocks:</b>\n"
            "1️⃣ Установите приложение Outline\n"
            "2️⃣ Откройте приложение\n"
            "3️⃣ Нажмите на плюсик в правом верхнем углу\n"
            "4️⃣ Вставьте скопированный ключ и добавьте сервер\n"
            "5️⃣ Нажмите 'Подключиться'\n\n"
            "✅ Готово! VPN подключен\n\n"
        )
    }

    await callback.message.answer(
        text=guides.get(device, "Инструкция недоступна"),
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == "invite")
async def invite_command(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """
    Обработчик команды приглашения с реферальной системой
    """
    try:
        channel_id = CHANNEL
        member = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            # Устанавливаем состояние ожидания подписки
            await state.set_state(SubscriptionStates.waiting_for_subscription)
            
            await callback.message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку».\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await callback.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
    user = await get_user(user_id=callback.from_user.id)
    user_id = user['user_id']
    referral_link = f"https://t.me/{(await callback.bot.me()).username}?start=ref_{user_id}"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
    
    invite_text = (
        f"🤝 <b>Реферальная программа</b>\n\n"
        f"📢 Приглашайте друзей и получайте бонусы:\n"
        f"└ 50₽ за каждого нового пользователя\n"
        f"└ 30% от пополнений рефералов\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{referral_link}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"└ Приглашено пользователей: {user.get('referral_count', 0)}\n"
        f"<i>Бонусы начисляются автоматически при регистрации новых пользователей по вашей ссылке</i>"
    )
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/01.jpg"),
            caption=invite_text
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "profile")
async def profile_command(callback: types.CallbackQuery):
    """
    Отображает подробную информацию о профиле пользователя
    """
    user = await get_user(user_id=callback.from_user.id)
    email = await get_user_email(callback.from_user.id)

    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Мой баланс", callback_data="pay_manager")
    kb.button(text="🔑 Мои подписки", callback_data="active_keys")
    kb.button(text="🎁 Активировать промокод", callback_data="promocode")
    kb.button(text="◀️ Главное меню", callback_data="back_to_menu")
    kb.adjust(1, 1, 1)
    
    keys_count = await get_keys_count(callback.from_user.id)
    referral_count = await get_referral_count(callback.from_user.id)

    profile_text = (
        "👤 <b>Мой профиль</b>\n\n"
        f"👥 Пользователь: @{callback.from_user.username or 'Без username'}\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"💳 Email: <code>{email if email else 'Не указан'}</code>\n"
        f"💰 Баланс: <b>{format(int(user['balance']), ',')}₽</b>\n"
        f"🤝 Приглашено друзей: <b>{referral_count}</b>\n"
        f"🔑 Активных ключей: <b>{keys_count}</b>\n\n"
    )

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/02.jpg"),
            caption=profile_text
        )
    )

    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())    
    await callback.answer()

@router.callback_query(F.data == "pay_manager")
async def pay_manager(callback: types.CallbackQuery, state: FSMContext):
    pay_manager_text = (
        "💳 <b>Управление платежами</b>\n\n"
        "Здесь вы можете:\n\n"
        "• 💰 Пополнить баланс - внести средства на счёт\n"
        "• 💳 Мои транзакции - посмотреть историю транзакций\n"
        "• ⚙️ Методы оплаты - управлять методами оплаты\n"
        "• 📨 Изменить email - для чеков и уведомлений\n\n"
        "<i>Все платежи безопасны и проходят через защищенные каналы.\n"
        "Чеки об оплате будут отправлены на указанный email.</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Пополнить баланс", callback_data="add_balance")
    kb.button(text="💳 Мои транзакции", callback_data="transactions") 
    kb.button(text="⚙️ Методы оплаты", callback_data="my_subscriptions")
    kb.button(text="📨 Изменить email", callback_data="change_email")
    kb.button(text="◀️ Назад в профиль", callback_data="profile")
    kb.adjust(1, 1, 1)
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/02.jpg"),
            caption=pay_manager_text
        )
    )

    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())    
    await callback.answer()    

@router.callback_query(F.data == "change_email")
async def change_email(callback: types.CallbackQuery, state: FSMContext):
    """
    Обрабатывает команду для изменения email пользователя
    """
    await state.set_state(AccountStates.waiting_for_email)
    await callback.message.answer("Введите новый email:")
    await callback.answer()

@router.message(AccountStates.waiting_for_email)
async def process_new_email(message: types.Message, state: FSMContext):
    """
    Обрабатывает новый email пользователя
    """
    await save_or_update_email(message.from_user.id, message.text)
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Главное меню", callback_data="back_to_menu")
    await message.answer(text="Email успешно изменен!", parse_mode="HTML", reply_markup=kb.as_markup())

@router.callback_query(F.data == "my_subscriptions")
async def my_subscriptions(callback: types.CallbackQuery):
    """
    Отображает список подписок пользователя
    """
    payment_methods = await get_user_payment_methods(callback.from_user.id)
    
    kb = InlineKeyboardBuilder()
    
    if payment_methods:
        for method in payment_methods:
            # Форматируем дату создания
            created_date = datetime.fromisoformat(method['created_at']).strftime("%d.%m.%Y")
            
            # Создаем кнопку для каждого метода оплаты
            kb.button(
                text=f"💳 {method['issuer_name']} от {created_date}",
                callback_data=f"payment_method_{method['id']}"
            )
    else:
        kb.button(text="🔑 У вас нет активных методов оплаты", callback_data="profile")
        
    kb.button(text="◀️ Назад в профиль", callback_data="profile")
    kb.adjust(1)
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/10.jpg"),
            caption="💳 <b>Мои методы оплаты</b>\n\n"
                   "Здесь отображаются ваши сохраненные методы оплаты для автоматического продления подписки.\n\n"
                   "Выберите метод оплаты для управления:"
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("payment_method_"))
async def payment_method_details(callback: types.CallbackQuery):
    """
    Отображает детали метода оплаты и опции управления
    """
    method_id = int(callback.data.split("_")[2])
    payment_method = await get_payment_method_by_id(method_id)
    
    if not payment_method:
        await callback.answer("Метод оплаты не найден", show_alert=True)
        await my_subscriptions(callback)
        return
    
    # Форматируем дату создания
    created_date = datetime.fromisoformat(payment_method['created_at']).strftime("%d.%m.%Y %H:%M")
    
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Удалить метод оплаты", callback_data=f"cancel_payment_method_{method_id}")
    kb.button(text="🔑 Сделать основным", callback_data=f"sync_payment_method_{method_id}")
    kb.button(text="◀️ Назад к подпискам", callback_data="my_subscriptions")
    kb.adjust(1)
    
    details_text = (
        f"💳 <b>Детали сохраненного метода оплаты</b>\n\n"
        f"└ 🆔 ID: <code>{payment_method['id']}</code>\n"
        f"└ 📅 Дата создания: {created_date}\n\n"
        f"ℹ️ Этот метод оплаты позволяет автоматически продлевать подписку VPN.\n"
        f"Для отмены автоматического продления нажмите кнопку «Удалить метод оплаты»."
    )
    
    await callback.message.edit_caption(
        caption=details_text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("sync_payment_method_"))
async def sync_payment_method(callback: types.CallbackQuery):
    """
    Сделать метод оплаты основным
    """
    method_id = int(callback.data.split("_")[3])
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, установить", callback_data=f"confirm_sync_payment_method_{method_id}")
    kb.button(text="❌ Нет, оставить", callback_data=f"payment_method_{method_id}")
    kb.adjust(2)
    await callback.message.edit_caption(
        caption="❓ <b>Подтверждение установки</b>\n\n"
               "Вы уверены, что хотите установить этот метод оплаты в качестве основного?\n\n"
               "Это действие установит данный метод оплаты для автоматического продления всех ключей в вашем профиле.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_sync_payment_method_"))
async def confirm_sync_payment_method(callback: types.CallbackQuery):
    """
    Подтверждает установку основного метода оплаты
    """
    method_id = int(callback.data.split("_")[4])
    try:
        await sync_payment_id_for_all_keys(callback.from_user.id, method_id)
        kb = InlineKeyboardBuilder()    
        kb.button(text="◀️ Назад", callback_data=f"payment_method_{method_id}")
        await callback.message.edit_caption(
            caption="✅ <b>Метод оплаты установлен в качестве основного</b>\n\n"
                "Теперь он будет использоваться для автоматического продления всех ключей в вашем профиле.\n\n"
                "Настроить индивидуально для каждого ключа метод оплаты вы можете в разделе <b>Мои ключи</b>",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при установке основного метода оплаты: {e}")
        await callback.answer("Произошла ошибка при установке основного метода оплаты. Попробуйте позже.")

@router.callback_query(F.data.startswith("cancel_payment_method_"))
async def confirm_cancel_payment_method(callback: types.CallbackQuery):
    """
    Запрашивает подтверждение отмены подписки
    """
    method_id = int(callback.data.split("_")[3])
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, отменить", callback_data=f"confirm_cancel_payment_{method_id}")
    kb.button(text="❌ Нет, оставить", callback_data=f"payment_method_{method_id}")
    kb.adjust(2)
    
    await callback.message.edit_caption(
        caption="❓ <b>Подтверждение удаления</b>\n\n"
               "Вы уверены, что хотите удалить метод оплаты?\n\n"
               "После удаления вам придется вручную продлевать подписку VPN.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_cancel_payment_"))
async def cancel_payment_method(callback: types.CallbackQuery, bot: Bot):
    """
    Отменяет подписку пользователя
    """
    method_id = int(callback.data.split("_")[3])
    
    try:
        # Получаем информацию о методе оплаты перед удалением
        payment_method = await get_payment_method_by_id(method_id)
        
        if not payment_method:
            await callback.answer("Метод оплаты не найден", show_alert=True)
            await my_subscriptions(callback)
            return
        
        # Проверяем, принадлежит ли метод оплаты текущему пользователю
        if payment_method['user_id'] != callback.from_user.id:
            await callback.answer("У вас нет прав для удаления этой подписки", show_alert=True)
            return
        
        # Удаляем метод оплаты
        success = await delete_payment_method(method_id)
        
        if success:
            kb = InlineKeyboardBuilder()
            kb.button(text="◀️ Назад к подпискам", callback_data="my_subscriptions")
            kb.adjust(1)
            
            await callback.message.edit_caption(
                caption="✅ <b>Подписка успешно отменена</b>\n\n"
                       "Автоматическое продление отключено.\n"
                       "Вы можете продолжать пользоваться VPN до окончания текущего периода.",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            
            # Логируем действие для администраторов
            await send_info_for_admins(
                f"[Удаление метода оплаты] Пользователь {callback.from_user.id} удалил метод оплаты ID: {method_id}",
                await get_admins(),
                bot,
                username=callback.from_user.username
            )
        else:
            await callback.answer("Произошла ошибка при удалении метода оплаты. Попробуйте позже.", show_alert=True)
    
    except Exception as e:
        logger.error(f"Ошибка при удалении метода оплаты: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        
        # Логируем ошибку для администраторов
        await send_info_for_admins(
            f"[Ошибка удаления метода оплаты] Пользователь {callback.from_user.id}, ошибка: {e}",
            await get_admins(),
            bot,
            username=callback.from_user.username
        )
    
    await callback.answer()



@router.callback_query(F.data == "transactions")
async def get_transactions(callback: types.CallbackQuery, bot: Bot):
    """
    Отображает список транзакций пользователя в виде кнопок
    """
    transactions = await get_user_transactions(callback.from_user.id)
    kb = InlineKeyboardBuilder()
    
    if transactions:
        for tx in transactions:
            status_emoji = {
                'pending': '⏳',
                'succeeded': '✅',
                'failed': '❌',
                'cancelled': '🚫'
            }.get(tx['status'], '❓')
            
            kb.button(
                text=f"{status_emoji} {tx['amount']}₽ - {tx['created_at'][:16]}",
                callback_data=f"transaction_{tx['transaction_id']}"
            )
    else:
        kb.button(text="Нет транзакций", callback_data="profile")
        
    kb.button(text="◀️ Назад", callback_data="profile")
    kb.adjust(1)
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/10banner.png"),
            caption="💳 <b>История транзакций</b>\n\nВыберите транзакцию для просмотра деталей:"
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())    
    await callback.answer()

@router.callback_query(F.data.startswith("transaction_"))
async def show_transaction_details(callback: types.CallbackQuery, state: FSMContext):
    """
    Показывает детали конкретной транзакции
    """
    transaction_id = callback.data.split('_')[1]
    transactions = await get_user_transactions(callback.from_user.id)
    transaction = next((tx for tx in transactions if tx['transaction_id'] == transaction_id), None)
    
    if not transaction:
        await callback.answer("Транзакция не найдена", show_alert=True)
        return
    
    status_emoji = {
        'pending': '⏳ В обработке',
        'succeeded': '✅ Выполнено',
        'failed': '❌ Ошибка',
        'cancelled': '🚫 Отменено'
    }.get(transaction['status'], '❓ Неизвестно')
    
    details = (
        f"💳 <b>Детали транзакции</b>\n\n"
        f"└ 🆔 ID: <code>{transaction['transaction_id']}</code>\n"
        f"└ 💰 Сумма: {transaction['amount']}₽\n"
        f"└ ⏱ Дата: {transaction['created_at']}\n"
        f"└ 📊 Статус: {status_emoji}"
    )
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопку проверки статуса только для pending транзакций
    if transaction['status'] == 'pending':
        await state.update_data(transaction_id=transaction['transaction_id'], amount=transaction['amount'])
        kb.button(text="🔄 Проверить статус", callback_data="check_transaction")
        
    kb.button(text="◀️ Назад", callback_data="transactions")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption=details,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "check_transaction")
async def check_user_transaction(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Проверяет статус транзакции и отображает результат
    """
    data = await state.get_data()
    kb = InlineKeyboardBuilder()
    try:
        transaction_id = data.get('transaction_id')
        amount = data.get("amount")
        # Получаем текущую транзакцию из БД
        current_transaction = await get_transaction_by_id(transaction_id)
        if not current_transaction:
            await callback.answer("Транзакция не найдена", show_alert=True)
            return
        # Получаем новый статус
        new_status, payment = await check_transaction_status(transaction_id)
        # Если статус не изменился и это не pending, показываем уведомление
        if current_transaction['status'] == new_status and new_status != 'pending':
            await callback.answer("Статус платежа не изменился", show_alert=True)
            return
        status_info = {
            'pending': {
                'emoji': '⏳',
                'text': 'Платёж в обработке',
                'description': 'Ожидаем подтверждение от платежной системы...'
            },
            'succeeded': {
                'emoji': '✅',
                'text': 'Платёж успешно завершен',
                'description': 'Средства успешно зачислены на ваш баланс!'
            },
            'failed': {
                'emoji': '❌',
                'text': 'Платёж не удался',
                'description': 'К сожалению, произошла ошибка при обработке платежа.'
            },
            'cancelled': {
                'emoji': '🚫',
                'text': 'Платёж отменен',
                'description': 'Транзакция была отменена.'
            }
        }.get(new_status, {
            'emoji': '❌',
            'text': 'Платёж истек',
            'description': 'Вы не успели оплатить данный заказ.'
        })
        message_text = (
            f"{status_info['emoji']} <b>{status_info['text']}</b>\n\n"
            f"└ 🆔 ID: <code>{transaction_id}</code>\n"
            f"└ 💰 Сумма: {amount}₽\n"
            f"└ 📝 {status_info['description']}"
        )
        buttons = []
        if new_status == 'pending':
            buttons = [
                ("🔄 Проверить снова", "check_transaction"),
                ("💭 Поддержка", "support"),
                (" Назад", "transactions")

            ]
        elif "succeeded" not in new_status:
            buttons = [
                ("💳 Попробовать снова", "add_funds"),
                ("💭 Поддержка", "support"),
                (" Назад", "transactions")
            ]
        elif new_status == 'succeeded':
            if current_transaction['status'] != 'succeeded':
                await update_transaction_status(transaction_id=transaction_id, new_status="succeeded")
                await update_balance(callback.from_user.id, amount)

                if payment: 
                    user = await get_user(user_id=callback.from_user.id)
                    first_deposit = await get_is_first_payment_done(user['user_id'])

                    if payment.payment_method.saved:
                        if not first_deposit:
                            await sync_payment_id_for_all_keys(user['user_id'], payment.payment_method.id)

                        await bot.send_message(callback.from_user.id, "💳 <b>Платеж успешно завершен</b>\n\n"
                                               f"💳 <b>Сумма:</b> {amount}₽\n\n"
                                               "💳 Метод оплаты сохранён в вашем профиле.\n\n"
                                               "Теперь отправьте желаемое название для этого метода оплаты",
                                               parse_mode="HTML",
                                               reply_markup=kb.as_markup()
                                               )        
                        await state.update_data(saved_id=payment.payment_method.id)
                        await state.set_state(SubscriptionStates.waiting_for_payment_method_name)
                # Проверка и начисление реферальных бонусов
                if user['referrer_id']:
                    referrer = await get_user(user_id=user['referrer_id'])

                    bonus_percentage = 0.5 if first_deposit else 0.3
                    await update_balance(referrer['user_id'], int(referrer['balance']) + int(amount) * bonus_percentage)

                    try:
                        kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                        await bot.send_message(
                            user['referrer_id'],
                            f"🎉 <b>Поздравляем!</b>\n\n"
                            f"Ваш реферал пополнил баланс на сумму {amount}₽\n"
                            f"Вам начислен бонус: <b>{int(amount) * bonus_percentage}₽</b> ({bonus_percentage * 100}%)",
                            parse_mode="HTML",
                            reply_markup=kb.as_markup()
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления рефереру: {e}")
                        await send_info_for_admins(f"[ЮKassa. Пополнение баланса] Ошибка при отправке уведомления рефереру: {e}", await get_admins(), bot, username=referrer.get("username"))

                await callback.answer("Баланс успешно пополнен!", show_alert=True)
                buttons.append(("◀️ Назад к транзакциям", "transactions"))
        for text, callback_data in buttons:
            kb.button(text=text, callback_data=callback_data)
        kb.adjust(1)
        try:
            await callback.message.edit_caption(
                caption=message_text,
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" in str(e):
                await callback.answer("Статус транзакции не изменился")
            else:
                raise
    except Exception as e:
        error_message = (
            "❌ <b>Произошла ошибка</b>\n\n"
            "Не удалось проверить статус платежа.\n"
            "Пожалуйста, обратитесь в поддержку."
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="💭 Поддержка", callback_data="support")
        kb.button(text="◀️ Назад", callback_data="transactions")
        kb.adjust(1)
        await callback.message.edit_caption(
            caption=error_message,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await send_info_for_admins(
            f"[Проверка транзакции] Ошибка: {e}",
            await get_admins(),
            bot,
            username=callback.from_user.username
        )
        await callback.answer()


@router.callback_query(F.data == "active_keys")
async def active_keys(callback: types.CallbackQuery, bot: Bot):
    """
    Отображает меню выбора категории ключей
    """
    try:
        user_keys = await get_user_keys(callback.from_user.id)
        kb = InlineKeyboardBuilder()
        if not user_keys:
            kb.button(text="◀️ Назад в профиль", callback_data="profile")
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=FSInputFile("handlers/images/09.jpg"),
                    caption="🔑 <b>Активные ключи</b>\n\nУ вас пока нет активных ключей."
                )
            )
            await callback.message.edit_reply_markup(reply_markup=kb.as_markup()) 
            return

        device_counts = {}
        for key in user_keys:
            original_device = key[1].lower()
            
            # Упорядоченный список замен (от длинных к коротким)
            replacements = [
                ("andtv", "androidtv"),
                ("androidtv", "androidtv"),
                ("and", "android"),
                ("win", "windows"),
                ("tv", "androidtv")  # Добавлена новая замена
            ]
            
            # Поиск первого совпадения
            normalized_device = next(
                (v for k, v in replacements if original_device.startswith(k)),
                original_device
            )
            
            device_counts[normalized_device] = device_counts.get(normalized_device, 0) + 1

        devices = {
            "ios": "📱 iOS",
            "androidtv": "📺 Android TV",
            "android": "🤖 Android",
            "windows": "🖥 Windows",
            "mac": "🍎 macOS",
        }

        for device_key, count in device_counts.items():
            if device_key in devices:
                kb.button(
                    text=f"{devices[device_key]} ({count})",
                    callback_data=f"show_keys_{device_key}"
                )
                
        kb.button(text="🔑 Настроить ключ", callback_data="key_settings")
        kb.button(text="◀️ Назад в профиль", callback_data="profile")
        kb.adjust(1)
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile("handlers/images/09.jpg"),
                caption="🔑 <b>Выберите категорию ключей:</b>",
            )
        ) 
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())

    except Exception as e:
        logger.error(f"Error in active_keys: {e}")
        await send_info_for_admins(
            f"[Активные ключи] Ошибка: {e}",
            await get_admins(),
            bot,
            username=callback.from_user.username
        )
        # Отправляем пользователю сообщение об ошибке
        await callback.message.answer(
            "❌ Произошла ошибка при загрузке ключей. Попробуйте позже."
        )


@router.callback_query(F.data == "key_settings")
async def key_settings(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Продлить подписку", callback_data="extend_subscription")
    kb.button(text="🔄 Заменить ключ", callback_data="replace_key")
    kb.button(text="🌍 Изменить страну", callback_data="change_key_country")
    kb.button(text="📡 Изменить протокол", callback_data="change_key_protocol")
    kb.button(text="📝 Изменить название ключа", callback_data="change_key_name")
    kb.button(text="◀️ Назад", callback_data="active_keys")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption=(
            "⚙️ <b>Настройки ключа</b>\n\n"
            "Выберите действие:\n\n"
            "🔄 <b>Продлить подписку</b>\n"
            "└ Продлить срок действия текущего ключа\n\n"
            "🔄 <b>Заменить ключ</b>\n"
            "└ Получить новый ключ взамен текущего\n\n"
            "🌍 <b>Изменить страну</b>\n"
            "└ Выбрать другой сервер подключения\n\n"
            "📡 <b>Изменить протокол</b>\n"
            "└ Сменить протокол подключения\n\n"
            "📝 <b>Изменить название ключа</b>\n"
            "└ Задать свое название для ключа\n\n"
        ),
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()




@router.callback_query(F.data == "change_key_name")
async def change_key_name(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображает список доступных ключей пользователя для изменения имени
    """
    user_keys = await get_user_keys(callback.from_user.id)
    
    if not user_keys:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="key_settings")
        await callback.message.edit_caption(
            caption="❌ <b>У вас нет активных ключей</b>\n\n"
            "Чтобы начать пользоваться VPN, приобретите подписку.",
            reply_markup=kb.as_markup()
        )
        return
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопку для каждого ключа
    for key_data in user_keys:
        key = key_data[0]            # key
        device_id = key_data[1]      # device_id
        expiration_date = key_data[2] # expiration_date
        name = key_data[3]           # name
        
        # Определяем протокол
        protocol = 'Shadowsocks' if key.startswith('ss://') else 'VLESS'
        
        # Определяем отображаемое имя для кнопки
        if name:
            display_name = f"«{name}»"  # Выделяем имя ключа кавычками
        else:
            # Если имя не задано, извлекаем email из ключа
            display_name = "Ключ"
            if "#Atlanta%20VPN-" in key:
                display_name = key.split("#Atlanta%20VPN-")[1]
        
        # Создаем сокращенный идентификатор ключа для callback_data
        short_key_id = hashlib.md5(key.encode()).hexdigest()[:10]
        kb.button(
            text=f"🔑 {device_id.upper()} - {protocol} - {display_name}", 
            callback_data=f"select_name_key_{short_key_id}"
        )
        
        # Сохраняем ключ в состоянии для последующего доступа
        await state.update_data({
            f"name_key_{short_key_id}": key
        })
    
    kb.button(text="◀️ Назад", callback_data="key_settings")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption="📝 <b>Выберите ключ для изменения названия</b>\n\n"
        "Нажмите на ключ, название которого хотите изменить:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("select_name_key_"))
async def select_key_for_name_change(callback: types.CallbackQuery, state: FSMContext):
    """
    Обрабатывает выбор ключа для изменения имени
    """
    key_id = callback.data.split("_")[-1]
    user_data = await state.get_data()
    key = user_data.get(f"name_key_{key_id}")
    
    if not key:
        await callback.answer("Ошибка: ключ не найден", show_alert=True)
        return
    
    # Сохраняем сам ключ в состоянии для последующего доступа
    await state.update_data({"selected_key_for_name": key})
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="change_key_name")
    
    await callback.message.edit_caption(
        caption="📝 <b>Введите новое название для ключа</b>\n\n"
        "Отправьте сообщение с новым названием для выбранного ключа.\n"
        "Максимальная длина: 20 символов.",
        reply_markup=kb.as_markup()
    )
    
    # Устанавливаем состояние ожидания нового имени
    await state.set_state(KeyNameStates.waiting_for_new_name)

@router.message(KeyNameStates.waiting_for_new_name)
async def process_new_key_name(message: Message, state: FSMContext, bot: Bot):
    """
    Обрабатывает ввод нового имени для ключа
    """
    new_name = message.text.strip()
    
    # Проверяем длину имени
    if len(new_name) > 20:
        await message.answer(
            "❌ Ошибка: имя ключа не должно превышать 20 символов.\n\n"
            "Пожалуйста, введите более короткое название."
        )
        return
    
    # Получаем ключ из состояния
    user_data = await state.get_data()
    key = user_data.get("selected_key_for_name")
    
    if not key:
        await message.answer("❌ Ошибка: ключ не найден.")
        await state.clear()
        return
    
    # Обновляем имя ключа в БД
    success = await update_key_name(key, new_name)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад к настройкам", callback_data="key_settings")
    
    if success:
        await message.answer_photo(
            photo=FSInputFile("handlers/images/09.jpg"),
            caption=f"✅ <b>Название ключа успешно изменено</b>\n\n"
                    f"Новое название: <b>{new_name}</b>",
            reply_markup=kb.as_markup()
        )
    else:
        await message.answer_photo(
            photo=FSInputFile("handlers/images/09.jpg"),
            caption="❌ <b>Не удалось изменить название ключа</b>\n\n"
                    "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=kb.as_markup()
        )
    
    # Очищаем состояние
    await state.clear()

@router.callback_query(F.data == "change_key_protocol")
async def change_key_protocol(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображает список доступных ключей пользователя для изменения протокола
    """
    user_keys = await get_user_keys(callback.from_user.id)
    
    if not user_keys:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="key_settings")
        await callback.message.edit_caption(
            caption="❌ <b>У вас нет активных ключей</b>\n\n"
            "Чтобы начать пользоваться VPN, приобретите подписку.",
            reply_markup=kb.as_markup()
        )
        return
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопку только для ключей Shadowsocks (разрешена смена только SS -> VLESS)
    shadowsocks_keys = []
    for key_data in user_keys:
        key = key_data[0]            # key
        device_id = key_data[1]      # device_id
        expiration_date = key_data[2] # expiration_date
        name = key_data[3]           # name
        
        # Определяем протокол
        protocol = 'Shadowsocks' if key.startswith('ss://') else 'VLESS'
        
        # Показываем только ключи Shadowsocks для смены на VLESS
        if protocol == 'Shadowsocks':
            shadowsocks_keys.append(key_data)
            
            # Определяем отображаемое имя для кнопки
            if name:
                display_name = f"«{name}»"  # Выделяем имя ключа кавычками
            else:
                # Если имя не задано, извлекаем email из ключа
                display_name = "Ключ"
                if "#Atlanta%20VPN-" in key:
                    display_name = key.split("#Atlanta%20VPN-")[1]
            
            # Создаем сокращенный идентификатор ключа для callback_data
            key_id = hashlib.md5(key.encode()).hexdigest()[:10]
            kb.button(
                text=f"🔑 {device_id.upper()} - {protocol} - {display_name}", 
                callback_data=f"select_protocol_key_{key_id}"
            )
            
            # Сохраняем ключ в состоянии для последующего доступа
            await state.update_data({f"protocol_key_{key_id}": key})
    
    # Проверяем, есть ли ключи Shadowsocks для смены
    if not shadowsocks_keys:
        kb.button(text="◀️ Назад", callback_data="key_settings")
        await callback.message.edit_caption(
            caption="❌ <b>Нет доступных ключей для смены протокола</b>\n\n"
            "Смена протокола доступна только с Shadowsocks на VLESS, по причине массовой блокировки провайдерами.\n"
            "У вас нет активных ключей Shadowsocks.",
            reply_markup=kb.as_markup()
        )
        return
    
    kb.button(text="◀️ Назад", callback_data="key_settings")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption="🔄 <b>Выберите ключ для изменения протокола</b>\n\n"
        "Доступна смена только с Shadowsocks на VLESS.\n"
        "Нажмите на ключ, протокол которого хотите изменить:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("select_protocol_key_"))
async def select_key_for_protocol_change(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обрабатывает выбор ключа для изменения протокола и запрашивает подтверждение
    """
    key_id = callback.data.split("_")[3]
    user_data = await state.get_data()
    key = user_data.get(f"protocol_key_{key_id}")
    
    if not key:
        await callback.answer("Ключ не найден. Попробуйте еще раз.")
        return
    
    protocol = 'Shadowsocks' if key.startswith('ss://') else 'VLESS'
    new_protocol = 'VLESS' if key.startswith('ss://') else 'Shadowsocks'
    
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"✅ Изменить на {new_protocol}", 
        callback_data=f"confirm_protocol_change_{key_id}"
    )
    kb.button(text="◀️ Отмена", callback_data="change_key_protocol")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption=f"🔄 <b>Изменение протокола</b>\n\n"
        f"Текущий протокол: <b>{protocol}</b>\n"
        f"Новый протокол: <b>{new_protocol}</b>\n\n"
        f"❗️ <b>Внимание:</b> После смены протокола вам потребуется заново настроить VPN клиент "
        f"с новым ключом. Хотите продолжить?",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("confirm_protocol_change_"))
async def process_protocol_change(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обрабатывает изменение протокола ключа с улучшенной последовательностью:
    1. Сначала создаем новый ключ с новым протоколом
    2. Показываем его пользователю
    3. Затем удаляем старый ключ
    4. При ошибке удаления старого ключа пользователь все равно получает новый ключ
    """
    key_id = callback.data.split("_")[3]
    user_data = await state.get_data()
    key = user_data.get(f"protocol_key_{key_id}")
    
    if not key:
        await callback.answer("Ключ не найден. Попробуйте еще раз.")
        return
    
    try:
        # Устанавливаем индикатор загрузки
        await callback.message.edit_caption(
            caption="⏳ <b>Пожалуйста, подождите...</b>\n\n"
            "Создаем для вас новый ключ с другим протоколом. Это может занять некоторое время."
        )
        
        # Получение данных ключа
        current_protocol = 'ss' if key.startswith('ss://') else 'vless'
        device, unique_id, unique_uuid, address, parts = extract_key_data(key)
        old_expiry_time = await get_key_expiry_date(key)
        server = await get_server_by_address(address, protocol="shadowsocks" if current_protocol == 'ss' else "vless")
        
        if not server:
            raise Exception("Не удалось найти сервер для данного ключа")
        
        # 1. Создание нового клиента с противоположным протоколом
        api, server_address, pbk, sid, sni, port, utls, new_protocol, country, inbound_id = await get_api_instance(
            country=server['country'],
            use_shadowsocks=(current_protocol == 'vless')  # Меняем протокол на противоположный
        )
        
        await send_info_for_admins(
            f"[Контроль Сервера, Функция: process_protocol_change]\nНайденый сервер:\n{server_address},\n{pbk},\n{sid}\n{sni}... ",
            await get_admins(),
            bot,
            username=callback.from_user.username
        )
        
        await api.login()
        
        # Создание уникального email для нового клиента
        random_part = ''.join(random.choice(string.ascii_lowercase) for _ in range(4))
        unique_email = f"{parts[0]}_{random_part}_{parts[2]}"
        
        # Создание клиента в зависимости от протокола
        if current_protocol == 'vless':  # Меняем VLESS на Shadowsocks
            method = "chacha20-ietf-poly1305"
            password = generate_random_string(32)
            new_client = Client(
                id=generate_random_string(8),
                email=unique_email,
                password=password,
                method=method,
                enable=True,
                expiry_time=old_expiry_time
            )
        else:  # Меняем Shadowsocks на VLESS
            new_uuid = str(uuid.uuid4())
            new_client = Client(
                id=new_uuid,
                email=unique_email,
                enable=True,
                expiry_time=old_expiry_time,
                flow="xtls-rprx-vision"
            )
        
        # Добавление нового клиента
        await api.client.add(inbound_id, [new_client])
        server_address_base = server_address.split(':')[0]
        
        # Генерация нового ключа
        if current_protocol == 'vless':  # Создаем Shadowsocks ключ
            ss_config = f"{method}:{password}"
            encoded_config = base64.urlsafe_b64encode(ss_config.encode()).decode().rstrip('=')
            new_key = f"ss://{encoded_config}@{server_address_base}:{port}?type=tcp#Atlanta%20VPN-{new_client.email}"
        else:  # Создаем VLESS ключ
            new_key = (
                f"vless://{new_uuid}@{server_address_base}:{port}"
                "?type=tcp&security=reality"
                f"&pbk={pbk}"
                f"&fp={utls}&sni={sni}&sid={sid}&spx=%2F"
                f"&flow=xtls-rprx-vision#Atlanta%20VPN-{new_client.email}"
            )
        
        # 2. Проверка создания нового клиента
        new_client_check = await api.client.get_by_email(new_client.email)
        if not new_client_check:
            raise Exception("Не удалось создать нового клиента на сервере")
        
        # 3. Добавляем новый ключ в БД пользователя
        await add_active_key(callback.from_user.id, new_key, device, old_expiry_time, device)
        
        # 4. Обновляем счетчик на новом сервере
        clients_count = await get_server_count_by_address(
            server_address_base, inbound_id, 
            protocol="shadowsocks" if new_protocol == 'ss' else "vless"
        )
        await update_server_clients_count(server_address_base, clients_count + 1, inbound_id)
        
        # 5. Отправка сообщения пользователю о новом ключе
        kb = InlineKeyboardBuilder()
        kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
        kb.button(text="🔑 Мои ключи", callback_data="active_keys")
        kb.button(text="🔧 Настройки ключа", callback_data="key_settings")
        kb.adjust(1)
        
        new_protocol_name = "Shadowsocks" if current_protocol == 'vless' else "VLESS"
        await callback.message.edit_caption(
            caption=f"✅ <b>Протокол успешно изменен на {new_protocol_name}!</b>\n\n"
            f"📱 Устройство: {device.upper()}\n"
            f"🔑 Новый ключ:\n<code>{new_key}</code>\n\n"
            "ℹ️ Используйте новый ключ для подключения.\n"
            "Старый ключ скоро будет деактивирован.",
            reply_markup=kb.as_markup()
        )
        
        logger.info(f"New key with protocol {new_protocol_name} created for user {callback.from_user.id}")
        
        # 6. Теперь пытаемся удалить старый ключ (асинхронно, не блокируя пользователя)
        try:
            # Получаем данные старого сервера для удаления клиента
            old_server = await get_server_by_address(
                address, 
                protocol="shadowsocks" if current_protocol == 'ss' else "vless"
            )
            
            if old_server:
                old_api = AsyncApi(
                    f"http://{old_server['address']}",
                    old_server['username'],
                    old_server['password'],
                    use_tls_verify=False
                )
                await old_api.login()
                
                # Удаляем старого клиента
                if current_protocol == 'ss':
                    await old_api.client.delete(
                        inbound_id=old_server['inbound_id'], 
                        client_uuid=f"{parts[0]}_{parts[1]}_{parts[2]}"
                    )
                else:
                    await old_api.client.delete(
                        inbound_id=old_server['inbound_id'], 
                        client_uuid=str(unique_uuid)
                    )
                
                # Обновляем счетчик на старом сервере
                old_clients_count = await get_server_count_by_address(
                    address, 
                    old_server['inbound_id'], 
                    protocol="shadowsocks" if current_protocol == 'ss' else "vless"
                )
                await update_server_clients_count(
                    address, 
                    old_clients_count - 1, 
                    old_server['inbound_id']
                )
            
            # Удаляем старый ключ из БД пользователя
            await remove_key_bd(key)
            logger.info(f"Old key successfully removed for user {callback.from_user.id}")
            
        except Exception as delete_error:
            # Если произошла ошибка при удалении старого ключа, логируем её,
            # но не прерываем процесс и не уведомляем пользователя
            logger.error(f"Error deleting old key: {delete_error}. Key might remain active.")
            await send_info_for_admins(
                f"[Смена протокола] Не удалось удалить старый ключ для пользователя {callback.from_user.id}: {delete_error}",
                await get_admins(),
                bot,
                username=callback.from_user.username
            )
            
    except Exception as e:
        # Если произошла ошибка при создании нового ключа
        logger.error(f"Error changing protocol: {str(e)}", exc_info=True)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Попробовать ещё раз", callback_data="change_key_protocol")
        kb.button(text="🔧 Настройки ключа", callback_data="key_settings")
        kb.button(text="◀️ В главное меню", callback_data="back_to_menu")
        kb.adjust(1)
        
        await callback.message.edit_caption(
            caption="❌ <b>Произошла ошибка при изменении протокола</b>\n\n"
            f"{str(e)}\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=kb.as_markup()
        )
    
    finally:
        await state.clear()



@router.callback_query(F.data == "change_key_country")
async def change_key_country(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображает список доступных ключей пользователя для изменения страны
    """
    user_keys = await get_user_keys(callback.from_user.id)
    
    if not user_keys:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="back_to_menu")
        await callback.message.edit_caption(
            caption="❌ <b>У вас нет активных ключей</b>\n\n"
            "Чтобы начать пользоваться VPN, приобретите подписку.",
            reply_markup=kb.as_markup()
        )
        return
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопку для каждого ключа
    for key_data in user_keys:
        key = key_data[0]            # key
        device_id = key_data[1]      # device_id
        expiration_date = key_data[2] # expiration_date
        name = key_data[3]           # name
        
        # Определяем отображаемое имя для кнопки
        if name:
            display_name = f"«{name}»"  # Выделяем имя ключа кавычками
        else:
            # Если имя не задано, извлекаем email из ключа
            display_name = "Ключ"
            if "#Atlanta%20VPN-" in key:
                display_name = key.split("#Atlanta%20VPN-")[1]
        
        # Извлекаем данные о стране из ключа
        protocol = 'ss' if key.startswith('ss://') else 'vless'
        _, _, _, address, _ = extract_key_data(key)
        server = await get_server_by_address(address, protocol="shadowsocks" if protocol == 'ss' else "vless")
        country = server.get('country', 'Неизвестно') if server else 'Неизвестно'
        
        # Создаем сокращенный идентификатор ключа для callback_data
        key_id = hashlib.md5(key.encode()).hexdigest()[:10]
        kb.button(
            text=f"🔑 {device_id.upper()} - {display_name} ({country})", 
            callback_data=f"select_country_key_{key_id}"
        )
        
        # Сохраняем ключ в состоянии для последующего доступа
        await state.update_data({f"country_key_{key_id}": key})
    
    kb.button(text="◀️ Назад", callback_data="key_settings")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption="🌍 <b>Выберите ключ для изменения страны</b>\n\n"
        "Нажмите на ключ, страну которого хотите изменить:",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("select_country_key_"))
async def select_key_for_country_change(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обрабатывает выбор ключа для изменения страны и показывает доступные страны
    """
    key_id = callback.data.split("_")[3]
    user_data = await state.get_data()
    key = user_data.get(f"country_key_{key_id}")
    
    if not key:
        await callback.answer("Ключ не найден. Попробуйте еще раз.")
        return
    
    protocol = 'ss' if key.startswith('ss://') else 'vless'
    device, unique_id, unique_uuid, address, parts = extract_key_data(key)
    server = await get_server_by_address(address, protocol="shadowsocks" if protocol == 'ss' else "vless")
    
    # Получаем все доступные страны
    countries = await get_available_countries(protocol=protocol)
    kb = InlineKeyboardBuilder()
    
    # Фильтруем страны, исключая текущую
    current_country = server['country']
    
    # Добавляем только страны, отличные от текущей
    available_countries = []
    for country in countries:
        if country['name'] != current_country:
            kb.button(
                text=f"{country['name']}", 
                callback_data=f"country_change_{key_id}_{country['code']}"
            )
            available_countries.append(country['name'])
    
    if not available_countries:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="change_key_country")
        await callback.message.edit_caption(
            caption="❌ <b>Нет доступных стран для изменения</b>\n\n"
            f"Текущая страна: <b>{current_country}</b>\n\n"
            "В данный момент нет других стран, доступных для выбора.",
            reply_markup=kb.as_markup()
        )
        return
    
    kb.button(text="◀️ Назад", callback_data="change_key_country")
    kb.adjust(1)
    
    # Сохраняем данные для последующей обработки
    await state.update_data(
        country_key=key,
        current_country=current_country,
        device=device
    )
    
    await callback.message.edit_caption(
        caption=f"🌍 <b>Изменение страны</b>\n\n"
        f"📱 Устройство: {device.upper()}\n"
        f"🌐 Текущая страна: <b>{current_country}</b>\n\n"
        "Выберите новую страну для ключа:",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("country_change_"))
async def process_country_change(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обрабатывает выбор новой страны для ключа с улучшенной последовательностью:
    1. Сначала создаем новый ключ
    2. Показываем его пользователю
    3. Затем удаляем старый ключ
    4. При ошибке удаления старого ключа пользователь все равно получает новый ключ
    """
    key_id = callback.data.split("_")[2]
    country_code = callback.data.split("_")[3]
    
    user_data = await state.get_data()
    key = user_data.get("country_key")
    current_country = user_data.get("current_country")
    device = user_data.get("device")
    
    if not key:
        await callback.answer("Ключ не найден. Попробуйте еще раз.")
        return
    
    try:
        # Устанавливаем индикатор загрузки
        await callback.message.edit_caption(
            caption="⏳ <b>Пожалуйста, подождите...</b>\n\n"
            "Создаем для вас новый ключ в выбранной стране. Это может занять некоторое время."
        )
        
        # Получение данных ключа
        protocol = 'ss' if key.startswith('ss://') else 'vless'
        device, unique_id, unique_uuid, address, parts = extract_key_data(key)
        old_expiry_time = await get_key_expiry_date(key)
        
        # 1. Создание нового клиента в выбранной стране
        api, server_address, pbk, sid, sni, port, utls, protocol, country, inbound_id = await get_api_instance(
            country=country_code,
            use_shadowsocks=(protocol == 'ss')
        )
        
        await send_info_for_admins(
            f"[Контроль Сервера, Функция: process_country_change]\nНайденый сервер:\n{server_address},\n{pbk},\n{sid}\n{sni}... ",
            await get_admins(),
            bot,
            username=callback.from_user.username
        )
        
        await api.login()
        
        # Создание уникального email для нового клиента
        random_part = ''.join(random.choice(string.ascii_lowercase) for _ in range(4))
        unique_email = f"{parts[0]}_{random_part}_{parts[2]}"
        
        # Создание клиента в зависимости от протокола
        if protocol == 'vless':
            new_client = Client(
                id=unique_uuid,
                email=unique_email,
                enable=True,
                expiry_time=old_expiry_time,
                flow="xtls-rprx-vision"
            )
        else:
            method = "chacha20-ietf-poly1305"
            password = generate_random_string(32)
            new_client = Client(
                id=generate_random_string(8),
                email=unique_email,
                password=password,
                method=method,
                enable=True,
                expiry_time=old_expiry_time
            )
        
        # Добавление нового клиента
        await api.client.add(inbound_id, [new_client])
        server_address_base = server_address.split(":")[0]
        
        # Генерация нового ключа
        if protocol == 'vless':
            new_key = (
                f"vless://{unique_uuid}@{server_address_base}:{port}"
                "?type=tcp&security=reality"
                f"&pbk={pbk}"
                f"&fp={utls}&sni={sni}&sid={sid}&spx=%2F"
                f"&flow=xtls-rprx-vision#Atlanta%20VPN-{new_client.email}"
            )
        else:
            ss_config = f"{method}:{password}"
            encoded_config = base64.urlsafe_b64encode(ss_config.encode()).decode().rstrip('=')
            new_key = f"ss://{encoded_config}@{server_address_base}:{port}?type=tcp#Atlanta%20VPN-{new_client.email}"
        
        # 2. Проверка создания нового клиента
        new_client_check = await api.client.get_by_email(new_client.email)
        if not new_client_check:
            raise Exception("Не удалось создать нового клиента на сервере")
        
        # 3. Добавляем новый ключ в БД пользователя
        await add_active_key(callback.from_user.id, new_key, device, old_expiry_time, device)
        
        # 4. Обновляем счетчик на новом сервере
        clients_count = await get_server_count_by_address(
            server_address_base, inbound_id, 
            protocol="shadowsocks" if protocol == 'ss' else "vless"
        )
        await update_server_clients_count(server_address_base, clients_count + 1, inbound_id)
        
        # 5. Отправка сообщения пользователю о новом ключе
        kb = InlineKeyboardBuilder()
        kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
        kb.button(text="🔑 Мои ключи", callback_data="active_keys")
        kb.button(text="🔧 Настройки ключа", callback_data="key_settings")
        kb.adjust(1)
        
        await callback.message.edit_caption(
            caption="✅ <b>Вы успешно изменили страну!</b>\n\n"
            f"📱 Устройство: {device.upper()}\n"
            f"🌍 Новая страна: <b>{country}</b>\n"
            f"🔑 Новый ключ:\n<code>{new_key}</code>\n\n"
            "ℹ️ Используйте новый ключ для подключения.\n"
            "Старый ключ скоро будет деактивирован.",
            reply_markup=kb.as_markup()
        )
        
        logger.info(f"New key successfully created for user {callback.from_user.id}: {new_key}")
        
        # 6. Теперь пытаемся удалить старый ключ (асинхронно, не блокируя пользователя)
        try:
            # Получаем данные старого сервера
            old_server = await get_server_by_address(
                address, 
                protocol="shadowsocks" if protocol == 'ss' else "vless"
            )
            
            if old_server:
                old_api = AsyncApi(
                    f"http://{old_server['address']}",
                    old_server['username'],
                    old_server['password'],
                    use_tls_verify=False
                )
                await old_api.login()
                
                # Удаляем старого клиента
                if protocol == 'ss':
                    await old_api.client.delete(
                        inbound_id=old_server['inbound_id'], 
                        client_uuid=f"{parts[0]}_{parts[1]}_{parts[2]}"
                    )
                else:
                    await old_api.client.delete(
                        inbound_id=old_server['inbound_id'], 
                        client_uuid=str(unique_uuid)
                    )
                
                # Обновляем счетчик на старом сервере
                old_clients_count = await get_server_count_by_address(
                    address, 
                    old_server['inbound_id'], 
                    protocol="shadowsocks" if protocol == 'ss' else "vless"
                )
                await update_server_clients_count(
                    address, 
                    old_clients_count - 1, 
                    old_server['inbound_id']
                )
            
            # Удаляем старый ключ из БД пользователя
            await remove_key_bd(key)
            logger.info(f"Old key successfully removed for user {callback.from_user.id}")
            
        except Exception as delete_error:
            # Если произошла ошибка при удалении старого ключа, логируем её,
            # но не прерываем процесс и не уведомляем пользователя
            logger.error(f"Error deleting old key: {delete_error}. Key might remain active.")
            await send_info_for_admins(
                f"[Замена страны] Не удалось удалить старый ключ для пользователя {callback.from_user.id}: {delete_error}",
                await get_admins(),
                bot,
                username=callback.from_user.username
            )
            
    except Exception as e:
        # Если произошла ошибка при создании нового ключа
        logger.error(f"Error creating new key: {str(e)}", exc_info=True)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Попробовать ещё раз", callback_data="change_key_country")
        kb.button(text="🔧 Настройки ключа", callback_data="key_settings")
        kb.button(text="◀️ В главное меню", callback_data="back_to_menu")
        kb.adjust(1)
        
        await callback.message.edit_caption(
            caption="❌ <b>Произошла ошибка при создании нового ключа</b>\n\n"
            f"{str(e)}\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=kb.as_markup()
        )
    
    finally:
        await state.clear()

@router.callback_query(F.data == "replace_key")
async def replace_key(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображает список доступных ключей пользователя для замены
    """
    user_keys = await get_user_keys(callback.from_user.id)
    
    if not user_keys:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="back_to_menu")
        await callback.message.edit_text(
            "❌ <b>У вас нет активных ключей</b>\n\n"
            "Чтобы начать пользоваться VPN, приобретите подписку.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        return
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопку для каждого ключа
    for key_data in user_keys:
        key = key_data[0]            # key
        device_id = key_data[1]      # device_id
        expiration_date = key_data[2] # expiration_date
        name = key_data[3]           # name
        
        # Определяем отображаемое имя для кнопки
        if name:
            display_name = f"«{name}»"  # Выделяем имя ключа кавычками
        else:
            # Если имя не задано, извлекаем email из ключа
            display_name = "Ключ"
            if "#Atlanta%20VPN-" in key:
                display_name = key.split("#Atlanta%20VPN-")[1]
        
        # Создаем сокращенный идентификатор ключа для callback_data
        key_id = hashlib.md5(key.encode()).hexdigest()[:10]
        kb.button(
            text=f"🔑 {device_id.upper()} - {display_name}", 
            callback_data=f"replace_key_{key_id}"
        )
        
        # Сохраняем ключ в состоянии для последующего доступа
        await state.update_data({f"key_{key_id}": key})
    
    kb.button(text="◀️ Назад", callback_data="active_keys")
    kb.adjust(1)
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/09.jpg"),
            caption="🔄 <b>Выберите ключ для замены</b>\n\n"
                    "Нажмите на ключ, который хотите заменить:"
        )
    ) 
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())    



@router.callback_query(F.data.startswith("replace_key_"))
async def select_key_for_replacement(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обрабатывает выбор ключа для замены
    """
    key_id = callback.data.split("_")[2]
    user_data = await state.get_data()
    key = user_data.get(f"key_{key_id}")
    
    if not key:
        await callback.answer("Ключ не найден. Попробуйте еще раз.")
        return
    
    # Вызываем функцию замены ключа
    await process_selected_key(callback.message, key, callback.from_user.id, state, bot)


async def process_selected_key(message, key, user_id, state, bot):
    """
    Обрабатывает выбранный ключ для замены
    """
    try:
        if not (key.startswith("vless://") or key.startswith("ss://")):
            kb = InlineKeyboardBuilder()
            kb.button(text="🔄 Попробовать ещё раз", callback_data="replace_key")
            kb.button(text="◀️ Мои ключи", callback_data="active_keys")
            kb.adjust(1)
            
            await message.edit_text(
                "❌ <b>Неверный формат ключа</b>\n\n"
                "Пожалуйста, выберите ключ снова.",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

        # Получаем базовые данные из ключа
        protocol = 'ss' if key.startswith('ss://') else 'vless'
        device, unique_id, unique_uuid, address, parts = extract_key_data(key)
        old_expiry_time = await get_key_expiry_date(key)

        # Генерируем случайный набор из 4 букв для уникальности email
        random_part = ''.join(random.choice(string.ascii_lowercase) for _ in range(4))
        unique_email = f"{parts[0]}_{random_part}_{parts[2]}"

        # 1. Создаем нового клиента на новом сервере
        new_api, server_address, pbk, sid, sni, port, utls, protocol, country, inbound_id = await get_api_instance(
            use_shadowsocks=(protocol == 'ss')
        ) 
        await send_info_for_admins(
            f"[Контроль Сервера, Функция: process_selected_key]\nНайденый сервер:\n{server_address},\n{pbk},\n{sid}\n{sni}... ",
            await get_admins(),
            bot,
            username=message.chat.username
        )        
        await new_api.login()
        
        # Создаем нового клиента с данными из ключа
        if protocol == 'vless':
            new_client = Client(
                id=unique_uuid, 
                email=unique_email, 
                enable=True, 
                expiry_time=old_expiry_time, 
                flow="xtls-rprx-vision"
            )  
        else:
            method = "chacha20-ietf-poly1305"
            password = generate_random_string(32)
            new_client = Client(
                id=generate_random_string(8),  # Генерируем новый ID для SS
                email=unique_email,
                password=password,
                method=method,
                enable=True,
                expiry_time=old_expiry_time
            )      
        
        # Добавляем нового клиента на новый сервер
        await new_api.client.add(inbound_id, [new_client])
        server_address = server_address.split(":")[0]
        
        # Генерируем новый ключ
        if protocol == 'ss':
            ss_config = f"{method}:{password}"
            encoded_config = base64.urlsafe_b64encode(ss_config.encode()).decode().rstrip('=')
            new_key = f"ss://{encoded_config}@{server_address}:{port}?type=tcp#Atlanta%20VPN-{new_client.email}"
        else:
            new_key = (
                f"vless://{unique_uuid}@{server_address}:{port}"
                "?type=tcp&security=reality"
                f"&pbk={pbk}"
                f"&fp={utls}&sni={sni}&sid={sid}&spx=%2F"
                f"&flow=xtls-rprx-vision#Atlanta%20VPN-{new_client.email}"
            )
            
        # Проверяем, что новый клиент успешно создан
        new_client_check = await new_api.client.get_by_email(new_client.email)
        if not new_client_check:
            raise Exception("Не удалось создать нового клиента на сервере")
            
        # 2. Добавляем новый ключ в БД
        await add_active_key(user_id, new_key, device, old_expiry_time, device)
        
        # 3. Обновляем счетчик на новом сервере
        clients_count = await get_server_count_by_address(
            server_address, inbound_id, 
            protocol="shadowsocks" if protocol == 'ss' else "vless"
        ) 
        await update_server_clients_count(server_address, clients_count + 1, inbound_id)
        
        # 4. Уведомляем пользователя об успешной замене
        kb = InlineKeyboardBuilder()
        kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
        kb.button(text="🔑 Мои ключи", callback_data="active_keys")
        kb.adjust(1)
        await message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile("handlers/images/10.jpg"),
                caption="✅ <b>Ключ успешно заменён!</b>\n\n"
                        f"📱 Устройство: {device.upper()}\n"
                        f"🔑 Новый ключ:\n<code>{new_key}</code>\n\n"
                        "ℹ️ Используйте новый ключ для подключения.\n"
                        "Старый ключ будет деактивирован."
            )
        ) 
        await message.edit_reply_markup(reply_markup=kb.as_markup())

        # 5. Пытаемся удалить старый ключ
        try:
            # Получаем данные старого сервера
            old_server = await get_server_by_address(address, protocol="shadowsocks" if protocol == 'ss' else "vless")
            if old_server:
                old_api = AsyncApi(
                    f"http://{old_server['address']}",
                    old_server['username'],
                    old_server['password'],
                    use_tls_verify=False
                )
                await old_api.login()
                
                # Пытаемся удалить старого клиента
                if protocol == 'ss':
                    await old_api.client.delete(inbound_id=old_server['inbound_id'], client_uuid=f"{parts[0]}_{parts[1]}_{parts[2]}")
                else:
                    await old_api.client.delete(inbound_id=old_server['inbound_id'], client_uuid=str(unique_uuid))
                
                # Обновляем счетчик на старом сервере
                old_clients_count = await get_server_count_by_address(
                    address, old_server['inbound_id'], 
                    protocol="shadowsocks" if protocol == 'ss' else "vless"
                ) 
                await update_server_clients_count(address, old_clients_count - 1, old_server['inbound_id'])
            
            # Удаляем старый ключ из БД в любом случае
            await remove_key_bd(key)
            logger.info(f"Старый ключ успешно удален для пользователя {user_id}")
            
        except Exception as delete_error:
            logger.error(f"Ошибка при удалении старого ключа: {delete_error}. Ключ останется активным в базе пользователя.")
            await send_info_for_admins(
                f"[Замена ключа] Не удалось удалить старый ключ для пользователя {user_id}: {delete_error}",
                await get_admins(),
                bot,
                username=message.chat.username
            )

        await state.clear()
        await send_info_for_admins(
            f"[Контроль ПРОТОКОЛА, Функция: process_selected_key]\nсервер: {server_address},\nюзер: {new_client.email},\nновый протокол: {protocol}",
            await get_admins(),
            bot,
            username=message.chat.username
        )
        
    except Exception as e:
        logger.error(f"Error replacing key: {str(e)}", exc_info=True)
        await send_info_for_admins(
            f"[Замена ключа] Ошибка при замене ключа для пользователя {user_id}: {e}", 
            await get_admins(), 
            bot,
            username=message.chat.username
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Попробовать ещё раз", callback_data="replace_key")
        kb.button(text="◀️ Мои ключи", callback_data="active_keys")
        kb.adjust(1)
        
        await message.edit_text(
            "❌ <b>Произошла ошибка при замене ключа</b>\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    

@router.callback_query(F.data.startswith("show_keys_"))
async def show_device_keys(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображает список ключей для выбранного устройства в виде кнопок
    """
    original_device = callback.data.split("_")[2].lower()
    
    # Упорядоченный словарь замен (от длинных к коротким)
    device_mapping = [
        ("androidtv", "androidtv"),
        ("andtv", "androidtv"),
        ("android", "android"),
        ("and", "android"),
        ("windows", "windows"),
        ("win", "windows"),
        ("ios", "ios"),
        ("mac", "mac")
    ]
    
    # Нормализация названия устройства
    device = next(
        (v for k, v in device_mapping if original_device.startswith(k)),
        original_device
    )
    
    # Получение всех возможных алиасов для устройства
    device_aliases = {k for k, v in device_mapping if v == device}
    device_aliases.add(device)  # Добавляем основное название
    
    user_keys = await get_user_keys(callback.from_user.id)
    
    # Фильтрация ключей с учетом всех алиасов
    device_keys = [
        key for key in user_keys 
        if key[1].lower() in device_aliases
    ]
    
    if not device_keys:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад к категориям", callback_data="active_keys")
        await callback.message.edit_text(
            text="❌ <b>У вас нет активных ключей для этого устройства</b>\n\n"
            "Чтобы получить ключ, приобретите подписку.",
            reply_markup=kb.as_markup()
        )
        return
    
    devices = {
        "ios": "📱 iOS",
        "android": "🤖 Android",
        "androidtv": "📺 Android TV",
        "windows": "🖥 Windows",
        "mac": "🍎 macOS"
    }
    
    # Безопасное получение названия устройства
    display_name = devices.get(device, f"❌ {device.capitalize()}")
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопки для каждого ключа
    for idx, key_data in enumerate(device_keys, 1):
        key = key_data[0]
        expiry_date = datetime.fromtimestamp(int(key_data[2])/1000).strftime('%d.%m.%Y')
        name = key_data[3]  # Имя ключа если доступно
        
        # Определяем отображаемое имя для кнопки
        if name:
            display_name = f"«{name}»"  # Выделяем имя ключа кавычками
        else:
            # Если имя не задано, извлекаем email из ключа
            display_name = "Ключ"
            if "#Atlanta%20VPN-" in key:
                display_name = key.split("#Atlanta%20VPN-")[1]
        
        # Создаем сокращенный идентификатор ключа для callback_data
        key_id = hashlib.md5(key.encode()).hexdigest()[:10]
        kb.button(
            text=f"🔑 {display_name} (до {expiry_date})", 
            callback_data=f"view_key_{key_id}"
        )
        
        # Сохраняем ключ в состоянии для последующего доступа
        await state.update_data({f"view_key_{key_id}": key_data})
    
    # Добавляем навигационные кнопки
    kb.button(text="🔑 Настроить ключ", callback_data="key_settings")
    kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
    kb.button(text="◀️ Назад к категориям", callback_data="active_keys")
    kb.adjust(1)  # Каждая кнопка на новой строке
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/10.jpg"),
            caption=f"🔑 <b>Ключи для {display_name}</b>\n\n"
            "Выберите ключ для просмотра подробной информации:",
            reply_markup=kb.as_markup()
        )
    )
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("view_key_"))
async def show_key_details(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображает детальную информацию о выбранном ключе
    """
    key_id = callback.data.split("_")[2]
    user_data = await state.get_data()
    key_data = user_data.get(f"view_key_{key_id}")
    
    if not key_data:
        await callback.answer("Ключ не найден. Попробуйте еще раз.")
        return
    
    key = key_data[0]
    device = key_data[1]
    expiry_timestamp = key_data[2]
    
    if device == "and":
        device = "android"
    elif device == "andtv":
        device = "androidtv"
    elif device == "win":
        device = "windows"
    elif device == "mac":
        device = "mac"
        
    
    # Извлекаем данные о ключе
    expiry_date = datetime.fromtimestamp(int(expiry_timestamp)/1000).strftime('%d.%m.%Y %H:%M')
    _, _, _, address, _ = extract_key_data(key)
    server = await get_server_by_address(address)
    country = server.get('country', 'Неизвестно') if server else 'Неизвестно'
    masked_key = key
    
    # Извлекаем email из ключа
    email = "Неизвестно"
    if "#Atlanta%20VPN-" in key:
        email = key.split("#Atlanta%20VPN-")[1]
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
    kb.button(text="💳 Изменить метод оплаты", callback_data="change_payment_method")
    kb.button(text="◀️ Назад к списку", callback_data=f"show_keys_{device}")
    kb.adjust(1)
    
    protocol = "vless" if "vless://" in key else "Shadowsocks"
    payment_method_id = await get_payment_id_for_key(masked_key)
    payment_method = await get_payment_method_by_id(payment_method_id)
    payment_method_title = payment_method.get("title") if payment_method else 'Не привязан'
    key_details = (
        f"🔑 <b>Информация о ключе</b>\n\n"
        f"📱 Устройство: {device.upper()}\n"
        f"📝 Протокол: {protocol}\n"
        f"📧 ID: {email}\n"
        f"📅 Действителен до: {expiry_date}\n"
        f"🌍 Страна: {country}\n"
        f"💳 Метод оплаты: {payment_method_title}\n"
        f"🆔 Ключ(Кликни на него, чтобы скопировать):\n\n<code>{masked_key}</code>\n\n"
        f"📜 <a href='{CHANNEL_LINK}/31'>Инструкция по подключению</a>"
    )
    
    await callback.message.edit_caption(
        caption=key_details,
        reply_markup=kb.as_markup()
    )
    await state.update_data(payment_method_id=payment_method_id, key=key, key_id=key_id)
    await callback.answer()

@router.callback_query(F.data == "change_payment_method")
async def change_payment_method(callback: types.CallbackQuery, state: FSMContext):
    """
    Обрабатывает запрос на изменение метода оплаты
    """
    user_data = await state.get_data()
    payment_method_id = user_data.get("payment_method_id")
    key = user_data.get("key")
    key_id = user_data.get("key_id")
    # Получаем все методы оплаты пользователя
    payment_methods = await get_user_payment_methods(callback.from_user.id)
    
    kb = InlineKeyboardBuilder()
    
    if payment_methods:
        for method in payment_methods:
            # Форматируем дату создания
            created_date = datetime.fromisoformat(method['created_at']).strftime("%d.%m.%Y")
            
            # Отмечаем текущий метод оплаты
            prefix = "✅ " if method['id'] == payment_method_id else ""
            
            # Создаем кнопку для каждого метода оплаты
            kb.button(
                text=f"{prefix}💳 {method['issuer_name']} от {created_date}",
                callback_data=f"set_payment_method_{method['id']}"
            )
    else:
        kb.button(text="💳 Добавьте метод оплаты", callback_data="add_balance")
    
    # Добавляем кнопку для отключения автопродления
    kb.button(text="❌ Отключить подписку", callback_data="disable_subscription")
    kb.button(text="◀️ Назад", callback_data=f"view_key_{key_id}")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption="💳 <b>Выберите метод оплаты</b>\n\n"
               "Выбранный метод будет использоваться для автоматического продления подписки для этого ключа.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "disable_subscription")
async def disable_subscription(callback: types.CallbackQuery, state: FSMContext):
    """
    Обрабатывает запрос на отключение автопродления
    """
    user_data = await state.get_data()
    key = user_data.get("key")
    key_id = user_data.get("key_id")
    await set_payment_id_for_key(key, None)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data=f"view_key_{key_id}")
    await callback.message.edit_caption(caption="✅ Автопродление подписки успешно отключено", reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("set_payment_method_"))
async def set_payment_method(callback: types.CallbackQuery, state: FSMContext):
    """
    Устанавливает выбранный метод оплаты для ключа
    """
    user_data = await state.get_data()
    key = user_data.get("key")
    key_id = user_data.get("key_id")
    # Получаем ID метода оплаты из callback_data
    payment_id = int(callback.data.split("_")[3])
    
    # Привязываем метод оплаты к ключу
    await set_payment_id_for_key(key, payment_id)
    
    # Обновляем данные состояния
    await state.update_data(payment_method_id=payment_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data=f"view_key_{key_id}")
    await callback.message.edit_caption(caption="✅ Метод оплаты успешно изменен", reply_markup=kb.as_markup())
    


@router.callback_query(F.data == "add_balance")
async def request_amount(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Запрашивает у пользователя сумму пополнения
    """
    # Проверяем наличие email у пользователя
    user_email = await get_user_email(callback.from_user.id)
    
    if not user_email:
        kb = InlineKeyboardBuilder()
        kb.button(text="✏️ Добавить email", callback_data="change_email")
        kb.button(text="◀️ Назад", callback_data="profile")
        kb.adjust(1)
        
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile("handlers/images/13.jpg"),
                caption="❗ <b>Для пополнения баланса требуется email</b>\n\n"
                    "У вас не указан email адрес в профиле.\n"
                    "Пожалуйста, добавьте email, чтобы продолжить."
            )
        )
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
        await callback.answer()
        return
        
    # Продолжаем стандартный процесс, если email есть
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="profile")
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/13.jpg"),
            caption="💰 <b>Введите сумму пополнения</b>\n\n"
                "▫️ Минимальная сумма: 2₽\n"
                "▫️ Максимальная сумма: 15000₽\n\n"
                "📝 Просто введите сумму цифрами\n"
                "Пример: <code>100</code>"
        )
    ) 

    # Сохраняем email пользователя в состоянии
    await state.update_data(user_email=user_email)
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
    await state.set_state(BalanceForm.waiting_for_amount)
    await callback.answer()

@router.message(BalanceForm.waiting_for_amount)
async def process_amount(message: Message, state: FSMContext, bot: Bot):
    """
    Обрабатывает введенную пользователем сумму
    """
    try:
        amount = int(message.text.strip())
        
        # Получаем email пользователя из состояния
        user_data = await state.get_data()
        email = user_data.get("user_email")
        
        if amount < 2:
            await message.answer(
                "❌ Минимальная сумма пополнения: 2₽\n"
                "Попробуйте ещё раз",
                parse_mode="HTML"
            )
            return
        
        if amount > 15000:
            await message.answer(
                "❌ Максимальная сумма пополнения: 15000₽\n"
                "Попробуйте ещё раз",
                parse_mode="HTML"
            )
            return
        
        try:
            url, label = await create_payment(amount, "Пополнение баланса", email)
        except Exception as e:
            await send_info_for_admins(f"[Пополнение баланса] Ошибка при создании платежа: {e}", await get_admins(), bot, username=message.from_user.username)
            await message.answer("❌ Произошла ошибка при создании платежа. Попробуйте позже или обратитесь в поддержку.")
            return
        
        kb = InlineKeyboardBuilder()
        kb.button(text="💳 Оплатить", url=url)
        kb.button(text="🔄 Проверить оплату", callback_data="check_payment")
        kb.button(text="📊 История транзакций", callback_data="transactions")
        kb.button(text="💭 Поддержка", callback_data="support")
        kb.button(text="◀️ Отмена", callback_data="profile")
        kb.adjust(1, 1, 1, 2)
        
        await message.answer_photo(
            photo=types.FSInputFile("handlers/images/13.jpg"),
            caption=(
                f"💳 <b>Пополнение баланса</b>\n\n"
                f"└ Сумма: {amount:,}₽\n"
                f"└ Email: {email}\n"
                f"<i>После оплаты нажмите кнопку «Проверить оплату»</i>\n\n"
                f"<b>Внимание!</b> Платеж будет проверен автоматически через 3 минуты\n\n"
                f"💡 <b>Важно:</b> Если вы случайно закрыли это окно,\n"
                f"вы всегда можете проверить статус оплаты\n"
                f"в разделе «📊 История транзакций» в вашем профиле.\n\n"
                f"❓ Если у вас возникли вопросы, обратитесь в нашу службу поддержки."
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )

        await state.update_data(
            request_link=url,
            request_label=label,
            amount=amount,
            email=email,
            action="add_balance"
        )
        await add_transaction(user_id=message.from_user.id, amount=amount, transaction_id=label, status="pending")
        
        # Планируем отложенную проверку платежа через 3 минуты
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            delayed_payment_check, 
            'date', 
            run_date=datetime.now() + timedelta(minutes=3),
            args=[bot, message.from_user.id, label, amount, "add_balance"]
        )
        scheduler.start()
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат суммы.\n"
            "Введите целое число.\n"
            "Пример: <code>100</code>",
            parse_mode="HTML"
        )

@router.callback_query(F.data == "extend_subscription")
async def extend_subscription(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обработчик выбора продления подписки
    """
    try:
        channel_id = CHANNEL
        member = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            # Устанавливаем состояние ожидания подписки
            await state.set_state(SubscriptionStates.waiting_for_subscription)
            
            await callback.message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку».\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await callback.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
    
    data = await state.get_data()
    if data.get("key_to_connect"):
        user_id = data.get("user_id") 
        expiration_date = data.get("expiration_date")
        device, unique_id, uniquie_uuid, address, parts = extract_key_data(data.get("key_to_connect"))
        
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(text="💳 30 дней - 99₽", callback_data=f"sub_{device}_30_99"))
        kb.add(InlineKeyboardButton(text="💳 3 месяца - 249₽", callback_data=f"sub_{device}_90_249"))
        kb.add(InlineKeyboardButton(text="💳 6 месяцев - 449₽", callback_data=f"sub_{device}_180_449"))
        kb.add(InlineKeyboardButton(text="💳 12 месяцев - 849₽", callback_data=f"sub_{device}_360_849"))
        kb.adjust(1, 1, 1, 1, 1)
        connection_text = (
            f"⚠️ <b>Ваша подписка скоро закончится!</b>\n\n"
            f"📅 Дата окончания: {expiration_date}\n"
            f"🔑 Ключ: <code>{data.get('key_to_connect')}</code>\n\n"
            f"🌐 IP адрес сервера: <code>{address}</code>\n\n"
            "Чтобы продолжить, <b>продлите подписку:</b>"
        )
        await callback.message.answer_photo(
            photo=types.FSInputFile("handlers/images/08.jpg"),
            caption=connection_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await state.update_data(
            device=device, 
            unique_id=unique_id, 
            uniquie_uuid=uniquie_uuid, 
            user_id=user_id, 
            expiration_date=expiration_date, 
            address=address, 
            key_to_connect=data.get("key_to_connect"), 
            user_name=parts[2]
        )
    else:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔑 Мои ключи", callback_data="active_keys")
        kb.button(text="◀️ Отмена", callback_data="back_to_menu")
        kb.adjust(1)
        
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile("handlers/images/13banner.png"),
                caption="🔑 <b>Отправьте ключ, который хотите продлить:</b>\n\n"
                        "Перейдите по кнопке 'Мои ключи', скопируйте и отправьте ключ.\n\n"
                        "Ключ <b>должен</b> начинаться с <code>vless://</code> или <code>ss://</code>\n\n"
                        "<i>Для отмены нажмите кнопку ниже</i>",
            )
        )
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
        await state.set_state(SubscriptionStates.waiting_for_key)


@router.message(SubscriptionStates.waiting_for_key)
async def process_key_for_extension(message: Message, state: FSMContext):
    """
    Обработка отправленного ключа для продления
    """
    key = message.text.strip()
    
    if not (key.startswith("vless://") or key.startswith("ss://")):
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(
            text="◀️ Отмена", 
            callback_data="back_to_menu"
        ))
        
        await message.answer(
            "❌ <b>Неверный формат ключа</b>\n\n"
            "Ключ должен начинаться с <code>vless://</code>\n"
            "Попробуйте еще раз или нажмите кнопку отмены",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        return

    device, unique_id, uniquie_uuid, address, parts = extract_key_data(key)
    if not all([device, unique_id, uniquie_uuid, address]):
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(
            text="◀️ Отмена", 
            callback_data="back_to_menu"
        ))
        
        await message.answer(
            "❌ <b>Не удалось распознать ключ</b>\n\n"
            "Пожалуйста, убедитесь, что вы отправили правильный ключ\n"
            "и попробуйте еще раз",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        return

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="💳 30 дней - 99₽", callback_data=f"sub_{device}_30_99"))
    kb.add(InlineKeyboardButton(text="💳 3 месяца - 249₽", callback_data=f"sub_{device}_90_249"))
    kb.add(InlineKeyboardButton(text="💳 6 месяцев - 449₽", callback_data=f"sub_{device}_180_449"))
    kb.add(InlineKeyboardButton(text="💳 12 месяцев - 849₽", callback_data=f"sub_{device}_360_849"))
    kb.adjust(1, 1, 1, 1, 1)
    connection_text = (
        "🔄 <b>Продление подписки</b>\n\n"
        f"🔑 Ключ: <code>{key}</code>\n"
        f"📱 Устройство: {device.upper()}\n"
        f"🌐 IP адрес сервера: <code>{address}</code>\n\n"
        "Выберите период продления:"
    )
    
    await message.answer_photo(
        photo=types.FSInputFile("handlers/images/08.jpg"),
        caption=connection_text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    
    await state.update_data(
        device=device, 
        unique_id=unique_id, 
        uniquie_uuid=uniquie_uuid,
        address=address, 
        key_to_connect=key,
        user_name=parts[2]
    )


@router.callback_query(F.data == "connection")
async def connection(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Отображает меню выбора устройства для подключения VPN
    
    args:
        callback: Callback query от кнопки
        state: Состояние FSM 
    """
    try:
        channel_id = CHANNEL
        member = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        is_subscribed = member.status not in ["left", "kicked", "banned"]
        
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.button(text="📢 Подписаться на канал", url=CHANNEL_LINK)
            kb.button(text="🔄 Проверить подписку", callback_data="check_subscription")
            kb.adjust(1)
            
            # Устанавливаем состояние ожидания подписки
            await state.set_state(SubscriptionStates.waiting_for_subscription)
            
            await callback.message.answer(
                "🔒 <b>Доступ ограничен</b>\n\n"
                "Для использования бота необходимо подписаться на наш канал:\n"
                f"• {channel_id}\n\n"
                "После подписки нажмите кнопку «Проверить подписку».\n\n"
                "Нажимая кнопку «Проверить подписку», вы автоматически подтверждаете то, что ознакомились и согласились с  пользовательским соглашением:\n"
                "https://telegra.ph/Polzovatelskoe-soglashenie-11-15-12\n\n",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            return

    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        await callback.answer("Произошла ошибка при проверке подписки. Попробуйте позже.")
        return
        
    kb = InlineKeyboardBuilder()
    kb.button(text="📱 iOS", callback_data="device_ios")
    kb.button(text="🤖 Android", callback_data="device_android")
    kb.button(text="📺 Android TV", callback_data="device_androidtv")
    kb.button(text="🖥 Windows", callback_data="device_windows")
    kb.button(text="🍎 macOS", callback_data="device_mac")
    kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
    kb.adjust(2, 2, 1, 1) 

    connection_text = (
        "🌐 <b>Выберите устройство для подключения:</b>\n\n"
        "Мы поддерживаем все основные платформы и операционные системы.\n"
        "Выберите ваше устройство для получения подробной инструкции."
    )

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/08.jpg"),
            caption=connection_text,
        )
    ) 
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())    
    await callback.answer()

@router.callback_query(F.data.startswith("device_"))
async def choose_subscription(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обработчик выбора устройства
    Отображает меню выбора типа подписки
    """
    device = callback.data.split("_")[1]
    try:
        user = await get_user(user_id=callback.from_user.id)
        free_keys_count = await get_free_keys_count(callback.from_user.id)
        if free_keys_count > 0:

            if await state.get_state() is not None:
                await callback.answer(
                    "Операция уже выполняется. Пожалуйста, подождите", 
                    show_alert=True
                )
                await update_free_keys_count(callback.from_user.id, 0)
                return
            await update_free_keys_count(callback.from_user.id, 0)

            logger.info(f"Starting free subscription creation for user {callback.from_user.id}")
            api, server_address, pbk, sid, sni, port, utls, protocol, country, inbound_id = await get_api_instance(use_shadowsocks=False) 
            await send_info_for_admins(
                f"[Контроль Сервера, Функция: choose_subscription]\nНайденый сервер:\n{server_address},\n{pbk},\n{sid}\n{sni}... ",
                await get_admins(),
                bot,
                username=callback.from_user.username
            )
            logger.info(f"Got API instance. Address: {server_address}")
            #clients_count = await get_server_count_by_address(address)
            #logger.info(f"Current clients count for address {address}: {clients_count}")
            login_result = await api.login()
            logger.info(f"API login result: {login_result}")
            free_days = await get_free_days(callback.from_user.id)

            current_time = datetime.now(timezone.utc).timestamp() * 1000
            expiry_time = int(current_time + int(free_days) * 86400000)
            logger.info(f"Expiry time set to: {datetime.fromtimestamp(expiry_time/1000)}")
            

            keys_count = await get_keys_count(callback.from_user.id)
            logger.info(f"Current keys count for user: {keys_count}")
            #await send_info_for_admins(f"[Бесплатная подписка] Количество ключей для пользователя: {keys_count}", await get_admins(), bot)

            client_id = str(uuid.uuid4())
            random_suffix = generate_random_string(4)
            logger.info(f"Generated client_id: {client_id}, suffix: {random_suffix}")
            #await send_info_for_admins(f"[Бесплатная подписка] Generated client_id: {client_id}, suffix: {random_suffix}", await get_admins(), bot)

            device_prefix = {
                "ios": "ios",
                "android": "and",
                "androidtv": "andtv",
                "windows": "win",
                "mac": "mac"
            }
            logger.info(f"Selected device: {device}")
            #await send_info_for_admins(f"[Бесплатная подписка] Выбранное устройство: {device}", await get_admins(), bot)

            if not user or 'username' not in user:
                await send_info_for_admins(f"[Бесплатная подписка] User data error. User object: {user}", await get_admins(), bot, username=callback.from_user.username)
                logger.error(f"User data error. User object: {user}")
                raise ValueError("User data is incomplete")

            email = f"{device_prefix.get(device, 'dev')}_{random_suffix}_{user['username'] or str(random.randint(100000, 999999))}"
            logger.info(f"Generated email: {email}")
            #await send_info_for_admins(f"[Бесплатная подписка] Generated email: {email}", await get_admins(), bot)
            
            new_client = Client(id=client_id, email=email, enable=True, expiry_time=expiry_time, flow="xtls-rprx-vision")
            logger.info(f"Created new client object: {new_client.__dict__}")
            
            add_result = await api.client.add(inbound_id, [new_client])
            logger.info(f"Add client result: {add_result}")
            #await send_info_for_admins(f"[Бесплатная подписка] Добавление в панель, результат: {add_result}", await get_admins(), bot)
            await api.login()
            
            client = await api.client.get_by_email(email)
            logger.info(f"Retrieved client by email: {client}")
            server_address = server_address.split(':', 1)[0]
            print("server addr " + server_address)
            if client:   
                vpn_link = (
                    f"vless://{client_id}@{server_address}:{port}"
                    "?type=tcp&security=reality"
                    f"&pbk={pbk}"
                    f"&fp={utls}&sni={sni}&sid={sid}&spx=%2F"
                    f"&flow=xtls-rprx-vision#Atlanta%20VPN-{client.email}"
                )
                logger.info("Generated VPN link for client")
                kb = InlineKeyboardBuilder()
                kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
                kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                kb.adjust(1)
                success_text = (
                    f"✅ Бесплатная подписка успешно активирована!\n\n"
                    f"📱 Устройство: {device.upper()}\n"
                    f"⏱ Срок действия: {free_days} дней\n\n"
                    f"📝 Данные для подключения:\n"
                    f"Уникальный ID: <code>{client.id}</code>\n"
                    f"Ссылка для подключения:\n<code>{vpn_link}</code>"
                )
                
                await callback.message.answer(success_text, parse_mode="HTML", reply_markup=kb.as_markup())
                await add_active_key(callback.from_user.id, vpn_link, device, client.expiry_time, device)
                await update_keys_count(callback.from_user.id, keys_count + 1)
                clients_count = await get_server_count_by_address(server_address, inbound_id, protocol="shadowsocks" if protocol == 'ss' else "vless") 
                await update_server_clients_count(server_address, clients_count + 1, inbound_id) 
                expiry_time = datetime.fromtimestamp(expiry_time/1000).strftime('%d.%m.%Y %H:%M')
                await update_subscription(callback.from_user.id, "Бесплатная", expiry_time)
                logger.info(f"Successfully completed subscription creation for user {callback.from_user.id}")
                await send_info_for_admins(f"[Бесплатная подписка] Успешное создание подписки для пользователя {user['username']}, user id: {callback.from_user.id}, device: {device}, days: {free_days}", await get_admins(), bot, username=user.get("username"))
                #await send_info_for_admins(f"[Бесплатная подписка] Информация о пользователе: {user}", await get_admins(), bot)
                await state.clear()
                await send_info_for_admins(
                    f"[Контроль ПРОТОКОЛА, Функция: choose_subscription.\nсервер: {server_address},\nюзер: {client.email},\nновый протокол: {protocol}]:\n{new_client}",
                    await get_admins(),
                    bot,
                    username=user.get("username")
                )
            else:
                await send_info_for_admins(f"[Бесплатная подписка] Клиент не найден после создания. Email: {email}", await get_admins(), bot, username=user.get("username"))
                logger.error(f"Client not found after creation. Email: {email}")
                await state.clear()
                raise ValueError("Client not found after creation")
        else:
            await state.update_data(device=device)

            kb = InlineKeyboardBuilder()
            kb.button(text="🚀 Быстрая настройка", callback_data=f"continue_sub_{device}")
            kb.button(text="⚙️ Расширенная настройка", callback_data=f"select_sub_protocol_{device}")
            kb.button(text="◀️ Назад к выбору устройства", callback_data="connection")
            kb.adjust(1)

            device_names = {
                "ios": "📱 iOS",
                "androidtv": "📺 Android TV",
                "android": "🤖 Android",
                "windows": "🖥 Windows",
                "mac": "🍎 macOS"
            }

            subscription_text = (
                f"🔥 <b>Настройка подписки</b>\n\n"
                f"📱 Устройство: {device_names.get(device, device.upper())}\n\n"
                f"<b>Выберите тип настройки:</b>\n\n"
                f"🚀 <b>Быстрая настройка</b>\n"
                f"• Автоматический выбор оптимального сервера\n"
                f"• Протокол VLESS для максимальной скорости\n"
                f"• Подходит для большинства пользователей\n\n"
                f"⚙️ <b>Расширенная настройка</b>\n"
                f"• Выбор протокола подключения\n"
                f"• Выбор страны подключения\n"
            )

            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=FSInputFile("handlers/images/03.jpg"),
                    caption=subscription_text,
                )
            ) 

            await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
            await callback.answer()

    except Exception as e:
        logger.error(f"Error creating free subscription: {str(e)}", exc_info=True)
        error_message = (
            f"❌ Произошла ошибка при создании бесплатной подписки:\n"
            f"Тип ошибки: {type(e).__name__}\n"
            f"Описание: {str(e)}\n"
            f"Пожалуйста, обратитесь в поддержку."
        )
        await callback.message.answer(error_message)

@router.callback_query(F.data.startswith("continue_sub_"))
async def continue_subscription(callback: types.CallbackQuery, state: FSMContext):
    """
    Автоматически устанавливает VLESS протокол и показывает тарифы
    """
    device = callback.data.split("_")[2]
    
    # Устанавливаем VLESS протокол автоматически
    await state.update_data(
        selected_protocol='vless'
    )
    
    # Показываем тарифы
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="💳 30 дней - 99₽", callback_data=f"sub_{device}_30_99"))
    kb.add(InlineKeyboardButton(text="💳 3 месяца - 249₽", callback_data=f"sub_{device}_90_249"))
    kb.add(InlineKeyboardButton(text="💳 6 месяцев - 449₽", callback_data=f"sub_{device}_180_449"))
    kb.add(InlineKeyboardButton(text="💳 12 месяцев - 849₽", callback_data=f"sub_{device}_360_849"))
    kb.add(InlineKeyboardButton(text="◀️ Назад к настройкам", callback_data=f"device_{device}"))
    kb.adjust(1)

    subscription_text = (
        f"🔥 <b>Выберите период подписки</b>\n\n"
        f"📱 Устройство: {device.upper()}\n"
        f"⚙️ Настройки:\n"
        f"• 📡 Протокол: VLESS\n"
        f"Выберите наиболее подходящий для вас тариф:\n"
        f"• Чем дольше период, тем выгоднее цена\n"
        f"• Все тарифы включают полный доступ\n"
        f"• Безлимитный трафик на максимальной скорости"
    )

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/03.jpg"),
            caption=subscription_text,
        )
    ) 
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("select_sub_protocol_"))
async def select_subscription_protocol(callback: types.CallbackQuery, state: FSMContext):
    """
    Показывает список доступных протоколов
    """
    device = callback.data.split("_")[3]
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📡 VLESS", callback_data=f"set_sub_protocol_{device}_vless")
    kb.button(text="◀️ Назад", callback_data=f"device_{device}")
    kb.adjust(1)
    
    protocol_message = (
        "🔒 <b>Выберите протокол подключения</b>\n\n"
        "<b>📡 VLESS</b> (Рекомендуется)\n"
        "• Новейший протокол с улучшенной безопасностью\n"
        "• Высокая скорость и стабильность соединения\n"
        "• Поддержка технологии REALITY для обхода блокировок\n"
        "• <b>РЕКОМЕНДУЕТСЯ</b> для большинства пользователей\n\n"
        "<b>🛡 Shadowsocks</b>\n"
        "💡 <i>Временно недоступен ввиду массовой блокировки провайдерами</i>\n"
    )
    
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile("handlers/images/03.jpg"),
            caption=protocol_message,
        )
    ) 
    await callback.message.edit_reply_markup(reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("set_sub_country_"))
async def set_subscription_country(callback: types.CallbackQuery, state: FSMContext):
    """
    Сохраняет выбранную страну и сразу показывает тарифы
    """
    _, _, _, device, country = callback.data.split("_")
    await state.update_data(selected_country=country)
    
    # Сразу показываем тарифы
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="💳 30 дней - 99₽", callback_data=f"sub_{device}_30_99"))
    kb.add(InlineKeyboardButton(text="💳 3 месяца - 249₽", callback_data=f"sub_{device}_90_249"))
    kb.add(InlineKeyboardButton(text="💳 6 месяцев - 449₽", callback_data=f"sub_{device}_180_449"))
    kb.add(InlineKeyboardButton(text="💳 12 месяцев - 849₽", callback_data=f"sub_{device}_360_849"))
    kb.add(InlineKeyboardButton(text="◀️ Назад к настройкам", callback_data=f"device_{device}"))
    kb.adjust(1)

    data = await state.get_data()
    selected_protocol = data.get('selected_protocol')
    
    settings_info = [f"🌍 Страна: {country}"]
    if selected_protocol:
        settings_info.append(f"📡 Протокол: {selected_protocol.upper()}")
    
    settings_text = "\n".join(settings_info)

    subscription_text = (
        f"🔥 <b>Выберите период подписки</b>\n\n"
        f"📱 Устройство: {device.upper()}\n"
        f"⚙️ Настройки:\n{settings_text}\n\n"
        f"Выберите наиболее подходящий для вас тариф:\n"
        f"• Чем дольше период, тем выгоднее цена\n"
        f"• Все тарифы включают полный доступ\n"
        f"• Безлимитный трафик на максимальной скорости"
    )

    await callback.message.edit_caption(
        caption=subscription_text,
        reply_markup=kb.as_markup(),
    )

@router.callback_query(F.data.startswith("set_sub_protocol_"))
async def set_subscription_protocol(callback: types.CallbackQuery, state: FSMContext):
    """
    Сохраняет выбранный протокол и показывает доступные страны
    """
    _, _, _, device, protocol = callback.data.split("_")
    await state.update_data(selected_protocol=protocol)
    
    # Get countries that support the selected protocol
    countries = await get_available_countries(protocol=protocol)
    
    kb = InlineKeyboardBuilder()
    for country in countries:
        kb.button(text=f"🌍 {country['name']}", callback_data=f"set_sub_country_{device}_{country['code']}")
    kb.button(text="◀️ Назад", callback_data=f"device_{device}")
    kb.adjust(1)
    
    await callback.message.edit_caption(
        caption=f"✅ Выбран протокол: {protocol.upper()}\n\n"
        "🌍 Выберите страну сервера:",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback_query: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Обработчик выбора типа подписки
    Запрашивает email перед созданием платежа только если его нет
    """
    # Сразу показываем сообщение о загрузке и убираем кнопки
    await callback_query.answer("Обработка запроса...")
    await callback_query.message.edit_caption(
        caption="⏳ <b>Подписка обрабатывается...</b>\n\n"
               "Пожалуйста, подождите.",
        parse_mode="HTML"
    )
    
    subscription_data = callback_query.data.split("_")
    await state.update_data(
        device=subscription_data[1],
        days=subscription_data[2],
        price=subscription_data[3]
    )
    
    # Проверяем наличие email
    user_email = await get_user_email(callback_query.from_user.id)
    
    if user_email:
        # Если email уже есть, переходим к обработке email
        await process_email(callback_query.message, state, bot, user_email, callback_query.from_user.id)
        return

    # Если email нет, запрашиваем его
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="connection")
    kb.adjust(1)

    await callback_query.message.edit_caption(
        caption="📧 Пожалуйста, введите ваш email адрес:\n\n"
        "❗️ Email необходим для:\n"
        "• Восстановления доступа\n"
        "• Технической поддержки\n"
        "• Важных уведомлений\n\n"
        "• Для отправки чека о покупке\n\n"
        "Пример: example@mail.com",
    ) 
    await callback_query.message.edit_reply_markup(reply_markup=kb.as_markup())
    await state.set_state(SubscriptionStates.waiting_for_email)

@router.message(SubscriptionStates.waiting_for_email)
async def process_email(message: Message, state: FSMContext, bot: Bot, existing_email: str = None, user_id: int = None):
    """
    Обработчик email
    Если email передан, использует его, иначе проверяет введенный пользователем
    """
    current_user_id = user_id if user_id else message.from_user.id
    if existing_email:
        UserEmail = existing_email
    else:
        UserEmail = message.text.lower().strip()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", UserEmail):
            kb = InlineKeyboardBuilder()
            kb.button(text="◀️ Отмена", callback_data="connection")
            await message.answer(
                "❌ Неверный формат email.\n"
                "Пожалуйста, введите корректный email адрес.\n"
                "Пример: example@mail.com",
                reply_markup=kb.as_markup()
            )
            return
        
        await save_or_update_email(message.from_user.id, UserEmail)

    data = await state.get_data()
    device = data.get('device')
    days = data.get('days')
    price = data.get('price')
    selected_country = data.get('selected_country')
    selected_protocol = data.get('selected_protocol')

    user = await get_user(user_id=current_user_id)
    balance = int(user['balance'])

    await send_info_for_admins(f"[Подписка] Обработка email для пользователя {current_user_id}", await get_admins(), bot, username=user.get("username"))

    user_id = data.get("user_id") 
    unique_id = data.get("unique_id") 
    unique_uuid = data.get("uniquie_uuid")
    address = data.get("address")
    user_name = data.get("user_name")
    key_to_connect = data.get("key_to_connect")

    print(data)
    if unique_id: 
        if balance < int(price):
            kb = InlineKeyboardBuilder()
            answer_message = (
                "❌ Недостаточно средств на балансе для продления подписки.\n\n"
                "💰 Пожалуйста, пополните баланс и повторите попытку."
            )
            kb.add(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="add_balance"))
            await message.answer(answer_message, reply_markup=kb.as_markup())
            return
        else:
            logger.info(f"Attempting to continue payment for user {current_user_id}")
            await send_info_for_admins(f"[Продление] Попытка продолжения оплаты для пользователя {current_user_id}", await get_admins(), bot, username=user.get("username"))
            logger.info(f"Key to connect: {key_to_connect}, unique_uuid: {unique_uuid}")
            try: 
                protocol = 'ss' if key_to_connect.startswith('ss://') else 'vless'

                server = await get_server_by_address(address)
                api = AsyncApi(
                            f"http://{server['address']}",
                            server['username'],
                            server['password'],
                            use_tls_verify=False
                )
                await send_info_for_admins(
                    f"[Контроль Сервера, Функция: process_email]\nНайденый сервер IP:\n{address}",
                    await get_admins(),
                    bot, 
                    username=user.get("username")
                )
                try:
                    await api.login()
                    email = f"{device}_{unique_id}_{user_name}"
                    print(f"Continue email: {email}")
                    client = await api.client.get_by_email(email)
                    
                    # Получаем оригинальную дату окончания из базы данных
                    original_expiry = await get_key_expiry_date(key_to_connect)
                    if original_expiry:
                        current_expiry = int(original_expiry)
                    else:
                        current_expiry = int(client.expiry_time)
                    
                    milliseconds_to_add = int(days) * 86400000
                    new_expiry_time = current_expiry + milliseconds_to_add
                    
                    print(f"Original expiry date: {datetime.fromtimestamp(current_expiry/1000).strftime('%d.%m.%Y')}")
                    print(f"Adding {days} days")
                    print(f"New expiry date: {datetime.fromtimestamp(new_expiry_time/1000).strftime('%d.%m.%Y')}")
                    
                    if protocol == 'vless':
                        client.expiry_time = new_expiry_time
                        client.flow="xtls-rprx-vision"
                        client.id = str(str(unique_uuid))
                        client.email = client.email
                        client.enable = client.enable
                        client.inbound_id=client.inbound_id
                        await api.client.update(
                            client_uuid=str(unique_uuid),
                            client=client
                        )
                    else:
                        client.expiry_time = new_expiry_time
                        client.inbound_id=client.inbound_id
                        await api.client.update(
                            client_uuid=str(email),
                            client=client
                        )
                        
                    await send_info_for_admins(f"[ПРОТОКОЛ ПРОДЛЕНИЯ]: {protocol}", await get_admins(), bot)
                    await api.login()
                    updated_client = await api.client.get_by_email(f"{device}_{unique_id}_{user_name}")
                    
                    # Проверяем обновленное значение
                    print(f"Updated client expiry_time: {updated_client.expiry_time}")
                    print(f"Updated date: {datetime.fromtimestamp(updated_client.expiry_time/1000).strftime('%d.%m.%Y')}")

                    success_text = (
                        f"✅ Подписка успешно продлена!\n\n"
                        f"📱 Устройство: {device}\n"
                        f"⏱ Срок действия: {days} дней\n\n"
                        f"🔄 Новая дата окончания: {datetime.fromtimestamp(updated_client.expiry_time/1000).strftime('%d.%m.%Y')}"
                    )

                    kb = InlineKeyboardBuilder()
                    kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                    kb.adjust(1, 1)

                    await message.answer_photo(
                        photo=types.FSInputFile("handlers/images/10.jpg"),
                        caption=success_text,
                        parse_mode="HTML",
                        reply_markup=kb.as_markup()
                    )

                    # Убеждаемся, что передаем корректное значение времени
                    await update_key_expiry_date(
                        key = key_to_connect, 
                        new_expiry_time=new_expiry_time
                    )
                    await update_balance(current_user_id, balance - int(price))
                    await send_info_for_admins(
                        f"[Контроль ПРОТОКОЛА, Функция: process_email.\nсервер: {address},\nюзер: {client.email},\nновый протокол: {protocol}]:\n{client}",
                        await get_admins(),
                        bot, 
                        username=user.get("username")
                    )
                except Exception as e:
                    logger.error(f"Error updating client: {str(e)}", exc_info=True)
                    error_message = (
                        "❌ Ошибка при продлении подписки.\n\n"
                        "Пожалуйста, обратитесь в поддержку и предоставьте следующую информацию:\n"
                        f"Error: {e}\n\n"
                        f"• Устройство: {device}\n"
                        f"• Дни: {days}\n"
                        f"• ID клиента: {client.id if client else 'Not found'}\n"
                        f"• Inbound ID: {client.inbound_id if client else 'Not found'}"
                    )
                    await message.answer(error_message)
                    print(f"Client details: {client}")
                    print(f"Unique UUID: {unique_uuid}")
                    await send_info_for_admins(f"[Продление] Unique UUID: {unique_uuid}", await get_admins(), bot, username=user.get("username"))
                return


            except Exception as e:
                logger.error(f"Error creating client: {str(e)}", exc_info=True)
                error_message = f"❌ Обратитесь в поддержку. Ошибка при продлении подписки: {str(e)}"
                await message.answer(error_message)
            return
    else:
        await send_info_for_admins(f"[Продление] Не найден уникальный ID для пользователя {current_user_id}", await get_admins(), bot, username=user.get("username"))
        logger.info(f"No unique_id found for user {current_user_id}")

    kb = InlineKeyboardBuilder()

    devices = {
        "ios": "📱 iOS",
        "android": "🤖 Android",
        "androidtv": "📺 Android TV",
        "windows": "🖥 Windows",
        "mac": "🍎 macOS"
    }

    if balance < int(price):
        kb = InlineKeyboardBuilder()
        answer_message = (
            "❌ Недостаточно средств на балансе для создания подписки.\n\n"
            "💰 Пожалуйста, пополните баланс и повторите попытку."
        )
        kb.add(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="add_balance"))
        await message.answer(answer_message, reply_markup=kb.as_markup())
        return
    else:
        logger.info(f"Attempting to create client for user {current_user_id} for {days} days")
        await send_info_for_admins(f"[Подключение подписки] Попытка создания клиента для пользователя {current_user_id} на {days} дней", await get_admins(), bot, username=user.get("username"))
        try:
            api, address, pbk, sid, sni, port, utls, protocol, country, inbound_id = await get_api_instance(
                country=selected_country,
                use_shadowsocks=(selected_protocol == 'ss') if selected_protocol else None
            )
            await send_info_for_admins(
                f"[Контроль Сервера, Функция: process_email]\nНайденый сервер:\n{address},\n{pbk},\n{sid}\n{sni}... ",
                await get_admins(),
                bot,
                username=user.get("username")
            )
            clients_count = await get_server_count_by_address(address, inbound_id, protocol="shadowsocks" if selected_protocol == 'ss' else "vless")
            current_time = datetime.now(timezone.utc).timestamp() * 1000
            expiry_time = int(current_time + (int(days) * 86400000))
            keys_count = await get_keys_count(current_user_id)

            try:
                client_id = str(uuid.uuid4())
                random_suffix = generate_random_string(4)
                device_prefix = {
                    "ios": "ios",
                    "android": "and", 
                    "androidtv": "andtv",
                    "windows": "win",
                    "mac": "mac"
                }
                
                username = user.get('username') or str(random.randint(100000, 999999))
                email = f"{device_prefix.get(device, 'dev')}_{random_suffix}_{username}"

                await api.login()

                if selected_protocol == 'vless':
                    new_client = Client(
                        id=client_id, 
                        email=email, 
                        enable=True, 
                        expiry_time=expiry_time, 
                        flow="xtls-rprx-vision"
                    ) 
                else:
                    method = "chacha20-ietf-poly1305"
                    password = generate_random_string(32)

                    new_client = Client(
                        id=client_id,
                        email=email,
                        password=password,
                        method=method,
                        enable=True,
                        expiry_time=expiry_time
                    )

                await api.client.add(inbound_id, [new_client])

            except Exception as e:
                print(e)

            server_address = address.split(':')[0]
            client = await api.client.get_by_email(email)

            if client:   
                if selected_protocol == 'vless':
                    vpn_link = (
                        f"vless://{client_id}@{server_address}:443"
                        "?type=tcp&security=reality"
                        f"&pbk={pbk}"
                        f"&fp={utls}&sni={sni}&sid={sid}&spx=%2F"
                        f"&flow=xtls-rprx-vision#Atlanta%20VPN-{client.email}"
                    )
                else:
                    # Генерация ссылки для Shadowsocks
                    ss_config = f"{method}:{password}"
                    encoded_config = base64.urlsafe_b64encode(ss_config.encode()).decode().rstrip('=')
                    vpn_link = f"ss://{encoded_config}@{server_address}:{port}?type=tcp#Atlanta%20VPN-{client.email}"
                
                # Добавляем информацию о протоколе в текст успеха
                protocol_info = "Shadowsocks" if selected_protocol == 'ss' else "VLESS"
                success_text = (
                    f"✅ Подписка успешно активирована!\n\n"
                    f"📱 Устройство: {devices.get(device, device.upper())}\n"
                    f"⏱ Срок действия: {days} дней\n"
                    f"📡 Протокол: {protocol_info}\n\n"
                    f"📝 Данные для подключения:\n"
                    f"Уникальный ID: <code>{client.id}</code>\n"
                    f"Ссылка для подключения:\n<code>{vpn_link}</code>\n\n\n"
                    "📜 <a href='https://t.me/AtlantaVPN/31'>Инструкция по подключению</a>"

                )

                kb = InlineKeyboardBuilder()
                kb.button(text="📖 Как подключить VPN", callback_data=f"guide_{device}")
                kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                
                await message.answer_photo(
                    photo=FSInputFile("handlers/images/10.jpg"),
                    caption=success_text,
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )

                await update_balance(current_user_id, int(user['balance']) - int(price))
                await add_active_key(current_user_id, vpn_link, device, client.expiry_time, device)
                await update_keys_count(current_user_id, keys_count + 1)
                await update_server_clients_count(address, clients_count + 1, inbound_id)
                expiry_time = datetime.fromtimestamp(expiry_time/1000).strftime('%d.%m.%Y %H:%M')
                await update_subscription(current_user_id, "Подписка куплена", expiry_time)
                await send_info_for_admins(
                    f"[Контроль ПРОТОКОЛА, Функция: process_email 2.\nсервер: {address},\nюзер: {client.email},\nновый протокол: {protocol}]:\n{client}",
                    await get_admins(),
                    bot,
                    username=user.get("username")
                )

                if selected_protocol == 'vless':    
                    try: 
                        await api.login()
                        client = await api.client.get_by_email(email)
                        updated_client = Client(
                            email=client.email,
                            enable=True,
                            id=client_id,
                            inbound_id=client.inbound_id,
                            expiry_time=int(client.expiry_time + (int(days) * 86400000)),
                            flow="xtls-rprx-vision"
                        )
                        await api.client.update(client_uuid=str(client_id), client=updated_client)
                        await api.login()
                        client = await api.client.get_by_email(email)
                        await send_info_for_admins(f"[Подключение подписки. Проверка Flow] Flow успешно добавлен {current_user_id}", await get_admins(), bot, username=user.get("username"))
                    except Exception as e:
                        await send_info_for_admins(f"[Подключение подписки. Проверка Flow] Ошибка при обновлении подписки: {str(e)}", await get_admins(), bot, username=user.get("username"))
                
            
        except Exception as e:
            await send_info_for_admins(f"[Подключение подписки ] Ошибка при создании подписки: {str(e)}", await get_admins(), bot, username=user.get("username"))
            error_message = f"❌ 1Ошибка при создании подписки: {str(e)}"
            logger.error(error_message)
            await message.answer(error_message)

async def delayed_payment_check(bot: Bot, user_id: int, payment_id: str, amount: int, action: str):
    """
    Функция для отложенной проверки платежа через 3 минуты
    """
    # Получаем информацию о пользователе
    user = await get_user(user_id=user_id)
    
    # Проверяем статус платежа
    payment_success, saved_payment_method_id, payment = await check_payment_status(payment_id, amount)
    
    # Если платеж не был обработан ранее (статус всё ещё "pending")
    transaction = await get_transaction_by_id(payment_id)
    if transaction and transaction['status'] == 'pending':
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Проверить вручную", callback_data="check_payment")
        kb.button(text="💭 Поддержка", callback_data="support")
        kb.adjust(1)
        
        if payment_success:
            # Обновляем баланс
            new_balance = int(user['balance']) + amount
            await update_transaction_status(transaction_id=payment_id, new_status="succeeded")
            await update_balance(user_id, new_balance)
            
            # Если платежный метод сохранён
            if saved_payment_method_id:
                # Уведомляем о сохранении метода оплаты
                await bot.send_message(
                    user_id,
                    "✅ <b>Платеж успешно завершен</b>\n\n"
                    f"💳 <b>Сумма:</b> {amount}₽\n"
                    "💳 <b>Метод оплаты сохранён</b>\n"
                    "💡 Вы можете присвоить ему название в разделе Методы оплаты",
                    parse_mode="HTML"
                )
                # Добавляем метод оплаты
                await add_payment_method(user_id, payment.payment_method.id, "Сохраненная карта")
            else:
                # Просто уведомляем об успешном платеже
                await bot.send_message(
                    user_id,
                    "✅ <b>Платеж успешно завершен</b>\n\n"
                    f"💳 <b>Сумма:</b> {amount}₽\n"
                    f"💰 <b>Новый баланс:</b> {new_balance}₽",
                    parse_mode="HTML"
                )
            
            # Обработка реферальной системы
            if user['referrer_id']:
                referrer = await get_user(user_id=user['referrer_id'])
                first_deposit = await get_is_first_payment_done(user_id)
                bonus_percentage = 0.5 if first_deposit else 0.3
                await update_balance(referrer['user_id'], int(referrer['balance']) + int(amount) * bonus_percentage)
                
                try:
                    ref_kb = InlineKeyboardBuilder()
                    ref_kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                    await bot.send_message(
                        user['referrer_id'],
                        f"🎉 <b>Поздравляем!</b>\n\n"
                        f"Ваш реферал пополнил баланс на сумму {amount}₽\n"
                        f"Вам начислен бонус: <b>{int(amount) * bonus_percentage}₽</b> ({bonus_percentage * 100}%)",
                        parse_mode="HTML",
                        reply_markup=ref_kb.as_markup()
                    )
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления рефереру: {e}")
                
                # Устанавливаем флаг, что первое пополнение выполнено
                await set_is_first_payment_done(user_id, True)
        else:
            # Уведомляем что платеж всё ещё не подтвержден
            await bot.send_message(
                user_id,
                "ℹ️ <b>Автоматическая проверка платежа</b>\n\n"
                "Ваш платеж еще не подтвержден. Если вы уже оплатили, нажмите кнопку проверки вручную или обратитесь в поддержку.",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )

@router.callback_query(F.data == "check_payment")
async def check_payment(callback_query: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    payment_id = data.get('request_label')
    payment_success, saved_payment_method_id, payment = await check_payment_status(payment_id, data.get('amount'))
    action = data.get('action')
    user = await get_user(user_id=callback_query.from_user.id)

    if action == "add_balance":
        await send_info_for_admins(f"[ЮKassa. Пополнение баланса] Попытка пополнения баланса для пользователя {callback_query.from_user.id}", await get_admins(), bot, username=user.get("username"))
        logger.info(f"Проверка платежа для пополнения баланса: {payment_id}")

        if payment_success:
            amount = int(data.get('amount', 0))
            print(amount)
            new_balance = int(user['balance']) + amount

            try:
                kb = InlineKeyboardBuilder()
                await update_transaction_status(transaction_id=payment_id, new_status="succeeded")
                await update_balance(callback_query.from_user.id, new_balance)
                if saved_payment_method_id:                    
                    await bot.send_message(user['user_id'], "✅ <b>Платеж успешно завершен</b>\n\n"
                                            f"💳<b>Сумма:</b> {amount}₽\n"
                                            "💳<b>Метод оплаты будет сохранён в вашем профиле</b>\n\nОтправьте желаемое название для этого метода оплаты",
                                            parse_mode="HTML",
                                            reply_markup=kb.as_markup()
                                            )
                    await state.update_data(saved_id=payment.payment_method.id)
                    await state.set_state(SubscriptionStates.waiting_for_payment_method_name)
                if user['referrer_id']:
                    referrer = await get_user(user_id=user['referrer_id'])
                    first_deposit = await get_is_first_payment_done(user['user_id'])
                    bonus_percentage = 0.5 if first_deposit else 0.3
                    await update_balance(referrer['user_id'], int(referrer['balance']) + int(amount) * bonus_percentage)

                    try:
                        kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
                        await bot.send_message(
                            user['referrer_id'],
                            f"🎉 <b>Поздравляем!</b>\n\n"
                            f"Ваш реферал пополнил баланс на сумму {amount}₽\n"
                            f"Вам начислен бонус: <b>{int(amount) * bonus_percentage}₽</b> ({bonus_percentage * 100}%)",
                            parse_mode="HTML",
                            reply_markup=kb.as_markup()
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления рефереру: {e}")
                        await send_info_for_admins(f"[ЮKassa. Пополнение баланса] Ошибка при отправке уведомления рефереру: {e}", await get_admins(), bot, username=user.get("username"))

                logger.info(f"Баланс успешно обновлен. Пользователь: {callback_query.from_user.id}, сумма: {amount}")
                await send_info_for_admins(f"[ЮKassa. Пополнение баланса] Баланс успешно обновлен. Пользователь: {callback_query.from_user.id}, сумма: {amount}", await get_admins(), bot, username=user.get("username"))

                # Устанавливаем флаг, что первое пополнение выполнено
                await set_is_first_payment_done(user['user_id'], True)

            except Exception as e:
                await send_info_for_admins(f"[ЮKassa. Пополнение баланса] Ошибка при обновлении баланса: {e}", await get_admins(), bot, username=user.get("username"))
                logger.error(f"Ошибка при обновлении баланса: {e}")
                await callback_query.message.answer(
                    text="❌ Произошла ошибка при обновлении баланса. Пожалуйста, обратитесь в поддержку.",
                    parse_mode="HTML"
                )
        else:
            kb = InlineKeyboardBuilder()
            kb.button(text="🔄 Проверить снова", callback_data="check_payment")
            kb.button(text="◀️ Отмена", callback_data="back_to_menu")
            kb.adjust(1)

            await callback_query.message.answer_photo(
                photo=types.FSInputFile("handlers/images/14.jpg"),
                caption="Платеж не подтвержден. Пожалуйста, убедитесь, что вы завершили оплату, или попробуйте снова позже.",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )
            await send_info_for_admins(f"[ЮKassa. Пополнение баланса] Платеж не подтвержден. Пользователь: {callback_query.from_user.id}", await get_admins(), bot, username=user.get("username"))

    await callback_query.answer()

@router.message(SubscriptionStates.waiting_for_payment_method_name)
async def waiting_for_payment_method_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    saved_id = data.get('saved_id')
    try:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться в меню", callback_data="back_to_menu")
        await add_payment_method(message.from_user.id, saved_id, message.text, message.text)
        await message.answer(f"💳 Метод оплаты с названием <b>{message.text}</b> успешно сохранён!", reply_markup=kb.as_markup(), parse_mode="HTML")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка при сохранении метода оплаты: {e}", parse_mode="HTML")

@router.message(Command("admin"))
async def admin_menu(message: types.Message):
    """
    Административное меню
    """
    user = await get_user(message.from_user.id)
    
    if not user or not user.get('is_admin'):
        await message.answer("⛔️ У вас нет доступа к админ-панели")
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить сервер", callback_data="add_server")
    kb.button(text="➖ Удалить сервер", callback_data="remove_server")
    kb.button(text="➕ Добавить инбаунд", callback_data="add_inbound")
    kb.button(text="➖ Удалить инбаунд", callback_data="remove_inbound")
    kb.button(text="🔄 Изменить кол-во клиентов", callback_data="update_servers")
    kb.button(text="🔄 Изменить данные сервера", callback_data="update_server_info")
    kb.button(text="📊 Информация о серверах", callback_data="servers_info")
    kb.button(text="📊 Информация о каналах", callback_data="channels_info")
    kb.button(text="🔑 Промокоды", callback_data="promocodes_info")
    kb.button(text="🔑➖ Удалить ключ", callback_data="remove_key")
    kb.button(text="📢 Рассылка", callback_data="admin_broadcast")
    kb.button(text="💾 Экспортировать данные", callback_data="export_data")
    kb.adjust(2, 2, 1, 1, 1, 1, 1)
    
    stats = await get_system_statistics()
    
    await message.answer(
        "👨‍💻 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика системы:</b>\n"
        f"💰 Общая сумма продаж: {stats['total_sales']} ₽\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🔄 Успешных транзакций: {stats['total_transactions']}\n"
        f"🔑 Активных ключей: {stats['active_keys']}\n\n"
        "Выберите действие:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "channels_info")
async def channels_info(callback: types.CallbackQuery):
    """
    Отображает статистику по каналам привлечения пользователей
    """
    try:
        # Получаем статистику по каналам
        channels_stats = await get_channel_statistics()
        
        if not channels_stats:
            await callback.message.answer("📊 Статистика по каналам пока недоступна")
            return
            
        # Формируем сообщение
        total_users = sum(channel['users_count'] for channel in channels_stats)
        
        message_parts = ["📊 <b>Статистика по каналам:</b>\n"]
        message_parts.append(f"👥 Всего пользователей: {total_users}\n")
        
        # Добавляем информацию по каждому каналу
        for i, channel in enumerate(channels_stats, 1):
            channel_name = channel['channel']
            users_count = channel['users_count']
            percentage = channel['percentage']
            
            # Добавляем эмодзи в зависимости от места в рейтинге
            prefix = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "📌"
            
            # Форматируем строку для канала
            channel_line = (
                f"{prefix} {channel_name}\n"
                f"└ {users_count} польз. ({percentage}%)"
            )
            message_parts.append(channel_line)
        
        # Объединяем все части сообщения
        full_message = "\n\n".join(message_parts)
        
        # Отправляем сообщение
        await callback.message.answer(
            full_message,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении статистики каналов: {e}")
        await callback.message.answer(
            "❌ Произошла ошибка при получении статистики каналов"
        )
    finally:
        await callback.answer()



@router.callback_query(F.data == "add_inbound")
async def add_inbound_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса добавления инбаунда - выбор сервера
    """
    servers = await get_all_servers()
    
    kb = InlineKeyboardBuilder()
    for server in servers:
        kb.button(
            text=f"🖥 {server['address']}", 
            callback_data=f"add_inbound_server_{server['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="admin")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "Выберите сервер для добавления инбаунда:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("add_inbound_server_"))
async def add_inbound_protocol(callback: types.CallbackQuery, state: FSMContext):
    """
    Выбор протокола для нового инбаунда
    """
    server_id = callback.data.split("_")[3]
    await state.update_data(server_id=server_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="VLESS", callback_data="add_inbound_protocol_vless")
    kb.button(text="Shadowsocks", callback_data="add_inbound_protocol_shadowsocks")
    kb.button(text="◀️ Отмена", callback_data="add_inbound")
    kb.adjust(2, 1)
    
    await callback.message.edit_text(
        "Выберите протокол для нового инбаунда:",
        reply_markup=kb.as_markup()
    )        

@router.callback_query(F.data.startswith("add_inbound_protocol_"))
async def add_inbound_id(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос ID для нового инбаунда
    """
    protocol = callback.data.split("_")[3]
    await state.update_data(protocol=protocol)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="add_inbound")
    
    await callback.message.edit_text(
        "Введите ID для нового инбаунда:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_inbound_id)

@router.message(AdminStates.waiting_for_inbound_id)
async def process_inbound_id(message: Message, state: FSMContext):
    """
    Обработка введенного ID и запрос дополнительных параметров
    """
    try:
        inbound_id = int(message.text)
        if inbound_id <= 0:
            await message.answer("❌ ID должен быть положительным числом")
            return
        
        await state.update_data(inbound_id=inbound_id)
        data = await state.get_data()
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Отмена", callback_data="add_inbound")
        
        if data['protocol'] == 'vless':
            await message.answer(
                "Введите PBK для VLESS:",
                reply_markup=kb.as_markup()
            )
            await state.set_state(AdminStates.waiting_for_pbk)
        else:
            await message.answer(
                "Введите SNI:",
                reply_markup=kb.as_markup()
            )
            await state.set_state(AdminStates.waiting_for_sni)
            
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")

@router.message(AdminStates.waiting_for_pbk)
async def process_pbk(message: Message, state: FSMContext):
    """
    Обработка введенного PBK и запрос SID для VLESS
    """
    pbk = message.text.strip()
    await state.update_data(pbk=pbk)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="add_inbound")
    
    await message.answer(
        "Введите SID для VLESS:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_sid)

@router.message(AdminStates.waiting_for_sid)
async def process_sid(message: Message, state: FSMContext):
    """
    Обработка введенного SID и запрос SNI
    """
    sid = message.text.strip()
    await state.update_data(sid=sid)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="add_inbound")
    
    await message.answer(
        "Введите SNI:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_sni)

@router.message(AdminStates.waiting_for_sni)
async def process_sni(message: Message, state: FSMContext):
    """
    Завершение процесса добавления инбаунда
    """
    sni = message.text.strip()
    data = await state.get_data()
    
    try:
        server = await get_server_by_id(int(data['server_id']))
        
        # Создаем новый инбаунд
        new_inbound = {
            'server_address': server['address'],
            'protocol': data['protocol'],
            'inbound_id': data['inbound_id'],
            'sni': sni,
            'pbk': data.get('pbk'),  # Будет None для shadowsocks
            'sid': data.get('sid'),  # Будет None для shadowsocks
            'max_clients': 100
        }

        await add_inbound(new_inbound)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться в админ-панель", callback_data="admin")
        
        success_message = (
            f"✅ Инбаунд успешно добавлен!\n\n"
            f"🖥 Сервер: {server['address']}\n"
            f"📡 Протокол: {data['protocol']}\n"
            f"🔢 ID: {data['inbound_id']}\n"
            f"🌐 SNI: {sni}"
        )
        
        if data['protocol'] == 'vless':
            success_message += f"\n🔑 PBK: {data['pbk']}\n🔑 SID: {data['sid']}"
        
        await message.answer(
            success_message,
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении инбаунда: {str(e)}")

@router.callback_query(F.data == "remove_inbound")
async def remove_inbound_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса удаления инбаунда - выбор сервера
    """
    servers = await get_all_servers()
    
    kb = InlineKeyboardBuilder()
    for server in servers:
        kb.button(
            text=f"🖥 {server['address']}", 
            callback_data=f"remove_inbound_server_{server['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="admin")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "Выберите сервер для удаления инбаунда:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("remove_inbound_server_"))
async def select_inbound_for_removal(callback: types.CallbackQuery, state: FSMContext):
    """
    Выбор инбаунда для удаления
    """
    server_id = callback.data.split("_")[3]
    server = await get_server_by_id(int(server_id))
    
    inbounds = await get_server_inbounds(server['address'])
    
    if not inbounds:
        await callback.answer("❌ У этого сервера нет инбаундов", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} (ID: {inbound['inbound_id']})", 
            callback_data=f"confirm_remove_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="remove_inbound")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "Выберите инбаунд для удаления:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("confirm_remove_inbound_"))
async def confirm_inbound_removal(callback: types.CallbackQuery):
    """
    Подтверждение удаления инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    
    try:
        await remove_inbound(inbound_id)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться в админ-панель", callback_data="admin")
        
        await callback.message.edit_text(
            "✅ Инбаунд успешно удален!",
            reply_markup=kb.as_markup()
        )
        
    except Exception as e:
        await callback.answer(f"❌ Ошибка при удалении инбаунда: {str(e)}", show_alert=True)



@router.callback_query(F.data == "remove_key")
async def remove_key_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса удаления ключа - запрос ID/username пользователя
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")

    await callback.message.edit_text(
        "👤 Введите ID или username пользователя:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminKeyRemovalStates.waiting_for_user)

def determine_device_type(device_id: str) -> str:
    """
    Определяет тип устройства по device_id
    """
    device_id = device_id.lower()
    if 'iphone' in device_id or 'ipad' in device_id or 'ios' in device_id:
        return 'ios'
    elif 'androidtv' in device_id:
        return 'androidtv'
    elif 'android' in device_id:
        return 'android'
    elif 'windows' in device_id:
        return 'windows'
    elif 'mac' in device_id or 'macos' in device_id:
        return 'mac'
    return 'other'  

@router.message(AdminKeyRemovalStates.waiting_for_user)
async def show_user_keys(message: Message, state: FSMContext):
    """
    Отображение ключей пользователя по категориям с пагинацией
    """
    # Поиск пользователя по ID или username
    user_input = message.text.strip()
    if user_input.isdigit():
        target_user = await get_user(int(user_input))
    else:
        username = user_input.lstrip('@')
        target_user = await get_user_by_username(username)

    if not target_user:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="remove_key")
        await message.answer(
            "❌ Пользователь не найден",
            reply_markup=kb.as_markup()
        )
        return

    # Получаем все ключи пользователя
    user_keys = await get_user_keys(target_user['user_id'])
    if not user_keys:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="remove_key")
        await message.answer(
            "❌ У пользователя нет активных ключей",
            reply_markup=kb.as_markup()
        )
        return

    await state.update_data(target_user_id=target_user['user_id'])
    
    keys_by_device = {
        "ios": [],
        "androidtv": [],
        "android": [],
        "windows": [],
        "mac": []
    }
    
    for key_data in user_keys:
        key_value, device_id, expiration_date = key_data
        device_type = determine_device_type(device_id)  
        if device_type in keys_by_device:
            keys_by_device[device_type].append({
                'key': key_value,
                'device_id': device_id,
                'expiration_date': expiration_date
            })

    keys_text = f"🔑 Ключи пользователя {target_user.get('username', target_user['user_id'])}:\n\n"
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопки для каждой категории устройств
    device_icons = {
        "ios": "📱 iOS",
        "android": "🤖 Android",
        "androidtv": "📺 Android TV",
        "windows": "🖥 Windows",
        "mac": "🍎 macOS"
    }

    for device, keys in keys_by_device.items():
        if keys:
            kb.button(
                text=f"{device_icons[device]} ({len(keys)})", 
                callback_data=f"show_remove_keys_{device}_1"
            )
            keys_text += f"{device_icons[device]}: {len(keys)} ключей\n"

    kb.button(text="◀️ Назад", callback_data="remove_key")
    kb.adjust(2, 1)
    await state.update_data(target_user_id=target_user['user_id'], device_type=device, page=1)
    await message.answer(
        keys_text,
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("show_remove_keys"))
async def show_device_keys_2(callback: types.CallbackQuery, state: FSMContext):
    """
    Отображение ключей для выбранного устройства с пагинацией
    """
    try:

        # Правильное разделение callback_data
        # Формат: "show_keys_remove_ios_1" или "show_keys_remove_android_1" и т.д.
        _, _, _, device_type, page_num = callback.data.split("_")
        parts = callback.data.split("_")
        page = int(parts[-1])    # Последний элемент - номер страницы
        
        # Получаем данные пользователя из state
        data = await state.get_data()
        target_user_id = data['target_user_id']
        
        # Получаем все ключи пользователя
        all_keys = await get_user_keys(target_user_id)
        
        # Фильтруем ключи для выбранного устройства
        device_keys = []
        for key_data in all_keys:
            key_value, device_id, expiration_date = key_data
            if determine_device_type(device_id) == device_type:
                device_keys.append({
                    'key': key_value,
                    'device_id': device_id,
                    'expiration_date': expiration_date
                })
        
        if not device_keys:
            await callback.answer("Ключи не найдены", show_alert=True)
            return

        # Настройки пагинации
        KEYS_PER_PAGE = 5
        total_pages = (len(device_keys) + KEYS_PER_PAGE - 1) // KEYS_PER_PAGE
        start_idx = (page - 1) * KEYS_PER_PAGE
        end_idx = start_idx + KEYS_PER_PAGE
        current_page_keys = device_keys[start_idx:end_idx]

        # Формируем клавиатуру
        kb = InlineKeyboardBuilder()
        kb.button(
            text="💣 Удалить ВСЕ ключи", 
            callback_data=f"confirm_remove_all_{device_type}"
        )        
        device_names = {
            "ios": "📱 iOS",
            "android": "🤖 Android",
            "androidtv": "📺 Android TV",
            "windows": "🖥 Windows",
            "mac": "🍎 macOS"
        }
        
        for key in current_page_keys:
            expiry_date = key['expiration_date']
            print(key)
            device, unique_id, uniquie_uuid, address, parts = extract_key_data(key['key'])
            print(unique_id, expiry_date, address)
            if isinstance(expiry_date, (int, float)):
                expiry_date = datetime.fromtimestamp(expiry_date/1000).strftime('%d.%m.%Y')
            kb.button(
                text=f"🗑 Ключ {unique_id}", 
                callback_data=f"remove_key_{unique_id}"
            )

        # Кнопки навигации
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(
                text="⬅️", 
                callback_data=f"show_remove_keys_{device_type}_{page-1}" 
            ))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(
                text="➡️", 
                callback_data=f"show_remove_keys_{device_type}_{page+1}"  
            ))
        if nav_row:
            kb.row(*nav_row)

        
        kb.button(text="◀️ Назад к устройствам", callback_data="remove_key")
        kb.adjust(1)

        # Формируем текст сообщения
        message_text = (
            f"🔑 {device_names[device_type]} (стр. {page}/{total_pages})\n"
            f"Выберите ключ для удаления:"
        )

        await callback.message.edit_text(
            text=message_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error in show_device_keys: {e}")
        await callback.answer("Произошла ошибка при отображении ключей", show_alert=True)

@router.callback_query(F.data.startswith("confirm_remove_all_"))
async def remove_all_keys(callback: types.CallbackQuery, state: FSMContext):
    """Удаление всех ключей пользователя"""
    device_type = callback.data.split("_")[3]
    
    confirm_kb = InlineKeyboardBuilder()
    confirm_kb.button(
        text="💣 ПОДТВЕРДИТЬ УДАЛЕНИЕ ВСЕХ", 
        callback_data=f"final_remove_all_{device_type}"
    )
    confirm_kb.button(text="❌ Отмена", callback_data="remove_key")
    
    await callback.message.edit_text(
        "🚨 Вы уверены, что хотите удалить ВСЕ ключи этого типа?\n"
        "Это действие нельзя отменить!",
        reply_markup=confirm_kb.as_markup()
    )

@router.callback_query(F.data.startswith("final_remove_all_"))
async def final_remove_all_keys(callback: types.CallbackQuery, state: FSMContext):
    device_type = callback.data.split("_")[3]
    data = await state.get_data()
    target_user_id = data['target_user_id']
    
    try:
        all_keys = await get_user_keys(target_user_id)
        deleted_count = 0
        skipped_count = 0  # Счетчик пропущенных ключей
        
        for key_data in all_keys:
            key_value, device_id, _ = key_data
            if determine_device_type(device_id) == device_type:
                try:
                    device, unique_id, uuid, address, parts = extract_key_data(key_value)
                    protocol = 'ss' if key_value.startswith('ss://') else 'vless'
                    
                    server = await get_server_by_address(
                        address, 
                        protocol="shadowsocks" if protocol == 'ss' else "vless"
                    )
                    api = AsyncApi(
                        f"http://{server['address']}",
                        server['username'],
                        server['password'],
                        use_tls_verify=False
                    )
                    
                    await api.login()
                    client = await api.client.get_by_email(f"{parts[0]}_{parts[1]}_{parts[2]}")
                    
                    # Пропускаем если клиент не найден
                    if not client:
                        skipped_count += 1
                        continue
                        
                    if protocol == 'ss':
                        await api.client.delete(
                            inbound_id=client.inbound_id, 
                            client_uuid=str(client.email)
                        )
                    else:
                        await api.client.delete(
                            inbound_id=client.inbound_id,
                            client_uuid=str(uuid)
                        )
                    
                    # Обновляем счетчик клиентов
                    try:
                        clients_count = await get_server_count_by_address(
                            address, 
                            client.inbound_id, 
                            protocol="shadowsocks" if protocol == 'ss' else "vless"
                        )
                        await update_server_clients_count(
                            address, 
                            clients_count - 1, 
                            client.inbound_id
                        )
                    except Exception as update_error:
                        logger.error(f"Ошибка обновления счетчика: {update_error}")
                    
                    await remove_key_bd(key_value)
                    deleted_count += 1
                    
                except Exception as key_error:
                    logger.error(f"Ошибка удаления ключа {key_value}: {key_error}")
                    skipped_count += 1

        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ В админ-панель", callback_data="admin_back")
        
        result_message = (
            f"✅ Успешно удалено: {deleted_count}\n"
            f"⏭ Пропущено: {skipped_count}\n"
            f"➖ Не найдено на сервере: {skipped_count}"
        )
        
        await callback.message.edit_text(
            result_message,
            reply_markup=kb.as_markup()
        )
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка массового удаления: {e}")
        await callback.answer("Произошла ошибка при удалении ключей", show_alert=True)


@router.callback_query(F.data.startswith("remove_key_"))
async def confirm_key_removal(callback: types.CallbackQuery, state: FSMContext):
    """
    Подтверждение удаления выбранного ключа
    """
    unique_id = callback.data.split("_")[2]
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=f"confirm_remove_key_{unique_id}")
    kb.button(text="❌ Отмена", callback_data="remove_key")
    kb.adjust(2)

    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите удалить этот ключ?\n"
        "Пользователь потеряет доступ к VPN.",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("confirm_remove_key_"))
async def remove_key_final(callback: types.CallbackQuery, state: FSMContext):
    """
    Финальное удаление ключа
    """
    unique_id = callback.data.split("_")[3]
    
    try:
        key = await get_key_by_uniquie_id(unique_id)
        
        device, unique_id, uniquie_uuid, address, parts = extract_key_data(key['key'])
        protocol = 'ss' if key['key'].startswith('ss://') else 'vless'
        server = await get_server_by_address(address, protocol = "shadowsocks" if protocol == 'ss' else "vless")
        api = AsyncApi(
                    f"http://{server['address']}",
                    server['username'],
                    server['password'],
                    use_tls_verify=False
        )
        await api.login()

        email = f"{parts[0]}_{parts[1]}_{parts[2]}"
        client = await api.client.get_by_email(email)

        try:    
            if protocol == 'ss':
                await api.client.delete(inbound_id = client.inbound_id, client_uuid=str(client.email))
            else:
                await api.client.delete(inbound_id = client.inbound_id, client_uuid=str(uniquie_uuid))
            await remove_key_bd(key['key'])
        except Exception as e:
            logger.error(f"Ошибка при удалении ключа: {e}")
            await callback.answer("Произошла ошибка при удалении ключа", show_alert=True)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться в админ-панель", callback_data="admin_back")
        
        await callback.message.edit_text(
            "✅ Ключ успешно удален!",
            reply_markup=kb.as_markup()
        )
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка при удалении ключа: {e}")
        await callback.answer("Произошла ошибка при удалении ключа", show_alert=True)

@router.message(Command("find"))
async def find_user(message: Message, state: FSMContext):
    """
    Поиск пользователя по ключу, username или user_id и вывод всей информации о клиенте.
    """
    user = await get_user(message.from_user.id)
    if not user.get('is_admin'):
        await message.answer("⛔️ У вас нет доступа")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Неверный формат команды.\nИспользуйте: /find ключ/username/user_id")
        return

    query = args[1]

    # Проверяем, является ли query числом (user_id) или строкой (ключ или username)
    if query.isdigit():
        user_info = await get_user_info(user_id=int(query))
    else:
        user_info = await get_user_info(key=query) or await get_user_info(username=query)

    if not user_info:
        await message.answer("❌ Пользователь не найден")
        return

    # Формируем сообщение с информацией о пользователе
    user_text = (
        f"👤 <b>Информация о пользователе</b>\n\n"
        f"🆔 ID: {user_info['user_id']}\n"
        f"👤 Username: {user_info['username']}\n"
        f"💰 Баланс: {user_info['balance']}₽\n"
        f"📅 Подписка: {user_info['subscription_type']}\n"
        f"📅 Окончание последней купленной подписки: {user_info['subscription_end']}\n"
        f"👥 Рефералов: {user_info['referral_count']}\n"
        f"🔑 Всего ключей: {user_info['keys_count']}\n"
        f"🔑 Бесплатные ключи(промокоды): {user_info['free_keys_count']}\n"
        f"🎁 Промо дни: {user_info['promo_days']}\n"
    )

    await message.answer(user_text, parse_mode="HTML")


@router.message(Command("balance"))
async def change_balance(message: Message):
    """
    Изменение баланса пользователя
    Формат: /balance id/username amount
    amount может быть положительным или отрицательным числом
    """
    admin = await get_user(message.from_user.id)
    if not admin.get('is_admin'):
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer(
                "❌ Неверный формат команды.\n"
                "Используйте: /balance id/username сумма\n"
                "Пример: /balance 123456789 1000\n"
                "Для снятия денег используйте минус: /balance 123456789 -1000"
            )
            return
        
        target = args[1]
        try:
            amount = int(args[2])
        except ValueError:
            await message.answer("❌ Сумма должна быть целым числом")
            return
        
        # Поиск пользователя по ID или username
        if target.isdigit():
            user = await get_user(int(target))
        else:
            user = await get_user_by_username(target.lstrip('@'))
            
        if not user:
            await message.answer("❌ Пользователь не найден")
            return
        
        # Получаем текущий баланс и вычисляем новый
        current_balance = int(user['balance'])
        new_balance = current_balance + amount
        
        if new_balance < 0:
            await message.answer(
                f"❌ Невозможно установить отрицательный баланс\n"
                f"Текущий баланс пользователя: {current_balance}₽"
            )
            return
        
        # Обновляем баланс
        await update_balance(user['user_id'], new_balance)
        
        # Отправляем уведомление пользователю
        try:
            if amount > 0:
                balance_message = (
                    f"💰 <b>Ваш баланс пополнен администратором</b>\n"
                    f"└ Сумма: +{amount:,}₽\n"
                    f"└ Текущий баланс: {new_balance:,}₽"
                )
            else:
                balance_message = (
                    f"💰 <b>Списание средств администратором</b>\n"
                    f"└ Сумма: {amount:,}₽\n"
                    f"└ Текущий баланс: {new_balance:,}₽"
                )
                
            await message.bot.send_message(
                user['user_id'],
                balance_message,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю: {e}")
        
        # Уведомление администратору
        action = "пополнен на" if amount > 0 else "уменьшен на"
        amount_abs = abs(amount)
        await message.answer(
            f"✅ Баланс пользователя {user.get('username', user['user_id'])} {action} {amount_abs:,}₽\n"
            f"└ Текущий баланс: {new_balance:,}₽"
        )
        
        # Логирование действия
        logger.info(
            f"Администратор {message.from_user.id} изменил баланс "
            f"пользователя {user['user_id']} на {amount:,}₽. "
            f"Новый баланс: {new_balance:,}₽"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при изменении баланса: {e}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")

@router.callback_query(F.data == "export_data")
async def export_data(callback: types.CallbackQuery):
    """
    Экспорт актуальных данных пользователей в Excel файл
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return

    try:
        # Получаем всех пользователей
        await update_user_pay_count()
        users = await get_all_users()
        
        # Создаем словарь для хранения дохода с рефералов
        referral_income = {}
        
        # Рассчитываем доход с рефералов для каждого пользователя
        for user in users:
            referrer_id = user.get('referrer_id')
            if referrer_id and referrer_id != 0:
                # Получаем все успешные транзакции пользователя
                transactions = await get_user_transactions(user['user_id'])
                # Суммируем только успешные транзакции
                total_amount = sum(tx['amount'] for tx in transactions if tx['status'] == 'succeeded')
                
                # Добавляем сумму к доходу реферера
                if referrer_id not in referral_income:
                    referral_income[referrer_id] = 0
                referral_income[referrer_id] += total_amount
        
        # Добавляем информацию о доходе с рефералов к данным пользователей
        for user in users:
            user['referral_income'] = referral_income.get(user['user_id'], 0)
        
        # Создаем DataFrame
        df = pd.DataFrame(users)
        
        # Преобразуем даты
        def format_date(date_str):
            if pd.isna(date_str) or date_str == "Без подписки" or not date_str:
                return "см. в 3x-ui"
            try:
                # Если дата в миллисекундах
                if isinstance(date_str, (int, float)):
                    return datetime.fromtimestamp(date_str/1000).strftime('%Y-%m-%d %H:%M:%S')
                return pd.to_datetime(date_str).strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logger.error(f"Ошибка при форматировании даты: {e}")
                return str(date_str)

        df['subscription_end'] = df['subscription_end'].apply(format_date)
        
        # Переименовываем колонки
        column_names = {
            'user_id': 'ID пользователя',
            'username': 'Имя пользователя',
            'balance': 'Баланс',
            'subscription_type': 'Тип подписки',
            'subscription_end': 'Окончание подписки',
            'is_admin': 'Админ',
            'referrer_id': 'ID реферера',
            'referral_count': 'Кол-во рефералов',
            'keys_count': 'Всего ключей',
            'free_keys_count': 'Бесплатные ключи',
            'promo_days': 'Промо дни',
            'from_channel': 'Канал',
            'pay_count': 'Кол-во платежей',
            'referral_income': 'Доход с рефералов'
        }
        df = df.rename(columns=column_names)

        # Сохраняем в Excel
        filename = f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False, engine='openpyxl')

        # Отправляем файл
        await callback.message.answer_document(
            document=FSInputFile(filename),
            caption="📊 Экспорт данных пользователей"
        )

        # Удаляем временный файл
        os.remove(filename)

        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться в админ-панель", callback_data="admin_back")
        
        await callback.message.answer(
            "✅ Экспорт данных успешно выполнен!",
            reply_markup=kb.as_markup()
        )

    except Exception as e:
        logger.error(f"Ошибка при экспорте данных: {e}")
        await callback.message.answer(
            f"❌ Произошла ошибка при экспорте данных: {str(e)}"
        )

    await callback.answer()

def get_group_name(group):
    """
    Возвращает читаемое название группы
    """
    group_names = {
        "all": "Все пользователи",
        "unused_free_keys": "Неактивированные бесплатные ключи",
        "zero_traffic": "Нулевой трафик",
        "balance_99": "Баланс 99₽",
        "expiring_subscriptions": "Истекающие подписки",
        "ip_server": "Пользователи сервера"  # Добавляем новое название группы
    }
    return group_names.get(group, f"Неизвестная группа ({group})")

@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса создания рассылки с выбором группы пользователей
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    
    # Добавляем кнопки для выбора группы
    kb.button(text="👥 Все пользователи", callback_data="broadcast_group_all")
    kb.button(text="🆓 Неактивированные бесплатные ключи", callback_data="broadcast_group_unused_free_keys")
    kb.button(text="🔄 Нулевой трафик", callback_data="broadcast_group_zero_traffic")
    kb.button(text="💰 Баланс 99₽", callback_data="broadcast_group_balance_99")
    kb.button(text="⏳ Истекающие подписки", callback_data="broadcast_group_expiring_subscriptions")
    kb.button(text="🔑 По айпи сервера", callback_data="broadcast_group_ip_server")
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    kb.adjust(1)  # Размещаем кнопки в один столбец
    
    await callback.message.edit_text(
        "📢 <b>Создание рассылки</b>\n\n"
        "Выберите группу пользователей для рассылки:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminBroadcastStates.select_group)

@router.callback_query(AdminBroadcastStates.select_group, F.data.startswith("broadcast_group_"))
async def select_broadcast_group(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора группы для рассылки
    """
    # Извлекаем идентификатор группы из callback_data
    group = callback.data.replace("broadcast_group_", "")
    
    # Сохраняем выбранную группу в состоянии
    await state.update_data(broadcast_group=group)
    
    # Если выбрана группа по IP сервера, запрашиваем IP
    if group == "ip_server":
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Отмена", callback_data="admin_back")
        
        await callback.message.edit_text(
            "📢 <b>Создание рассылки</b>\n\n"
            "Введите IP-адрес или домен сервера для рассылки пользователям с ключами на этом сервере:",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        await state.set_state(AdminBroadcastStates.waiting_for_ip)
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await callback.message.edit_text(
        "📢 <b>Создание рассылки</b>\n\n"
        f"Группа: <b>{get_group_name(group)}</b>\n\n"
        "Введите текст сообщения для рассылки.\n"
        "Поддерживается форматирование:\n"
        "• <b>Жирный текст</b>\n"
        "• <i>Курсив</i>\n"
        "• <u>Подчеркивание</u>\n"
        "• <code>Моноширный текст</code>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminBroadcastStates.waiting_for_message)

@router.message(AdminBroadcastStates.waiting_for_ip)
async def process_server_ip(message: Message, state: FSMContext):
    """
    Обработка ввода IP-адреса сервера
    """
    server_address = message.text.strip()
    
    # Сохраняем IP-адрес в состоянии
    await state.update_data(server_address=server_address)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await message.answer(
        "📢 <b>Создание рассылки</b>\n\n"
        f"Группа: <b>{get_group_name('ip_server')}</b>\n"
        f"Сервер: <b>{server_address}</b>\n\n"
        "Введите текст сообщения для рассылки.\n"
        "Поддерживается форматирование:\n"
        "• <b>Жирный текст</b>\n"
        "• <i>Курсив</i>\n"
        "• <u>Подчеркивание</u>\n"
        "• <code>Моноширный текст</code>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminBroadcastStates.waiting_for_message)

@router.message(AdminBroadcastStates.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """
    Обработка текста сообщения для рассылки
    """
    # Сохраняем HTML-текст сообщения
    await state.update_data(broadcast_text=message.html_text)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📸 Добавить медиа", callback_data="add_broadcast_media")
    kb.button(text="➡️ Пропустить", callback_data="skip_broadcast_media")
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    kb.adjust(1)
    
    await message.answer(
        "📸 Хотите добавить медиафайл к рассылке?",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data == "add_broadcast_media")
async def add_broadcast_media(callback: types.CallbackQuery, state: FSMContext):
    """
    Добавление медиафайла к рассылке
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await callback.message.edit_text(
        "📸 Отправьте медиафайл:\n"
        "• Фото\n"
        "• Видео\n"
        "• Документ",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminBroadcastStates.waiting_for_media)

@router.callback_query(F.data == "skip_broadcast_media")
async def skip_broadcast_media(callback: types.CallbackQuery, state: FSMContext):
    """
    Пропуск добавления медиа
    """
    data = await state.get_data()
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data="confirm_broadcast")
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await callback.message.edit_text(
        "📢 <b>Предварительный просмотр рассылки:</b>\n\n"
        f"{data.get('broadcast_text', 'Текст отсутствует')}",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminBroadcastStates.confirm_broadcast)

@router.message(AdminBroadcastStates.waiting_for_media, 
                F.photo | F.video | F.document)
async def process_broadcast_media(message: Message, state: FSMContext):
    """
    Обработка медиафайла для рассылки
    """
    # Определяем тип медиа
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    elif message.document:
        file_id = message.document.file_id
        media_type = "document"
    
    # Сохраняем информацию о медиа
    await state.update_data(
        broadcast_media_id=file_id, 
        broadcast_media_type=media_type
    )
    
    data = await state.get_data()
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data="confirm_broadcast")
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    # Отправляем предварительный просмотр с медиафайлом
    if media_type == "photo":
        await message.answer_photo(
            photo=file_id,
            caption=f"📢 <b>Предварительный просмотр рассылки:</b>\n\n{data.get('broadcast_text', 'Текст отсутствует')}",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    elif media_type == "video":
        await message.answer_video(
            video=file_id,
            caption=f"📢 <b>Предварительный просмотр рассылки:</b>\n\n{data.get('broadcast_text', 'Текст отсутствует')}",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    elif media_type == "document":
        await message.answer_document(
            document=file_id,
            caption=f"📢 <b>Предварительный просмотр рассылки:</b>\n\n{data.get('broadcast_text', 'Текст отсутствует')}",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    
    await state.set_state(AdminBroadcastStates.confirm_broadcast)

@router.callback_query(F.data == "confirm_broadcast")
async def confirm_broadcast(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    Подтверждение и выполнение рассылки для выбранной группы пользователей
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    data = await state.get_data()
    broadcast_text = data.get('broadcast_text')
    broadcast_media_id = data.get('broadcast_media_id')
    broadcast_media_type = data.get('broadcast_media_type')
    broadcast_group = data.get('broadcast_group', 'all')
    server_address = data.get('server_address')
    
    # Получаем список пользователей в зависимости от выбранной группы
    if broadcast_group == 'ip_server' and server_address:
        target_users = await get_users_by_server_address(server_address)
    else:
        target_users = await get_target_users(broadcast_group)
    
    success_count = 0
    error_count = 0
    
    # Индикатор прогресса
    group_name = get_group_name(broadcast_group)
    if broadcast_group == 'ip_server' and server_address:
        group_name = f"{group_name} ({server_address})"
        
    progress_message = await callback.message.answer(
        f"🔄 Начало рассылки для группы '{group_name}'...\n"
        f"Всего получателей: {len(target_users)}"
    )
    
    for user_id in target_users:
        try:
            # Отправка с медиа
            if broadcast_media_id:
                if broadcast_media_type == "photo":
                    await bot.send_photo(
                        chat_id=user_id, 
                        photo=broadcast_media_id, 
                        caption=broadcast_text, 
                        parse_mode="HTML"
                    )
                elif broadcast_media_type == "video":
                    await bot.send_video(
                        chat_id=user_id, 
                        video=broadcast_media_id, 
                        caption=broadcast_text, 
                        parse_mode="HTML"
                    )
                elif broadcast_media_type == "document":
                    await bot.send_document(
                        chat_id=user_id, 
                        document=broadcast_media_id, 
                        caption=broadcast_text, 
                        parse_mode="HTML"
                    )
            else:
                # Отправка только текста
                await bot.send_message(
                    chat_id=user_id, 
                    text=broadcast_text, 
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            
            success_count += 1
            
            # Обновляем прогресс каждые 10 отправленных сообщений
            if success_count % 10 == 0:
                try:
                    await progress_message.edit_text(
                        f"🔄 Рассылка в процессе...\n"
                        f"Отправлено: {success_count}/{len(target_users)}"
                    )
                except Exception:
                    pass
                
        except Exception as e:
            error_count += 1
            logger.error(f"Ошибка при рассылке пользователю {user_id}: {e}")
    
    # Удаляем сообщение с индикатором
    await progress_message.delete()
    
    # Финальный отчет
    group_display = group_name
    await callback.message.answer(
        f"📊 <b>Рассылка завершена</b>\n\n"
        f"Группа: <b>{group_display}</b>\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Ошибок: {error_count}",
        parse_mode="HTML"
    )
    
    await state.clear()

async def get_target_users(group):
    """
    Получает список ID пользователей для рассылки в зависимости от выбранной группы
    
    Args:
        group (str): Идентификатор группы
        
    Returns:
        list: Список ID пользователей
    """
    if group == 'all':
        # Получаем всех пользователей
        users = await get_all_users()
        return [user['user_id'] for user in users]
    
    # Для группы неактивированных бесплатных ключей используем прямой запрос
    if group == 'unused_free_keys':
        return await get_users_with_unused_free_keys()
    
    # Получаем сегменты пользователей для остальных групп
    segments = await get_user_segments()
    
    if group == 'zero_traffic':
        # Для zero_traffic у нас кортежи (user_id, key), нам нужны только user_id
        return [user_id for user_id, _ in segments['zero_traffic']]
    elif group == 'balance_99':
        return segments['balance_99']
    elif group == 'expiring_subscriptions':
        return segments['expiring_subscriptions']
    
    # Если группа не распознана, возвращаем пустой список
    return []

@router.callback_query(F.data.startswith("promocodes_info"))
async def promocodes_info(callback: types.CallbackQuery):
    """
    Показывает информацию о промокодах и действия с ними с пагинацией
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    # Получаем номер страницы из callback_data
    page = int(callback.data.split('_')[-1]) if callback.data != "promocodes_info" else 1
    items_per_page = 5  # Количество промокодов на странице
    
    # Получаем список всех промокодов
    promocodes = await get_all_promocodes()
    
    kb = InlineKeyboardBuilder()
    
    # Формируем текст со списком промокодов для текущей страницы
    if promocodes:
        total_pages = (len(promocodes) + items_per_page - 1) // items_per_page
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        current_page_promocodes = promocodes[start_idx:end_idx]
        
        promo_text = f"📋 <b>Список активных промокодов (стр. {page}/{total_pages}):</b>\n\n"
        
        for promo in current_page_promocodes:
            promo_id, code, user_id, amount, gift_balance, gift_days, expiration_date = promo
            promo_text += (
                f"🔑 <b>{code}</b>\n"
                f"└ 🆔 ID: {promo_id}\n"
                f"└ 👥 Макс. использований: {amount}\n"
                f"└ 💰 Бонус: {gift_balance}₽\n"
                f"└ 📅 Действителен до: {expiration_date}\n\n"
            )
        
        # Добавляем кнопки навигации
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(
                text="◀️", 
                callback_data=f"promocodes_info_{page-1}"
            ))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(
                text="▶️", 
                callback_data=f"promocodes_info_{page+1}"
            ))
        if nav_buttons:
            kb.row(*nav_buttons)
    else:
        promo_text = "❌ Нет активных промокодов"
    
    # Добавляем основные кнопки управления
    kb.button(text="➕ Создать промокод", callback_data="create_promocode")
    kb.button(text="🗑 Удалить промокод", callback_data="delete_promocode")
    kb.button(text="◀️ Назад", callback_data="admin_back")
    kb.adjust(2, 1, 1)  # Настраиваем расположение кнопок
    
    try:
        await callback.message.edit_text(
            promo_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise e


@router.callback_query(F.data == "delete_promocode")
async def start_delete_promocode(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса удаления промокода
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    # Получаем список всех промокодов
    promocodes = await get_all_promocodes()
    
    kb = InlineKeyboardBuilder()
    for promo in promocodes:
        promo_id, code, _, amount, gift_balance, gift_days, expiration_date = promo
        kb.button(
            text=f"🗑 {code} ({amount} исп.)", 
            callback_data="confirm_delete_promo"
        )
        await state.update_data({
            "promo_id": promo_id,
            "promo_code": code,
            "amount": amount,
            "gift_balance": gift_balance,
            "gift_days": gift_days,
            "expiration_date": expiration_date
        })
    kb.button(text="◀️ Назад", callback_data="promocodes_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🗑 <b>Выберите промокод для удаления:</b>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "confirm_delete_promo")
async def confirm_delete_promocode(callback: types.CallbackQuery, state: FSMContext):
    """
    Подтверждение удаления промокода
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    # Получаем данные о промокоде из state
    data = await state.get_data()
    
    promo_code = data.get('promo_code')
    amount = data.get('amount')
    gift_balance = data.get('gift_balance')
    gift_days = data.get('gift_days')
    expiration_date = data.get('expiration_date')
    
    # Проверяем существование промокода
    promo = await get_promocode(promo_code)
    
    if promo:
        # Удаляем промокод
        await remove_promocode(promo_code)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться", callback_data="promocodes_info")
        
        await callback.message.edit_text(
            f"✅ Промокод <b>{promo_code}</b> успешно удален!\n\n"
            f"📊 Детали:\n"
            f"└ Осталось использований: {amount}\n"
            f"└ Бонус: {gift_balance}₽\n"
            f"└ Дней VPN: {gift_days}\n"
            f"└ Действителен до: {expiration_date}",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        
        # Очищаем state после удаления
        await state.clear()
    else:
        await callback.answer("❌ Промокод не найден", show_alert=True)

@router.callback_query(F.data == "create_promocode")
async def start_create_promocode(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса создания промокода
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="promocodes_info")
    
    await callback.message.edit_text(
        "🎁 <b>Создание промокода</b>\n\n"
        "Введите код промокода (заглавными буквами):",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(PromoCodeAdminStates.waiting_promo_code)

@router.message(PromoCodeAdminStates.waiting_promo_code)
async def process_promo_code(message: Message, state: FSMContext):
    """
    Обработка кода промокода
    """
    promo_code = message.text.strip().upper()
    
    # Проверяем, что код не занят
    existing_promo = await get_promocode(promo_code)
    if existing_promo:
        await message.answer(
            "❌ Такой промокод уже существует. Введите другой код:",
            parse_mode="HTML"
        )
        return
    
    await state.update_data(promo_code=promo_code)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="promocodes_info")
    
    await message.answer(
        "🔢 Введите максимальное количество использований промокода:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(PromoCodeAdminStates.waiting_promo_amount)

@router.message(PromoCodeAdminStates.waiting_promo_amount)
async def process_promo_amount(message: Message, state: FSMContext):
    """
    Обработка количества использований промокода
    """
    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
        
        await state.update_data(amount=amount)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Отмена", callback_data="promocodes_info")
        
        await message.answer(
            "💰 Введите сумму бонуса в рублях:",
            reply_markup=kb.as_markup()
        )
        await state.set_state(PromoCodeAdminStates.waiting_promo_gift_balance)
    
    except ValueError:
        await message.answer("❌ Введите корректное число использований:")

@router.message(PromoCodeAdminStates.waiting_promo_gift_balance)
async def process_promo_gift_balance(message: Message, state: FSMContext):
    """
    Обработка суммы бонуса
    """
    try:
        gift_balance = int(message.text)
        if gift_balance < 0:
            raise ValueError
        
        await state.update_data(gift_balance=gift_balance)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Отмена", callback_data="promocodes_info")
        
        await message.answer(
            "🕰 Введите количество дней для VPN:",
            reply_markup=kb.as_markup()
        )
        await state.set_state(PromoCodeAdminStates.waiting_promo_gift_days)
    
    except ValueError:
        await message.answer("❌ Введите корректную сумму бонуса:")     

@router.message(PromoCodeAdminStates.waiting_promo_gift_days)
async def process_promo_gift_days(message: Message, state: FSMContext):
    """
    Обработка количества дней для VPN
    """
    try:
        gift_days = int(message.text)        
        await state.update_data(gift_days=gift_days)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Отмена", callback_data="promocodes_info")
        
        await message.answer(
            "📅 Введите дату истечения промокода (ГГГГ-ММ-ДД):",
            reply_markup=kb.as_markup()
        )
        await state.set_state(PromoCodeAdminStates.waiting_promo_expiration_date)
    
    except ValueError:
        await message.answer("❌ Введите корректное количество дней:")

@router.message(PromoCodeAdminStates.waiting_promo_expiration_date)
async def process_promo_expiration_date(message: Message, state: FSMContext):
    """
    Обработка даты истечения промокода
    """
    try:
        expiration_date = datetime.strptime(message.text, "%Y-%m-%d")
        
        # Получаем все данные о промокоде
        data = await state.get_data()
        
        # Создаем промокод
        await add_promocode(
            code=data['promo_code'], 
            user_id=message.from_user.id,  # ID администратора, создавшего промокод
            amount=data['amount'], 
            gift_balance=data['gift_balance'], 
            gift_days=data['gift_days'],  # Добавляем дни VPN
            expiration_date=expiration_date.isoformat()
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться", callback_data="promocodes_info")
        
        await message.answer(
            "✅ Промокод успешно создан!\n\n"
            f"🔑 Код: {data['promo_code']}\n"
            f"💰 Бонус: {data['gift_balance']}₽\n"
            f"🕰 Дней VPN: {data['gift_days']}\n"
            f"📅 Действителен до: {message.text}",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        
        await state.clear()
    
    except ValueError:
        await message.answer("❌ Введите дату в формате ГГГГ-ММ-ДД:")

@router.callback_query(F.data == "remove_server")
async def show_servers_to_remove(callback: types.CallbackQuery):
    """
    Показывает список серверов для удаления
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
        
    servers = await get_all_servers()
    
    if not servers:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="admin_back")
        await callback.message.edit_text(
            "❌ Нет доступных серверов",
            reply_markup=kb.as_markup()
        )
        return
    
    # Группируем серверы по ID для подсчета общего количества клиентов
    grouped_servers = {}
    for server in servers:
        if server['id'] not in grouped_servers:
            grouped_servers[server['id']] = {
                'address': server['address'],
                'id': server['id'],
                'total_clients': 0,
                'total_max': 0
            }
        if server['protocol']:  # Учитываем только активные инбаунды
            grouped_servers[server['id']]['total_clients'] += server['clients_count']
            grouped_servers[server['id']]['total_max'] += server['max_clients']
    
    kb = InlineKeyboardBuilder()
    for server_data in grouped_servers.values():
        kb.button(
            text=f"🖥 {server_data['address']} ({server_data['total_clients']}/{server_data['total_max']})", 
            callback_data=f"del_server_{server_data['id']}"
        )
    kb.button(text="◀️ Назад", callback_data="admin_back")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🗑 <b>Выберите сервер для удаления:</b>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("del_server_"))
async def remove_server_confirm(callback: types.CallbackQuery):
    """
    Подтверждение удаления сервера
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
        
    server_id = int(callback.data.split("_")[2])
    servers = await get_all_servers()
    server_inbounds = [s for s in servers if s['id'] == server_id]
    
    if not server_inbounds:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    server = server_inbounds[0]  # Берем первый инбаунд для получения адреса сервера
    
    # Подсчитываем общее количество клиентов по всем инбаундам
    total_clients = sum(s['clients_count'] for s in server_inbounds if s['protocol'])
    
    # Формируем информацию об инбаундах
    inbounds_info = []
    for inbound in server_inbounds:
        if inbound['protocol']:
            inbounds_info.append(
                f"└ {inbound['protocol']}: {inbound['clients_count']} клиентов"
            )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=f"confirm_del_{server_id}")
    kb.button(text="❌ Отмена", callback_data="remove_server")
    kb.adjust(2)
    
    await callback.message.edit_text(
        f"⚠️ <b>Подтвердите удаление сервера</b>\n\n"
        f"Сервер: {server['address']}\n"
        f"Всего клиентов: {total_clients}\n\n"
        f"Активные инбаунды:\n"
        f"{chr(10).join(inbounds_info)}\n\n"
        f"❗️ Все пользователи будут отключены от этого сервера!",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("confirm_del_"))
async def remove_server_final(callback: types.CallbackQuery):
    """
    Финальное удаление сервера
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
        
    server_id = int(callback.data.split("_")[2])
    await delete_server(server_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Вернуться в админ-панель", callback_data="admin_back")
    
    await callback.message.edit_text(
        "✅ Сервер успешно удален!",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("servers_info"))
async def show_servers_info(callback: types.CallbackQuery):
    """
    Показывает информацию о всех серверах с пагинацией
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
        
    page = int(callback.data.split('_')[-1]) if len(callback.data.split('_')) > 2 else 1
    SERVERS_PER_PAGE = 3
    
    servers = await get_all_servers()
    
    if not servers:
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Назад", callback_data="admin_back")
        await callback.message.edit_text(
            "❌ Нет доступных серверов",
            reply_markup=kb.as_markup()
        )
        return
    
    # Группируем серверы по ID
    grouped_servers = {}
    for server in servers:
        if server['id'] not in grouped_servers:
            grouped_servers[server['id']] = []
        grouped_servers[server['id']].append(server)
    
    total_servers = len(grouped_servers)
    total_pages = (total_servers + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE
    start_idx = (page - 1) * SERVERS_PER_PAGE
    end_idx = min(start_idx + SERVERS_PER_PAGE, total_servers)
    
    current_page_server_ids = list(grouped_servers.keys())[start_idx:end_idx]
    
    info_text = f"📊 <b>Информация о серверах (стр. {page}/{total_pages}):</b>\n\n"
    
    for server_id in current_page_server_ids:
        server_group = grouped_servers[server_id]
        first_server = server_group[0]
        status = "🟢 Активен" if first_server['is_active'] else "🔴 Отключен"
        
        # Считаем общее количество клиентов и максимум для сервера
        total_clients = sum(s['clients_count'] for s in server_group if s['protocol'])
        total_max = sum(s['max_clients'] for s in server_group if s['protocol'])
        total_load = (total_clients / total_max * 100) if total_max > 0 else 0
        
        info_text += (
            f"🖥 <b>Сервер #{first_server['id']}</b>\n"
            f"├ 📍 Адрес: {first_server['address']}\n"
            f"├ 🔌 Порт: 2053\n"
            f"├ 🌍 Страна: {first_server['country'] or 'Не указана'}\n"
            f"├ 📡 Статус: {status}\n"
            f"├ 👥 Всего клиентов: {total_clients}/{total_max}\n"
            f"└ 📊 Общая загрузка: {total_load:.1f}%\n"
        )
        info_text += "    Протоколы сервера:\n"
        for server in server_group:
            if server['protocol']:
                load_percent = (server['clients_count'] / server['max_clients'] * 100) if server['max_clients'] > 0 else 0
                info_text += (
                    f"   ┌ 📡 Протокол: {server['protocol']}\n"
                    f"   ├ 👥 Клиентов: {server['clients_count']}/{server['max_clients']}\n"
                    f"   ├ 📊 Загрузка: {load_percent:.1f}%\n"
                    f"   └ 🔢 ID: {server['inbound_id']}\n"
                )
        info_text += "\n"
    
    kb = InlineKeyboardBuilder()
    
    # Навигация
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️",
            callback_data=f"servers_info_{page-1}"
        ))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️",
            callback_data=f"servers_info_{page+1}"
        ))
    if nav_buttons:
        kb.row(*nav_buttons)
    
    kb.button(text="◀️ Назад", callback_data="admin_back")
    kb.adjust(2, 1)
    
    try:
        await callback.message.edit_text(
            info_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise e


@router.callback_query(F.data == "update_server_info")
async def update_server_info_row(callback: types.CallbackQuery):
    """
    Показывает список серверов для обновления информации
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
    
    servers = await get_all_servers()

    if not servers:
        await callback.answer("❌ Нет доступных серверов", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    grouped_servers = {}
    
    # Группируем серверы по ID
    for server in servers:
        if server['id'] not in grouped_servers:
            grouped_servers[server['id']] = []
        grouped_servers[server['id']].append(server)
    
    # Создаем кнопки для каждого сервера
    for server_id, server_group in grouped_servers.items():
        first_server = server_group[0]
        
        # Считаем общее количество клиентов и максимум
        total_clients = sum(s['clients_count'] for s in server_group if s['protocol'])
        total_max = sum(s['max_clients'] for s in server_group if s['protocol'])
        
        # Собираем информацию о протоколах
        protocols_info = []
        for s in server_group:
            if s['protocol']:
                protocols_info.append(f"{s['protocol']}({s['clients_count']}/{s['max_clients']})")
        
        protocols_str = ", ".join(protocols_info) if protocols_info else "Нет данных"
        
        kb.button(
            text=(f"🖥 {first_server['address']} "
                  f"[{total_clients}/{total_max}] "
                  f"| {protocols_str}"),
            callback_data=f"upd_info_{server_id}"
        )
    
    kb.button(text="◀️ Назад", callback_data="servers_info")
    kb.adjust(1)

    await callback.message.edit_text(
        "🔄 <b>Выберите сервер для изменения информации:</b>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("upd_info_"))
async def update_server_info_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения информации о сервере
    """
    server_id = int(callback.data.split("_")[2])
    server = await get_server_by_id(server_id)

    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return

    # Получаем все инбаунды сервера
    inbounds = await get_server_inbounds(server['address'])
    await state.update_data(server_id=server_id, server_address=server['address'])

    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Изменить протокол", callback_data="change_protocol")
    kb.button(text="🌍 Изменить страну", callback_data="change_country")
    kb.button(text="🔢 Изменить ID", callback_data="change_id")
    kb.button(text="🔢 Изменить порт", callback_data="change_port")
    kb.button(text="🔢 Изменить SNI", callback_data="change_sni")
    kb.button(text="🔢 Изменить pbk", callback_data="change_pbk")
    kb.button(text="🔢 Изменить utls", callback_data="change_utls")
    kb.button(text="🔢 Изменить SID", callback_data="change_sid")
    kb.button(text="◀️ Назад", callback_data="update_server_info")
    kb.adjust(1)

    # Формируем информацию о протоколах
    protocols_info = ""
    if inbounds:
        for inbound in inbounds:
            protocols_info += (
                f"└ {inbound['protocol']} (ID: {inbound['inbound_id']})\n"
            )
    else:
        protocols_info = "Нет активных инбаундов\n"

    current_info = (
        f"🖥 <b>Сервер {server['address']}</b>\n\n"
        f"📡 Протоколы сервера:\n{protocols_info}\n"
        f"🌍 Страна: {server['country'] or 'Не указана'}"
    )

    await callback.message.edit_text(
        current_info,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "change_sid")
async def change_sid_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения SID
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} sid: {inbound['sid']}", 
            callback_data=f"change_sid_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔢 Выберите инбаунд для изменения SID:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("change_sid_inbound_"))
async def change_server_sid_input(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос нового SID после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="update_server_info")


    await callback.message.edit_text(
        "🔢 Введите новый SID для инбаунда:\n\n"
        "ℹ️ SID используется для inbound в панели управления.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_sid_update)

@router.message(AdminStates.waiting_server_sid_update)
async def process_server_sid_update(message: Message, state: FSMContext):
    """
    Обработка нового SID сервера
    """
    try:
        new_sid = message.text

        data = await state.get_data()
        inbound_id = data.get('selected_inbound_id')
        
        await update_inbound_sid(inbound_id, new_sid)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
        
        await message.answer(
            f"✅ SID инбаунда успешно изменен на {new_sid}",
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")


@router.callback_query(F.data == "change_utls")
async def change_utls_start(callback: types.CallbackQuery, state: FSMContext):
    """

    Начало процесса изменения utls
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} utls: {inbound['utls']}", 
            callback_data=f"change_utls_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔢 Выберите инбаунд для изменения utls:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("change_utls_inbound_"))
async def change_server_utls_input(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос нового utls после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="update_server_info")

    await callback.message.edit_text(
        "🔢 Введите новый utls для инбаунда:\n\n"
        "ℹ️ utls используется для inbound в панели управления.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_utls_update)

@router.message(AdminStates.waiting_server_utls_update)
async def process_server_utls_update(message: Message, state: FSMContext):
    """
    Обработка нового utls сервера
    """
    try:
        new_utls = message.text

        data = await state.get_data()
        inbound_id = data.get('selected_inbound_id')
        
        await update_inbound_utls(inbound_id, new_utls)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
        
        await message.answer(
            f"✅ utls инбаунда успешно изменен на {new_utls}",
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")


@router.callback_query(F.data == "change_pbk")
async def change_pbk_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения pbk
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} pbk: {inbound['pbk']}", 
            callback_data=f"change_pbk_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔢 Выберите инбаунд для изменения pbk:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("change_pbk_inbound_"))
async def change_server_pbk_input(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос нового pbk после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="update_server_info")

    await callback.message.edit_text(
        "🔢 Введите новый pbk для инбаунда:\n\n"
        "ℹ️ pbk используется для inbound в панели управления.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_pbk_update)

@router.message(AdminStates.waiting_server_pbk_update)
async def process_server_pbk_update(message: Message, state: FSMContext):
    """
    Обработка нового pbk сервера
    """
    try:
        new_pbk = message.text

        data = await state.get_data()
        inbound_id = data.get('selected_inbound_id')
        
        await update_inbound_pbk(inbound_id, new_pbk)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
        
        await message.answer(
            f"✅ pbk инбаунда успешно изменен на {new_pbk}",
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")



@router.callback_query(F.data == "change_sni")
async def change_sni_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения порта
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} SNI: {inbound['sni']}", 
            callback_data=f"change_sni_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔢 Выберите инбаунд для изменения SNI:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("change_sni_inbound_"))
async def change_server_sni_input(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос нового ID после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="update_server_info")

    await callback.message.edit_text(
        "🔢 Введите новый SNI для инбаунда:\n\n"
        "ℹ️ SNI используется для inbound в панели управления.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_sni_update)

@router.message(AdminStates.waiting_server_sni_update)
async def process_server_sni_update(message: Message, state: FSMContext):
    """
    Обработка нового ID сервера
    """
    try:
        new_sni = message.text

        data = await state.get_data()
        inbound_id = data.get('selected_inbound_id')
        
        await update_inbound_sni(inbound_id, new_sni)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
        
        await message.answer(
            f"✅ SNI инбаунда успешно изменен на {new_sni}",
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")


@router.callback_query(F.data == "change_port")
async def change_port_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения порта
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} порт: {inbound['port']}", 
            callback_data=f"change_port_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔢 Выберите инбаунд для изменения порта:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("change_port_inbound_"))
async def change_server_port_input(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос нового ID после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    
    await callback.message.edit_text(
        "🔢 Введите новый порт для инбаунда:\n\n"
        "ℹ️ порт используется для inbound в панели управления.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_port_update)

@router.message(AdminStates.waiting_server_port_update)
async def process_server_port_update(message: Message, state: FSMContext):
    """
    Обработка нового ID сервера
    """
    try:
        new_id = int(message.text)
        if new_id <= 0:
            await message.answer("❌ Порт должен быть положительным числом")
            return
            
        data = await state.get_data()
        inbound_id = data.get('selected_inbound_id')
        
        await update_inbound_port(inbound_id, new_id)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
        
        await message.answer(
            f"✅ Порт инбаунда успешно изменен на {new_id}",
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")

@router.callback_query(F.data == "change_id")
async def change_server_id_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения ID сервера - сначала выбор инбаунда
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} (ID: {inbound['inbound_id']})", 
            callback_data=f"change_id_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔢 Выберите инбаунд для изменения ID:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    

@router.callback_query(F.data.startswith("change_id_inbound_"))
async def change_server_id_input(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрос нового ID после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    
    await callback.message.edit_text(
        "🔢 Введите новый ID для инбаунда:\n\n"
        "ℹ️ ID используется для inbound в панели управления.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_id_update)

@router.message(AdminStates.waiting_server_id_update)
async def process_server_id_update(message: Message, state: FSMContext):
    """
    Обработка нового ID сервера
    """
    try:
        new_id = int(message.text)
        if new_id <= 0:
            await message.answer("❌ ID должен быть положительным числом")
            return
            
        data = await state.get_data()
        inbound_id = data.get('selected_inbound_id')
        
        await update_inbound_id(inbound_id, new_id)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
        
        await message.answer(
            f"✅ ID инбаунда успешно изменен на {new_id}",
            reply_markup=kb.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")

@router.callback_query(F.data == "change_protocol")
async def change_protocol_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения протокола - сначала выбор инбаунда
    """
    data = await state.get_data()
    server_address = data.get('server_address')
    
    inbounds = await get_server_inbounds(server_address)

    if not inbounds:
        await callback.answer("❌ Инбаунды не найдены", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for inbound in inbounds:
        kb.button(
            text=f"📡 {inbound['protocol']} (ID: {inbound['inbound_id']})", 
            callback_data=f"change_protocol_inbound_{inbound['id']}"
        )
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "📡 Выберите инбаунд для изменения протокола:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("change_protocol_inbound_"))
async def change_protocol_select(callback: types.CallbackQuery, state: FSMContext):
    """
    Выбор нового протокола после выбора инбаунда
    """
    inbound_id = int(callback.data.split("_")[3])
    await state.update_data(selected_inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    protocols = ["vless", "shadowsocks"]
    
    for protocol in protocols:
        kb.button(text=protocol, callback_data=f"set_protocol_{protocol}")
    
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(2)

    await callback.message.edit_text(
        "📡 Выберите новый протокол:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("set_protocol_"))
async def set_protocol(callback: types.CallbackQuery, state: FSMContext):
    """
    Установка нового протокола
    """
    protocol = callback.data.split("_")[2]
    data = await state.get_data()
    inbound_id = data.get('selected_inbound_id')

    await update_inbound_protocol(inbound_id, protocol)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
    
    await callback.message.edit_text(
        f"✅ Протокол успешно изменен на {protocol}",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data == "change_country")
async def change_country_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса изменения страны
    """
    kb = InlineKeyboardBuilder()
    countries = {
        "🇫🇷 Франция": "France",
        "🇩🇪 Германия": "Germany",
        "🇨🇿 Чехия": "Czechia",
        "🇳🇱 Нидерланды": "Netherlands",
        "🇰🇿 Казахстан": "Kazakhstan",
        "🇫🇷 Финляндия": "Finland",
    }
    
    for display_name, country_code in countries.items():
        kb.button(text=display_name, callback_data=f"set_country_{country_code}")
    
    kb.button(text="◀️ Отмена", callback_data="update_server_info")
    kb.adjust(2)

    await callback.message.edit_text(
        "🌍 Выберите страну расположения сервера:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("set_country_"))
async def set_country(callback: types.CallbackQuery, state: FSMContext):
    """
    Установка новой страны
    """
    country = callback.data.split("_")[2]
    data = await state.get_data()
    server_address = data.get('server_address')

    await update_server_info(server_address, None, country)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Вернуться к серверу", callback_data=f"upd_info_{data['server_id']}")
    
    await callback.message.edit_text(
        f"✅ Страна успешно изменена на {country}",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data == "update_servers")
async def update_servers(callback: types.CallbackQuery):
    """
    Показывает список серверов для обновления количества клиентов
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
        
    servers = await get_servers_with_total_clients()
    
    if not servers:
        await callback.answer("❌ Нет доступных серверов", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    for server in servers:
        kb.button(
            text=f"🖥 {server['address']} ({server['total_clients']}/{server['total_max_clients']})", 
            callback_data=f"update_server_{server['id']}"
        )
    kb.button(text="◀️ Назад", callback_data="servers_info")
    kb.adjust(1)
    
    await callback.message.edit_text(
        "🔄 <b>Выберите сервер для изменения лимита:</b>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("update_server_total_"))
async def update_server_total_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрашивает новое общее максимальное количество клиентов для сервера
    """
    server_id = int(callback.data.split("_")[3])
    servers = await get_all_servers()
    server_inbounds = [s for s in servers if s['id'] == server_id]
    
    if not server_inbounds:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    server = server_inbounds[0]
    inbounds_info = []
    total_clients = 0
    total_max = 0
    
    for inbound in server_inbounds:
        if inbound['protocol']:
            inbounds_info.append(
                f"📡 {inbound['protocol']}: {inbound['clients_count']}/{inbound['max_clients']}"
            )
            total_clients += inbound['clients_count']
            total_max += inbound['max_clients']
    
    await state.update_data(server_id=server_id, update_type="total")
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data=f"update_server_{server_id}")
    
    await callback.message.edit_text(
        f"🖥 <b>Сервер {server['address']}</b>\n\n"
        f"Текущие лимиты по инбаундам:\n"
        f"{chr(10).join(inbounds_info)}\n\n"
        f"Общий лимит: {total_max}\n"
        f"Общее количество: {total_clients}\n\n"
        f"Введите новое общее максимальное количество клиентов:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    
    await state.set_state(AdminStates.waiting_server_max_clients_update)

@router.callback_query(F.data.startswith("update_server_inbound_"))
async def update_server_inbound_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Показывает список инбаундов для выбора
    """
    server_id = int(callback.data.split("_")[3])
    servers = await get_all_servers()
    server_inbounds = [s for s in servers if s['id'] == server_id]
    
    if not server_inbounds:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    kb = InlineKeyboardBuilder()
    for inbound in server_inbounds:
        if inbound['protocol']:
            kb.button(
                text=f"📡 {inbound['protocol']} ({inbound['clients_count']}/{inbound['max_clients']})", 
                callback_data=f"update_inbound_{server_id}_{inbound['inbound_id']}"
            )
    kb.button(text="◀️ Отмена", callback_data=f"update_server_{server_id}")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"🖥 <b>Сервер {server_inbounds[0]['address']}</b>\n\n"
        "Выберите инбаунд для изменения лимита:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("update_inbound_"))
async def update_inbound_max_clients_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Запрашивает новое максимальное количество клиентов для выбранного инбаунда
    """
    _, server_id, inbound_id = callback.data.split("_")[1:]
    server_id = int(server_id)
    inbound_id = int(inbound_id)
    
    servers = await get_all_servers()
    inbound = next((s for s in servers if s['id'] == server_id and s['inbound_id'] == inbound_id), None)
    
    if not inbound:
        await callback.answer("❌ Инбаунд не найден", show_alert=True)
        return
    
    await state.update_data(server_id=server_id, inbound_id=inbound_id)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data=f"update_server_{server_id}")
    
    await callback.message.edit_text(
        f"🖥 <b>Сервер {inbound['address']}</b>\n"
        f"📡 <b>Инбаунд {inbound['protocol']}</b>\n\n"
        f"Текущий лимит: {inbound['max_clients']}\n"
        f"Текущее количество: {inbound['clients_count']}\n\n"
        f"Введите новое максимальное количество клиентов:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    
    await state.set_state(AdminStates.waiting_server_max_clients_update)

@router.callback_query(F.data.startswith("update_server_"))
async def update_server_max_clients_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Показывает меню выбора: обновить лимит сервера или конкретного инбаунда
    """
    # Проверяем, не является ли это другим callback'ом
    if callback.data.startswith("update_server_total_") or callback.data.startswith("update_server_inbound_"):
        return
        
    server_id = int(callback.data.split("_")[2])
    servers = await get_all_servers()
    server_inbounds = [s for s in servers if s['id'] == server_id]
    
    if not server_inbounds:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return
    
    server = server_inbounds[0]
    total_clients = sum(s['clients_count'] for s in server_inbounds if s['protocol'])
    total_max = sum(s['max_clients'] for s in server_inbounds if s['protocol'])
    
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🖥 Изменить общий лимит сервера", 
        callback_data=f"update_server_total_{server_id}"
    )
    kb.button(
        text="📡 Изменить лимит конкретного инбаунда", 
        callback_data=f"update_server_inbound_{server_id}"
    )
    kb.button(text="◀️ Отмена", callback_data="update_servers")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"🖥 <b>Сервер {server['address']}</b>\n\n"
        f"Общее количество клиентов: {total_clients}\n"
        f"Общий лимит: {total_max}\n\n"
        "Выберите действие:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.message(AdminStates.waiting_server_max_clients_update)
async def process_server_max_clients_update(message: Message, state: FSMContext):
    """
    Обрабатывает введенное новое максимальное количество клиентов
    """
    try:
        new_max_clients = int(message.text)
        if new_max_clients <= 0:
            await message.answer("❌ Количество клиентов должно быть положительным числом")
            return
            
        data = await state.get_data()
        server_id = data['server_id']
        update_type = data.get('update_type')
        inbound_id = data.get('inbound_id')
        
        servers = await get_all_servers()
        server_inbounds = [s for s in servers if s['id'] == server_id]
        
        if update_type == "total":
            # Получаем только активные инбаунды
            active_inbounds = [s for s in server_inbounds if s['protocol']]
            
            # Сначала проверяем общее количество клиентов
            total_clients = sum(inbound['clients_count'] for inbound in active_inbounds)
            if total_clients > new_max_clients:
                clients_info = "\n".join(
                    f"📡 {inbound['protocol']}: {inbound['clients_count']} клиентов"
                    for inbound in active_inbounds
                )
                await message.answer(
                    f"❌ Новый общий лимит ({new_max_clients}) меньше текущего количества клиентов ({total_clients})!\n\n"
                    f"Текущее распределение:\n{clients_info}"
                )
                return
            
            # Если проверка прошла, распределяем новый лимит
            inbound_count = len(active_inbounds)
            if inbound_count > 0:
                per_inbound = new_max_clients // inbound_count
                remaining = new_max_clients % inbound_count
                
                # Обновляем лимиты для всех инбаундов
                for i, inbound in enumerate(active_inbounds):
                    current_max = per_inbound + (1 if i < remaining else 0)
                    await update_inbound_max_clients(server_id, inbound['inbound_id'], current_max)
        else:
            # Обработка для конкретного инбаунда остается без изменений
            inbound = next((s for s in server_inbounds if s['inbound_id'] == inbound_id), None)
            if inbound['clients_count'] > new_max_clients:
                await message.answer(
                    "❌ Новый лимит меньше текущего количества клиентов!\n"
                    f"Текущее количество: {inbound['clients_count']}"
                )
                return
            await update_inbound_max_clients(server_id, inbound_id, new_max_clients)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="◀️ Вернуться к серверу", callback_data=f"update_server_{server_id}")
        
        success_message = (
            "✅ Лимит клиентов успешно обновлен!\n\n"
            f"🖥 Сервер: {server_inbounds[0]['address']}\n"
        )
        if update_type == "total":
            per_inbound_info = "\n".join(
                f"📡 {inbound['protocol']}: {per_inbound + (1 if i < remaining else 0)}"
                for i, inbound in enumerate(active_inbounds)
            )
            success_message += (
                f"📊 Новый общий лимит: {new_max_clients}\n"
                f"Распределение по инбаундам:\n{per_inbound_info}"
            )
        else:
            inbound = next((s for s in server_inbounds if s['inbound_id'] == inbound_id), None)
            success_message += (
                f"📡 Инбаунд: {inbound['protocol']}\n"
                f"📊 Новый лимит: {new_max_clients}"
            )
        
        await message.answer(success_message, reply_markup=kb.as_markup())
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число")    

@router.callback_query(F.data == "add_server")
async def add_server_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса добавления сервера
    """
    user = await get_user(callback.from_user.id)
    if not user.get('is_admin'):
        await callback.answer("⛔️ У вас нет доступа", show_alert=True)
        return
        
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await callback.message.edit_text(
        "🖥 <b>Добавление нового сервера</b>\n\n"
        "Введите адрес сервера:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_server_address)

@router.message(AdminStates.waiting_server_address)
async def process_server_address(message: types.Message, state: FSMContext):
    """
    Обработка адреса сервера
    """
    await state.update_data(address=message.text)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await message.answer(
        "👤 Введите логин для сервера:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminStates.waiting_server_username)

@router.message(AdminStates.waiting_server_username)
async def process_server_username(message: types.Message, state: FSMContext):
    """
    Обработка логина сервера
    """
    await state.update_data(username=message.text)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await message.answer(
        "🔑 Введите пароль для сервера:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminStates.waiting_server_password)

@router.message(AdminStates.waiting_server_password)
async def process_server_password(message: types.Message, state: FSMContext):
    """
    Обработка пароля сервера
    """
    await state.update_data(password=message.text)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="admin_back")
    
    await message.answer(
        "🔢 Введите максимальное количество клиентов:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(AdminStates.waiting_server_max_clients)

@router.message(AdminStates.waiting_server_max_clients)
async def process_server_max_clients(message: types.Message, state: FSMContext):
    """
    Завершение добавления сервера
    """
    try:
        max_clients = int(message.text)
        if max_clients <= 0:
            raise ValueError
            
        data = await state.get_data()
        
        await add_server(
            address=data['address'],
            username=data['username'],
            password=data['password'],
            max_clients=max_clients
        )
        
        await message.answer(
            "✅ Сервер успешно добавлен!\n\n"
            f"📍 Адрес: {data['address']}\n"
            f"👤 Логин: {data['username']}\n"
            f"🔢 Макс. клиентов: {max_clients}"
        )
        
    except ValueError:
        await message.answer("❌ Введите корректное число клиентов!")
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении сервера: {str(e)}")
        return
    
    await state.clear()
    await admin_menu(message)

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: types.CallbackQuery, state: FSMContext):
    """
    Возврат в админ-меню
    """
    await state.clear()
    await callback.message.answer("Состояние сброшено. Отправьте команду /admin для доступа к админ-панели")
    await callback.message.delete()
