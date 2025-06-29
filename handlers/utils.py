# handlers.utils.py
import logging
import random
import re
import string
from aiogram import Bot

logger = logging.getLogger(__name__)

def extract_key_data(key: str):
    """
    Извлекает данные из VPN ключа

    Args:
        key (str): VPN ключ в формате:
            vless://uuid@ip:port...#Atlanta VPN-device_unique_id_*
            или
            ss://base64@ip:port...#Atlanta VPN-device_unique_id_*

    Returns:
        tuple: (device, unique_id, unique_uuid, ip_address, parts)
    """
    
    try:
        # Декодируем URL-encoded символы
        decoded_key = key.replace("%20", " ")

        # Извлекаем часть после #
        name_part = decoded_key.split("#")[-1]
        if "VPN-" in name_part:
            identifier_part = name_part.split("VPN-")[1]
        else:
            identifier_part = name_part

        parts = []
        first_parts = identifier_part.split("_", 2)
        parts.extend(first_parts)

        device = parts[0]  # ios
        unique_id = parts[1]  # WFfq/C3iO

        # Определяем тип ключа
        if key.startswith("vless://"):
            # Извлекаем UUID для VLESS
            uuid_pattern = r"vless://([0-9a-f-]+)@"
            uuid_match = re.search(uuid_pattern, decoded_key)
            unique_uuid = uuid_match.group(1) if uuid_match else None
        else:
            # Извлекаем base64 часть для Shadowsocks
            ss_pattern = r"ss://([A-Za-z0-9+/=]+)@"
            ss_match = re.search(ss_pattern, decoded_key)
            unique_uuid = ss_match.group(1) if ss_match else None

        # Извлекаем IP адрес (паттерн работает для обоих типов)
        ip_pattern = r"@([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+):"
        ip_match = re.search(ip_pattern, decoded_key)
        ip_address = ip_match.group(1) if ip_match else None

        # Отладочная информация
        logger.info(f"""
            Key type: {'VLESS' if key.startswith('vless://') else 'Shadowsocks'}
            Decoded key: {decoded_key}
            Name part: {name_part}
            Identifier part: {identifier_part}
            Parts: {parts}
            Device: {device}
            Unique ID: {unique_id}
            UUID/Base64: {unique_uuid}
            IP: {ip_address}
        """)

        return device, unique_id, unique_uuid, ip_address, parts

    except Exception as e:
        logger.error(f"Ошибка при извлечении данных из ключа: {e}")
        logger.error(f"Полный ключ: {key}")
        return None, None, None, None, None

def generate_random_string(length=4):
    """
    Генерирует случайную строку заданной длины
    """
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

async def send_channel_log(bot: Bot, channel_from: str, username: str):
    """
    Логирует переходы пользователей из определенного канала в соответствующий топик.
    
    Args:
        bot (Bot): Экземпляр бота
        channel_from (str): Название канала, откуда пришел пользователь
        username (str): Имя пользователя, который перешел
    """
    log_channel = "@reklama228ESS"  # Ваш лог-канал
    from handlers.database import get_forum_topic_id, save_forum_topic_id
    try:
        # Проверяем существование топика для данного канала
        topic_data = await get_forum_topic_id(channel_from)
        
        if topic_data:
            # Используем существующий топик
            topic_id, _ = topic_data
        else:
            # Создаем новый топик для канала
            try:
                new_topic = await bot.create_forum_topic(
                    chat_id=log_channel,
                    name=f"Канал: {channel_from}",
                    icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F])
                )
                topic_id = new_topic.message_thread_id
                
                # Сохраняем топик в базу
                await save_forum_topic_id(channel_from, topic_id, log_channel)
                logger.info(f"Создан новый топик для канала {channel_from}")
            except Exception as e:
                logger.error(f"Ошибка при создании топика для канала {channel_from}: {e}")
                return
        
        # Отправляем сообщение в соответствующий топик
        await bot.send_message(
            chat_id=log_channel,
            text=f"Пользователь @{username} перешел из канала {channel_from}",
            message_thread_id=topic_id
        )
        
    except Exception as e:
        logger.error(f"Ошибка при отправке лога: {e}")

