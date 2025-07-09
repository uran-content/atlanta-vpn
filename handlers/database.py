# handlers.database.py
import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import aiohttp
import aiosqlite
from aiogram.types import FSInputFile
from aiohttp.client_exceptions import ClientError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from py3xui import AsyncApi

from handlers.utils import extract_key_data, unix_to_str
from config import NEW_LOGIN, NEW_PASSWORD

logger = logging.getLogger(__name__)

DB_PATH = 'local_database.db'
BACKUP_DIR = 'database_backups'
_bot_instance = None

def set_bot_instance(bot):
    """Устанавливает экземпляр бота для использования в бэкапах"""
    global _bot_instance
    _bot_instance = bot

async def create_database_backup():
    """
    Создает резервную копию базы данных и отправляет её админам
    """
    try:
        # Создаем директорию для бэкапов если её нет
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
            
        # Формируем имя файла бэкапа с текущей датой и временем
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = os.path.join(BACKUP_DIR, f'database_backup_{timestamp}.db')
        
        # Создаем атомарную копию базы
        async with aiosqlite.connect(DB_PATH) as source_db:
            # Ждем завершения всех транзакций
            await source_db.execute('PRAGMA wal_checkpoint(FULL)')
            
            # Создаем бэкап
            shutil.copy2(DB_PATH, backup_path)
            
            logger.info(f"Created database backup: {backup_path}")
            
            # Получаем список админов и отправляем им бэкап
            if _bot_instance:
                admins = await get_admins()
                for admin_id in admins:
                    try:
                        # Создаем FSInputFile из пути к файлу
                        document = FSInputFile(backup_path)
                        await _bot_instance.send_document(
                            chat_id=admin_id,
                            document=document,
                            caption=f"Database backup {timestamp}"
                        )
                        logger.info(f"Sent backup to admin {admin_id}")
                    except Exception as e:
                        logger.error(f"Failed to send backup to admin {admin_id}: {e}")
            
            # Очищаем старые бэкапы (оставляем только за последние 24 часа)
            await cleanup_old_backups()
            
    except Exception as e:
        logger.error(f"Failed to create database backup: {e}")

async def cleanup_old_backups():
    """
    Удаляет бэкапы старше 24 часов
    """
    try:
        current_time = datetime.now()
        backup_dir = Path(BACKUP_DIR)
        
        if not backup_dir.exists():
            return
            
        for backup_file in backup_dir.glob('database_backup_*.db'):
            try:
                # Получаем время создания файла из имени
                timestamp_str = backup_file.stem.split('_')[2]
                file_time = datetime.strptime(timestamp_str, '%Y%m%d')
                
                # Удаляем файлы старше 24 часов
                if current_time - file_time > timedelta(hours=24):
                    backup_file.unlink()
                    logger.info(f"Deleted old backup: {backup_file}")
            except Exception as e:
                logger.error(f"Error processing backup file {backup_file}: {e}")
                
    except Exception as e:
        logger.error(f"Error cleaning up old backups: {e}")

async def init_db():
    db_exists = os.path.exists(DB_PATH)

    async with aiosqlite.connect(DB_PATH) as db:
        if not db_exists:
            print(f"База данных {DB_PATH} не существует. Создаем новую базу данных.")
        else:
            print(f"База данных {DB_PATH} уже существует. Проверяем и обновляем структуру.")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS card_cancel_stats (
                user_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                email TEXT,
                balance INTEGER DEFAULT 0,
                subscription_type TEXT,
                subscription_end TEXT,
                is_admin INTEGER DEFAULT 0,
                referrer_id INTEGER DEFAULT 0,
                referral_count INTEGER DEFAULT 0,
                keys_count INTEGER DEFAULT 0,
                free_keys_count INTEGER DEFAULT 1,
                promo_days INTEGER DEFAULT 3,
                from_channel TEXT DEFAULT NULL,
                pay_count INTEGER DEFAULT 0
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key TEXT PRIMARY KEY,
                user_id INTEGER,
                device_id TEXT,
                expiration_date TEXT,
                price INTEGER NOT NULL,
                days INTEGER NOT NULL,
                payment_id TEXT,
                name TEXT DEFAULT NULL
            )
        """)


        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                country TEXT,
                max_clients INTEGER DEFAULT 100,
                is_active INTEGER DEFAULT 1
            )
        """)


        await db.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                user_id INTEGER,
                amount INTEGER,
                gift_balance INTEGER,
                gift_days INTEGER,
                expiration_date TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS used_promocodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                promocode TEXT,
                used_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL,
                transaction_id TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        

        await db.execute("""
            CREATE TABLE IF NOT EXISTS payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                payment_method_id TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                payment_method_id TEXT NOT NULL,
                issuer_name TEXT NOT NULL,
                title TEXT NOT NULL,
                when_valid TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создаем таблицу для хранения топиков форума
        await db.execute("""
            CREATE TABLE IF NOT EXISTS forum_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                topic_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS key_usage_reminders (
                key TEXT PRIMARY KEY,
                last_traffic INTEGER DEFAULT 0,
                first_reminder_sent INTEGER DEFAULT 0,
                second_reminder_sent INTEGER DEFAULT 0,
                third_reminder_sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(key) REFERENCES keys(key) ON DELETE CASCADE
            )
        """)
        await update_server_credentials(NEW_LOGIN, NEW_PASSWORD)
        #await add_channel_column_to_forum_topics()
        await db.commit()
        
        await _ensure_column_exists(db, "keys", "price", "INTEGER")
        await _ensure_column_exists(db, "keys", "days", "INTEGER")
        await _ensure_column_exists(db, "user_payment_methods", "when_valid", "TEXT")

    print("Инициализация базы данных завершена.")
    # await cleanup_expired_keys()
    await sync_server_clients_count()
    await add_name_column_to_keys()
    #await add_payment_id_column()
    #await add_pay_count_column()
    #await add_channel_column_to_users()
    #await migrate_servers_data()
    #await add_inbound_columns()

async def _ensure_column_exists(
    db: aiosqlite.Connection, table: str, column: str, col_type: str
):
    """Добавляет колонку, если её нет (SQLite не поддерживает IF NOT EXISTS)."""
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        cols = [row[1] async for row in cursor]
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")

async def add_name_column_to_keys():
    """
    Adds a name column to the keys table if it doesn't exist
    
    Returns:
        bool: True if column was added or already exists, False if error occurred
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Check if column exists
            cursor = await db.execute("PRAGMA table_info(keys)")
            columns = await cursor.fetchall()
            
            # Check if name column exists
            if not any(column[1] == 'name' for column in columns):
                # Add column if it doesn't exist
                await db.execute("""
                    ALTER TABLE keys 
                    ADD COLUMN name TEXT DEFAULT NULL
                """)
                await db.commit()
                logger.info("Column 'name' successfully added to keys table")
            else:
                logger.info("Column 'name' already exists in keys table")
            
            return True
            
    except Exception as e:
        logger.error(f"Error adding name column to keys table: {e}")
        return False


async def add_payment_id_column():
    """
    Добавляет колонку payment_id в таблицу keys, если она еще не существует.
    
    Returns:
        bool: True, если колонка успешно добавлена или уже существует, False в случае ошибки
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем, существует ли уже колонка payment_id в таблице keys
            cursor = await db.execute("PRAGMA table_info(keys)")
            columns = await cursor.fetchall()
            
            # Ищем колонку payment_id в списке колонок
            payment_id_exists = False
            for column in columns:
                if column[1] == 'payment_id':
                    payment_id_exists = True
                    break
            
            # Если колонки нет, добавляем её
            if not payment_id_exists:
                await db.execute("ALTER TABLE keys ADD COLUMN payment_id TEXT")
                await db.commit()
                logger.info("Колонка payment_id успешно добавлена в таблицу keys")
            else:
                logger.info("Колонка payment_id уже существует в таблице keys")
                
            return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении колонки payment_id: {e}")
        return False

async def add_payment_method(user_id: int, payment_method_id: str, issuer_name: str, title: str, days_delay: int):
    """
    Добавляет новый метод оплаты для пользователя
    
    Args:
        user_id (int): ID пользователя
        amount (int): Сумма платежа
        payment_method_id (str): Идентификатор метода оплаты
        
    Returns:
        int: ID добавленного метода оплаты или None в случае ошибки
    """
    date = datetime.now(tz=timezone.utc) + timedelta(days=days_delay)
    timestamp_ms = int(date.timestamp() * 1000)

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем, существует ли запись
            cursor = await db.execute("""
                SELECT id FROM user_payment_methods WHERE user_id = ? AND payment_method_id = ?
            """, (user_id, payment_method_id))
            row = await cursor.fetchone()
            if row:
                # Запись существует, обновляем её
                method_id = row[0]
                await db.execute("""
                    UPDATE user_payment_methods
                    SET issuer_name = ?, title = ?, when_valid = ?
                    WHERE id = ?
                """, (issuer_name, title, timestamp_ms, method_id))
            else:
                # Записи нет, вставляем новую
                cursor = await db.execute("""
                    INSERT INTO user_payment_methods (user_id, payment_method_id, issuer_name, title, when_valid)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, payment_method_id, issuer_name, title, timestamp_ms))
                method_id = cursor.lastrowid
            await db.commit()
            return method_id
    except Exception as e:
        logger.error(f"Ошибка при добавлении/обновлении метода оплаты: {e}")
        return None

