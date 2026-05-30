"""
团队聊天室后端 — FastAPI + WebSocket
启动: python server.py
"""
import asyncio
import io, json, os, sys, threading
from contextlib import redirect_stdout
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manager import load_workers, run_worker, default_client, DEFAULT_MODEL

app = FastAPI()
workers = load_workers()
HISTORY = os.path.join(os.path.dirname(__file__), "chat_history.json")

MEMBERS = [
    {"name":"Boss","role":"老板","color":"#F5A623"},
    {"name":"Manager","role":"技术总监","color":"#4A90D9"},
    {"name":"维克托","role":"副经理","color":"#7B68EE"},
]
WC = {"亚历克斯":"#27AE60","索菲亚":"#E74C3C","马库斯":"#E67E22","埃琳娜":"#E91E90","纳撒尼尔":"#00BCD4"}
for n, c in workers.items():
    MEMBERS.append({"name":n,"role":c["role"],"color":WC.get(n,"#999")})

conns = []

def ld():
    if not os.path.exists(HISTORY): return []
    try:
        with open(HISTORY,"r",encoding="utf-8") as f: return json.load(f)
    except: return []

def sv():
    try:
        with open(HISTORY,"w",encoding="utf-8") as f: json.dump(msgs[-200:],f,ensure_ascii=False,indent=2)
    except: pass

msgs = ld()[-500:]

def am(msg): msgs.append(msg); sv()

async def bc(msg):
    dead = []
    for w in conns:
        try: await w.send_json(msg)
        except: dead.append(w)
    for w in dead: conns.remove(w)

async def run_worker_task(wname, task):
    if wname not in workers: return
    c = workers[wname]
    await bc({"type":"typing","worker":wname,"status":True})

    q = asyncio.Queue()
    rh = {"r":""}
    def _run():
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                r = run_worker(c, task, use_memory=True)
            rh["r"] = r.get("result","")
            o = buf.getvalue()
        except Exception as e:
            o = f"Error: {e}"; rh["r"] = o
        for line in o.strip().split("\n"):
            if line.strip():
                asyncio.run_coroutine_threadsafe(q.put(line.strip()), loop)
        asyncio.run_coroutine_threadsafe(q.put(None), loop)

    loop = asyncio.get_event_loop()
    threading.Thread(target=_run,daemon=True).start()
    while True:
        line = await q.get()
        if line is None: break
        cl = line.lstrip()
        if "工具:" in cl: await bc({"type":"tool_call","worker":wname,"role":c["role"],"text":cl})
        elif "返回:" in cl: await bc({"type":"tool_result","worker":wname,"role":c["role"],"text":cl})
        else: await bc({"type":"worker_msg","worker":wname,"role":c["role"],"text":cl})

    await bc({"type":"typing","worker":wname,"status":False})
    await bc({"type":"task_done","worker":wname,"role":c["role"],"text":rh["r"][:500]})
    am({"type":"task_done","worker":wname,"role":c["role"],"result":rh["r"][:500]})

async def chat_reply(txt):
    await bc({"type":"typing","worker":"Manager","status":True})
    rt = ""
    def _chat():
        nonlocal rt
        try:
            r = default_client.messages.create(model=DEFAULT_MODEL,max_tokens=256,
                system="你是Manager,在群聊中回复。中文,友好,1-2句话。",
                messages=[{"role":"user","content":txt}])
            for b in r.content:
                if b.type == "text": rt = b.text
        except: rt = "..."

    await asyncio.get_event_loop().run_in_executor(None,_chat)
    await bc({"type":"typing","worker":"Manager","status":False})
    if rt:
        await bc({"type":"worker_msg","worker":"Manager","role":"技术总监","text":rt})
        am({"type":"worker_msg","worker":"Manager","role":"技术总监","text":rt})

UPDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),"uploads")
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs(UPDIR,exist_ok=True)
    fp = os.path.join(UPDIR,file.filename)
    ct = await file.read()
    with open(fp,"wb") as f: f.write(ct)
    await bc({"type":"system","text":f"文件: {file.filename} ({len(ct)} bytes)"})
    return JSONResponse({"ok":True,"file":file.filename,"size":len(ct)})

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    conns.append(ws)
    await ws.send_json({"type":"member_list","members":MEMBERS})
    for m in msgs[-50:]: await ws.send_json(m)
    await bc({"type":"system","text":"Boss 上线"})
    try:
        while True:
            d = await ws.receive_json()
            t = d.get("type","")
            if t == "task":
                w = d.get("worker",""); c = d.get("content","")
                if w in workers:
                    am({"type":"task","worker":w,"content":c})
                    await bc({"type":"system","text":f"派给 {w}: {c[:80]}"})
                    asyncio.create_task(run_worker_task(w,c))
            elif t == "chat":
                txt = d.get("text","")
                am({"type":"chat","text":txt})
                asyncio.create_task(chat_reply(txt))
            elif t == "broadcast":
                txt = d.get("text","")
                am({"type":"broadcast","text":txt})
                await bc({"type":"broadcast","text":txt})
    except WebSocketDisconnect:
        conns.remove(ws)
        await bc({"type":"system","text":"Boss 离线"})

@app.get("/")
async def root():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),"chat.html")
    return FileResponse(p)

if __name__ == "__main__":
    print(f"http://localhost:8000 | {len(MEMBERS)} members")
    uvicorn.run(app,host="0.0.0.0",port=8000,log_level="info")
