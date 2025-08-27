import os, logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application
from telegram.request import HTTPXRequest
from bot_logic import build_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot-webhook")

TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "secret123")
APP_URL = os.environ["APP_URL"].rstrip("/")

request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, pool_timeout=30.0)
application: Application = build_application(TOKEN, request)

app = FastAPI()

@app.on_event("startup")
async def startup():
    url = f"{APP_URL}/webhook/{WEBHOOK_SECRET}"
    await application.initialize()
    await application.bot.set_webhook(url=url, allowed_updates=Update.ALL_TYPES)
    await application.start()
    logger.info("Webhook set: %s", url)

@app.on_event("shutdown")
async def shutdown():
    await application.stop()
    await application.shutdown()

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "ok"}