async def get_user_payment_methods(user_id: int, include_balance: bool = False):
    """
    Получает все методы оплаты пользователя
    
    Args:
        user_id (int): ID пользователя
        
    Returns:
        list: Список словарей с информацией о методах оплаты
    """
    try:
        current_timestamp_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, user_id, payment_method_id, issuer_name, title, created_at
                FROM user_payment_methods
                WHERE user_id = ? AND when_valid <= ?
                ORDER BY created_at DESC
            """, (user_id, current_timestamp_ms))
            methods = await cursor.fetchall()
            methods = [dict(row) for row in methods]

            if include_balance:
                cursor = await db.execute("""
                    SELECT balance
                    FROM users
                    WHERE user_id = ?
                """, (user_id,))
                row = await cursor.fetchone()
                balance = int(row['balance']) if row else 0
                return methods, balance

            return methods
    except Exception as e:
        logger.error(f"Ошибка при получении методов оплаты пользователя: {e}")
        return []

async def get_payment_method_by_id(method_id: int):
    """
    Получает информацию о методе оплаты по его ID
    
    Args:
        method_id (int): ID метода оплаты
        
    Returns:
        dict: Словарь с информацией о методе оплаты или None если метод не найден
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, user_id, payment_method_id, issuer_name, title, created_at
                FROM user_payment_methods
                WHERE id = ?
            """, (method_id,))
            method = await cursor.fetchone()
            return dict(method) if method else None
    except Exception as e:
        logger.error(f"Ошибка при получении метода оплаты: {e}")
        return None

async def delete_payment_method(method_id: int):
    """
    Удаляет метод оплаты по его ID
    
    Args:
        method_id (int): ID метода оплаты
        
    Returns:
        bool: True если метод успешно удален, False в противном случае
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                DELETE FROM user_payment_methods
                WHERE id = ?
            """, (method_id,))
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при удалении метода оплаты: {e}")
        return False
    
async def delete_payment_method_by_id(user_id: str | int, payment_method_id: str):
    """
    Удаляет метод оплаты по его ID
    
    Args:
        method_id (int): ID метода оплаты
        
    Returns:
        bool: True если метод успешно удален, False в противном случае
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                DELETE FROM user_payment_methods
                WHERE user_id = ? AND payment_method_id = ?
            """, (user_id, payment_method_id))
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при удалении метода оплаты: {e}")
        return False


async def get_payment_method_by_payment_id(payment_method_id: str):
    """
    Получает информацию о методе оплаты по его внешнему идентификатору
    
    Args:
        payment_method_id (str): Внешний идентификатор метода оплаты
        
    Returns:
        dict: Словарь с информацией о методе оплаты или None если метод не найден
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, user_id, payment_method_id, issuer_name, title, created_at
                FROM user_payment_methods
                WHERE payment_method_id = ?
            """, (payment_method_id,))
            method = await cursor.fetchone()
            return dict(method) if method else None
    except Exception as e:
        logger.error(f"Ошибка при получении метода оплаты по payment_method_id: {e}")
        return None


##каналы

async def update_server_credentials(new_username: str, new_password: str, server_id: int = None):
    """
    Срочно обновляет учетные данные (логин и пароль) для серверов.
    
    Args:
        new_username (str): Новый логин для серверов
        new_password (str): Новый пароль для серверов
        server_id (int, optional): ID конкретного сервера для обновления.
                                  Если None, обновляет все серверы.
    
    Returns:
        dict: Словарь с результатами операции:
            {
                'success': bool,  # Успешность операции
                'updated_count': int,  # Количество обновленных серверов
                'message': str  # Сообщение о результате
            }
    """
    try:
        if not new_username or not new_password:
            return {
                'success': False,
                'updated_count': 0,
                'message': "Логин и пароль не могут быть пустыми"
            }
            
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('BEGIN EXCLUSIVE TRANSACTION'):
                if server_id is not None:
                    # Обновляем конкретный сервер
                    await db.execute(
                        "UPDATE servers SET username = ?, password = ? WHERE id = ?",
                        (new_username, new_password, server_id)
                    )
                    
                    # Проверяем, был ли сервер обновлен
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM servers WHERE id = ?", 
                        (server_id,)
                    )
                    count = (await cursor.fetchone())[0]
                    
                    if count == 0:
                        return {
                            'success': False,
                            'updated_count': 0,
                            'message': f"Сервер с ID {server_id} не найден"
                        }
                        
                    await db.commit()
                    logger.warning(f"Экстренное обновление учетных данных для сервера ID {server_id}")
                    return {
                        'success': True,
                        'updated_count': 1,
                        'message': f"Учетные данные для сервера ID {server_id} успешно обновлены"
                    }
                else:
                    # Обновляем все серверы
                    await db.execute(
                        "UPDATE servers SET username = ?, password = ?",
                        (new_username, new_password)
                    )
                    
                    # Получаем количество обновленных серверов
                    cursor = await db.execute("SELECT COUNT(*) FROM servers")
                    count = (await cursor.fetchone())[0]
                    
                    await db.commit()
                    logger.warning(f"Экстренное обновление учетных данных для ВСЕХ серверов ({count})")
                    return {
                        'success': True,
                        'updated_count': count,
                        'message': f"Учетные данные для {count} серверов успешно обновлены"
                    }
                    
    except Exception as e:
        logger.error(f"Ошибка при обновлении учетных данных серверов: {e}", exc_info=True)
        return {
            'success': False,
            'updated_count': 0,
            'message': f"Произошла ошибка: {str(e)}"
        }

async def get_channel_statistics():
    """
    Получает статистику по количеству пользователей из разных каналов
    
    Returns:
        list: Список словарей с информацией о каналах:
            [
                {
                    'channel': str,  # Название канала
                    'users_count': int,  # Количество пользователей
                    'percentage': float  # Процент от общего числа пользователей
                },
                ...
            ]
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем общее количество пользователей
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cursor.fetchone())[0]
            
            # Получаем статистику по каналам
            query = """
                SELECT 
                    COALESCE(from_channel, 'Неизвестно') as channel,
                    COUNT(*) as users_count
                FROM users 
                GROUP BY from_channel
                ORDER BY users_count DESC, channel
            """
            
            cursor = await db.execute(query)
            channels = await cursor.fetchall()
            
            # Формируем результат с процентами
            statistics = []
            for channel, count in channels:
                percentage = (count / total_users * 100) if total_users > 0 else 0
                statistics.append({
                    'channel': channel,
                    'users_count': count,
                    'percentage': round(percentage, 2)
                })
            
            logger.info(f"Получена статистика по {len(statistics)} каналам")
            return statistics
            
    except Exception as e:
        logger.error(f"Ошибка при получении статистики по каналам: {e}")
        return []

async def add_channel_column_to_users():
    """
    Добавляет колонку from_channel в таблицу users, если её нет
    
    Returns:
        bool: True если колонка добавлена или уже существует, False если произошла ошибка
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем существование колонки
            cursor = await db.execute("PRAGMA table_info(users)")
            columns = await cursor.fetchall()
            
            # Проверяем, есть ли колонка from_channel
            if not any(column[1] == 'from_channel' for column in columns):
                # Если колонки нет, добавляем её
                await db.execute("""
                    ALTER TABLE users 
                    ADD COLUMN from_channel TEXT DEFAULT NULL
                """)
                await db.commit()
                logger.info("Колонка from_channel успешно добавлена в таблицу users")
            else:
                logger.info("Колонка from_channel уже существует в таблице users")
            
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении колонки from_channel: {e}")
        return False

async def add_pay_count_column():
    """
    Добавляет колонку pay_count в таблицу users для подсчета успешных транзакций
    
    Returns:
        bool: True если колонка добавлена или уже существует, False если произошла ошибка
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем существование колонки
            cursor = await db.execute("PRAGMA table_info(users)")
            columns = await cursor.fetchall()
            
            # Проверяем, есть ли колонка pay_count
            if not any(column[1] == 'pay_count' for column in columns):
                # Если колонки нет, добавляем её
                await db.execute("""
                    ALTER TABLE users 
                    ADD COLUMN pay_count INTEGER DEFAULT 0
                """)
                await db.commit()
                logger.info("Колонка pay_count успешно добавлена в таблицу users")
            else:
                logger.info("Колонка pay_count уже существует в таблице users")
            
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении колонки pay_count: {e}")
        return False

async def update_user_pay_count(user_id: int = None):
    """
    Обновляет количество успешных транзакций у пользователя или всех пользователей
    
    Args:
        user_id (int, optional): ID пользователя. Если None, обновляет для всех пользователей
    
    Returns:
        bool: True если обновление успешно, False если произошла ошибка
        
    Raises:
        ValueError: Если user_id отрицательный
    """
    try:
        if user_id is not None and user_id < 0:
            raise ValueError("User ID не может быть отрицательным")
            
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('BEGIN EXCLUSIVE TRANSACTION'):
                if user_id:
                    # Проверяем существование пользователя
                    cursor = await db.execute(
                        "SELECT 1 FROM users WHERE user_id = ?", 
                        (user_id,)
                    )
                    if not await cursor.fetchone():
                        logger.warning(f"Пользователь {user_id} не найден")
                        return False
                        
                    # Обновляем pay_count для конкретного пользователя
                    await db.execute("""
                        UPDATE users 
                        SET pay_count = (
                            SELECT COUNT(*) 
                            FROM user_transactions 
                            WHERE user_id = ? 
                            AND status = 'succeeded'
                        )
                        WHERE user_id = ?
                    """, (user_id, user_id))
                    
                    # Проверяем успешность обновления
                    cursor = await db.execute(
                        "SELECT pay_count FROM users WHERE user_id = ?", 
                        (user_id,)
                    )
                    new_count = await cursor.fetchone()
                    logger.info(f"Обновлен pay_count для пользователя {user_id}: {new_count[0]}")
                else:
                    # Оптимизированное массовое обновление
                    await db.execute("""
                        WITH payment_counts AS (
                            SELECT user_id, COUNT(*) as succeeded_count
                            FROM user_transactions 
                            WHERE status = 'succeeded'
                            GROUP BY user_id
                        )
                        UPDATE users 
                        SET pay_count = COALESCE(
                            (SELECT succeeded_count 
                             FROM payment_counts 
                             WHERE payment_counts.user_id = users.user_id),
                            0
                        )
                    """)
                    logger.info("Обновлен pay_count для всех пользователей")
                
                await db.commit()
                return True
                
    except ValueError as ve:
        logger.error(f"Ошибка валидации: {ve}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при обновлении pay_count: {e}", exc_info=True)
        return False


async def set_is_first_payment_done(user_id: int, is_first_payment_done: bool):
    """Устанавливает значение is_first_payment_done для пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET is_first_payment_done = ?
            WHERE user_id = ?
        """, (is_first_payment_done, user_id))
        await db.commit()
        logger.info(f"Значение is_first_payment_done для пользователя {user_id} установлено на {is_first_payment_done}.")

async def get_is_first_payment_done(user_id: int) -> bool:
    """Получает значение is_first_payment_done для пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT is_first_payment_done
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else False


async def add_is_first_payment_done_column():
    """Добавляет колонку is_first_payment_done в таблицу users, если её нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in await cursor.fetchall()}

        if 'is_first_payment_done' not in columns:
            await db.execute("""
                ALTER TABLE users
                ADD COLUMN is_first_payment_done BOOLEAN DEFAULT 0
            """)
            await db.commit()
            logger.info("Колонка is_first_payment_done добавлена в таблицу users.")
        else:
            logger.info("Колонка is_first_payment_done уже существует в таблице users.")

async def get_user_transactions(user_id: int):
    """
    Получает все транзакции пользователя, отсортированные по дате создания (новые первые)
    
    Args:
        user_id (int): ID пользователя
        
    Returns:
        list: Список транзакций пользователя
        Пример: [
            {
                'id': 1,
                'amount': 100,
                'status': 'completed',
                'transaction_id': 'tx_123',
                'created_at': '2024-03-20 12:34:56'
            },
            ...
        ]
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, amount, status, transaction_id, created_at
            FROM user_transactions 
            WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,))
        transactions = await cursor.fetchall()
        return [dict(row) for row in transactions]

async def add_multiple_payment_methods(user_id: int, payment_methods: List[Dict], days_delay: int = 0):
    for method in payment_methods:
        await add_payment_method(user_id=user_id,
                                 payment_method_id=method["id"],
                                 issuer_name=method["type"],
                                 title=method["type"],
                                 days_delay=days_delay)

async def get_transaction_by_id(transaction_id: str) -> dict | None:
    """
    Получает информацию о транзакции по её ID
    
    Args:
        transaction_id (str): Уникальный идентификатор транзакции
        
    Returns:
        dict: Словарь с данными транзакции или None если транзакция не найдена
        Пример: {
            'id': 1,
            'user_id': 123456789,
            'amount': 1000,
            'status': 'pending',
            'transaction_id': 'tx_123abc',
            'created_at': '2024-03-20 12:34:56'
        }
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        query = """
            SELECT 
                id,
                user_id,
                amount,
                status,
                transaction_id,
                created_at
            FROM user_transactions
            WHERE transaction_id = ?
        """
        
        try:
            cursor = await db.execute(query, (transaction_id,))
            row = await cursor.fetchone()
            
            if row:
                return dict(row)
            
            logger.warning(f"Transaction not found: {transaction_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting transaction: {e}")
            return None

async def add_transaction(user_id: int, amount: int, transaction_id: str, status: str = 'pending'):
    """
    Добавляет новую транзакцию в базу данных
    
    Args:
        user_id (int): ID пользователя
        amount (int): Сумма транзакции
        transaction_id (str): Уникальный ID транзакции
        status (str): Статус транзакции (по умолчанию 'pending')
    
    Returns:
        bool: True если транзакция успешно добавлена, False если произошла ошибка
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO user_transactions (user_id, amount, transaction_id, status)
                VALUES (?, ?, ?, ?)
            """, (user_id, amount, transaction_id, status))
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding transaction: {e}")
        return False
    

