import sqlite3
import stripe
import os
import secrets
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from passlib.context import CryptContext

# --- 配置 ---
# 从环境变量获取 Stripe Key，如果没配则用空
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
if STRIPE_SECRET_KEY: stripe.api_key = STRIPE_SECRET_KEY

DB_NAME = "medical.db"

# 获取环境变量里的管理密码，如果没有则默认为 admin888
ADMIN_PASS_RAW = os.getenv("ADMIN_PASSWORD", "admin888")
# 简单的 Token (正式上线建议用 JWT)
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "default-secret-token")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ADMIN_PASS_HASH = pwd_context.hash(ADMIN_PASS_RAW)

app = FastAPI()

# 关键：允许跨域 (CORS)
# 因为前端在 Cloudflare，后端在 Render，域名不同，必须开启。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 试运行阶段允许所有，正式上线换成 Cloudflare 的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 数据库 ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS doctors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, hospital TEXT, city TEXT, specialty TEXT, languages TEXT, price INTEGER, description TEXT, image_url TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS appointments (id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_id INTEGER, patient_name TEXT, contact TEXT, date TEXT, symptoms TEXT, status TEXT DEFAULT 'Pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# --- 模型 ---
class LoginModel(BaseModel):
    username: str
    password: str

class DoctorModel(BaseModel):
    name: str
    hospital: str
    city: str
    specialty: str
    languages: str
    price: int
    description: str
    image_url: Optional[str] = ""

class BookingModel(BaseModel):
    doctor_id: int
    patient_name: str
    contact: str
    date: str
    symptoms: Optional[str] = ""

class PaymentIntentRequest(BaseModel):
    amount: int 

# --- 验证 ---
def verify_admin(authorization: str = Header(None)):
    if not authorization: raise HTTPException(401, "Missing Token")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != 'bearer' or token != SECRET_TOKEN: raise HTTPException(401, "Invalid Token")
    except: raise HTTPException(401, "Invalid Token")

# --- API ---
@app.get("/")
def home():
    return {"message": "ChinaMed API is running. Please access the Frontend URL."}

@app.post("/api/login")
def login(creds: LoginModel):
    # 默认账号 admin
    if creds.username == "admin" and pwd_context.verify(creds.password, ADMIN_PASS_HASH):
        return {"token": SECRET_TOKEN}
    raise HTTPException(401, "Invalid credentials")

@app.get("/api/doctors")
def get_doctors(city: Optional[str] = "All"):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if city and city != "All": cursor.execute("SELECT * FROM doctors WHERE city = ?", (city,))
    else: cursor.execute("SELECT * FROM doctors")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/book")
def book(booking: BookingModel):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO appointments (doctor_id, patient_name, contact, date, symptoms) VALUES (?,?,?,?,?)', 
                   (booking.doctor_id, booking.patient_name, booking.contact, booking.date, booking.symptoms))
    conn.commit()
    conn.close()
    return {"message": "Request received"}

@app.post("/api/create-payment-intent")
def payment(req: PaymentIntentRequest):
    if not STRIPE_SECRET_KEY: return {"clientSecret": "demo_mode", "mode": "demo"}
    try:
        intent = stripe.PaymentIntent.create(amount=req.amount, currency="usd", automatic_payment_methods={"enabled": True})
        return {"clientSecret": intent.client_secret, "mode": "live"}
    except Exception as e: raise HTTPException(400, str(e))

# 管理员接口
@app.post("/api/admin/doctors", dependencies=[Depends(verify_admin)])
def add_doc(doc: DoctorModel):
    if not doc.image_url: doc.image_url = f"https://source.unsplash.com/random/400x300/?doctor,{doc.specialty}"
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO doctors (name, hospital, city, specialty, languages, price, description, image_url) VALUES (?,?,?,?,?,?,?,?)',
                   (doc.name, doc.hospital, doc.city, doc.specialty, doc.languages, doc.price, doc.description, doc.image_url))
    conn.commit()
    return {"msg": "ok"}

@app.put("/api/admin/doctors/{id}", dependencies=[Depends(verify_admin)])
def update_doc(id: int, doc: DoctorModel):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE doctors SET name=?, hospital=?, city=?, specialty=?, languages=?, price=?, description=?, image_url=? WHERE id=?',
                   (doc.name, doc.hospital, doc.city, doc.specialty, doc.languages, doc.price, doc.description, doc.image_url, id))
    conn.commit()
    return {"msg": "updated"}

@app.delete("/api/admin/doctors/{id}", dependencies=[Depends(verify_admin)])
def delete_doc(id: int):
    conn = sqlite3.connect(DB_NAME)
    conn.execute('DELETE FROM doctors WHERE id=?', (id,))
    conn.commit()
    return {"msg": "deleted"}

@app.get("/api/admin/orders", dependencies=[Depends(verify_admin)])
def get_orders():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT a.*, d.name as doctor_name FROM appointments a LEFT JOIN doctors d ON a.doctor_id = d.id ORDER BY a.id DESC")
    return [dict(row) for row in cursor.fetchall()]

if __name__ == "__main__":
    import uvicorn
    # Render 默认使用端口 10000
    uvicorn.run(app, host="0.0.0.0", port=10000)