import asyncio
import logging

from typing import Optional, List
from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from config import LOCAL_SCAN_DIR

from websocket_manager import ws_manager

# Подключаем функцию main из CLI и флаги
import email_extractor.cli.main as core_main

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
        "is_currently_running": core_main._IS_RUNNING
    }


@app.post("/start-extraction")
async def start_extraction(background_tasks: BackgroundTasks, files: Optional[List[UploadFile]] = File(None)):
    """
    Запускает сборщик контактов. 
    Ответ возвращается мгновенно, а сам `main()` крутится в фоне сервера.
    """
    # Если парсер уже работает, сообщаем об этом
    if core_main._IS_RUNNING:
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "message": "Парсинг уже запущен. Дождитесь окончания текущей сессии.",
                "is_running": True
            }
        )
    
    # Сохраняем загруженные файлы в локальную директорию
    if files:
        LOCAL_SCAN_DIR.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.filename:
                file_path = LOCAL_SCAN_DIR / f.filename
                with open(file_path, "wb") as buffer:
                    data = await f.read()
                    buffer.write(data)
    
    # Добавляем задачу в фон
    background_tasks.add_task(core_main.main)
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message": "Задача успешно поставлена в очередь и запущена в фоновом режиме.",
            "is_running": True
        }
    )

@app.post("/stop")
async def stop_extraction():
    if not core_main._IS_RUNNING:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Парсинг сейчас не запущен."})
    core_main._STOP_REQUESTED = True
    return JSONResponse(status_code=200, content={"status": "success", "message": "Отправлен сигнал на остановку."})

@app.post("/cancel")
async def cancel_extraction():
    if not core_main._IS_RUNNING:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Парсинг сейчас не запущен."})
    core_main._CANCEL_REQUESTED = True
    return JSONResponse(status_code=200, content={"status": "success", "message": "Отправлен сигнал на отмену."})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Просто поддерживаем соединение, фронт не присылает команды сюда
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
