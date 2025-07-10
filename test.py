from datetime import datetime
import pytz

# Устанавливаем дату 6 июля 2025 года, 00:00:00 UTC
date = datetime(2025, 7, 10, 0, 0, 0, tzinfo=pytz.UTC)
# Преобразуем в метку времени Unix в миллисекундах
timestamp_ms = int(date.timestamp() * 1000)
print(timestamp_ms)
