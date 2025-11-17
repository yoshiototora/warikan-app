# main.py の冒頭部分
import os # 忘れずに追加
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Table
from sqlalchemy.orm import sessionmaker, relationship, Session, declarative_base
from datetime import datetime
from typing import List
import math

# --- Database Setup (Modified for Render) ---

# 環境変数からURLを取得（Render上では設定したURLが、ローカルではNoneが入る）
DATABASE_URL = os.environ.get("DATABASE_URL")

# RenderのURLは "postgres://" で始まるが、SQLAlchemyは "postgresql://" を必要とするため修正
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 環境変数がない（ローカル環境）ならSQLiteを使う
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./warikan.db"

# SQLiteの場合のみ check_same_thread が必要
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ... (以下、 expense_targets = Table ... から下のコードは変更なし) ...

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

    # --- ▼ 精算ロジック (端数対応版) ▼ ---
    
    settlement = {
        m.id: {"name": m.name, "paid": 0, "owed": 0, "balance": 0}
        for m in group.members
    }
    
    total_group_spend = 0

    for expense in group.expenses:
        total_group_spend += expense.amount
        
        # 1. 支払者: 支払額を加算
        if expense.payer_id in settlement:
            settlement[expense.payer_id]["paid"] += expense.amount
        
        # 2. 対象者: 割り勘計算（端数処理）
        if expense.targets:
            num_targets = len(expense.targets)
            split_amount = expense.amount // num_targets # 切り捨てで等分
            remainder = expense.amount % num_targets   # 余り
            
            for i, target_member in enumerate(expense.targets):
                if target_member.id in settlement:
                    # 余りの分は、リストの前半の人たちに1円ずつ上乗せする
                    add_amount = split_amount + (1 if i < remainder else 0)
                    settlement[target_member.id]["owed"] += add_amount

    # 3. 差額計算 (支払 - 負担)
    for member_id, data in settlement.items():
        data["balance"] = data["paid"] - data["owed"]

    # --- ▼ 最小取引回数ロジック ▼ ---
    transactions = []
    
    debtors = [
        [data['name'], data['balance']] 
        for data in settlement.values() if data['balance'] < 0
    ]
    creditors = [
        [data['name'], data['balance']] 
        for data in settlement.values() if data['balance'] > 0
    ]
    
    # 金額の絶対値が大きい順にソート
    debtors.sort(key=lambda x: x[1])       # 昇順 (-1000, -500...)
    creditors.sort(key=lambda x: x[1], reverse=True) # 降順 (1000, 500...)

    i = 0 # debtor index
    j = 0 # creditor index

    while i < len(debtors) and j < len(creditors):
        debtor = debtors[i]
        creditor = creditors[j]
        
        # 取引額（負債の絶対値 と 債権 の小さい方）
        amount = min(abs(debtor[1]), creditor[1])
        
        if amount > 0:
            transactions.append(f"{debtor[0]} は {creditor[0]} に {amount:,} 円支払う")
            
            # 残高更新
            debtor[1] += amount
            creditor[1] -= amount
            
        # どちらかが精算完了したらインデックスを進める
        # (浮動小数点ではないので == 0 で判定可能だが、念のため判定)
        if abs(debtor[1]) == 0:
            i += 1
        if abs(creditor[1]) == 0:
            j += 1

    # --- ▲ ロジック終了 ▲ ---

    return templates.TemplateResponse(
        "group.html", 
        {
            "request": request, 
            "group": group,
            "settlement": settlement,
            "total_group_spend": total_group_spend,
            "transactions": transactions
        }
    )
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

#
# main.py の一番下に追加
#

@app.post("/groups/{group_id}/members")
def add_member(
    group_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    """グループに新しいメンバーを追加する"""
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # すでに同名のメンバーがいないかチェック
    existing = db.query(Member).filter_by(group_id=group_id, name=name.strip()).first()
    if not existing and name.strip(): # 空白でなく、重複もない場合
        new_member = Member(name=name.strip(), group_id=group_id)
        db.add(new_member)
        db.commit()
    
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)


@app.get("/expenses/{expense_id}/delete")
def delete_expense(expense_id: int, db: Session = Depends(get_db)):
    """立替履歴を削除する"""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    
    group_id = expense.group_id # リダイレクト先を確保
    
    # 支払い情報を削除
    db.delete(expense)
    db.commit()
    
    # 元のグループページに戻る
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)

#
# main.py の一番下に追加
#

@app.get("/expenses/{expense_id}/edit")
def edit_expense_form(
    request: Request,
    expense_id: int,
    db: Session = Depends(get_db)
):
    """編集フォームのページを表示する (GET)"""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    
    # テンプレートに渡すため、現在チェックされているメンバーのIDリストを作成
    current_target_ids = {member.id for member in expense.targets}
    
    return templates.TemplateResponse(
        "edit_expense.html",
        {
            "request": request,
            "expense": expense,
            "current_target_ids": current_target_ids
        }
    )

@app.post("/expenses/{expense_id}/edit")
def update_expense(
    expense_id: int,
    description: str = Form(...),
    amount: int = Form(...),
    payer_id: int = Form(...),
    target_ids: List[int] = Form(...), # フォームから送られたIDリスト
    db: Session = Depends(get_db)
):
    """編集内容を保存する (POST)"""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    # 1. 基本情報を更新
    expense.description = description
    expense.amount = amount
    expense.payer_id = payer_id
    
    # 2. 対象メンバー（targets）を更新
    # フォームから送られてきたIDのリストで、対象メンバーを上書きする
    targets = db.query(Member).filter(Member.id.in_(target_ids)).all()
    expense.targets = targets
    
    db.commit()
    
    # 元のグループページに戻る
    return RedirectResponse(url=f"/groups/{expense.group_id}", status_code=303)