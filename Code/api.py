import asyncio
import logging

from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import LOCAL_SCAN_DIR, DB_OUTPUT, FRONTEND_URL

from email_extractor.infrastructure.sqlite_repository import SqliteContactRepository
import email_extractor.cli.main as core_main
from engine import parser_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Restore state from DB in case of server restart
    from config import DB_OUTPUT, CHECKPOINT_FILE
    repo = SqliteContactRepository(db_path=DB_OUTPUT)
    count = repo.get_count()
    processed_count = repo.get_processed_count()
    
    # Наличие чекпоинта означает, что парсер был убит (OOM) или приостановлен
    was_interrupted = CHECKPOINT_FILE.exists()

    if count > 0 or processed_count > 0:
        parser_engine.email_count = count
        if count > 0:
            parser_engine.log_history.append(f"[Система] Сервер был перезапущен. Восстановлено {count} адресов из БД.")
        else:
            parser_engine.log_history.append(f"[Система] Сервер был перезапущен. Адресов пока нет, но обработано {processed_count} URL.")
            
        # If there's data but no checkpoint, create a dummy one so the next run doesn't wipe it
        if not was_interrupted:
            import json
            try:
                CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                    json.dump({"processed": []}, f)
            except:
                pass

    try:
        start_time_file = OUTPUT_DIR / "start_time.txt"
        if start_time_file.exists():
            with open(start_time_file, "r") as f:
                parser_engine.job_start_time = float(f.read().strip())
    except Exception as e:
        log.error(f"Failed to load start_time: {e}")

    engine_task = asyncio.create_task(parser_engine.run_engine_loop())
    
    # Автоматика для возобновления парсинга после краша Railway
    if was_interrupted:
        parser_engine.is_running = True
        
        async def _auto_resume():
            try:
                msg = "[АВТОМАТИКА] Обнаружен прерванный сеанс парсинга. Даем серверу остыть 2 минуты перед авто-запуском..."
                parser_engine.log_history.append(msg)
                await parser_engine.broadcast({"type": "LOG", "message": msg})
                
                await asyncio.sleep(120)  # Ждём 2 минуты
                
                # Перепроверяем: если пользователь нажал стоп во время охлаждения — не запускаем
                if parser_engine._user_stopped:
                    parser_engine.is_running = False
                    return
                
                msg_start = "[АВТОМАТИКА] Сервер охладился. Автоматически возобновляем парсинг!"
                parser_engine.log_history.append(msg_start)
                await parser_engine.broadcast({"type": "LOG", "message": msg_start})
                await parser_engine.add_job()
            except asyncio.CancelledError:
                parser_engine.log_history.append("[АВТОМАТИКА] Автозапуск был отменён пользователем во время охлаждения.")

        parser_engine.auto_resume_task = asyncio.create_task(_auto_resume())

    yield
    # Shutdown
    engine_task.cancel()

app = FastAPI(
    title="Email Extractor API",
    description="API для серверной инициализации парсинга",
    version="2.0.0",
    lifespan=lifespan
)

origins = [url.strip() for url in FRONTEND_URL.split(",") if url.strip()]
if not origins:
    origins = ["http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

log = logging.getLogger("api")

@app.get("/")
async def healthcheck():
    """Эндпоинт для проверки активности сервера (Railway)"""
    return {"status": "online", "service": "email-extractor"}

@app.get("/state")
async def get_state():
    """Возвращает глобальный статус движка для новых клиентов"""
    files_count = 0
    if LOCAL_SCAN_DIR.exists():
        files_count = len([f for f in LOCAL_SCAN_DIR.iterdir() if f.is_file()])
        
    start_time = getattr(parser_engine, "job_start_time", None)
    elapsed = 0
    if start_time is not None:
        import time
        elapsed = time.time() - start_time

    return {
        "status": "online",
        "is_currently_running": parser_engine.is_running,
        "files_count": files_count,
        "email_count": parser_engine.email_count,
        "log_history": parser_engine.log_history,
        "start_time": start_time,
        "elapsed_seconds": elapsed
    }

@app.post("/jobs")
async def add_job(files: Optional[List[UploadFile]] = File(None)):
    """
    Постановка задания в очередь конвейера.
    Фронтенд отправляет эту команду вместо прямого старта.
    """
    # Если бэкенд в фазе авто-запуска (ждёт 2 минуты после рестарта),
    # отменяем авто-запуск и принимаем явный запрос от пользователя
    if parser_engine.is_running:
        auto_task = getattr(parser_engine, 'auto_resume_task', None)
        if auto_task and not auto_task.done():
            auto_task.cancel()
            parser_engine.auto_resume_task = None
            parser_engine.is_running = False
            msg = "[АВТОМАТИКА] Авто-запуск отменён — получен явный запрос от пользователя."
            parser_engine.log_history.append(msg)
            log.info(msg)
        else:
            return JSONResponse(
                status_code=409,
                content={"status": "error", "message": "Парсинг сейчас выполняется. Дождитесь завершения."}
            )

    if files:
        LOCAL_SCAN_DIR.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.filename:
                file_path = LOCAL_SCAN_DIR / f.filename
                with open(file_path, "wb") as buffer:
                    while True:
                        chunk = await f.read(1024 * 1024)
                        if not chunk:
                            break
                        buffer.write(chunk)
                    
    await parser_engine.add_job()
    
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message": "Задание добавлено в очередь сервера."}
    )

@app.post("/stop")
async def stop_extraction():
    parser_engine.cancel_all_jobs(soft_stop=True)
    return JSONResponse(status_code=200, content={"status": "success", "message": "Отправлен сигнал на паузу/остановку."})

@app.post("/cancel")
async def cancel_extraction():
    parser_engine.cancel_all_jobs(soft_stop=False)
    return JSONResponse(status_code=200, content={"status": "success", "message": "Отправлен сигнал на отмену."})

@app.delete("/files")
async def delete_files():
    deleted_count = 0
    if LOCAL_SCAN_DIR.exists():
        for f in LOCAL_SCAN_DIR.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    deleted_count += 1
                except Exception as e:
                    log.error(f"Failed to delete {f}: {e}")
    return JSONResponse(status_code=200, content={"status": "success", "message": f"Deleted {deleted_count} files."})

@app.get("/download/csv")
async def download_csv():
    repo = SqliteContactRepository(db_path=DB_OUTPUT)
    return StreamingResponse(
        repo.stream_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=extracted_emails.csv"}
    )

@app.get("/download/txt")
async def download_txt():
    repo = SqliteContactRepository(db_path=DB_OUTPUT)
    return StreamingResponse(
        repo.stream_txt(),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=extracted_emails.txt"}
    )

@app.get("/download/info")
async def download_info():
    """Возвращает информацию о файле для прогресс-бара скачивания"""
    repo = SqliteContactRepository(db_path=DB_OUTPUT)
    count = repo.get_count()
    # Оценка размера: ~25 байт на email для TXT, ~40 для CSV
    avg_email_len = 25
    avg_csv_line_len = 40
    return {
        "email_count": count,
        "estimated_txt_bytes": count * avg_email_len,
        "estimated_csv_bytes": count * avg_csv_line_len + 30,  # +header
    }

@app.get("/emails/json")
async def get_emails_json():
    """Возвращает все email как JSON-массив (для кросс-бэкенд дедупликации)."""
    repo = SqliteContactRepository(db_path=DB_OUTPUT)
    return {"emails": repo.get_all_emails(), "count": repo.get_count()}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await parser_engine.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        parser_engine.disconnect(websocket)