async def update_transaction_status(transaction_id: str, new_status: str):
    """
    Обновляет статус транзакции
    
    Args:
        transaction_id (str): ID транзакции
        new_status (str): Новый статус ('completed', 'failed', 'cancelled')
    
    Returns:
        bool: True если статус успешно обновлен, False если произошла ошибка
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE user_transactions 
                SET status = ? 
                WHERE transaction_id = ?
            """, (new_status, transaction_id))
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Error updating transaction status: {e}")
        return False


async def update_user_channel(user_id: int, from_channel: str):
    """
    Обновляет канал у существующего пользователя
    
    Args:
        user_id (int): ID пользователя
        from_channel (str): Новый канал
        
    Returns:
        bool: True если успешно, False если произошла ошибка
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE users 
                SET from_channel = ?
                WHERE user_id = ?
                """,
                (from_channel, user_id)
            )
            await db.commit()
            logger.info(f"Обновлен канал для пользователя {user_id}: {from_channel}")
            return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении канала пользователя: {e}")
        return False

async def add_inbound_columns():
    """
    Добавляет новые колонки в таблицу inbounds если их нет
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем информацию о существующих колонках
        cursor = await db.execute("PRAGMA table_info(inbounds)")
        columns = {row[1] for row in await cursor.fetchall()}

        # Добавляем новые колонки если их нет
        new_columns = {
            "pbk": "TEXT",
            "sid": "TEXT",
            "sni": "TEXT",
            "port": "INTEGER",
            "utls": "TEXT",
        }

        for column, type_ in new_columns.items():
            if column not in columns:
                await db.execute(f"ALTER TABLE inbounds ADD COLUMN {column} {type_}")

        await db.commit()


async def migrate_servers_data():
    """
    Миграция данных серверов с переносом всех клиентов в VLESS
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            # 1. Создаем новые таблицы
            await db.execute("""
                CREATE TABLE IF NOT EXISTS servers_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    country TEXT,
                    max_clients INTEGER DEFAULT 100,
                    is_active INTEGER DEFAULT 1
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS inbounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER,
                    server_address TEXT NOT NULL,
                    inbound_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    clients_count INTEGER DEFAULT 0,
                    max_clients INTEGER DEFAULT 100,
                    FOREIGN KEY(server_id) REFERENCES servers_new(id)
                )
            """)

            # 2. Копируем данные серверов
            cursor = await db.execute("""
                SELECT 
                    id, address, username, password, 
                    clients_count, max_clients, is_active, 
                    country
                FROM servers
            """)
            old_servers = await cursor.fetchall()

            # 3. Переносим данные в новую структуру
            for server in old_servers:
                (old_id, address, username, password, 
                 clients_count, max_clients, is_active, country) = server

                # Вставляем информацию о сервере
                await db.execute("""
                    INSERT INTO servers_new (
                        id, address, username, password, 
                        country, max_clients, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (old_id, address, username, password, country, max_clients, is_active))

                # Создаем VLESS инбаунд со всеми текущими клиентами
                await db.execute("""
                    INSERT INTO inbounds (
                        server_id, server_address, inbound_id, protocol, 
                        clients_count, max_clients
                    ) VALUES (?, ?, 19, 'vless', ?, ?)
                """, (old_id, address, clients_count, max_clients))

            # 4. Проверяем миграцию
            cursor = await db.execute("SELECT COUNT(*) FROM servers_new")
            new_count = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT COUNT(*) FROM servers")
            old_count = (await cursor.fetchone())[0]

            if new_count == old_count:
                # 5. Заменяем старую таблицу
                await db.execute("DROP TABLE servers")
                await db.execute("ALTER TABLE servers_new RENAME TO servers")
                logger.info("Миграция серверов успешно завершена")
            else:
                raise Exception("Количество записей после миграции не совпадает")

            await db.commit()
            logger.info(f"Миграция завершена. Перенесено {new_count} серверов")

        except Exception as e:
            logger.error(f"Ошибка при миграции: {e}")
            await db.execute("DROP TABLE IF EXISTS servers_new")
            await db.execute("DROP TABLE IF EXISTS inbounds")
            raise

async def get_admins():
    """
    Получает список ID администраторов
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE is_admin = 1")
        return [row[0] for row in await cursor.fetchall()]

async def sync_server_clients_count():
    """
    Синхронизирует счетчики активных ключей для всех серверов и их инбаундов
    путем подсчета актуальных ключей в базе данных
    """
    try:
        logger.info("Начало синхронизации счетчиков серверов...")
        async with aiosqlite.connect(DB_PATH) as db:
            current_time = int(datetime.now().timestamp() * 1000)
            
            # Получаем все активные серверы с их инбаундами
            cursor = await db.execute("""
                SELECT 
                    s.address,
                    i.inbound_id,
                    i.protocol,
                    i.server_address
                FROM servers s
                JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
                WHERE s.is_active = 1
            """)
            server_inbounds = await cursor.fetchall()
            
            if not server_inbounds:
                logger.info("Активные серверы не найдены")
                return
                
            for server_address, inbound_id, protocol, server_addr in server_inbounds:
                try:
                    # Получаем базовый адрес сервера без порта
                    base_address = server_address.split(':')[0].strip().lower()
                    
                    # Определяем префикс ключа в зависимости от протокола
                    key_prefix = 'ss://' if protocol == 'shadowsocks' else 'vless://'
                    
                    # Получаем все активные ключи для данного сервера и протокола
                    cursor = await db.execute("""
                        SELECT COUNT(*) FROM keys 
                        WHERE key LIKE ? 
                        AND expiration_date > ?
                        AND key LIKE ?
                    """, (
                        f"%{base_address}%", 
                        current_time,
                        f"{key_prefix}%"
                    ))
                    
                    active_keys_count = (await cursor.fetchone())[0]
                    
                    # Проверяем, что счетчик не стал нулевым, если есть активные ключи
                    if active_keys_count == 0:
                        # Дополнительная проверка, возможно проблема в формате ключей
                        cursor = await db.execute("""
                            SELECT COUNT(*) FROM keys 
                            WHERE key LIKE ? 
                            AND expiration_date > ?
                        """, (f"%{base_address}%", current_time))
                        total_keys = (await cursor.fetchone())[0]
                        
                        if total_keys > 0:
                            logger.warning(
                                f"Обнаружено несоответствие: для сервера {server_address}, "
                                f"инбаунд {inbound_id} ({protocol}) найдено {total_keys} ключей, "
                                f"но с префиксом {key_prefix} - 0. Возможно, проблема с форматом ключей."
                            )
                    
                    # Обновляем счетчик в таблице inbounds
                    await db.execute("""
                        UPDATE inbounds 
                        SET clients_count = ? 
                        WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                        AND inbound_id = ?
                        AND protocol = ?
                    """, (active_keys_count, server_addr, inbound_id, protocol))
                    
                    logger.info(
                        f"Сервер {server_address}, "
                        f"инбаунд {inbound_id} ({protocol}): "
                        f"обновлен счетчик клиентов на {active_keys_count}"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"Ошибка при синхронизации счетчика для сервера "
                        f"{server_address}, инбаунд {inbound_id}: {e}"
                    )
                    continue
            
            await db.commit()
            logger.info("Синхронизация счетчиков серверов завершена")
            
    except Exception as e:
        logger.error(f"Ошибка при синхронизации счетчиков серверов: {e}")

async def cleanup_expired_keys():
    """
    Удаляет все истекшие ключи с серверов при запуске
    """
    try:
        logger.info("Начало очистки истекших ключей при запуске...")
        async with aiosqlite.connect(DB_PATH) as db:
            current_time = int(datetime.now().timestamp() * 1000)
            
            # Получаем все истекшие ключи
            cursor = await db.execute("""
                SELECT key FROM keys 
                WHERE expiration_date < ?
            """, (current_time,))
            
            expired_keys = await cursor.fetchall()
            
            if not expired_keys:
                logger.info("Истекших ключей не найдено")
                return
                
            logger.info(f"Найдено {len(expired_keys)} истекших ключей")
            
            # Группируем ключи по серверам для оптимизации
            server_clients = {}
            for (key,) in expired_keys:
                try:
                    _, _, unique_uuid, address, parts = extract_key_data(key)
                    if address:
                        if address not in server_clients:
                            server_clients[address] = []
                        server_clients[address].append(unique_uuid)
                except Exception as e:
                    logger.error(f"Ошибка при обработке ключа {key}: {e}")

            # Обрабатываем каждый сервер
            for address, uuids in server_clients.items():
                try:
                    protocol = 'ss' if key.startswith('ss://') else 'vless'
                    server = await get_server_by_address(address, protocol = protocol)
                    if server:
                        api = AsyncApi(
                            f"http://{server['address']}",
                            server['username'],
                            server['password'],
                            use_tls_verify=False
                        )
                        await api.login()
                        
                        # Удаляем клиентов с сервера
                        for uuid in uuids:
                            try:
                                # await api.client.delete(inbound_id=server['inbound_id'], client_uuid=str(uuid))
                                logger.info(f"Клиент {uuid} удален с сервера {address}")
                            except Exception as e:
                                logger.error(f"Ошибка при удалении клиента {uuid} с сервера {address}: {e}")
                        
                        # Обновляем количество клиентов на сервере
                        clients_count = await get_server_count_by_address(address, server['inbound_id'], protocol)
                        await update_server_clients_count(address, clients_count, server['inbound_id'])
                        
                except Exception as e:
                    logger.error(f"Ошибка при обработке сервера {address}: {e}")

            # Удаляем все истекшие ключи из БД
            await db.execute("DELETE FROM keys WHERE expiration_date <= ?", (current_time,))
            
            # Обновляем счетчики ключей у всех пользователей
            await db.execute("""
                UPDATE users 
                SET keys_count = (
                    SELECT COUNT(*) FROM keys 
                    WHERE keys.user_id = users.user_id
                )
            """)
            
            await db.commit()
            logger.info("Очистка истекших ключей завершена")
            
    except Exception as e:
        logger.error(f"Ошибка при очистке истекших ключей: {e}")

async def get_available_countries(protocol: str = None):
    """
    Получает список доступных стран с учетом свободных слотов
    
    Args:
        protocol (str, optional): Протокол для фильтрации (vless/ss)
    
    Returns:
        list: Список словарей с информацией о странах
        Пример: [{'code': 'France', 'name': '🇫🇷 Франция', 'slots': 5}, ...]
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Базовый запрос с проверкой доступности
            query = """
                SELECT 
                    s.country,
                    SUM(i.max_clients - i.clients_count) as available_slots
                FROM servers s
                JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
                WHERE s.is_active = 1
                    AND i.clients_count < i.max_clients
                    AND i.max_clients > 0
                    AND s.country IS NOT NULL
                    AND s.country != ''
            """
            params = []
            
            # Фильтр по протоколу
            if protocol:
                protocol = 'shadowsocks' if protocol == 'ss' else protocol
                query += " AND i.protocol = ?"
                params.append(protocol)
                
            query += """
                GROUP BY s.country
                HAVING available_slots > 0
                ORDER BY s.country
            """
            
            cursor = await db.execute(query, params)
            countries_data = await cursor.fetchall()
            
            # Словарь соответствия стран
            country_names = {
                'France': '🇫🇷 Франция',
                'Germany': '🇩🇪 Германия',
                'Czechia': '🇨🇿 Чехия',
                'Netherlands': '🇳🇱 Нидерланды',
                'Kazakhstan': '🇰🇿 Казахстан',
                'Finland': '🇫🇮 Финляндия',
            }
            
            # Формируем результат
            available_countries = []
            for country_code, slots in countries_data:
                if country_code in country_names:
                    available_countries.append({
                        'code': country_code,
                        'name': country_names[country_code],
                        'slots': slots
                    })
            
            logger.info(f"Found {len(available_countries)} available countries with slots")
            return available_countries
            
    except Exception as e:
        logger.error(f"Error getting available countries: {e}")
        return []

async def ping_server(address: str, port: int, timeout: int = 5) -> bool:
    """
    Проверяет доступность сервера по HTTP
    
    Args:
        address (str): Адрес сервера
        port (int): Порт
        timeout (int): Таймаут в секундах
    
    Returns:
        bool: True если сервер доступен, False если нет
    """
    try:
        url = f"http://{address}:{port}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as response:
                return response.status < 500  # Любой ответ кроме 5xx считаем успешным
    except (ClientError, asyncio.TimeoutError):
        logger.warning(f"Сервер {address}:{port} недоступен")
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке сервера {address}:{port}: {e}")
        return False

async def get_api_instance(country: str = None, use_shadowsocks: bool = None):
    """
    Получает экземпляр API для доступного сервера с учетом фильтров
    
    Args:
        country (str, optional): Код страны для фильтрации серверов
        use_shadowsocks (bool, optional): True для SS, False или None для VLESS
    
    Returns:
        tuple: (AsyncApi, address, pbk, sid, sni, port, utls, protocol, country, inbound_id)
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Начинаем транзакцию для блокировки таблицы на время выбора сервера
            async with db.execute('BEGIN EXCLUSIVE TRANSACTION'):
                query = """
                    SELECT 
                        s.address, s.username, s.password,
                        i.clients_count, i.max_clients,
                        i.pbk, i.sid, i.sni, i.port, i.utls, i.protocol, s.country, i.inbound_id,
                        CAST(i.clients_count AS FLOAT) / NULLIF(CAST(i.max_clients AS FLOAT), 0) as load_ratio
                    FROM servers s
                    INNER JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
                    WHERE s.is_active = 1 
                    AND i.clients_count < i.max_clients
                    AND i.max_clients > 0
                """
                params = []
                
                if country:
                    query += " AND s.country = ?"
                    params.append(country)
                
                if use_shadowsocks is not None:
                    query += " AND i.protocol = ?"
                    params.append('shadowsocks' if use_shadowsocks else 'vless')
                
                # Сортировка по загрузке и случайности
                query += """ 
                    ORDER BY 
                        load_ratio ASC,
                        i.clients_count ASC,
                        RANDOM()
                """
                
                cursor = await db.execute(query, params)
                servers = await cursor.fetchall()
                
                if not servers:
                    error_msg = []
                    if country:
                        error_msg.append(f"страны {country}")
                    if use_shadowsocks is not None:
                        protocol = "Shadowsocks" if use_shadowsocks else "vless"
                        error_msg.append(f"протокола {protocol}")
                    
                    raise Exception(
                        "Нет доступных серверов" + 
                        (f" для {' и '.join(error_msg)}" if error_msg else "")
                    )
                
                # Перебираем серверы, пока не найдем доступный
                for server in servers:
                    address, username, password, clients_count, max_clients, pbk, sid, sni, port, utls, protocol, country, inbound_id, _ = server
                    
                    # Проверяем доступность сервера
                    if not await ping_server(address.split(':')[0], 2053):
                        logger.warning(f"Сервер {address} недоступен, пробуем следующий")
                        continue
                    
                    # Дополнительная проверка лимита
                    if clients_count >= max_clients:
                        logger.warning(f"Сервер {address} достиг лимита клиентов: {clients_count}/{max_clients}")
                        continue
                    
                    # Резервируем место для нового клиента
                    await db.execute("""
                        UPDATE inbounds 
                        SET clients_count = clients_count + 1
                        WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                        AND inbound_id = ?
                        AND protocol = ?
                        AND clients_count < max_clients
                    """, (address, inbound_id, protocol))
                    
                    # Проверяем успешность обновления
                    cursor = await db.execute("""
                        SELECT clients_count 
                        FROM inbounds 
                        WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                        AND inbound_id = ?
                        AND protocol = ?
                    """, (address, inbound_id, protocol))
                    
                    new_count = await cursor.fetchone()
                    if not new_count or new_count[0] <= clients_count:
                        logger.warning(f"Не удалось зарезервировать место на сервере {address}")
                        continue
                    
                    # Нашли доступный сервер
                    await db.commit()
                    
                    logger.info(
                        f"Выбран сервер: {address} ({protocol}), "
                        f"загрузка: {new_count[0]}/{max_clients} "
                        f"({(new_count[0]/max_clients*100):.1f}%), "
                        f"страна: {country}"
                    )
                    
                    return (
                        AsyncApi(
                            f"http://{address}",
                            username,
                            password,
                            use_tls_verify=False
                        ),
                        address, pbk, sid, sni, port, utls, protocol, country, inbound_id
                    )
                
                # Если не нашли доступный сервер
                await db.rollback()
                raise Exception("Нет доступных серверов, прошедших проверку доступности")
                
    except Exception as e:
        logger.error(f"Ошибка при получении API: {e}")
        raise


async def save_or_update_email(user_id: int, email: str):
    """
    Сохраняет или обновляет email пользователя
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE users SET email = ? WHERE user_id = ?
            """, (email, user_id))
            await db.commit()
            logger.info(f"Email {email} сохранен для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении email: {e}")

