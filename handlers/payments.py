# handlers.payments.py
from yookassa import Payment, Configuration
from config import SHOP_ID, SECRET_KEY, DEFAULT_EMAIL

# Инициализируем конфигурацию YooKassa
Configuration.account_id = SHOP_ID
Configuration.secret_key = SECRET_KEY

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
    print("СДЕЛАЛИ АВТОМАТИЧЕСКИЙ ПЛАТЕЖ")
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

async def check_payment_status(payment_id: str, amount: int) -> bool:
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

        if payment and payment.status == "succeeded":
            if payment.amount.value == amount:
                if payment.payment_method.saved:
                    return True, payment.payment_method.id, payment
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