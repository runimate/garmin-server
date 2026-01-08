# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from garminconnect import Garmin
import os

app = Flask(__name__)
# 모든 곳에서 접속 허용 (보안을 위해 나중에는 Vercel 주소만 허용하게 수정 가능)
CORS(app)

@app.route('/', methods=['GET'])
def home():
    return "Runimate Server is Running!"

@app.route('/api/garmin', methods=['POST'])
def get_garmin_data():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "이메일과 비밀번호가 필요합니다."}), 400

    try:
        # 가민 로그인
        client = Garmin(email, password)
        client.login()

        # 최근 활동 5개 가져오기
        activities = client.get_activities(0, 20)

        # 데이터 가공
        simplified = []
        for act in activities:
            # 거리(m) -> km 변환
            km = act['distance'] / 1000
            # 시간(초)
            seconds = act['duration']
            # 페이스(초/km) 계산 (거리가 0이면 0 처리)
            pace = (seconds / km) if km > 0 else 0

            simplified.append({
                "date": act['startTimeLocal'][:10].replace('-', '.'), # YYYY.MM.DD
                "km": km,
                "timeSec": seconds,
                "paceSec": pace
            })

        return jsonify({"success": True, "data": simplified})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": "로그인 실패 혹은 가민 서버 오류"}), 500

if __name__ == '__main__':
    # 로컬 테스트용 포트
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