async def get_user_email(user_id: int) -> str:
    """
    Получает email пользователя
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT email FROM users WHERE user_id = ?
            """, (user_id,))
            result = await cursor.fetchone()
            return result[0] if result and result[0] else None
    except Exception as e:
        logger.error(f"Ошибка при получении email: {e}")
        return None

async def check_unused_free_keys():
    """
    Проверяет пользователей с неиспользованными бесплатными ключами
    
    Returns:
        list: Список user_id пользователей с неиспользованными бесплатными ключами
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT user_id FROM users 
                WHERE free_keys_count > 0 
                AND user_id NOT IN (
                    SELECT DISTINCT user_id FROM keys 
                    WHERE user_id IS NOT NULL
                )
            """)
            users = await cursor.fetchall()
            return [user[0] for user in users]
    except Exception as e:
        logger.error(f"Ошибка при проверке неиспользованных ключей: {e}")
        return []

async def check_expiring_subscriptions():
    """
    Возвращает все ключи, чей expiration_date (мс Unix‑эпохи) попадает на завтрашнюю дату UTC.
    """
    try:
        # Вычисляем начало и конец завтрашнего дня в UTC
        now_utc = datetime.now(timezone.utc)
        today_utc = now_utc.date()
        tomorrow_utc = today_utc + timedelta(days=1)
        start_of_tomorrow = datetime.combine(tomorrow_utc, datetime.min.time(), tzinfo=timezone.utc)
        end_of_tomorrow = start_of_tomorrow + timedelta(days=1)
        start_ms = int(start_of_tomorrow.timestamp() * 1000)
        end_ms = int(end_of_tomorrow.timestamp() * 1000)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT *
                FROM   keys
                WHERE  expiration_date >= ? AND expiration_date < ?;
            """
            cursor = await db.execute(query, (start_ms, end_ms))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Ошибка при получении ключей: {e}")
        return []

async def check_expiring_in_3_days_subscriptions():
    """
    Возвращает все ключи, чей expiration_date (мс Unix‑эпохи) попадает на дату через три дня UTC.
    """
    try:
        # Вычисляем начало и конец завтрашнего дня в UTC
        now_utc = datetime.now(timezone.utc)
        today_utc = now_utc.date()
        tomorrow_utc = today_utc + timedelta(days=3)
        start_of_tomorrow = datetime.combine(tomorrow_utc, datetime.min.time(), tzinfo=timezone.utc)
        end_of_tomorrow = start_of_tomorrow + timedelta(days=1)
        start_ms = int(start_of_tomorrow.timestamp() * 1000)
        end_ms = int(end_of_tomorrow.timestamp() * 1000)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT *
                FROM   keys
                WHERE  expiration_date >= ? AND expiration_date < ?;
            """
            cursor = await db.execute(query, (start_ms, end_ms))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Ошибка при получении ключей: {e}")
        return []


async def get_users_with_unused_free_keys():
    """
    Получает список пользователей, у которых есть неиспользованные бесплатные ключи
    (free_keys_count = 1)
    
    Returns:
        list: Список ID пользователей
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT user_id FROM users 
                WHERE free_keys_count = 1 
                AND user_id NOT IN (
                    SELECT DISTINCT user_id FROM keys 
                    WHERE user_id IS NOT NULL
                )
            """)
            users = await cursor.fetchall()
            logger.info(f"Найдено {len(users)} пользователей с неиспользованными бесплатными ключами")
            return [user[0] for user in users]
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с неиспользованными ключами: {e}")
        return []

async def get_users_with_zero_traffic_keys():
    """
    Получает список пользователей, у которых есть ключи с нулевым трафиком
    
    Returns:
        list: Список кортежей (user_id, key)
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT k.user_id, k.key 
                FROM keys k
                JOIN key_usage_reminders r ON k.key = r.key
                WHERE r.last_traffic = 0
                AND k.user_id IS NOT NULL
                AND k.expiration_date > ?
            """, (int(datetime.now().timestamp() * 1000),))
            
            results = await cursor.fetchall()
            logger.info(f"Найдено {len(results)} пользователей с ключами с нулевым трафиком")
            return results
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с ключами с нулевым трафиком: {e}")
        return []

async def get_users_with_specific_balance(balance=99):
    """
    Получает список пользователей с указанным балансом и без активных подписок
    
    Args:
        balance (int): Сумма баланса для поиска
    
    Returns:
        list: Список ID пользователей
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            current_time = int(datetime.now().timestamp() * 1000)
            
            cursor = await db.execute("""
                SELECT user_id FROM users 
                WHERE balance = ? 
                AND (
                    user_id NOT IN (
                        SELECT DISTINCT user_id FROM keys 
                        WHERE expiration_date > ?
                    )
                    OR (
                        SELECT COUNT(*) FROM keys 
                        WHERE keys.user_id = users.user_id 
                        AND expiration_date > ?
                    ) = 0
                )
            """, (balance, current_time, current_time))
            
            users = await cursor.fetchall()
            logger.info(f"Найдено {len(users)} пользователей с балансом {balance} руб. без активных подписок")
            return [user[0] for user in users]
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с балансом {balance}: {e}")
        return []

async def get_users_with_expiring_subscriptions(days=3):
    """
    Получает список пользователей, у которых подписка истекает в ближайшие дни
    
    Args:
        days (int): Количество дней до истечения подписки
        
    Returns:
        list: Список ID пользователей
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Текущее время в миллисекундах
            current_time = int(datetime.now().timestamp() * 1000)
            
            # Время через указанное количество дней
            expiry_time = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)
            
            cursor = await db.execute("""
                SELECT DISTINCT user_id FROM keys
                WHERE expiration_date > ?
                AND expiration_date < ?
                AND user_id IS NOT NULL
            """, (current_time, expiry_time))
            
            users = await cursor.fetchall()
            logger.info(f"Найдено {len(users)} пользователей с истекающими подписками в ближайшие {days} дней")
            return [user[0] for user in users]
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с истекающими подписками: {e}")
        return []