async def send_info_for_admins(message: str, admins: list, bot: Bot, username: str = None):
    """
    Отправляет информацию в один из лог-каналов с разбиением на страницы.
    Использует существующую ветку обсуждения для пользователя или создает новую.
    Распределяет логи по нескольким каналам для избежания лимитов Telegram.
    
    Args:
        message (str): Текст сообщения для отправки
        admins (list): Список ID администраторов (не используется для отправки)
        bot (Bot): Экземпляр бота для отправки сообщений
        username (str, optional): Имя пользователя для названия топика
        
    Returns:
        None
        
    Complexity:
        Временная: O(n), где n - количество частей сообщения
        Пространственная: O(n)
    """
    from handlers.database import get_forum_topic_id, save_forum_topic_id
    MAX_LENGTH = 1024
    parts = [message[i : i + MAX_LENGTH] for i in range(0, len(message), MAX_LENGTH)]
    
    # Список доступных лог-каналов
    log_channels = [
        "@atlanta_logsss",
        "@atlanta_logsss2",
        "@atlanta_logsss3",
        "@atlanta_logsss4",
        "@atlanta_logsss5"
    ]
    
    # Определяем ключ для поиска топика
    cache_key = username if username else "system"
    topic_id = None
    log_channel = None
    
    try:
        # Проверяем, есть ли уже топик для этого пользователя в БД
        topic_data = await get_forum_topic_id(cache_key)
        
        if topic_data:
            # Если есть данные о топике, распаковываем их
            topic_id, log_channel = topic_data
            logger.info(f"Используем существующий топик с ID: {topic_id} в канале {log_channel} для {cache_key}")
        else:
            # Если топика нет, выбираем случайный канал
            log_channel = random.choice(log_channels)
            logger.info(f"Выбран канал {log_channel} для нового топика пользователя {cache_key}")
            
            # Создаем заголовок для темы форума на основе юзернейма
            topic_title = username if username else "Системное сообщение"
            
            try:
                # Создаем новую тему в форуме
                new_topic = await bot.create_forum_topic(
                    chat_id=log_channel,
                    name=topic_title,
                    icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F])
                )
                topic_id = new_topic.message_thread_id
                
                # Сохраняем ID топика и канал в БД
                await save_forum_topic_id(cache_key, topic_id, log_channel)
                logger.info(f"Создана новая тема форума с ID: {topic_id} в канале {log_channel} для {cache_key}")
            except Exception as e:
                # Если не удалось создать тему, пробуем другой канал
                logger.error(f"Не удалось создать тему форума в канале {log_channel}: {e}")
                
                # Пробуем другие каналы по очереди
                for alt_channel in log_channels:
                    if alt_channel != log_channel:
                        try:
                            new_topic = await bot.create_forum_topic(
                                chat_id=alt_channel,
                                name=topic_title,
                                icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F])
                            )
                            topic_id = new_topic.message_thread_id
                            log_channel = alt_channel
                            
                            # Сохраняем ID топика и канал в БД
                            await save_forum_topic_id(cache_key, topic_id, log_channel)
                            logger.info(f"Создана новая тема форума с ID: {topic_id} в альтернативном канале {log_channel} для {cache_key}")
                            break
                        except Exception as alt_e:
                            logger.error(f"Не удалось создать тему форума в альтернативном канале {alt_channel}: {alt_e}")
                
                # Если не удалось создать тему ни в одном канале
                if topic_id is None:
                    logger.error("Не удалось создать тему форума ни в одном из каналов")
                    # Отправляем сообщение без указания thread_id в первый канал
                    log_channel = log_channels[0]
                    topic_id = None
        
        # Отправка сообщения в одной части
        if len(parts) == 1:
            try:
                await bot.send_message(
                    chat_id=log_channel, 
                    text=message,
                    message_thread_id=topic_id
                )
            except Exception as e:
                # Если не удалось отправить в существующий топик (возможно, он был удален)
                if "Bad Request: message thread not found" in str(e):
                    # Удаляем запись о топике из БД
                    from handlers.database import delete_forum_topic
                    await delete_forum_topic(cache_key)
                    
                    # Пробуем создать новый топик и отправить сообщение
                    await send_info_for_admins(message, admins, bot, username)
                else:
                    logger.error(f"Ошибка при отправке сообщения в канал {log_channel}: {e}")
            return
        
        # Отправка сообщения по частям
        for index, part in enumerate(parts):
            page_info = f"\n\nСтраница {index + 1}/{len(parts)}"
            
            try:
                await bot.send_message(
                    chat_id=log_channel, 
                    text=part + page_info,
                    message_thread_id=topic_id
                )
            except Exception as e:
                # Если не удалось отправить в существующий топик при первой части
                if index == 0 and "Bad Request: message thread not found" in str(e):
                    # Удаляем запись о топике из БД
                    from handlers.database import delete_forum_topic
                    await delete_forum_topic(cache_key)
                    
                    # Пробуем создать новый топик и отправить сообщение
                    await send_info_for_admins(message, admins, bot, username)
                    return
                else:
                    logger.error(f"Ошибка при отправке части {index+1} в канал {log_channel}: {e}")
                    
    except Exception as e:
        logger.error(f"Ошибка при отправке в лог-канал: {e}")



    