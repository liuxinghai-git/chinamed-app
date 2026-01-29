import os
import stripe
import secrets
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from passlib.context import CryptContext
from dotenv import load_dotenv

# --- 数据库驱动 ---
# 根据环境自动切换 SQLite 或 PostgreSQL
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None

import sqlite3

load_dotenv()

# --- 配置 ---
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
if STRIPE_SECRET_KEY: stripe.api_key = STRIPE_SECRET_KEY

ADMIN_PASS_RAW = os.getenv("ADMIN_PASSWORD", "admin888")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "default-secret-token")
DATABASE_URL = os.getenv("postgresql://chinamed_db_user:gJutVbfVbXOOis6w7R9syQAjHI9Nqq7e@dpg-d5to5f4oud1c73bq92dg-a/chinamed_db") # Render 会自动注入这个变量

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# 密码截断保护
if len(ADMIN_PASS_RAW) > 70: ADMIN_PASS_RAW = ADMIN_PASS_RAW[:70]
ADMIN_PASS_HASH = pwd_context.hash(ADMIN_PASS_RAW)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 数据库连接助手 ---
def get_db_connection():
    """自动判断使用 PostgreSQL 还是 SQLite"""
    if DATABASE_URL and psycopg2:
        # 使用 PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        return conn, "pg"
    else:
        # 使用 SQLite
        conn = sqlite3.connect("medical.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def execute_query(query_pg: str, query_sqlite: str, args=()):
    """执行 SQL，兼容两种数据库语法"""
    conn, db_type = get_db_connection()
    try:
        if db_type == "pg":
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query_pg, args)
            # 如果是查询语句 (SELECT)
            if query_pg.strip().upper().startswith("SELECT"):
                res = cur.fetchall()
                # 把 RealDictRow 转成普通 dict
                return [dict(row) for row in res]
            conn.commit()
            return {"msg": "ok"}
        else:
            cur = conn.cursor()
            cur.execute(query_sqlite, args)
            if query_sqlite.strip().upper().startswith("SELECT"):
                res = cur.fetchall()
                return [dict(row) for row in res]
            conn.commit()
            return {"msg": "ok"}
    finally:
        conn.close()

# --- 数据库初始化 ---
def init_db():
    conn, db_type = get_db_connection()
    cur = conn.cursor()
    
    if db_type == "pg":
        # PostgreSQL 建表 (SERIAL 自增)
        cur.execute('''CREATE TABLE IF NOT EXISTS doctors (id SERIAL PRIMARY KEY, name TEXT, hospital TEXT, city TEXT, specialty TEXT, languages TEXT, price INTEGER, description TEXT, image_url TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS appointments (id SERIAL PRIMARY KEY, doctor_id INTEGER, patient_name TEXT, contact TEXT, date TEXT, symptoms TEXT, status TEXT DEFAULT 'Pending', payment_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    else:
        # SQLite 建表 (AUTOINCREMENT)
        cur.execute('''CREATE TABLE IF NOT EXISTS doctors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, hospital TEXT, city TEXT, specialty TEXT, languages TEXT, price INTEGER, description TEXT, image_url TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS appointments (id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_id INTEGER, patient_name TEXT, contact TEXT, date TEXT, symptoms TEXT, status TEXT DEFAULT 'Pending', payment_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
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
    payment_id: Optional[str] = ""

# --- 验证 ---
def verify_admin(authorization: str = Header(None)):
    if not authorization: raise HTTPException(401)
    try:
        scheme, token = authorization.split()
        if scheme.lower() != 'bearer' or token != SECRET_TOKEN: raise HTTPException(401)
    except: raise HTTPException(401)

# --- API ---
@app.post("/api/login")
def login(creds: LoginModel):
    if creds.username == "admin" and pwd_context.verify(creds.password, ADMIN_PASS_HASH):
        return {"token": SECRET_TOKEN}
    raise HTTPException(401, "Invalid credentials")

@app.get("/api/doctors")
def get_doctors(city: Optional[str] = "All"):
    # PG 使用 %s, SQLite 使用 ?
    query_base = "SELECT * FROM doctors"
    if city and city != "All":
        return execute_query(query_base + " WHERE city = %s", query_base + " WHERE city = ?", (city,))
    else:
        return execute_query(query_base, query_base)

@app.post("/api/book")
def book(booking: BookingModel):
    # PG 使用 %s, SQLite 使用 ?
    sql_pg = 'INSERT INTO appointments (doctor_id, patient_name, contact, date, symptoms, payment_id) VALUES (%s,%s,%s,%s,%s,%s)'
    sql_lite = 'INSERT INTO appointments (doctor_id, patient_name, contact, date, symptoms, payment_id) VALUES (?,?,?,?,?,?)'
    
    execute_query(sql_pg, sql_lite, 
                  (booking.doctor_id, booking.patient_name, booking.contact, booking.date, booking.symptoms, booking.payment_id))
    return {"message": "received"}

# 管理员接口
@app.post("/api/admin/doctors", dependencies=[Depends(verify_admin)])
def add_doc(doc: DoctorModel):
    if not doc.image_url: doc.image_url = f"https://source.unsplash.com/random/400x300/?doctor,{doc.specialty}"
    
    sql_pg = 'INSERT INTO doctors (name, hospital, city, specialty, languages, price, description, image_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)'
    sql_lite = 'INSERT INTO doctors (name, hospital, city, specialty, languages, price, description, image_url) VALUES (?,?,?,?,?,?,?,?)'
    
    execute_query(sql_pg, sql_lite, 
                  (doc.name, doc.hospital, doc.city, doc.specialty, doc.languages, doc.price, doc.description, doc.image_url))
    return {"msg": "ok"}

@app.put("/api/admin/doctors/{id}", dependencies=[Depends(verify_admin)])
def update_doc(id: int, doc: DoctorModel):
    sql_pg = 'UPDATE doctors SET name=%s, hospital=%s, city=%s, specialty=%s, languages=%s, price=%s, description=%s, image_url=%s WHERE id=%s'
    sql_lite = 'UPDATE doctors SET name=?, hospital=?, city=?, specialty=?, languages=?, price=?, description=?, image_url=? WHERE id=?'
    
    execute_query(sql_pg, sql_lite, 
                  (doc.name, doc.hospital, doc.city, doc.specialty, doc.languages, doc.price, doc.description, doc.image_url, id))
    return {"msg": "updated"}

@app.delete("/api/admin/doctors/{id}", dependencies=[Depends(verify_admin)])
def delete_doc(id: int):
    sql_pg = 'DELETE FROM doctors WHERE id=%s'
    sql_lite = 'DELETE FROM doctors WHERE id=?'
    execute_query(sql_pg, sql_lite, (id,))
    return {"msg": "deleted"}

@app.get("/api/admin/orders", dependencies=[Depends(verify_admin)])
def get_orders():
    # 注意：LEFT JOIN 语法两个数据库是通用的
    sql = "SELECT a.*, d.name as doctor_name FROM appointments a LEFT JOIN doctors d ON a.doctor_id = d.id ORDER BY a.id DESC"
    return execute_query(sql, sql)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
