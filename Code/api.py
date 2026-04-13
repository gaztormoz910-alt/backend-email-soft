import asyncio
import logging

from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from websocket_manager import ws_manager

# Подключаем функцию main из CLI
from email_extractor.cli.main import main, _IS_RUNNING

app = FastAPI(
    title="Email Extractor API",
    description="API для удаленного запуска фонового парсинга email-адресов",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Просто поддерживаем соединение, фронт не присылает команды сюда
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
