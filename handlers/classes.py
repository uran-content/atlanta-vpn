# handlers.classes.py
from aiogram.fsm.state import State, StatesGroup


class SubscriptionStates(StatesGroup):
    waiting_for_subscription = State()
    referral_pending = State()
    waiting_for_key = State()
    waiting_for_email = State()
    waiting_for_payment_method_name = State()

class ReplaceKeyForm(StatesGroup):
    waiting_for_key = State()
    country_waiting_key = State()
    protocol_waiting_key = State()

class KeyNameStates(StatesGroup):
    waiting_for_new_name = State()

class AdminStates(StatesGroup):
    waiting_server_address = State()
    waiting_server_username = State()
    waiting_server_password = State()
    waiting_server_max_clients = State()
    waiting_server_max_clients_update = State()
    waiting_inbound_id = State()
    waiting_for_inbound_id = State()
    waiting_for_pbk = State()
    waiting_for_sid = State()
    waiting_for_sni = State()
    waiting_server_id_update = State()
    waiting_server_port_update = State()
    waiting_server_sni_update = State()
    waiting_server_utls_update = State()
    waiting_server_pbk_update = State()
    waiting_server_sid_update = State()


class AdminKeyRemovalStates(StatesGroup):
    waiting_for_user = State()


class AdminBroadcastStates(StatesGroup):
    """
    Состояния для рассылки администратора
    """

    waiting_for_message = State()
    waiting_for_media = State()
    confirm_broadcast = State()
    select_group = State()
    waiting_for_ip = State()


class PromoCodeAdminStates(StatesGroup):
    """
    Состояния для администрирования промокодов
    """

    waiting_promo_code = State()
    waiting_promo_amount = State()
    waiting_promo_gift_balance = State()
    waiting_promo_gift_days = State()
    waiting_promo_expiration_date = State()


class PromoCodeState(StatesGroup):
    """
    Состояние ожидания ввода промокода
    """

    waiting_for_promocode = State()


class BalanceForm(StatesGroup):
    """
    Форма для ожидания ввода суммы пополнения
    """

    waiting_for_amount = State()

class AccountStates(StatesGroup):
    """
    Состояния для учетной записи
    """

    waiting_for_email = State()

