from fastapi import FastAPI
from app.agent.agent import run_agent

app = FastAPI()

@app.get("/")
def home():
    return {"message": "File Agent Running 🚀"}

@app.post("/run")
def run(user_input: str):
    result = run_agent(user_input)
    return {"response": result}