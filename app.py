import sqlite3
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)
DB = "warikan.db"

# --- DB 初期化 ---
def init_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payer TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT
        )
    """)
    con.commit()
    con.close()

init_db()


# --- トップページ（一覧表示） ---
@app.route("/")
def index():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT id, payer, amount, description FROM payments")
    rows = cur.fetchall()
    con.close()

    total = sum([r[2] for r in rows])

    return render_template("index.html", payments=rows, total=total)


# --- 支払い追加ページ ---
@app.route("/add", methods=["GET", "POST"])
def add_payment():
    if request.method == "POST":
        payer = request.form["payer"]
        amount = int(request.form["amount"])
        description = request.form["description"]

        con = sqlite3.connect(DB)
        cur = con.cursor()
        cur.execute("INSERT INTO payments (payer, amount, description) VALUES (?, ?, ?)",
                    (payer, amount, description))
        con.commit()
        con.close()

        return redirect(url_for("index"))

    return render_template("add_payment.html")


# --- 精算結果ページ ---
@app.route("/settlement")
def settlement():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT payer, amount FROM payments")
    rows = cur.fetchall()
    con.close()

    # 各ユーザーの合計支払いを集計
    summary = {}
    for payer, amount in rows:
        summary[payer] = summary.get(payer, 0) + amount

    members = summary.keys()
    total = sum(summary.values())
    per_person = total / len(members)

    # 精算（差額計算）
    balance = {m: summary[m] - per_person for m in members}

    # 正の人＝もらう、負の人＝払う
    positives = [(m, b) for m, b in balance.items() if b > 0]
    negatives = [(m, -b) for m, b in balance.items() if b < 0]

    settlements = []
    i = j = 0

    # 最適マッチング（貰う側 ↔ 払う側）
    while i < len(positives) and j < len(negatives):
        recv_name, recv_amt = positives[i]
        pay_name, pay_amt = negatives[j]

        amount = min(recv_amt, pay_amt)

        settlements.append(f"{pay_name} → {recv_name} に {int(amount)} 円支払い")

        positives[i] = (recv_name, recv_amt - amount)
        negatives[j] = (pay_name, pay_amt - amount)

        if positives[i][1] == 0:
            i += 1
        if negatives[j][1] == 0:
            j += 1

    return render_template("settlement.html", settlements=settlements)
    

if __name__ == "__main__":
    app.run(debug=True)
