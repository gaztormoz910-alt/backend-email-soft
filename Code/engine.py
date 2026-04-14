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

    async def run_engine_loop(self):
        log.info("BackgroundParserEngine started, waiting for jobs...")
        while True:
            job = await self.queue.get()
            self.is_running = True
            log.info("Job received. Starting extraction execution.")
            
            import email_extractor.cli.main as core_main
            
            try:
                # Execution happens inside the infinite engine loop
                await core_main.main()
            except Exception as e:
                log.error(f"Engine execution failure: {e}", exc_info=True)
            finally:
                self.is_running = False
                self.queue.task_done()
                log.info("Job processing finished. Engine is ready for next job.")

    async def add_job(self):
        """Add parsing job to queue"""
        await self.queue.put({"action": "run_parsing"})

    def cancel_all_jobs(self, soft_stop=False):
        """Clear queue and propagate stop/cancel signal to active worker"""
        # Empty the queue first
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        # Interact with the core module logic to halt
        import email_extractor.cli.main as core_main
        if soft_stop:
            core_main._STOP_REQUESTED = True
        else:
            core_main._CANCEL_REQUESTED = True
            self.clear_history()            

        # If a task is currently executing in core_main, we cancel it directly
        if getattr(core_main, "_CURRENT_TASK", None):
            core_main._CURRENT_TASK.cancel()

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
