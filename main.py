from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Table
from sqlalchemy.orm import sessionmaker, relationship, Session, declarative_base
from datetime import datetime
from typing import List

# --- Database Setup ---
DATABASE_URL = "sqlite:///./warikan.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 中間テーブル（割り勘の対象メンバー）
expense_targets = Table(
    'expense_targets', Base.metadata,
    Column('expense_id', Integer, ForeignKey('expenses.id')),
    Column('member_id', Integer, ForeignKey('members.id'))
)

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.now)
    members = relationship("Member", back_populates="group")
    expenses = relationship("Expense", back_populates="group")

class Member(Base):
    __tablename__ = "members"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    group_id = Column(Integer, ForeignKey("groups.id"))
    group = relationship("Group", back_populates="members")

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    description = Column(String) # 用途（ランチ、タクシーなど）
    amount = Column(Integer)
    payer_id = Column(Integer, ForeignKey("members.id")) # 誰が払ったか
    group_id = Column(Integer, ForeignKey("groups.id"))
    created_at = Column(DateTime, default=datetime.now)
    
    group = relationship("Group", back_populates="expenses")
    payer = relationship("Member")
    targets = relationship("Member", secondary=expense_targets) # 誰の分か

Base.metadata.create_all(bind=engine)

# --- FastAPI App Setup ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Routes ---

@app.get("/")
def list_groups(request: Request, db: Session = Depends(get_db)):
    """グループ一覧と作成フォームを表示"""
    groups = db.query(Group).order_by(Group.created_at.desc()).all()
    return templates.TemplateResponse("index.html", {"request": request, "groups": groups})

@app.post("/groups")
def create_group(
    name: str = Form(...),
    members_str: str = Form(...), # カンマ区切りの文字列
    db: Session = Depends(get_db)
):
    """グループを作成しメンバーを登録"""
    new_group = Group(name=name)
    db.add(new_group)
    db.commit()
    db.refresh(new_group)

    # メンバー登録処理（カンマ区切りを分割）
    member_names = [m.strip() for m in members_str.split(",") if m.strip()]
    for m_name in member_names:
        new_member = Member(name=m_name, group_id=new_group.id)
        db.add(new_member)
    
    db.commit()
    return RedirectResponse(url=f"/groups/{new_group.id}", status_code=303)

@app.get("/groups/{group_id}")
def get_group(request: Request, group_id: int, db: Session = Depends(get_db)):
    """グループ詳細画面（メンバー表示、立替登録、履歴）"""
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    return templates.TemplateResponse("group.html", {"request": request, "group": group})

@app.post("/groups/{group_id}/expenses")
def add_expense(
    group_id: int,
    description: str = Form(...),
    amount: int = Form(...),
    payer_id: int = Form(...),
    target_ids: List[int] = Form(...), # 複数選択されたIDのリスト
    db: Session = Depends(get_db)
):
    """立替（支払い）情報の登録"""
    # 支払い情報の作成
    new_expense = Expense(
        description=description,
        amount=amount,
        payer_id=payer_id,
        group_id=group_id
    )
    
    # 対象メンバー（誰の分か）を紐付け
    targets = db.query(Member).filter(Member.id.in_(target_ids)).all()
    new_expense.targets = targets
    
    db.add(new_expense)
    db.commit()
    
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)