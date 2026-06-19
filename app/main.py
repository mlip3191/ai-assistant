import logging
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.assistant import chat
from app.memory import ConversationMemory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="AI Personal Assistant")
app.mount("/static", StaticFiles(directory="frontend"), name="static")

web_memories: dict[str, ConversationMemory] = {}

class Message(BaseModel):
    text: str

@app.get("/")
def root():
    return FileResponse("frontend/index.html")

@app.post("/chat")
def chat_endpoint(msg: Message, request: Request):
    key = request.client.host
    if key not in web_memories:
        web_memories[key] = ConversationMemory()
    memory = web_memories[key]
    memory.add_user(msg.text)
    response = chat(memory.get_history())
    memory.add_assistant(response)
    return {"response": response}

@app.post("/clear")
def clear_memory(request: Request):
    key = request.client.host
    if key in web_memories:
        web_memories[key].clear()
    return {"status": "Memory cleared"}

@app.get("/history")
def get_history(request: Request):
    key = request.client.host
    memory = web_memories.get(key, ConversationMemory())
    return {"history": memory.get_history()}
