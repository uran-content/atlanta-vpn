from py3xui import Api, Client
import uuid
from datetime import datetime, timezone
api = Api(
    "http://45.12.133.236:2053",
    "patapon",
    "Patapon1336.",
    use_tls_verify=False
)

api.login()

current_time = datetime.now(timezone.utc).timestamp() * 1000  # Текущее время в миллисекундах
expiry_time = int(current_time + (7 * 86400000))  # Добавляем дни в миллисекундах

new_client = Client(id=str(uuid.uuid4()), email="test", enable=True, expiry_time=expiry_time)
inbound_id = 1

api.client.add(inbound_id, [new_client])