async def get_user_segments(server_address: str = None):
    """
    Получает сегменты пользователей для маркетинговых кампаний
    
    Args:
        server_address (str, optional): Адрес сервера для получения пользователей с ключами на этом сервере
    
    Returns:
        dict: Словарь с сегментами пользователей
        {
            'unused_free_keys': [user_id1, user_id2, ...],
            'zero_traffic': [(user_id1, key1), (user_id2, key2), ...],
            'balance_99': [user_id1, user_id2, ...],
            'expiring_subscriptions': [user_id1, user_id2, ...],
            'server_users': [user_id1, user_id2, ...] # если указан server_address
        }
    """
    try:
        segments = {
            'unused_free_keys': await get_users_with_unused_free_keys(),
            'zero_traffic': await get_users_with_zero_traffic_keys(),
            'balance_99': await get_users_with_specific_balance(99),
            'expiring_subscriptions': await get_users_with_expiring_subscriptions(3)
        }
        
        # Добавляем сегмент пользователей с ключами на указанном сервере, если адрес предоставлен
        if server_address:
            segments['server_users'] = await get_users_by_server_address(server_address)
        
        return segments
    except Exception as e:
        logger.error(f"Ошибка при получении сегментов пользователей: {e}")
        return {
            'unused_free_keys': [],
            'zero_traffic': [],
            'balance_99': [],
            'expiring_subscriptions': [],
            'server_users': [] if server_address else None
        }

async def remove_key(key: str, user_id: str):
    """
    Удаляет ключ для этого пользователя с сервера и из бызы данных, обновляет счётчик.
    """
    from handlers.scheduler import active_jobs

    job_id = f'remove_{key}'
    try:
        active_jobs.remove(job_id)
    except ValueError:
        logger.warning(f"По какой-то причине нет этого элемента в списке: {job_id}")

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            try:
                # Удаляем ключ с сервера
                await server_remove_key(key, user_id)
                # Удаляем истекшие ключи из БД
                await remove_active_key(key, db)
            except Exception as e:
                logger.error(f"Ошибка при обработке ключа {key}: {e}")
                return
            # Обновляем счетчики ключей у пользователей
            await update_multiple_keys_count([user_id], db)
            logger.info(f"Удалён 1 ключ")
    except Exception as e:
        logger.error(f"Ошибка при удалении истекших ключей: {e}")

async def remove_expired_keys(excluding_keys: List[str]):
    """
    Удаляет истекшие ключи и обновляет счетчики у пользователей и серверов
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            current_time = int(datetime.now().timestamp() * 1000)
            
            # Получаем все истекшие ключи с информацией о пользователях
            cursor = await db.execute("""
                SELECT key, user_id FROM keys 
                WHERE expiration_date < ?
            """, (current_time,))
            
            expired_keys = await cursor.fetchall()
            
            if not expired_keys:
                return
            
            # Обрабатываем каждый истекший ключ
            user_ids = []
            for key, user_id in expired_keys:
                if key in excluding_keys:
                    continue

                try:
                    await server_remove_key(key, user_id)
                    user_ids.append(user_id)

                    # Удаляем истекшие ключи из БД
                    await remove_active_key(key, db)
                except Exception as e:
                    logger.error(f"Ошибка при обработке ключа {key}: {e}")
                    continue
            
            # Обновляем счетчики ключей у пользователей
            await update_multiple_keys_count(user_ids, db)
            
            logger.info(f"Удалено {len(user_ids)} истекших ключей")
            
    except Exception as e:
        logger.error(f"Ошибка при удалении истекших ключей: {e}")

async def server_remove_key(key: str, user_id: str):
    """
    Полностью удаляет указанный ключ и обновляет счетчики у пользователей и серверов
    """
    # Извлекаем данные из ключа
    device, unique_id, unique_uuid, address, parts = extract_key_data(key)
    protocol = 'ss' if key.startswith('ss://') else 'vless' 
    if address:
        # Получаем информацию о сервере
        server = await get_server_by_address(address, protocol = protocol)
        if server:
            try:
                # Подключаемся к API сервера
                api = AsyncApi(
                    f"http://{server['address']}",
                    server['username'],
                    server['password'],
                    use_tls_verify=False
                )
                await api.login()

                inbound_id = server['inbound_id']
                email = f"{parts[0]}_{parts[1]}_{parts[2]}"
                client = await api.client.get_by_email(email)
                clients_count = await get_server_count_by_address(address, inbound_id, protocol)

                # Удаляем клиента с сервера
                if protocol == 'vless':
                    await api.client.delete(inbound_id=inbound_id, client_uuid=str(unique_uuid))
                else:
                    await api.client.delete(inbound_id=inbound_id, client_uuid=str(client.email))
                logger.info(f"Клиент {unique_uuid} удален с сервера {address}")
                
                await update_server_clients_count(address, clients_count - 1, inbound_id)

                logger.info(f"Уменьшено количество клиентов на сервере {address}")
            except Exception as e:
                logger.error(f"Ошибка при удалении клиента с сервера {address}: {e}")
                raise e

async def update_multiple_keys_count(user_ids: List[str], db = None):
    current_time = int(datetime.now().timestamp() * 1000)

    if db is not None:
        for user_id in user_ids:
            cursor = await db.execute("""
                SELECT COUNT(*) FROM keys 
                WHERE user_id = ? AND expiration_date >= ?
            """, (user_id, current_time))
            new_keys_count = (await cursor.fetchone())[0]
            
            await db.execute("""
                UPDATE users 
                SET keys_count = ? 
                WHERE user_id = ?
            """, (new_keys_count, user_id))

            await db.commit()
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            for user_id in user_ids:
                cursor = await db.execute("""
                    SELECT COUNT(*) FROM keys 
                    WHERE user_id = ? AND expiration_date >= ?
                """, (user_id, current_time))
                new_keys_count = (await cursor.fetchone())[0]
                
                await db.execute("""
                    UPDATE users 
                    SET keys_count = ? 
                    WHERE user_id = ?
                """, (new_keys_count, user_id))

                await db.commit()


def setup_scheduler():
    """
    Настраивает планировщик для регулярных проверок и уведомлений
    """
    scheduler = AsyncIOScheduler()
    
    # Удаление истекших ключей (каждый час)
    # scheduler.add_job(
    #    remove_expired_keys,
    #    trigger=IntervalTrigger(hours=1),
    #    id='remove_expired_keys',
    #    name='Remove expired keys',
    #    replace_existing=True
    #)
    
    # Синхронизация счетчиков клиентов (каждые 15 минут)
    scheduler.add_job(
        sync_server_clients_count,
        trigger=IntervalTrigger(minutes=15),
        id='sync_server_clients_count',
        name='Sync server clients count',
        replace_existing=True
    )    
    
    # Создание резервных копий базы данных (каждые 5 минут)
    scheduler.add_job(
        create_database_backup,
        trigger=IntervalTrigger(minutes=5),
        id='create_database_backup',
        name='Create database backup',
        replace_existing=True
    )

   

    
    scheduler.start()
    logger.info("Планировщик задач запущен")
    return scheduler

async def add_used_promocode(user_id, promocode):
    """
    Регистрирует использованный промокод для пользователя
    
    Args:
        user_id (int): ID пользователя
        promocode (str): Код использованного промокода
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO used_promocodes (user_id, promocode, used_at) 
            VALUES (?, ?, ?)
        """, (user_id, promocode, datetime.now().isoformat()))
        await db.commit()

async def check_user_used_promocode(user_id, promocode):
    """
    Проверяет, использовал ли пользователь данный промокод ранее
    
    Args:
        user_id (int): ID пользователя
        promocode (str): Код промокода
    
    Returns:
        bool: True, если промокод уже использован, иначе False
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT COUNT(*) FROM used_promocodes 
            WHERE user_id = ? AND promocode = ?
        """, (user_id, promocode))
        result = await cursor.fetchone()
        return result[0] > 0

async def get_user_used_promocodes(user_id):
    """
    Получает список использованных промокодов пользователя
    
    Args:
        user_id (int): ID пользователя
    
    Returns:
        list: Список использованных промокодов
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT promocode, used_at FROM used_promocodes 
            WHERE user_id = ? 
            ORDER BY used_at DESC
        """, (user_id,))
        return await cursor.fetchall()

async def get_free_keys_count(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT free_keys_count FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def get_free_days(user_id):
    """
    Получает количество промо-дней для пользователя
    
    Args:
        user_id (int): ID пользователя
    
    Returns:
        int: Количество промо-дней или 0, если не найдено
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT promo_days FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def set_free_keys_count(user_id, count):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET free_keys_count = ? WHERE user_id = ?", (count, user_id))
        await db.commit()

async def add_promocode_days(user_id, days):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET promo_days = ? WHERE user_id = ?", (days, user_id))
        await db.commit()

async def update_promocode_amount(promo_id):
    """
    Уменьшает количество использований промокода на 1
    
    Args:
        promo_id (int): ID промокода
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE promocodes 
            SET amount = amount - 1 
            WHERE id = ?
        """, (promo_id,))
        await db.commit()

async def add_promocode(code, user_id, amount, gift_balance, gift_days, expiration_date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO promocodes (code, user_id, amount, gift_balance, gift_days, expiration_date) VALUES (?, ?, ?, ?, ?, ?)", (code, user_id, amount, gift_balance, gift_days, expiration_date))
        await db.commit()

async def get_promocode(code):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM promocodes WHERE code = ?", (code,))
        return await cursor.fetchone()

async def remove_promocode(code):
    """
    Удаляет промокод по его коду
    
    Args:
        code (str): Код промокода
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM promocodes WHERE code = ?", (code,))
        await db.commit()

async def get_all_promocodes():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM promocodes")
        return await cursor.fetchall()

async def get_server_count_by_address(address, inbound_id=None, protocol=None):
    """
    Получает текущее количество клиентов на сервере по адресу и inbound_id
    Args:
    address (str): Адрес сервера
    inbound_id (int, optional): ID инбаунда
    protocol (str, optional): Протокол (ss или vless)
    Returns:
    int: Количество клиентов на указанном инбаунде сервера
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            query = """
            SELECT i.clients_count
            FROM servers s
            LEFT JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(?))
            WHERE TRIM(LOWER(s.address)) = TRIM(LOWER(?))
            """
            params = [address, address]

            if inbound_id is not None:
                query += " AND i.inbound_id = ?"
                params.append(inbound_id)

            if protocol is not None:
                query += " AND i.protocol = ?"
                params.append(protocol)

            cursor = await db.execute(query, params)
            result = await cursor.fetchone()
            if result:
                return result['clients_count']
            logger.warning(f"No data found for {address}, inbound {inbound_id}, protocol {protocol}")
            return 0
    except Exception as e:
        logger.error(f"Error getting server count for {address}, inbound {inbound_id}, protocol {protocol}: {e}")
        return 0

async def get_all_servers():
    """
    Получает список всех серверов с информацией об их инбаундах
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        logger.info("Checking servers table...")
        cursor = await db.execute("SELECT * FROM servers")
        cursor = await db.execute("""
            SELECT 
                id,
                server_id,
                server_address,
                inbound_id,
                protocol,
                clients_count,
                max_clients,
                pbk,
                sid,
                sni
            FROM inbounds
        """)
        cursor = await db.execute("""
            SELECT 
                s.id,
                s.address,
                s.username,
                s.password,
                s.country,
                s.is_active,
                i.protocol,
                i.clients_count,
                i.max_clients,
                i.inbound_id,
                i.pbk,
                i.sid,
                i.sni
            FROM servers s
            LEFT JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
            ORDER BY s.id ASC, i.protocol ASC
        """)
        result = await cursor.fetchall()

        return result

async def get_server_by_id(server_id: int):
    """
    Получает информацию о сервере по ID
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM servers WHERE id = ?", 
            (server_id,)
        )
        return await cursor.fetchone()

