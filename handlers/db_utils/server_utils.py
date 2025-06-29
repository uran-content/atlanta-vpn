# handlers.db_utils.server_utils.py
import aiosqlite
from handlers.database import DB_PATH
import logging

logger = logging.getLogger(__name__)


async def get_servers_with_total_clients():
    """
    Получает список серверов с суммарным количеством клиентов по всем инбаундам
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        cursor = await db.execute("""
            SELECT 
                s.id,
                s.address,
                s.username,
                s.password,
                s.country,
                s.is_active,
                COALESCE(SUM(i.clients_count), 0) as total_clients,
                COALESCE(SUM(i.max_clients), 0) as total_max_clients
            FROM servers s
            LEFT JOIN inbounds i ON TRIM(LOWER(s.address)) = TRIM(LOWER(i.server_address))
            GROUP BY s.id, s.address, s.username, s.password, s.country, s.is_active
            ORDER BY s.id ASC
        """)
        
        result = await cursor.fetchall()
        logger.info(f"Servers with total clients: {[dict(r) for r in result]}")
        return result

async def get_inbound_info(address: str, protocol: str):
    """
    Получает информацию об инбаунде по адресу сервера и протоколу
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM inbounds 
            WHERE server_address = ? AND protocol = ?
        """, (address, protocol))
        return await cursor.fetchone()
    
async def update_inbound_max_clients(server_id: int, inbound_id: int, new_max_clients: int):
    """
    Обновляет максимальное количество клиентов для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET max_clients = ? 
            WHERE server_id = ? AND inbound_id = ?
        """, (new_max_clients, server_id, inbound_id))
        await db.commit()

async def get_server_inbounds(server_address: str):
    """
    Получает все инбаунды для конкретного сервера
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM inbounds 
            WHERE server_address = ?
        """, (server_address,))
        return await cursor.fetchall()

async def get_server_inbound(server_address: str):
    """
    Получает информацию об инбаунде сервера
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM inbounds 
            WHERE server_address = ?
        """, (server_address,))
        return await cursor.fetchone()
    
async def update_inbound_protocol(inbound_id: int, protocol: str):
    """
    Обновляет протокол для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET protocol = ? 
            WHERE id = ?
        """, (protocol, inbound_id))
        await db.commit()

async def update_inbound_id(inbound_id: int, new_inbound_id: int):
    """
    Обновляет inbound_id для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET inbound_id = ? 
            WHERE id = ?
        """, (new_inbound_id, inbound_id))
        await db.commit()

async def update_inbound_port(inbound_id: int, new_port: int):
    """
    Обновляет порт для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET port = ? 
            WHERE id = ?
        """, (new_port, inbound_id))
        await db.commit()

async def update_inbound_sni(inbound_id: int, new_sni: str):
    """
    Обновляет SNI для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET sni = ? 
            WHERE id = ?
        """, (new_sni, inbound_id))
        await db.commit()

async def update_inbound_pbk(inbound_id: int, new_pbk: str):
    """
    Обновляет pbk для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET pbk = ? 
            WHERE id = ?
        """, (new_pbk, inbound_id))
        await db.commit()

async def update_inbound_utls(inbound_id: int, new_utls: str):
    """
    Обновляет utls для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET utls = ? 
            WHERE id = ?
        """, (new_utls, inbound_id))
        await db.commit()

async def update_inbound_sid(inbound_id: int, new_sid: str):
    """
    Обновляет sid для конкретного инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET sid = ? 
            WHERE id = ?
        """, (new_sid, inbound_id))
        await db.commit()

async def update_inbound_clients_count(address: str, protocol: str, count: int):
    """
    Обновляет количество клиентов для инбаунда
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE inbounds 
            SET clients_count = ?
            WHERE server_address = ? AND protocol = ?
        """, (count, address, protocol))
        await db.commit()

async def add_inbound(inbound_data: dict):
    """
    Добавляет новый инбаунд для сервера с дополнительными параметрами
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            # Проверяем существование сервера
            cursor = await db.execute(
                "SELECT id FROM servers WHERE address = ?", 
                (inbound_data['server_address'],)
            )
            server = await cursor.fetchone()
            if not server:
                raise ValueError(f"Сервер {inbound_data['server_address']} не найден")
            
            server_id = server[0]

            # Проверяем существование инбаунда
            cursor = await db.execute("""
                SELECT id FROM inbounds 
                WHERE server_address = ? AND protocol = ?
            """, (inbound_data['server_address'], inbound_data['protocol']))
            
            if await cursor.fetchone():
                raise ValueError(f"Инбаунд с protocol={inbound_data['protocol']} уже существует на сервере {inbound_data['server_address']}")

            # Добавляем новый инбаунд с дополнительными полями
            await db.execute("""
                INSERT INTO inbounds (
                    server_id, server_address, inbound_id, 
                    protocol, max_clients, pbk, sid, sni,
                    clients_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                server_id,
                inbound_data['server_address'],
                inbound_data['inbound_id'],
                inbound_data['protocol'],
                inbound_data.get('max_clients', 100),
                inbound_data.get('pbk'),
                inbound_data.get('sid'),
                inbound_data.get('sni'),
                0  # начальное количество клиентов
            ))
            
            await db.commit()
            logger.info(f"Добавлен новый инбаунд: {inbound_data}")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении инбаунда: {e}")
            raise

async def remove_inbound(inbound_id: int):
    """
    Удаляет инбаунд из базы данных
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM inbounds WHERE id = ?", (inbound_id,))
        await db.commit()

        