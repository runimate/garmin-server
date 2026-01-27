from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from garminconnect import Garmin
import requests
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ==========================================
# [1] GARMIN (파이썬 라이브러리 사용)
# ==========================================
@app.route('/api/garmin', methods=['POST'])
def garmin_login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and password required'}), 400

    try:
        # 가민 로그인 시도
        client = Garmin(email, password)
        client.login()
        
        # 최근 활동 20개 가져오기
        activities = client.get_activities(0, 20)
        
        formatted_data = []
        for act in activities:
            # 날짜 포맷팅 (YYYY-MM-DD -> YYYY.MM.DD)
            start_time = act.get('startTimeLocal', '')
            date_str = start_time[:10].replace('-', '.')
            
            # 거리 (미터 -> 킬로미터)
            distance_meters = act.get('distance', 0)
            km = round(distance_meters / 1000, 2)
            
            # 시간 (초)
            duration = act.get('duration', 0)
            
            # 페이스 계산 (초당 거리 역산)
            pace_sec = 0
            if km > 0:
                pace_sec = duration / km

            formatted_data.append({
                'date': date_str,
                'km': "{:.2f}".format(km),
                'paceSec': pace_sec,
                'timeSec': duration,
                'type': 'garmin'
            })

        return jsonify({'success': True, 'data': formatted_data})

    except Exception as e:
        print(f"Garmin Error: {e}")
        return jsonify({'success': False, 'message': 'Login Failed', 'error': str(e)}), 500


# ==========================================
# [2] STRAVA (New Logic)
# ==========================================
# Railway 환경 변수에서 가져오거나 하드코딩된 값 사용
STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '192399')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET', '937ff714ee7020fbfaf11f89df49adee08bec445')

@app.route('/api/strava', methods=['POST'])
def get_strava_data():
    try:
        # 1. 프론트엔드에서 보낸 인증 코드 받기
        data = request.get_json()
        code = data.get('code')
        
        if not code:
            return jsonify({"success": False, "message": "Authorization code is missing"}), 400

        # 2. 토큰 교환 (Code -> Access Token)
        token_url = "https://www.strava.com/oauth/token"
        payload = {
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code"
        }
        
        response = requests.post(token_url, data=payload)
        response.raise_for_status() 
        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
             return jsonify({"success": False, "message": "Failed to get access token"}), 400

        # 3. 활동 기록 조회 (최근 30개)
        activities_url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"per_page": 30}
        
        act_response = requests.get(activities_url, headers=headers, params=params)
        act_response.raise_for_status()
        activities = act_response.json()

        # 4. 데이터 가공 (Runimate 포맷)
        formatted_data = []
        for activity in activities:
            if activity.get("type") == "Run": # 달리기만 필터링
                # 날짜 변환
                start_date = activity.get("start_date_local", "")
                local_date = start_date[:10].replace("-", ".")
                
                # 거리 변환 (m -> km)
                raw_km = activity.get("distance", 0) / 1000
                km = f"{raw_km:.2f}"
                
                # 시간 (초)
                moving_time = activity.get("moving_time", 0)
                
                # 페이스 계산 (초/km)
                pace_sec = 0
                if raw_km > 0:
                    pace_sec = int(moving_time / raw_km)
                
                formatted_data.append({
                    "date": local_date,
                    "km": km,
                    "paceSec": pace_sec,
                    "timeSec": moving_time,
                    "type": "strava"
                })

        return jsonify({"success": True, "data": formatted_data})

    except Exception as e:
        print(f"Strava Error: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080)) # Railway 기본 포트 처리
    app.run(host='0.0.0.0', port=port)