async def delete_server(server_id: int):
    """
    Удаляет сервер из базы данных
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM servers WHERE id = ?", 
            (server_id,)
        )
        await db.commit()

async def add_server(address, username, password, max_clients=100):
    """
    Добавляет новый сервер в базу данных
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO servers (address, username, password, max_clients) 
            VALUES (?, ?, ?, ?)
        """, (address, username, password, max_clients))
        await db.commit()


async def get_server_by_address(address: str, protocol: str = None):
    """
    Получает информацию о доступном сервере по его адресу и протоколу.
    Если указанный сервер недоступен, пытается найти другой доступный сервер.
    
    Args:
        address (str): IP адрес сервера
        protocol (str, optional): Протокол (ss/vless)
        
    Returns:
        dict: Словарь с данными сервера или None если сервер не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        clean_address = address.split(':')[0]
        
        # Сначала пытаемся найти серверы с указанным адресом
        query = """
            SELECT 
                s.address,
                s.username,
                s.password as server_password,
                s.country,
                i.clients_count,
                i.max_clients,
                i.pbk,
                i.sid,
                i.sni,
                i.protocol,
                i.port,
                i.utls,
                i.inbound_id
            FROM servers s
            INNER JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
            WHERE TRIM(LOWER(s.address)) LIKE TRIM(LOWER(?)) || '%'
            AND s.is_active = 1
        """
        params = [clean_address]
        
        if protocol:
            query += " AND i.protocol = ?"
            params.append(protocol)
            
        cursor = await db.execute(query, params)
        matching_servers = await cursor.fetchall()
        
        # Проверяем доступность найденных серверов
        for row in matching_servers:
            server_address = row['address'].split(':')[0]
            if await ping_server(server_address, 2053):
                logger.info(f"Найден доступный сервер: {server_address}:{row['port']}")
                return {
                    'address': row['address'],
                    'username': row['username'],
                    'password': row['server_password'],
                    'country': row['country'],
                    'clients_count': row['clients_count'],
                    'max_clients': row['max_clients'],
                    'pbk': row['pbk'],
                    'sid': row['sid'],
                    'sni': row['sni'],
                    'protocol': row['protocol'],
                    'port': row['port'],
                    'utls': row['utls'],
                    'inbound_id': row['inbound_id']
                }
        
        # Если указанные серверы недоступны, ищем любой другой доступный сервер
        logger.warning(f"Сервер {address} недоступен, ищем альтернативный сервер")
        
        query = """
            SELECT 
                s.address,
                s.username,
                s.password as server_password,
                s.country,
                i.clients_count,
                i.max_clients,
                i.pbk,
                i.sid,
                i.sni,
                i.protocol,
                i.port,
                i.utls,
                i.inbound_id,
                CAST(i.clients_count AS FLOAT) / NULLIF(i.max_clients, 0) as load_ratio
            FROM servers s
            INNER JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
            WHERE s.is_active = 1 
            AND i.clients_count < i.max_clients
        """
        params = []
        
        if protocol:
            query += " AND i.protocol = ?"
            params.append(protocol)
            
        # Сортируем по загрузке
        query += """
            ORDER BY 
                load_ratio ASC,
                i.clients_count ASC,
                RANDOM()
        """
        
        cursor = await db.execute(query, params)
        alternative_servers = await cursor.fetchall()
        
        # Проверяем доступность альтернативных серверов
        for row in alternative_servers:
            server_address = row['address'].split(':')[0]
            # Пропускаем изначально запрошенный адрес
            if server_address.lower() == clean_address.lower():
                continue
                
            if await ping_server(server_address, 2053):
                logger.info(f"Найден альтернативный доступный сервер: {server_address}:{row['port']}")
                return {
                    'address': row['address'],
                    'username': row['username'],
                    'password': row['server_password'],
                    'country': row['country'],
                    'clients_count': row['clients_count'],
                    'max_clients': row['max_clients'],
                    'pbk': row['pbk'],
                    'sid': row['sid'],
                    'sni': row['sni'],
                    'protocol': row['protocol'],
                    'port': row['port'],
                    'utls': row['utls'],
                    'inbound_id': row['inbound_id']
                }
        
        logger.error(f"Не найдено доступных серверов для протокола: {protocol}")
        return None

async def get_available_server():
    """
    Получает доступный сервер с наименьшим количеством клиентов
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT address, username, password, clients_count, max_clients, pbk, sid, protocol, country, inbound_id
            FROM servers 
            WHERE is_active = 1 AND clients_count < max_clients 
            ORDER BY clients_count ASC 
            LIMIT 1
        """)
        return await cursor.fetchone()

async def update_server_max_clients(server_id, max_clients):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE servers SET max_clients = ? WHERE id = ?", (max_clients, server_id))
        await db.commit()

async def update_server_info(address, protocol=None, country=None, inbound_id=None):
    """
    Обновляет информацию о сервере
    
    Args:
        address (str): Адрес сервера
        protocol (str, optional): Протокол сервера
        country (str, optional): Страна сервера
        inbound_id (int, optional): ID сервера для inbound
    """
    async with aiosqlite.connect(DB_PATH) as db:
        update_parts = []
        params = []
        
        if protocol is not None:
            update_parts.append("protocol = ?")
            params.append(protocol)
        
        if country is not None:
            update_parts.append("country = ?")
            params.append(country)
            
        if inbound_id is not None:
            update_parts.append("inbound_id = ?")
            params.append(inbound_id)
        
        if update_parts:
            query = f"UPDATE servers SET {', '.join(update_parts)} WHERE address = ?"
            params.append(address)
            await db.execute(query, params)
            await db.commit()


async def update_free_keys_count(user_id, count):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET free_keys_count = ? WHERE user_id = ?", (count, user_id))
        await db.commit()

async def update_server_clients_count(address, count, inbound_id):
    """
    Обновляет количество клиентов для конкретного инбаунда на сервере
    
    Args:
        address (str): Адрес сервера
        count (int): Новое количество клиентов
        inbound_id (int): ID инбаунда
        
    Returns:
        bool: True если обновление успешно, False в противном случае
    """
    try:
        # Нормализуем адрес сервера
        server_ip = address.split(':')[0].strip().lower()
        
        # Проверяем входные данные
        if not server_ip or not isinstance(inbound_id, int):
            logger.error(f"Некорректные входные данные: адрес={address}, inbound_id={inbound_id}")
            return False
            
        # Проверяем count
        try:
            count = int(count)
        except (ValueError, TypeError):
            logger.error(f"Некорректное значение count={count} для сервера {server_ip}")
            return False
            
        async with aiosqlite.connect(DB_PATH) as db:
            # Используем транзакцию с блокировкой
            async with db.execute('BEGIN EXCLUSIVE TRANSACTION'):
                # Проверяем существование записи с точным соответствием
                cursor = await db.execute("""
                    SELECT clients_count FROM inbounds 
                    WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                    AND inbound_id = ?
                """, (server_ip, inbound_id))
                
                current_count = await cursor.fetchone()
                
                if current_count is None:
                    logger.error(f"Инбаунд не найден для сервера {server_ip} с ID {inbound_id}")
                    return False
                
                # Проверка на отрицательные значения
                if count < 0:
                    logger.warning(f"Попытка установить отрицательное значение {count} для сервера {server_ip}. Устанавливаем 0.")
                    count = 0
                
                # Логируем изменение, особенно если счетчик обнуляется
                if current_count[0] > 0 and count == 0:
                    logger.warning(
                        f"Внимание: счетчик клиентов для сервера {server_ip}, инбаунд {inbound_id} "
                        f"изменяется с {current_count[0]} на 0. Запускаем дополнительную проверку."
                    )
                    # Здесь можно добавить дополнительную проверку или запустить синхронизацию
                
                # Выполняем обновление с точным соответствием адреса
                await db.execute("""
                    UPDATE inbounds 
                    SET clients_count = ?
                    WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                    AND inbound_id = ?
                """, (count, server_ip, inbound_id))
                
                # Проверяем результат обновления
                cursor = await db.execute("""
                    SELECT clients_count FROM inbounds 
                    WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                    AND inbound_id = ?
                """, (server_ip, inbound_id))
                
                new_count = await cursor.fetchone()
                
                if new_count and new_count[0] == count:
                    await db.commit()
                    logger.info(f"Успешно обновлен счетчик клиентов на {count} для сервера {server_ip}, инбаунд {inbound_id}")
                    return True
                else:
                    await db.rollback()
                    logger.error(f"Не удалось обновить счетчик клиентов для сервера {server_ip}, инбаунд {inbound_id}")
                    return False
                    
    except Exception as e:
        logger.error(f"Ошибка при обновлении счетчика клиентов для {address}: {e}", exc_info=True)
        return False

async def remove_active_key(key, db = None):
    if db is None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM keys WHERE key = ?", (key,))
            await db.commit()
    else:
        await db.execute("DELETE FROM keys WHERE key = ?", (key,))
        await db.commit()

async def add_active_key(user_id, key, device_id, expiration_date, name, price: int, days: int):
    try:
        logger.info(f"Adding key to database with params: user_id={user_id}, device_id={device_id}, expiration_date={expiration_date}")
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверка наличия ключа в базе данных
            cursor = await db.execute(
                "SELECT * FROM keys WHERE key = ? AND user_id = ? AND device_id = ? AND name = ?",
                (key, user_id, device_id, name)
            )
            existing_key = await cursor.fetchone()
            if existing_key:
                logger.warning(f"Key already exists in the database for user {user_id}, device {device_id}")
                return

            # Добавление нового ключа
            await db.execute(
                "INSERT INTO keys (key, user_id, device_id, expiration_date, name, price, days) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, user_id, device_id, expiration_date, name, price, days)
            )
            
            # Сначала удаляем запись из key_usage_reminders, если она существует
            await db.execute(
                "DELETE FROM key_usage_reminders WHERE key = ?",
                (key,)
            )
            
            # Добавляем запись для отслеживания использования ключа
            await db.execute(
                "INSERT INTO key_usage_reminders (key, last_traffic) VALUES (?, ?)",
                (key, 0)
            )
            
            await db.commit()
            logger.info(f"Successfully added key to database for user {user_id}")
    except Exception as e:
        logger.error(f"Error adding key to database: {e}", exc_info=True)
        raise

async def get_next_expiration_date(user_id: int | str, include_time: bool = False) -> str:
    """
    Получает следующую дату экспирации для пользователя.
    """
    query = """
        SELECT MIN(expiration_date)
        FROM keys
        WHERE user_id = ? AND expiration_date > ?
        """
    current_time = int(datetime.now().timestamp() * 1000)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, (user_id, current_time)) as cursor:
            row = await cursor.fetchone()
            if row and row[0] is not None:
                exp_date = unix_to_str(row[0], include_time=include_time)
                return exp_date
            else:
                return None


async def update_key_days_price(key_str: str, days: int | str, price: int | str):
    """
    Обновляет параметры days и price для ключа
    
    Args:
        key_str (str): ключ
        days (int | str): дни, на сколько ключ действителен
        price (int | str): цена, за которую пользователь продлевает ключ
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE keys SET days = ?, price = ? WHERE key = ?
        """, (days, price, key_str))
        await db.commit()

