from handlers.utils import once_per_string
import asyncio

async def main():
    s = "строка 1"

    z = False
    async for _ in once_per_string(s):
        z = True
    
    if z:
        print("Зашли!")
    else:
        print("НЕ ЗАШЛИ!!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен пользователем")
    except Exception as e:
        print(f"Критическая ошибка: {e}")
        raise
