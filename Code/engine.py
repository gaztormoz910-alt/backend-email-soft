import asyncio
import logging
from typing import Set

from fastapi.websockets import WebSocket

log = logging.getLogger("engine")

class BackgroundParserEngine:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active_connections: Set[WebSocket] = set()
        
        self.is_running = False
        self.log_history = []
        self.email_count = 0
        self.auto_resume_task = None
        self._user_stopped = False  # Флаг: пользователь явно нажал Стоп/Отмена
        
        self.job_start_time = None
        try:
            from config import OUTPUT_DIR
            start_time_file = OUTPUT_DIR / "start_time.txt"
            if start_time_file.exists():
                with open(start_time_file, "r") as f:
                    self.job_start_time = float(f.read().strip())
        except Exception:
            pass

    async def run_engine_loop(self):
        log.info("BackgroundParserEngine started, waiting for jobs...")
        while True:
            job = await self.queue.get()
            self.is_running = True
            self._user_stopped = False  # Сбрасываем при старте нового задания
            log.info(f"Job received (single_server={job.get('single_server')}). Starting extraction execution.")
            
            import email_extractor.cli.main as core_main
            import config
            
            # Если это режим "Один сервер", игнорируем любые деления и парсим всё
            if job.get("single_server"):
                log.info("[SINGLE_SERVER] Overriding config to run ALL sources fully.")
                config.PARSER_SOURCES = {"pipermail", "hyperkitty", "comb", "github", "dorks"}
                config.PIPERMAIL_SERVERS = config._ALL_PIPERMAIL_SERVERS
                config.HYPERKITTY_SERVERS[0]["lists"] = config._ALL_HK_LISTS
                config.COMB_DOMAINS = config._ALL_COMB_DOMAINS
                config.EMAIL_DORKS = config._ALL_EMAIL_DORKS
                config.BACKEND_INDEX = None
            
            try:
                # Execution happens inside the infinite engine loop
                await core_main.main()
            except Exception as e:
                log.error(f"Engine execution failure: {e}", exc_info=True)
                # Авто-возобновление ТОЛЬКО если пользователь НЕ нажимал стоп/отмену
                if not self._user_stopped and not getattr(core_main, '_STOP_REQUESTED', False) and not getattr(core_main, '_CANCEL_REQUESTED', False):
                    async def _internal_auto_resume():
                        try:
                            msg = "[АВТОМАТИКА] Ошибка внутри процесса. Даем серверу остыть 1 минуту перед авто-запуском..."
                            self.log_history.append(msg)
                            await self.broadcast({"type": "LOG", "message": msg})
                            await asyncio.sleep(60)
                            # Перепроверяем флаг перед реальным запуском
                            if self._user_stopped:
                                log.info("Auto-resume cancelled: user stopped during cooldown.")
                                return
                            msg_start = "[АВТОМАТИКА] Возобновляем работу после внутренней ошибки..."
                            self.log_history.append(msg_start)
                            await self.broadcast({"type": "LOG", "message": msg_start})
                            await self.add_job(single_server=job.get("single_server", False))
                        except asyncio.CancelledError:
                            pass
                    self.auto_resume_task = asyncio.create_task(_internal_auto_resume())
                else:
                    log.info("Skipping auto-resume: user explicitly stopped/cancelled.")
            finally:
                if getattr(self, 'auto_resume_task', None) and not self.auto_resume_task.done():
                    pass # Автоматика запущена, не меняем is_running на False
                else:
                    self.is_running = False
                self.queue.task_done()
                log.info("Job processing finished. Engine is ready for next job.")

    async def add_job(self, single_server=False):
        """Add parsing job to queue"""
        import time
        from pathlib import Path
        self._user_stopped = False  # Сброс при явном запуске нового задания
        if self.job_start_time is None:
            self.job_start_time = time.time()
            try:
                from config import OUTPUT_DIR
                start_time_file = OUTPUT_DIR / "start_time.txt"
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                with open(start_time_file, "w") as f:
                    f.write(str(self.job_start_time))
            except Exception as e:
                log.error(f"Failed to save start_time: {e}")
        await self.queue.put({"action": "run_parsing", "single_server": single_server})

    def cancel_all_jobs(self, soft_stop=False):
        """Clear queue and propagate stop/cancel signal to active worker"""
        # Ставим флаг ПЕРВЫМ ДЕЛОМ — блокирует авто-возобновление
        self._user_stopped = True

        # Empty the queue first
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        # Если находимся в стадии охлаждения (auto-resume), отменяем её
        if getattr(self, 'auto_resume_task', None):
            self.auto_resume_task.cancel()
            self.auto_resume_task = None

        # Interact with the core module logic to halt
        import email_extractor.cli.main as core_main
        if soft_stop:
            core_main._STOP_REQUESTED = True
        else:
            core_main._CANCEL_REQUESTED = True
            self.clear_history()
            self.job_start_time = None
            try:
                from config import OUTPUT_DIR
                start_time_file = OUTPUT_DIR / "start_time.txt"
                if start_time_file.exists():
                    start_time_file.unlink()
            except:
                pass

        # If a task is currently executing in core_main, we cancel it directly
        if getattr(core_main, "_CURRENT_TASK", None):
            core_main._CURRENT_TASK.cancel()
            
        self.is_running = False

    # -- WS and State proxies --
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        log.info(f"Client connected. Active count: {len(self.active_connections)}")
        
        # Send initial snapshot to connecting client
        await websocket.send_json({"type": "LOG_HISTORY", "data": self.log_history})
        await websocket.send_json({"type": "EMAIL_COUNT", "count": self.email_count})
        
    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except:
                self.active_connections.discard(connection)

    async def send_log(self, text: str):
        self.log_history.append(text)
        if len(self.log_history) > 1000:
            self.log_history = self.log_history[-1000:]
        await self.broadcast({"type": "LOG", "message": text})

    async def send_count(self, count: int):
        self.email_count = count
        await self.broadcast({"type": "EMAIL_COUNT", "count": count})
        
    def clear_history(self):
        self.log_history.clear()
        self.email_count = 0

parser_engine = BackgroundParserEngine()