async def get_users_without_payment_methods():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        sql = """
        SELECT u.*
        FROM   users AS u
        LEFT JOIN user_payment_methods AS upm
               ON upm.user_id = u.user_id
        WHERE  upm.user_id IS NULL;
        """
        async with db.execute(sql) as cursor:
            rows = await cursor.fetchall()
    return rows

async def delete_all_payment_methods():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_payment_methods")
        await db.commit()

async def update_key_expriration_date(key, new_end_date):
    """
    Обновляет дату окончания подписки для пользователя
    
    new_end_date обязательно передавать в формате UNIX timestamp в миллисекундах

    Args:
        user_id (int): ID пользователя
        new_end_date (str): Новая дата окончания подписки
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE keys SET expiration_date = ? WHERE key = ?
        """, (new_end_date, key))
        await db.commit()

async def get_key_traffic(key):
    """
    Получает информацию о трафике для указанного ключа с сервера
    
    Args:
        key (str): VPN ключ
        
    Returns:
        int: Объем использованного трафика в байтах или 0 в случае ошибки
    """
    try:
        # Извлекаем данные из ключа
        device, unique_id, unique_uuid, address, parts = extract_key_data(key)
        protocol = 'ss' if key.startswith('ss://') else 'vless'
        
        if not address:
            logger.error(f"Не удалось извлечь адрес сервера из ключа {key}")
            return 0
            
        # Получаем информацию о сервере
        server = await get_server_by_address(address, protocol=protocol)
        if not server:
            logger.error(f"Сервер не найден для адреса {address}")
            return 0
            
        # Подключаемся к API сервера
        api = AsyncApi(
            f"http://{server['address']}",
            server['username'],
            server['password'],
            use_tls_verify=False
        )
        await api.login()
        
        email = f"{parts[0]}_{parts[1]}_{parts[2]}"
        
        try:
            client = await api.client.get_by_email(email)
                
            # Возвращаем сумму входящего и исходящего трафика
            return client.up + client.down
            
        except Exception as e:
            logger.error(f"Ошибка при получении информации о клиенте {unique_uuid}: {e}")
            return 0
            
    except Exception as e:
        logger.error(f"Ошибка при получении трафика для ключа {key}: {e}")
        return 0

async def update_key_traffic(key, traffic):
    """
    Обновляет информацию о последнем известном трафике для ключа
    
    Args:
        key (str): VPN ключ
        traffic (int): Объем трафика в байтах
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE key_usage_reminders SET last_traffic = ? WHERE key = ?",
                (traffic, key)
            )
            await db.commit()
            logger.debug(f"Обновлен трафик для ключа {key}: {traffic} байт")
    except Exception as e:
        logger.error(f"Ошибка при обновлении трафика для ключа {key}: {e}")

async def check_unused_keys():
    """
    Проверяет неиспользуемые ключи и отправляет напоминания пользователям
    """
    try:
        logger.info("Начало проверки неиспользуемых ключей...")
        current_time = datetime.now()
        
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            
            # Получаем все активные ключи с информацией о напоминаниях
            query = """
                SELECT 
                    k.key, k.user_id, k.device_id, k.expiration_date,
                    r.last_traffic, r.first_reminder_sent, r.second_reminder_sent, r.third_reminder_sent,
                    r.created_at
                FROM keys k
                LEFT JOIN key_usage_reminders r ON k.key = r.key
                WHERE k.expiration_date > ?
            """
            
            current_timestamp = int(current_time.timestamp() * 1000)
            cursor = await db.execute(query, (current_timestamp,))
            keys = await cursor.fetchall()
            
            for key_data in keys:
                key = key_data['key']
                user_id = key_data['user_id']
                last_traffic = key_data['last_traffic'] or 0
                first_reminder = key_data['first_reminder_sent'] == 1
                second_reminder = key_data['second_reminder_sent'] == 1
                third_reminder = key_data['third_reminder_sent'] == 1
                
                # Если запись о напоминаниях отсутствует, создаем её
                if key_data['created_at'] is None:
                    await db.execute(
                        "INSERT OR IGNORE INTO key_usage_reminders (key, last_traffic) VALUES (?, ?)",
                        (key, 0)
                    )
                    created_at = current_time
                else:
                    created_at = datetime.fromisoformat(key_data['created_at'].replace('Z', '+00:00'))
                
                # Получаем текущий трафик
                current_traffic = await get_key_traffic(key)
                
                # Если трафик не изменился, проверяем необходимость отправки напоминаний
                if current_traffic <= last_traffic:
                    days_since_creation = (current_time - created_at).days
                    
                    # Первое напоминание через 1 день
                    if not first_reminder and days_since_creation >= 1:
                        await send_key_reminder(user_id, key, 1)
                        await db.execute(
                            "UPDATE key_usage_reminders SET first_reminder_sent = 1 WHERE key = ?",
                            (key,)
                        )
                        logger.info(f"Отправлено первое напоминание для ключа {key} пользователю {user_id}")
                    
                    # Второе напоминание через 5 дней
                    elif first_reminder and not second_reminder and days_since_creation >= 5:
                        await send_key_reminder(user_id, key, 2)
                        await db.execute(
                            "UPDATE key_usage_reminders SET second_reminder_sent = 1 WHERE key = ?",
                            (key,)
                        )
                        logger.info(f"Отправлено второе напоминание для ключа {key} пользователю {user_id}")
                    
                    # Третье напоминание через 15 дней
                    elif second_reminder and not third_reminder and days_since_creation >= 15:
                        await send_key_reminder(user_id, key, 3)
                        await db.execute(
                            "UPDATE key_usage_reminders SET third_reminder_sent = 1 WHERE key = ?",
                            (key,)
                        )
                        logger.info(f"Отправлено третье напоминание для ключа {key} пользователю {user_id}")
                
                # Обновляем информацию о трафике
                await db.execute(
                    "UPDATE key_usage_reminders SET last_traffic = ? WHERE key = ?",
                    (current_traffic, key)
                )
            
            await db.commit()
            logger.info("Проверка неиспользуемых ключей завершена")
            
    except Exception as e:
        logger.error(f"Ошибка при проверке неиспользуемых ключей: {e}")

async def send_key_reminder(user_id, key, reminder_number):
    """
    Отправляет напоминание пользователю о неиспользуемом ключе
    
    Args:
        user_id (int): ID пользователя
        key (str): VPN ключ
        reminder_number (int): Номер напоминания (1, 2 или 3)
    """
    try:
        if not _bot_instance:
            logger.error("Бот не инициализирован для отправки напоминаний")
            return
            
        # Извлекаем данные из ключа для более информативного сообщения
        device, unique_id, unique_uuid, address, parts = extract_key_data(key)
        
        # Формируем сообщение в зависимости от номера напоминания
        if reminder_number == 1:
            message = (
                "🔑 <b>Напоминание о вашем VPN-ключе</b>\n\n"
                f"Мы заметили, что вы еще не начали использовать ваш ключ для устройства <b>{device}</b>.\n\n"
                "Если у вас возникли трудности с подключением, наша команда поддержки всегда готова помочь!"
            )
        elif reminder_number == 2:
            message = (
                "🔑 <b>Важное напоминание о вашем VPN-ключе</b>\n\n"
                f"Прошло уже 5 дней, а ваш ключ для устройства <b>{device}</b> все еще не используется.\n\n"
                "Не теряйте оплаченное время! Если вам нужна помощь с настройкой, обратитесь в поддержку."
            )
        else:
            message = (
                "🔑 <b>Последнее напоминание о вашем VPN-ключе</b>\n\n"
                f"Прошло уже 15 дней, а ваш ключ для устройства <b>{device}</b> так и не был использован.\n\n"
                "Рекомендуем настроить VPN, чтобы не терять оплаченное время. Наша поддержка всегда готова помочь с настройкой!"
            )
            
        # Отправляем сообщение пользователю
        await _bot_instance.send_message(
            chat_id=user_id,
            text=message,
            parse_mode="HTML"
        )
        
        logger.info(f"Отправлено напоминание #{reminder_number} пользователю {user_id} о ключе {key}")
        
    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")

async def update_key_expiry_date(key: str, new_expiry_time: int):
    """
    Обновляет дату истечения ключа в базе данных
    
    Args:
        key (str): VPN ключ
        new_expiry_time (int): Новое время истечения в миллисекундах
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE keys SET expiration_date = ? WHERE key = ?",
                (new_expiry_time, key)
            )
            await db.commit()
            logger.info(f"Updated expiry date for key {key} to {datetime.fromtimestamp(new_expiry_time/1000)}")
    except Exception as e:
        logger.error(f"Error updating key expiry date: {e}")
        raise

async def get_key_expiry_date(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT expiration_date FROM keys WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def update_user_ban_status(user_id: int, is_banned: bool):
    """Обновляет статус бана пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (is_banned, user_id)
        )
        await db.commit()

async def get_user_by_username(username: str):
    """Получает пользователя по username"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ) as cursor:
            user = await cursor.fetchone()
            if user:
                return dict(zip([col[0] for col in cursor.description], user))
            return None

async def get_key_price(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT price FROM keys WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result[0] if result else None
    
async def get_key_days(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT days FROM keys WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_user_info(key=None, username=None, user_id=None):
    """
    Получает информацию о пользователе по ключу, username или user_id.

    Args:
        key (str): VPN ключ.
        username (str): Имя пользователя.
        user_id (int): ID пользователя.

    Returns:
        dict: Словарь с информацией о пользователе или None, если пользователь не найден.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # Устанавливаем row_factory для получения словаря

        if key:
            cursor = await db.execute("SELECT * FROM users WHERE user_id = (SELECT user_id FROM keys WHERE key = ?)", (key,))
        elif username:
            cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        elif user_id:
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        else:
            return None

        row = await cursor.fetchone()

        if row:
            return dict(row)
        return None
        

async def update_key_name(key: str, new_name: str) -> bool:
    """
    Обновляет имя ключа по значению самого ключа
    Возвращает True при успехе, False при ошибке
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE keys SET name = ? WHERE key = ?",
                (new_name, key)
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении имени ключа: {e}")
        return False
    
async def get_user_keys(user_id, to_dict: bool = False):
    """
    Получает список всех ключей пользователя
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if to_dict:
            db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT key, device_id, expiration_date, name FROM keys WHERE user_id = ?", 
            (user_id,)
        )
        result = await cursor.fetchall()
        return result if result else []

async def get_keys_count(user_id):
    """
    Получает количество ключей пользователя
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM keys WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def update_keys_count(user_id, count):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET keys_count = ? WHERE user_id = ?", (count, user_id))
        await db.commit()

async def update_referral_count(user_id):
    """
    Увеличивает счетчик рефералов пользователя на 1
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users 
            SET referral_count = referral_count + 1 
            WHERE user_id = ?
        """, (user_id,))
        await db.commit()

async def get_referral_count(user_id):
    """
    Получает количество рефералов пользователя
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT referral_count FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 0


async def update_balance(user_id, balance):
    """
    Устанавливает новое значение баланса для пользователя
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET balance = ? WHERE user_id = ?
        """, (balance, user_id))
        await db.commit()

async def update_subscription(user_id, subscription_type, subscription_end):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET subscription_type = ?, subscription_end = ? WHERE user_id = ?
        """, (subscription_type, subscription_end, user_id))
        await db.commit()

async def update_user_subscription(user_id, new_end_date):
    """
    Обновляет дату окончания подписки для пользователя

    Args:
        user_id (int): ID пользователя
        new_end_date (str): Новая дата окончания подписки
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET subscription_end = ? WHERE user_id = ?
        """, (new_end_date, user_id))
        await db.commit()



