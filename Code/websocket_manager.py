import logging
from fastapi.websockets import WebSocket

log = logging.getLogger("websocket")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

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

    async def send_log(self, text: str):
        """Хелпер для отправки текстовых логов в терминал фронта."""
        await self.broadcast({
            "type": "LOG",
            "message": text
        })

    async def send_emails(self, emails: list[str]):
        """Отправляет массив новых найденных email на фронт (для скачивания)."""
        await self.broadcast({
            "type": "NEW_EMAILS",
            "data": emails
        })

ws_manager = ConnectionManager()
