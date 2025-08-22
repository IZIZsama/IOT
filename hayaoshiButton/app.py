from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
import time

app = Flask(__name__)
app.secret_key = 'secret_key_here'  # ここは安全なキーに変更してください

# MySQL接続文字列（ユーザー名・パスワード・DB名を環境に合わせて修正）
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://new_user:new_password@localhost/new_quiz_game?charset=utf8mb4'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- DBモデル ---
class Player(db.Model):
    __tablename__ = 'players'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())

# --- グローバル変数 ---
early_press_game_active = False
early_press_log = []  # [{'address':..., 'button_id':..., 'timestamp':...}, ...]
fallback_names = ["Aさん", "Bさん", "Cさん", "Dさん"]
bluetooth_status = {"No1": False, "No2": False, "No3": False, "No4": False}

# --- 画面ルーティング ---
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/ranking')
def ranking():
    try:
        players = Player.query.order_by(Player.points.desc()).all()
        if not players:
            raise ValueError("データなし")
        ranking = []
        last_point = None
        last_rank = 0
        for i, player in enumerate(players):
            if player.points != last_point:
                last_rank = len(ranking) + 1
                last_point = player.points
            ranking.append({
                "rank": last_rank,
                "name": player.name or fallback_names[i],
                "points": player.points
            })
        return render_template('ranking.html', ranking=ranking, error=None)
    except Exception:
        fallback_ranking = [{"rank": i + 1, "name": fallback_names[i], "points": 0} for i in range(4)]
        return render_template('ranking.html', ranking=fallback_ranking, error="バックエンドに接続できませんでした。")

@app.route('/answer', methods=['GET', 'POST'])
def answer():
    message = None
    first_responder = request.args.get('first') or ""
    if request.method == 'POST':
        result = request.form.get('result')
        if result == 'correct':
            message = "正解です！"
        elif result == 'wrong':
            message = "不正解です。"
    return render_template('answer.html', message=message, first_responder=first_responder)

@app.route('/name', methods=['GET', 'POST'])
def name():
    if request.method == 'POST':
        names = [
            request.form.get("name1"),
            request.form.get("name2"),
            request.form.get("name3"),
            request.form.get("name4"),
        ]
        for name in names:
            if not name:
                continue
            player = Player.query.filter_by(name=name).first()
            if not player:
                player = Player(name=name, points=0)
                db.session.add(player)
            else:
                player.points = 0
        db.session.commit()
        return redirect(url_for('ranking'))
    return render_template('name.html')

@app.route('/reset_confirm')
def reset_confirm():
    return render_template('reset_confirm.html')

@app.route('/reset', methods=['POST'])
def reset():
    if request.form.get("confirm") == "yes":
        try:
            num_deleted = Player.query.delete()
            db.session.commit()
            flash(f"初期化が完了しました！ {num_deleted}件のデータを削除しました。")
        except Exception as e:
            db.session.rollback()
            flash(f"初期化に失敗しました: {e}")
    else:
        flash("初期化をキャンセルしました。")
    return redirect(url_for('ranking'))

@app.route('/bluetooth', methods=['GET', 'POST'])
def bluetooth():
    global bluetooth_status
    if request.method == 'POST':
        action = request.form.get("action")
        if action == "connect":
            return redirect(url_for('bluetooth_loading'))
        elif action == "disconnect":
            for key in bluetooth_status:
                bluetooth_status[key] = False
            flash("Bluetooth接続を解除しました。")
            return redirect(url_for('bluetooth'))
    return render_template('bluetooth.html', status=bluetooth_status)

@app.route('/bluetooth_loading')
def bluetooth_loading():
    return render_template('bluetooth_loading.html')

@app.route('/bluetooth_connecting')
def bluetooth_connecting():
    global bluetooth_status
    for key in bluetooth_status:
        bluetooth_status[key] = True
    flash("Bluetooth接続を実行しました。")
    return redirect(url_for('bluetooth'))

# --- 早押しゲームAPI ---
@app.route('/early_press/start', methods=['POST'])
def early_press_start():
    global early_press_game_active, early_press_log
    early_press_game_active = True
    early_press_log.clear()
    socketio.emit('early_press_game_reset')
    socketio.emit('early_press_order_updated', [])
    return jsonify({"status": "game_started"})

@app.route('/early_press/stop', methods=['POST'])
def early_press_stop():
    global early_press_game_active
    early_press_game_active = False
    socketio.emit('early_press_game_stopped')
    return jsonify({"status": "game_stopped"})

@app.route('/early_press/current_order', methods=['GET'])
def early_press_current_order():
    order = []
    for i, press in enumerate(sorted(early_press_log, key=lambda x: x["timestamp"])):
        order.append({
            "address": press["address"],
            "button_id": press["button_id"],
            "order": i + 1,
            "name": "不明なデバイス"
        })
    return jsonify({"order": order})

# --- Socket.IOイベント（BLEからのボタン押下イベント受信想定） ---
@socketio.on('button_pressed')
def handle_button_pressed(data):
    global early_press_game_active, early_press_log

    if not early_press_game_active:
        return

    addr = data.get('address')
    button_id = data.get('button_id')
    timestamp = data.get('timestamp', time.time())

    if any(p['address'] == addr for p in early_press_log):
        return

    early_press_log.append({
        'address': addr,
        'button_id': button_id,
        'timestamp': timestamp
    })
    early_press_log.sort(key=lambda x: x['timestamp'])

    order = []
    for i, press in enumerate(early_press_log):
        order.append({
            "address": press["address"],
            "name": "不明なデバイス",
            "button_id": press["button_id"],
            "order": i + 1
        })
    emit('early_press_order_updated', order, broadcast=True)

    if len(early_press_log) == 1:
        winner = early_press_log[0]
        emit('early_press_winner', winner, broadcast=True)

# --- メイン起動 ---
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
