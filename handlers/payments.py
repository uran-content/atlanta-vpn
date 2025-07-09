# handlers.payments.py
from yookassa import Payment, Configuration
from config import SHOP_ID, SECRET_KEY, DEFAULT_EMAIL

# Инициализируем конфигурацию YooKassa
Configuration.account_id = SHOP_ID
Configuration.secret_key = SECRET_KEY


PAYMENT_TYPES = {
    "electronic_certificate": "Электронный сертификат",
    "sber_loan": "Кредит в сбербанке",
    "alfabank": "Альфа-клик",
    "apple_pay": "Apple Pay",
    "bank_card": "Банковская карта",
    "cash": "Кэш",
    "mobile_balance": "Телефон",
    "sbp": "СБП",
    "google_pay": "Google Pay",
    "installments": "Частями",
    "qiwi": "QIWI",
    "b2b_sberbank": "Sberbank Business Online",
    "sberbank": "SberPay",
    "tinkoff_bank": "T-Pay",
    "wechat": "WeChat",
    "webmoney": "WebMoney",
    "yoo_money": "YooMoney",
    "Внутренний баланс": "Внутренний баланс"
}


async def create_payment(amount, description, email: str = DEFAULT_EMAIL):
    payment = Payment.create(
        {
            "amount": {"value": amount, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/AtlantaVPN_bot",
            },
            "receipt": {
                "customer": {"email": email},
                "items": [
                    {
                        "description": description,
                        "quantity": "1",
                        "amount": {"value": amount, "currency": "RUB"},
                        "vat_code": "1",
                        "payment_subject": "service",
                    }
                ],
            },
            "capture": True,
            "description": description,
            "save_payment_method": True
        }
    )

    return payment.confirmation.confirmation_url, payment.id

async def create_auto_payment(amount, description, saved_method_id, email: str = DEFAULT_EMAIL):
    payment = Payment.create(
        {
            "amount": {"value": amount, "currency": "RUB"},
            "payment_method_id": saved_method_id,
            "description": description,
            "receipt": {
                "customer": {"email": email},
                "items": [
                    {
                        "description": description,
                        "quantity": "1",
                        "amount": {"value": amount, "currency": "RUB"},
                        "vat_code": "1",
                        "payment_subject": "service",
                    }
                ],
            },
            "capture": True,
        }
    )
    return payment.id

async def get_payment_method_id(transaction_id: str) -> str:
    """
    Получает payment_method.id по transaction_id.
    Он нужен для автоматического платежа без участия клиента.
    """
    payment_info = Payment.find_one(transaction_id)
    if payment_info.payment_method.saved:
        return payment_info.payment_method.id
    return None

async def get_payment_info(transaction_id: str):
    """
    Получает payment_method.id по transaction_id.
    Он нужен для автоматического платежа без участия клиента.
    """
    payment_info = Payment.find_one(transaction_id)
    return payment_info

async def check_payment_status(payment_id: str, amount: int, second_arg: str = "id") -> bool:
    """
    Check payment status in YooKassa

    Args:
        payment_id (str): Payment ID from YooKassa
        amount (int): Expected payment amount

    Returns:
        bool: True if payment succeeded, False otherwise
    """
    try:
        payment = Payment.find_one(payment_id)

        print("CHECK PAYMENT STATUS")
        if payment and payment.status == "succeeded":
            print("SUCCEEDED")
            print(f"payment.amount.value = {payment.amount.value}")
            if (payment.amount.value == int(amount)) or (payment.amount.value == float(amount)):
                print("AMOUNT EQUALS")
                if payment.payment_method.saved:
                    print("SAVED")
                    if second_arg == "id":
                        return True, payment.payment_method.id, payment
                    elif second_arg == "type":
                        print("TYPE")
                        return True, payment.payment_method.type, payment
                return True, None, None
        return False, None, None

    except ValueError as e:
        print(f"Invalid payment ID: {payment_id}, {e}")
        return False

    except Exception as e:
        print(f"Error checking payment status: {str(e)}")
        return False

async def check_transaction_status(payment_id: str):
    try:
        payment = Payment.find_one(payment_id)
        if payment: 
            return payment.status, payment
    except Exception as e:
        print(f"Error checking payment status: {str(e)}")