async def get_key(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM keys WHERE key = ?", (key,))
        return await cursor.fetchone()

async def get_all_keys():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM keys")
        return await cursor.fetchall()

async def sync_payment_id_for_all_keys(user_id, payment_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE keys SET payment_id = ? WHERE user_id = ?
        """, (payment_id, user_id))
        await db.commit()

async def set_payment_id_for_key(key, payment_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE keys SET payment_id = ? WHERE key = ?
        """, (payment_id, key))
        await db.commit()

async def get_payment_id_for_key(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT payment_id FROM keys WHERE key = ?", (key,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_all_keys_with_payment_method():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT key, expiration_date, payment_id 
            FROM keys 
            WHERE payment_id IS NOT NULL AND payment_id != 'none'
        """)
        result = await cursor.fetchall()
        return result if result else []

async def get_user_id_by_key(key):
    """
    Получает ID пользователя по ключу
    
    Args:
        key (str): VPN ключ
        
    Returns:
        int: ID пользователя или None, если ключ не найден
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id FROM keys WHERE key = ?", (key,))
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка при получении ID пользователя по ключу: {e}")
        return None

async def update_key_expiration(key, new_expiry_timestamp):
    """
    Обновляет дату истечения ключа в базе данных
    
    Args:
        key (str): VPN ключ
        new_expiry_timestamp (int): Новое время истечения в миллисекундах
        
    Returns:
        bool: True если обновление успешно, False в противном случае
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE keys SET expiration_date = ? WHERE key = ?",
                (new_expiry_timestamp, key)
            )
            await db.commit()
            expiry_date = datetime.fromtimestamp(new_expiry_timestamp/1000).strftime('%d.%m.%Y %H:%M')
            logger.info(f"Обновлена дата истечения для ключа {key} на {expiry_date}")
            return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении даты истечения ключа: {e}")
        return False

async def get_key_by_uniquie_id(unique_id):
    """
    Ищет ключ, содержащий указанный unique_id в любой части строки key
    
    Args:
        unique_id (str): Уникальный идентификатор для поиска
        
    Returns:
        dict: Словарь с данными ключа или None, если ключ не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # Устанавливаем row_factory для получения словаря
        cursor = await db.execute("""
            SELECT key, user_id, device_id, expiration_date 
            FROM keys 
            WHERE key LIKE ?
        """, (f'%{unique_id}%',))
        row = await cursor.fetchone()
        
        if row:
            return dict(row)  # Преобразуем Row в dict
        return None

async def remove_key_bd(key):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM keys WHERE key = ?
        """, (key,))
        await db.commit()

async def get_all_keys_to_expire() -> list[dict]:
    """
    Возвращает все ключи, чей expiration_date (мс Unix‑эпохи) попадает на сегодняшнюю дату UTC.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT *
                FROM   keys
                WHERE  date(expiration_date / 1000, 'unixepoch') <= date('now', 'utc');
            """
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Ошибка при получении ключей: {e}")
        return []

async def add_or_update_user(user_id, username, subscription_type, is_admin, balance, subscription_end, referrer_id=None, promo_days=3):
    """
    Добавляет или обновляет пользователя в базе данных
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users 
            (user_id, username, subscription_type, is_admin, balance, subscription_end, referrer_id, promo_days) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, subscription_type, is_admin, balance, subscription_end, referrer_id, promo_days))
        await db.commit()

async def add_referral_bonus(referrer_id, amount):
    """
    Начисляет бонус рефереру
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET balance = balance + ? WHERE user_id = ?
        """, (amount, referrer_id))
        await db.commit()

async def get_user(user_id):
    """
    Получает данные пользователя из базы данных и возвращает их в виде словаря
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        # Обновляем порядок колонок в соответствии с порядком в CREATE TABLE
        columns = [
            'user_id',
            'username',
            'email',
            'balance',
            'subscription_type',
            'subscription_end',
            'is_admin',
            'referrer_id',
            'referral_count',
            'keys_count',
            'free_keys_count',
            'promo_days',
            'from_channel',
            'pay_count',
        ]
        row = await cursor.fetchone()
        if row:
            return dict(zip(columns, row))
        return None

async def get_all_users():
    """
    Получает актуальные данные всех пользователей
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row  # Это позволит получать данные в виде словаря
            
            query = """
                SELECT 
                    u.user_id,
                    u.username,
                    u.balance,
                    u.subscription_type,
                    u.subscription_end,
                    u.is_admin,
                    u.referrer_id,
                    (SELECT COUNT(*) FROM users r WHERE r.referrer_id = u.user_id) as referral_count,
                    (SELECT COUNT(*) FROM keys k WHERE k.user_id = u.user_id AND k.expiration_date > ?) as keys_count,
                    u.free_keys_count,
                    u.promo_days,
                    u.from_channel,
                    u.pay_count
                FROM users u
                ORDER BY u.user_id
            """
            
            current_time = int(datetime.now(timezone.utc).timestamp() * 1000)
            cursor = await db.execute(query, (current_time,))
            users = await cursor.fetchall()
            
            # Преобразуем Row objects в список словарей
            return [dict(row) for row in users]
            
    except Exception as e:
        logger.error(f"Ошибка при получении списка пользователей: {e}")
        return []

async def get_forum_topic_id(username: str):
    """
    Получает ID топика форума и канал для указанного пользователя
    
    Args:
        username (str): Имя пользователя или 'system' для системных сообщений
        
    Returns:
        tuple: (topic_id, channel) или None, если топик не найден
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT topic_id, channel FROM forum_topics WHERE username = ?", 
                (username,)
            )
            result = await cursor.fetchone()
            return result if result else None
    except Exception as e:
        logger.error(f"Ошибка при получении ID топика для {username}: {e}")
        return None

async def save_forum_topic_id(username: str, topic_id: int, channel: str):
    """
    Сохраняет ID топика форума и канал для указанного пользователя
    
    Args:
        username (str): Имя пользователя или 'system' для системных сообщений
        topic_id (int): ID топика форума
        channel (str): Канал, в котором создан топик
        
    Returns:
        bool: True если успешно, иначе False
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Используем INSERT OR REPLACE для обновления существующей записи
            await db.execute(
                """
                INSERT OR REPLACE INTO forum_topics (username, topic_id, channel) 
                VALUES (?, ?, ?)
                """, 
                (username, topic_id, channel)
            )
            await db.commit()
            logger.info(f"Сохранен ID топика {topic_id} в канале {channel} для пользователя {username}")
            return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении ID топика для {username}: {e}")
        return False

async def delete_forum_topic(username: str):
    """
    Удаляет запись о топике форума для указанного пользователя
    
    Args:
        username (str): Имя пользователя или 'system' для системных сообщений
        
    Returns:
        bool: True если успешно, иначе False
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM forum_topics WHERE username = ?", 
                (username,)
            )
            await db.commit()
            logger.info(f"Удалена запись о топике для пользователя {username}")
            return True
    except Exception as e:
        logger.error(f"Ошибка при удалении записи о топике для {username}: {e}")
        return False

async def add_channel_column_to_forum_topics():
    """
    Добавляет колонку channel в таблицу forum_topics, если её нет.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, существует ли колонка channel
        cursor = await db.execute("PRAGMA table_info(forum_topics)")
        columns = {row[1] for row in await cursor.fetchall()}
        
        if 'channel' not in columns:
            try:
                # Добавляем колонку channel
                await db.execute("""
                    ALTER TABLE forum_topics
                    ADD COLUMN channel TEXT DEFAULT '@atlanta_logsss'
                """)
                
                # Обновляем существующие записи
                await db.execute("""
                    UPDATE forum_topics
                    SET channel = '@atlanta_logsss'
                    WHERE channel IS NULL
                """)
                
                await db.commit()
                logger.info("Колонка channel добавлена в таблицу forum_topics")
            except Exception as e:
                logger.error(f"Ошибка при добавлении колонки channel: {e}")
        else:
            logger.info("Колонка channel уже существует в таблице forum_topics")

async def release_server_slot(address, inbound_id, protocol):
    """
    Освобождает зарезервированное место на сервере при ошибке добавления клиента
    
    Args:
        address (str): Адрес сервера
        inbound_id (int): ID инбаунда
        protocol (str): Протокол (shadowsocks/vless)
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE inbounds 
                SET clients_count = MAX(clients_count - 1, 0)
                WHERE TRIM(LOWER(server_address)) = TRIM(LOWER(?))
                AND inbound_id = ?
                AND protocol = ?
            """, (address, inbound_id, protocol))
            await db.commit()
            logger.info(f"Освобождено место на сервере {address}, инбаунд {inbound_id} ({protocol})")
    except Exception as e:
        logger.error(f"Ошибка при освобождении места на сервере {address}: {e}")

async def get_system_statistics():
    """
    Получает общую статистику системы
    
    Returns:
        dict: Словарь со статистикой:
            - total_sales: Общая сумма продаж
            - total_users: Общее количество пользователей
            - total_transactions: Общее количество успешных транзакций
            - active_keys: Количество активных ключей
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем текущее время в миллисекундах
            current_time = int(datetime.now().timestamp() * 1000)
            
            # Формируем запрос для получения всех метрик одним запросом
            query = """
                SELECT 
                    (SELECT COUNT(*) FROM users) as total_users,
                    (SELECT COUNT(*) 
                     FROM user_transactions 
                     WHERE status = 'succeeded') as total_transactions,
                    (SELECT COALESCE(SUM(amount), 0) 
                     FROM user_transactions 
                     WHERE status = 'succeeded') as total_sales,
                    (SELECT COUNT(*) 
                     FROM keys 
                     WHERE expiration_date > ?) as active_keys
            """
            
            cursor = await db.execute(query, (current_time,))
            result = await cursor.fetchone()
            
            if result:
                return {
                    'total_users': result[0],
                    'total_transactions': result[1],
                    'total_sales': result[2],
                    'active_keys': result[3]
                }
            
            return {
                'total_users': 0,
                'total_transactions': 0,
                'total_sales': 0,
                'active_keys': 0
            }
            
    except Exception as e:
        logger.error(f"Ошибка при получении системной статистики: {e}")
        return {
            'total_users': 0,
            'total_transactions': 0,
            'total_sales': 0,
            'active_keys': 0
        }

async def get_all_users_with_subscription():
    """
    Получает список всех пользователей с активной подпиской
    
    Returns:
        list: Список словарей с информацией о пользователях
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT user_id, subscription_end
                FROM users
                WHERE subscription_end IS NOT NULL
                AND subscription_end != ''
            """)
            
            users = await cursor.fetchall()
            return [dict(user) for user in users]
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с подпиской: {e}")
        return []

async def get_users_by_server_address(server_address: str):
    """
    Получает список ID пользователей, у которых есть ключи с указанным адресом сервера
    
    Args:
        server_address (str): Адрес сервера (IP или домен)
        
    Returns:
        list: Список ID пользователей
    """
    try:
        # Нормализуем адрес сервера (убираем порт если есть)
        base_address = server_address.split(':')[0].strip().lower()
        
        async with aiosqlite.connect(DB_PATH) as db:
            # Ищем ключи, содержащие указанный адрес
            cursor = await db.execute("""
                SELECT DISTINCT user_id 
                FROM keys 
                WHERE key LIKE ? 
                AND user_id IS NOT NULL
            """, (f'%{base_address}%',))
            
            users = await cursor.fetchall()
            logger.info(f"Найдено {len(users)} пользователей с ключами на сервере {server_address}")
            return [user[0] for user in users]
            
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с ключами на сервере {server_address}: {e}")
        return []
