from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        payer = request.form["payer"]
        amount = int(request.form["amount"])
        members = request.form["members"].split(",")

        split = amount / len(members)
        return render_template(
            "index.html",
            result=f"{payer} が {amount}円 を立て替え → 1人あたり {int(split)}円"
        )

    return render_template("index.html", result=None)


if __name__ == "__main__":
    app.run(debug=True)
