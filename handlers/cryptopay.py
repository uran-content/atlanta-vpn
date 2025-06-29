# handlers.cryptopay.py
from aiocryptopay import AioCryptoPay, Networks
import aiohttp
from config import API_CRYPTO_TOKEN, CRYPTOCLOUD_API_KEY, CRYPTOCLOUD_SHOP_ID
from datetime import datetime, timedelta

crypto = AioCryptoPay(token=API_CRYPTO_TOKEN, network=Networks.MAIN_NET)

# Словарь для маппинга криптовалют с их coingecko id и минимальными суммами
CURRENCIES = {
    'BTC': {'min': 0.000040, 'name': 'BTC', 'coingecko_id': 'bitcoin'},
    'BNB': {'min': 0.060, 'name': 'BNB', 'coingecko_id': 'binancecoin'},
    'ETH': {'min': 0.002, 'name': 'ETH', 'coingecko_id': 'ethereum'},
    'TON': {'min': 1.5, 'name': 'TON', 'coingecko_id': 'the-open-network'},
    'USDT': {'min': 3, 'name': 'USDT', 'coingecko_id': 'tether'},
    "USDT CryptoCloud": {'min': 3, 'name': 'USDT', 'coingecko_id': 'tether'},
    "Monero (XMR)": {'min': 0.02, 'name': 'XMR', 'coingecko_id': 'monero'}
}

# Кэширование курсов для уменьшения количества запросов к API
_crypto_rates_cache = {}
_last_update_time = None
_cache_ttl = timedelta(minutes=15)  # Обновлять кэш каждые 15 минут
_rate_discount = 0.8  # Скидка 20% на курс (множитель 0.8)

async def get_crypto_rate(currency: str, target_currency: str = 'rub'):
    """
    Получение актуального курса криптовалюты к указанной валюте (по умолчанию к рублю)
    через CoinGecko API с кэшированием результатов.
    Применяется скидка 20% для учета комиссий.
    
    Args:
        currency (str): Код криптовалюты (BTC, ETH, и т.д.)
        target_currency (str): Целевая валюта (rub, usd, и т.д.)
        
    Returns:
        float: Курс криптовалюты к целевой валюте со скидкой 20%
    """
    global _crypto_rates_cache, _last_update_time
    
    # Определение coingecko_id для запрашиваемой валюты
    if currency in CURRENCIES:
        coin_id = CURRENCIES[currency]['coingecko_id']
    else:
        # Ищем в значениях name
        coin_id = None
        for key, value in CURRENCIES.items():
            if value['name'] == currency:
                coin_id = value['coingecko_id']
                break
        
        if not coin_id:
            # Если не нашли, используем валюту как id
            coin_id = currency.lower()
    
    # Проверяем кэш
    current_time = datetime.now()
    cache_key = f"{coin_id}_{target_currency}"
    
    if (_last_update_time is None or 
        current_time - _last_update_time > _cache_ttl or 
        cache_key not in _crypto_rates_cache):
        
        # Обновление кэша
        try:
            # API запрос к CoinGecko
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={target_currency}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if coin_id in data and target_currency in data[coin_id]:
                            # Получаем курс и применяем скидку 20%
                            original_rate = data[coin_id][target_currency]
                            discounted_rate = original_rate * _rate_discount
                            _crypto_rates_cache[cache_key] = discounted_rate
                            _last_update_time = current_time
                            print(f"Курс {currency}/{target_currency}: {original_rate} -> {discounted_rate} (скидка 20%)")
                            return discounted_rate
                        else:
                            # Альтернативный источник или резервное значение
                            return _get_fallback_rate(currency, target_currency)
                    else:
                        # Если API недоступен, используем резервное значение
                        return _get_fallback_rate(currency, target_currency)
        except Exception as e:
            print(f"Ошибка при получении курса криптовалюты: {e}")
            return _get_fallback_rate(currency, target_currency)
    
    # Возвращаем кэшированное значение
    return _crypto_rates_cache.get(cache_key, _get_fallback_rate(currency, target_currency))

