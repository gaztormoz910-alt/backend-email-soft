import asyncio
import logging

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse

# Подключаем функцию main из CLI
from email_extractor.cli.main import main, _IS_RUNNING

app = FastAPI(
    title="Email Extractor API",
    description="API для удаленного запуска фонового парсинга email-адресов",
    version="1.0.0"
)

log = logging.getLogger("api")


@app.get("/")
async def healthcheck():
    """
    Эндпоинт для проверки активности сервера (Нужен для Railway/Render).
    """
    return {
        "status": "online",
        "service": "email-extractor",
        "is_currently_running": _IS_RUNNING
    }


@app.post("/start-extraction")
async def start_extraction(background_tasks: BackgroundTasks):
    """
    Запускает сборщик контактов. 
    Ответ возвращается мгновенно, а сам `main()` крутится в фоне сервера.
    """
    # Если парсер уже работает, сообщаем об этом
    if _IS_RUNNING:
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "message": "Парсинг уже запущен. Дождитесь окончания текущей сессии.",
                "is_running": True
            }
        )
    
    # Добавляем задачу в фон
    background_tasks.add_task(main)
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message": "Задача успешно поставлена в очередь и запущена в фоновом режиме.",
            "is_running": True
        }
    )
