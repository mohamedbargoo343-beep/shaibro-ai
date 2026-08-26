
import os, sqlite3, secrets, json, base64, email
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

BASE = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "shaibro.db"
UPLOADS = DATA_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-this-secret-before-production")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "shaibro123")
MOBILE_API_TOKEN = os.getenv("MOBILE_API_TOKEN", "")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback")

app = FastAPI(title="SHAIBRO AI", version="4.0")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=os.getenv("COOKIE_SECURE","false").lower()=="true")
app.mount("/static", StaticFiles(directory=BASE/"static"), name="static")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con=db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS tasks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      event_time TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS files(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      path TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL,
      text TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS google_tokens(
      id INTEGER PRIMARY KEY CHECK(id=1),
      access_token TEXT,
      refresh_token TEXT,
      expires_at TEXT,
      email TEXT
    );
    """)
    con.commit(); con.close()
init_db()

def require_login(request: Request):
    # Browser session
    if request.session.get("logged_in"):
        return

    # Mobile/API Bearer token
    auth = request.headers.get("authorization", "")
    if MOBILE_API_TOKEN and auth.startswith("Bearer "):
        supplied = auth[7:].strip()
        if secrets.compare_digest(supplied, MOBILE_API_TOKEN):
            return

    raise HTTPException(status_code=401, detail="Login required")

def get_google_tokens():
    con=db(); row=con.execute("SELECT * FROM google_tokens WHERE id=1").fetchone(); con.close()
    return dict(row) if row else None

def save_google_tokens(access_token, refresh_token=None, expires_in=3600, email_addr=None):
    old=get_google_tokens() or {}
    refresh_token = refresh_token or old.get("refresh_token")
    email_addr = email_addr or old.get("email")
    expires_at=(datetime.utcnow()+timedelta(seconds=max(60,int(expires_in)-60))).isoformat()
    con=db()
    con.execute("""
      INSERT INTO google_tokens(id,access_token,refresh_token,expires_at,email)
      VALUES(1,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
      access_token=excluded.access_token,
      refresh_token=excluded.refresh_token,
      expires_at=excluded.expires_at,
      email=excluded.email
    """,(access_token,refresh_token,expires_at,email_addr))
    con.commit(); con.close()

async def google_access_token():
    tok=get_google_tokens()
    if not tok or not tok.get("access_token"):
        raise HTTPException(400,"Google account not connected")
    exp=datetime.fromisoformat(tok["expires_at"]) if tok.get("expires_at") else datetime.utcnow()
    if exp > datetime.utcnow():
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise HTTPException(400,"Google authorization expired. Reconnect account.")
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.post("https://oauth2.googleapis.com/token",data={
            "client_id":GOOGLE_CLIENT_ID,
            "client_secret":GOOGLE_CLIENT_SECRET,
            "refresh_token":tok["refresh_token"],
            "grant_type":"refresh_token"
        })
        if r.status_code>=400:
            raise HTTPException(400,"Could not refresh Google token")
        data=r.json()
    save_google_tokens(data["access_token"], tok["refresh_token"], data.get("expires_in",3600), tok.get("email"))
    return data["access_token"]

class TaskIn(BaseModel):
    title: str
class EventIn(BaseModel):
    title: str
    event_time: Optional[str]=None
class ChatIn(BaseModel):
    message: str
class DraftIn(BaseModel):
    to: str
    subject: str
    body: str
class ReplyDraftIn(BaseModel):
    body: str

@app.get("/health")
def health():
    return {"ok":True,"version":"4.0"}

@app.get("/")
def home(request: Request):
    if not request.session.get("logged_in"):
        return FileResponse(BASE/"static"/"login.html")
    return FileResponse(BASE/"static"/"index.html")

@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        return HTMLResponse("<h3>كلمة المرور غير صحيحة</h3><a href='/'>رجوع</a>", status_code=401)
    request.session["logged_in"]=True
    return RedirectResponse("/",303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/",303)

@app.get("/api/status")
def status(request: Request):
    require_login(request)
    tok=get_google_tokens()
    return {
      "google_connected":bool(tok and tok.get("access_token")),
      "google_email":tok.get("email") if tok else None,
      "ai_enabled":bool(os.getenv("OPENAI_API_KEY"))
    }

@app.get("/auth/google")
def google_auth(request: Request):
    require_login(request)
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(400,"Google OAuth environment variables are not configured")
    state=secrets.token_urlsafe(24)
    request.session["oauth_state"]=state
    scope=" ".join([
        "openid","email","profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/calendar"
    ])
    params={
        "client_id":GOOGLE_CLIENT_ID,
        "redirect_uri":GOOGLE_REDIRECT_URI,
        "response_type":"code",
        "scope":scope,
        "access_type":"offline",
        "prompt":"consent",
        "include_granted_scopes":"true",
        "state":state
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?"+urlencode(params))

@app.get("/auth/google/callback")
async def google_callback(request: Request, code: str, state: str):
    require_login(request)
    if state != request.session.get("oauth_state"):
        raise HTTPException(400,"Invalid OAuth state")
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.post("https://oauth2.googleapis.com/token",data={
            "code":code,
            "client_id":GOOGLE_CLIENT_ID,
            "client_secret":GOOGLE_CLIENT_SECRET,
            "redirect_uri":GOOGLE_REDIRECT_URI,
            "grant_type":"authorization_code"
        })
        if r.status_code>=400:
            raise HTTPException(400,f"Google token exchange failed: {r.text}")
        data=r.json()
        access=data["access_token"]
        info=await client.get("https://openidconnect.googleapis.com/v1/userinfo",headers={"Authorization":f"Bearer {access}"})
        email_addr=info.json().get("email") if info.status_code<400 else None
    save_google_tokens(access,data.get("refresh_token"),data.get("expires_in",3600),email_addr)
    return RedirectResponse("/?google=connected",303)

@app.post("/auth/google/disconnect")
def disconnect_google(request: Request):
    require_login(request)
    con=db(); con.execute("DELETE FROM google_tokens WHERE id=1"); con.commit(); con.close()
    return {"ok":True}

# ---------- Tasks ----------
@app.get("/api/tasks")
def list_tasks(request: Request):
    require_login(request)
    con=db(); rows=con.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall(); con.close()
    return [dict(r) for r in rows]

@app.post("/api/tasks")
def add_task(item: TaskIn, request: Request):
    require_login(request)
    title=item.title.strip()
    if not title: raise HTTPException(400,"Task title required")
    con=db(); cur=con.execute("INSERT INTO tasks(title,status,created_at) VALUES(?,?,?)",(title,"open",datetime.utcnow().isoformat()))
    con.commit(); row=con.execute("SELECT * FROM tasks WHERE id=?",(cur.lastrowid,)).fetchone(); con.close()
    return dict(row)

@app.patch("/api/tasks/{task_id}/toggle")
def toggle_task(task_id:int, request: Request):
    require_login(request)
    con=db(); row=con.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
    if not row: con.close(); raise HTTPException(404,"Task not found")
    status="done" if row["status"]=="open" else "open"
    con.execute("UPDATE tasks SET status=? WHERE id=?",(status,task_id)); con.commit(); con.close()
    return {"ok":True}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id:int, request: Request):
    require_login(request)
    con=db(); con.execute("DELETE FROM tasks WHERE id=?",(task_id,)); con.commit(); con.close()
    return {"ok":True}

# ---------- Local calendar ----------
@app.get("/api/events")
def list_events(request: Request):
    require_login(request)
    con=db(); rows=con.execute("SELECT * FROM events ORDER BY id DESC").fetchall(); con.close()
    return [dict(r) for r in rows]

@app.post("/api/events")
def add_event(item: EventIn, request: Request):
    require_login(request)
    con=db(); cur=con.execute("INSERT INTO events(title,event_time,created_at) VALUES(?,?,?)",(item.title.strip(),item.event_time,datetime.utcnow().isoformat()))
    con.commit(); row=con.execute("SELECT * FROM events WHERE id=?",(cur.lastrowid,)).fetchone(); con.close()
    return dict(row)

@app.delete("/api/events/{event_id}")
def delete_event(event_id:int, request: Request):
    require_login(request)
    con=db(); con.execute("DELETE FROM events WHERE id=?",(event_id,)); con.commit(); con.close()
    return {"ok":True}

# ---------- Files ----------
@app.post("/api/files")
async def upload_file(request: Request, file: UploadFile=File(...)):
    require_login(request)
    safe=Path(file.filename or "file").name
    path=UPLOADS/f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{safe}"
    content=await file.read()
    if len(content)>25*1024*1024:
        raise HTTPException(413,"File is larger than 25 MB")
    path.write_bytes(content)
    con=db(); cur=con.execute("INSERT INTO files(name,path,created_at) VALUES(?,?,?)",(safe,str(path),datetime.utcnow().isoformat()))
    con.commit(); fid=cur.lastrowid; con.close()
    return {"id":fid,"name":safe}

@app.get("/api/files")
def files(request: Request):
    require_login(request)
    con=db(); rows=con.execute("SELECT id,name,created_at FROM files ORDER BY id DESC").fetchall(); con.close()
    return [dict(r) for r in rows]

# ---------- Gmail ----------
async def gmail_get(message_id:str, fmt="full"):
    token=await google_access_token()
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers={"Authorization":f"Bearer {token}"},
            params={"format":fmt}
        )
    if r.status_code>=400: raise HTTPException(r.status_code,r.text)
    return r.json()

def header_map(payload):
    return {h.get("name",""):h.get("value","") for h in payload.get("headers",[])}

def decode_b64url(data):
    if not data: return ""
    pad="="*((4-len(data)%4)%4)
    try: return base64.urlsafe_b64decode((data+pad).encode()).decode("utf-8","replace")
    except: return ""

def extract_text(part):
    mime=part.get("mimeType","")
    body=part.get("body",{})
    if mime=="text/plain" and body.get("data"):
        return decode_b64url(body["data"])
    for child in part.get("parts",[]) or []:
        txt=extract_text(child)
        if txt: return txt
    if body.get("data"):
        return decode_b64url(body["data"])
    return ""

@app.get("/api/gmail/messages")
async def gmail_messages(request: Request, max_results:int=15, q:str="in:inbox"):
    require_login(request)
    token=await google_access_token()
    headers={"Authorization":f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",headers=headers,params={"maxResults":min(max_results,30),"q":q})
        if r.status_code>=400: raise HTTPException(r.status_code,r.text)
        ids=r.json().get("messages",[])
        out=[]
        for item in ids:
            m=await client.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}",headers=headers,params={"format":"metadata","metadataHeaders":["From","To","Subject","Date","Message-ID","References"]})
            if m.status_code<400:
                data=m.json(); hs=header_map(data.get("payload",{}))
                out.append({"id":data["id"],"threadId":data.get("threadId"),"snippet":data.get("snippet",""),"from":hs.get("From",""),"to":hs.get("To",""),"subject":hs.get("Subject","(بدون عنوان)"),"date":hs.get("Date","")})
    return out

@app.get("/api/gmail/messages/{message_id}")
async def gmail_message_detail(message_id:str, request: Request):
    require_login(request)
    data=await gmail_get(message_id,"full")
    payload=data.get("payload",{})
    hs=header_map(payload)
    return {
      "id":data["id"],
      "threadId":data.get("threadId"),
      "from":hs.get("From",""),
      "to":hs.get("To",""),
      "subject":hs.get("Subject","(بدون عنوان)"),
      "date":hs.get("Date",""),
      "message_id_header":hs.get("Message-ID",""),
      "references":hs.get("References",""),
      "snippet":data.get("snippet",""),
      "body":extract_text(payload)[:20000]
    }

def make_raw_email(to, subject, body, in_reply_to=None, references=None):
    lines=[
      f"To: {to}",
      f"Subject: {subject}",
      "MIME-Version: 1.0",
      "Content-Type: text/plain; charset=utf-8",
      "Content-Transfer-Encoding: 8bit"
    ]
    if in_reply_to: lines.append(f"In-Reply-To: {in_reply_to}")
    if references: lines.append(f"References: {references}")
    lines += ["", body]
    raw="\r\n".join(lines).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")

@app.post("/api/gmail/draft")
async def gmail_create_draft(item: DraftIn, request: Request):
    require_login(request)
    token=await google_access_token()
    encoded=make_raw_email(item.to,item.subject,item.body)
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.post("https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
            json={"message":{"raw":encoded}})
    if r.status_code>=400: raise HTTPException(r.status_code,r.text)
    return {"ok":True,"draft_id":r.json().get("id")}

@app.post("/api/gmail/messages/{message_id}/reply-draft")
async def gmail_reply_draft(message_id:str, item: ReplyDraftIn, request: Request):
    require_login(request)
    original=await gmail_get(message_id,"full")
    hs=header_map(original.get("payload",{}))
    recipient=hs.get("Reply-To") or hs.get("From","")
    subject=hs.get("Subject","")
    if not subject.lower().startswith("re:"):
        subject="Re: "+subject
    msgid=hs.get("Message-ID","")
    refs=(hs.get("References","")+" "+msgid).strip()
    encoded=make_raw_email(recipient,subject,item.body,msgid,refs)
    token=await google_access_token()
    payload={"message":{"raw":encoded,"threadId":original.get("threadId")}}
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.post("https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
            json=payload)
    if r.status_code>=400: raise HTTPException(r.status_code,r.text)
    return {"ok":True,"draft_id":r.json().get("id")}

# ---------- Google Calendar ----------
@app.get("/api/google-calendar/events")
async def google_calendar_events(request: Request):
    require_login(request)
    token=await google_access_token()
    now=datetime.utcnow().isoformat()+"Z"
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization":f"Bearer {token}"},
            params={"timeMin":now,"singleEvents":"true","orderBy":"startTime","maxResults":30})
    if r.status_code>=400: raise HTTPException(r.status_code,r.text)
    items=r.json().get("items",[])
    return [{"id":x.get("id"),"summary":x.get("summary","(بدون عنوان)"),"start":x.get("start",{}).get("dateTime") or x.get("start",{}).get("date"),"htmlLink":x.get("htmlLink")} for x in items]

@app.post("/api/google-calendar/events")
async def create_google_event(item: EventIn, request: Request):
    require_login(request)
    token=await google_access_token()
    if not item.event_time:
        raise HTTPException(400,"event_time required")
    try:
        dt=datetime.fromisoformat(item.event_time)
        if not dt.tzinfo:
            raise ValueError()
        end=(dt+timedelta(hours=1)).isoformat()
    except:
        raise HTTPException(400,"Use ISO datetime with timezone offset")
    body={"summary":item.title,"start":{"dateTime":item.event_time},"end":{"dateTime":end}}
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.post("https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=body)
    if r.status_code>=400: raise HTTPException(r.status_code,r.text)
    return {"ok":True,"event":r.json()}

# ---------- Assistant ----------
async def contextual_reply(message:str):
    con=db()
    tasks=[dict(r) for r in con.execute("SELECT title,status FROM tasks ORDER BY id DESC LIMIT 30").fetchall()]
    events=[dict(r) for r in con.execute("SELECT title,event_time FROM events ORDER BY id DESC LIMIT 30").fetchall()]
    con.close()

    if message.startswith("أضف مهمة"):
        title=message.replace("أضف مهمة","",1).strip() or "مهمة جديدة"
        con=db(); con.execute("INSERT INTO tasks(title,status,created_at) VALUES(?,?,?)",(title,"open",datetime.utcnow().isoformat())); con.commit(); con.close()
        return f"تمت إضافة المهمة: {title}"

    if "مهام" in message:
        open_tasks=[x for x in tasks if x["status"]=="open"]
        return "مهامك المفتوحة:\n"+("\n".join(f"{i+1}. {x['title']}" for i,x in enumerate(open_tasks)) if open_tasks else "لا توجد مهام مفتوحة.")

    if "رتب يومي" in message:
        open_tasks=[x for x in tasks if x["status"]=="open"]
        return "الترتيب المقترح لليوم:\n"+("\n".join(f"{i+1}. {x['title']}" for i,x in enumerate(open_tasks)) if open_tasks else "لا توجد مهام حالياً.")

    if any(k in message for k in ["إيميل","ايميل","بريد"]):
        try:
            token=await google_access_token()
            headers={"Authorization":f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=20) as client:
                r=await client.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",headers=headers,params={"maxResults":5,"q":"in:inbox"})
                ids=r.json().get("messages",[]) if r.status_code<400 else []
                out=[]
                for item in ids[:5]:
                    m=await client.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}",headers=headers,params={"format":"metadata","metadataHeaders":["From","Subject"]})
                    if m.status_code<400:
                        hs=header_map(m.json().get("payload",{}))
                        out.append(f"- {hs.get('Subject','بدون عنوان')} — {hs.get('From','')}")
            return "أحدث رسائل البريد:\n"+("\n".join(out) if out else "لا توجد رسائل.")
        except:
            return "اربط Google من قسم الحساب أولاً، ثم أستطيع قراءة بريدك."

    key=os.getenv("OPENAI_API_KEY")
    if key:
        try:
            from openai import OpenAI
            client=OpenAI(api_key=key)
            context=json.dumps({"tasks":tasks,"events":events},ensure_ascii=False)
            resp=client.responses.create(
                model=os.getenv("OPENAI_MODEL","gpt-5.6-luna"),
                instructions=(
                    "أنت SHAIBRO AI، سكرتير تنفيذي شخصي. المستخدم هو المدير وصاحب القرار. "
                    "ساعد في التنظيم والكتابة والتحليل. لا تدّع إرسال بريد أو تنفيذ إجراء خارجي لم يتم فعلياً. "
                    "بالنسبة للبريد، اقترح مسودة فقط واترك الإرسال للمستخدم."
                ),
                input=f"السياق: {context}\nطلب المستخدم: {message}"
            )
            return resp.output_text
        except:
            pass
    return "أنا جاهز. اطلب مني إضافة مهمة، عرض مهامك، ترتيب يومك، أو مراجعة البريد بعد ربط Google."

@app.post("/api/chat")
async def chat(item: ChatIn, request: Request):
    require_login(request)
    text=item.message.strip()
    if not text: raise HTTPException(400,"Message required")
    reply=await contextual_reply(text)
    con=db(); now=datetime.utcnow().isoformat()
    con.execute("INSERT INTO messages(role,text,created_at) VALUES(?,?,?)",("user",text,now))
    con.execute("INSERT INTO messages(role,text,created_at) VALUES(?,?,?)",("assistant",reply,now))
    con.commit(); con.close()
    return {"reply":reply}

@app.get("/api/messages")
def messages(request: Request):
    require_login(request)
    con=db(); rows=con.execute("SELECT role,text,created_at FROM messages ORDER BY id ASC LIMIT 300").fetchall(); con.close()
    return [dict(r) for r in rows]