def _get_fallback_rate(currency: str, target_currency: str = 'rub'):
    """
    Возвращает резервное значение курса криптовалюты на случай, 
    если API недоступно. Также применяет скидку 20%.
    
    Args:
        currency (str): Код криптовалюты
        target_currency (str): Целевая валюта
        
    Returns:
        float: Приблизительный курс криптовалюты со скидкой 20%
    """
    # Приблизительные курсы на случай недоступности API (уже со скидкой 20%)
    fallback_rates = {
        'BTC': {'rub': 3600000, 'usd': 48000},  # 4500000 * 0.8, 60000 * 0.8
        'ETH': {'rub': 200000, 'usd': 2400},    # 250000 * 0.8, 3000 * 0.8
        'BNB': {'rub': 28000, 'usd': 320},      # 35000 * 0.8, 400 * 0.8
        'TON': {'rub': 320, 'usd': 4},          # 400 * 0.8, 5 * 0.8
        'USDT': {'rub': 72, 'usd': 0.8},        # 90 * 0.8, 1 * 0.8
        'XMR': {'rub': 14400, 'usd': 160}       # 18000 * 0.8, 200 * 0.8
    }
    
    # Используем имя криптовалюты для поиска в резервных значениях
    crypto_key = currency
    if currency in CURRENCIES:
        crypto_key = CURRENCIES[currency]['name']
    
    # Возвращаем резервное значение или 0.8 в крайнем случае (1 * 0.8)
    return fallback_rates.get(crypto_key, {}).get(target_currency, 0.8)

async def calculate_fiat_amount(crypto_amount: float, crypto_currency: str, target_currency: str = 'rub'):
    """
    Рассчитывает стоимость криптовалюты в фиатной валюте
    
    Args:
        crypto_amount (float): Количество криптовалюты
        crypto_currency (str): Код криптовалюты
        target_currency (str): Целевая фиатная валюта
        
    Returns:
        float: Стоимость в фиатной валюте
    """
    rate = await get_crypto_rate(crypto_currency, target_currency)
    return crypto_amount * rate

async def calculate_crypto_amount(fiat_amount: float, crypto_currency: str, fiat_currency: str = 'rub'):
    """
    Рассчитывает количество криптовалюты на указанную сумму в фиатной валюте
    
    Args:
        fiat_amount (float): Сумма в фиатной валюте
        crypto_currency (str): Код криптовалюты
        fiat_currency (str): Код фиатной валюты
        
    Returns:
        float: Количество криптовалюты
    """
    rate = await get_crypto_rate(crypto_currency, fiat_currency)
    if rate <= 0:
        return 0
    return fiat_amount / rate

async def create_cryptobot_payment(currency: str, amount: float):
    """
    Создает платеж через CryptoBot
    
    Args:
        currency (str): Код криптовалюты
        amount (float): Сумма в криптовалюте
        
    Returns:
        dict: Данные платежа (payment_url, invoice_id)
    """
    try:
        invoice = await crypto.create_invoice(
            asset=currency,
            amount=amount
        )
        return {
            'payment_url': invoice.bot_invoice_url,
            'invoice_id': invoice.invoice_id
        }
    except Exception as e:
        print(f"Ошибка при создании платежа через CryptoBot: {e}")
        raise

async def check_cryptobot_payment(invoice_id: str):
    """
    Проверяет статус платежа через CryptoBot
    
    Args:
        invoice_id (str): ID инвойса
        
    Returns:
        bool: True если платеж выполнен, иначе False
    """
    try:
        invoice = await crypto.get_invoices(invoice_ids=invoice_id)
        return invoice.status == 'paid'
    except Exception as e:
        print(f"Ошибка при проверке инвойса CryptoBot: {e}")
        return False
    
async def create_cryptocloud_payment(amount: float):
    """
    Создает платеж через CryptoCloud
    
    Args:
        amount (float): Сумма в USD
        
    Returns:
        dict: Данные платежа (payment_url, uuid)
    """
    url = "https://api.cryptocloud.plus/v2/invoice/create"
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "amount": amount,
        "shop_id": CRYPTOCLOUD_SHOP_ID,
        "currency": "USD",
        "add_fields": {"time_to_pay": {"hours": 0, "minutes": 10}}
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return {
                        'payment_url': result['result']['link'],
                        'uuid': result['result']['uuid']
                    }
                else:
                    print(f"Ошибка CryptoCloud API: {response.status}")
                    raise Exception(f"CryptoCloud API error: {response.status}")
    except Exception as e:
        print(f"Ошибка при создании платежа через CryptoCloud: {e}")
        raise

async def check_cryptocloud_payment(uuid: str):
    """
    Проверяет статус платежа через CryptoCloud
    
    Args:
        uuid (str): UUID платежа
        
    Returns:
        bool: True если платеж выполнен, иначе False
    """
    headers = {"Authorization": f"Token {CRYPTOCLOUD_API_KEY}"}
    data = {"uuid": [uuid]}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.cryptocloud.plus/v2/invoice/merchant/info", 
                headers=headers, 
                json=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    # CryptoCloud считает платеж успешным при статусах "paid" или "overpaid"
                    return result['result'][0]['status'] in ('paid', 'overpaid')
                else:
                    print(f"Ошибка проверки платежа CryptoCloud: {response.status}")
                    return False
    except Exception as e:
        print(f"Ошибка при проверке платежа CryptoCloud: {e}")
        return False