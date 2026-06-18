from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.assistant import chat
from app.memory import memory

app = FastAPI(title="AI Personal Assistant")
app.mount("/static", StaticFiles(directory="frontend"), name="static")

class Message(BaseModel):
    text: str

@app.get("/")
def root():
    return FileResponse("frontend/index.html")

@app.post("/chat")
def chat_endpoint(msg: Message):
    memory.add_user(msg.text)
    response = chat(memory.get_history())
    memory.add_assistant(response)
    return {"response": response}

@app.post("/clear")
def clear_memory():
    memory.clear()
    return {"status": "Memory cleared"}

@app.get("/history")
def get_history():
    return {"history": memory.get_history()}
