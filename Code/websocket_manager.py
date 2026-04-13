import logging
from fastapi.websockets import WebSocket

log = logging.getLogger("websocket")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.log_history: list[str] = []
        self.email_count: int = 0

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

        # Отправляем историю при подключении для восстановления состояния
        await websocket.send_json({
            "type": "LOG_HISTORY",
            "data": self.log_history
        })
        await websocket.send_json({
            "type": "EMAIL_COUNT",
            "count": self.email_count
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            log.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        # Отправляем сообщение всем подключенным клиентам
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                log.warning(f"Error sending message to client: {e}")
                self.disconnect(connection)

    def clear_history(self):
        self.log_history = []
        self.email_count = 0

    async def send_log(self, text: str):
        """Хелпер для отправки текстовых логов в терминал фронта."""
        self.log_history.append(text)
        if len(self.log_history) > 500:
            self.log_history = self.log_history[-500:]

        await self.broadcast({
            "type": "LOG",
            "message": text
        })

    async def send_count(self, count: int):
        """Отправляем актуальное количество собранных почт на фронт."""
        self.email_count = count
        await self.broadcast({
            "type": "EMAIL_COUNT",
            "count": count
        })

ws_manager = ConnectionManager()
