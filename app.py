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
# [2] STRAVA (OAuth 2.0)
# ==========================================
# ※ 발급받은 키를 여기에 입력하세요! (따옴표 안에 입력)
STRAVA_CLIENT_ID = '192399' 
STRAVA_CLIENT_SECRET = '937ff714ee7020fbfaf11f89df49adee08bec445'
FRONTEND_URL = 'https://runimate.vercel.app'

# 2-1. 로그인 요청 (사용자가 버튼 누르면 이동)
@app.route('/api/strava/login', methods=['GET'])
def strava_login():
    host = request.host
    # 로컬 테스트가 아니면 https 강제
    protocol = 'http' if 'localhost' in host else 'https'
    redirect_uri = f"{protocol}://{host}/api/strava/callback"
    
    scope = "activity:read_all"
    auth_url = (
        f"https://www.strava.com/oauth/authorize?"
        f"client_id={STRAVA_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code&"
        f"scope={scope}"
    )
    return redirect(auth_url)

# 2-2. 콜백 처리 (인증 후 복귀)
@app.route('/api/strava/callback', methods=['GET'])
def strava_callback():
    code = request.args.get('code')
    if not code:
        return "Error: No code provided"

    try:
        # 토큰 교환 요청
        response = requests.post('https://www.strava.com/oauth/token', data={
            'client_id': STRAVA_CLIENT_ID,
            'client_secret': STRAVA_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code'
        })
        
        data = response.json()
        access_token = data.get('access_token')
        
        # 프론트엔드로 복귀
        return redirect(f"{FRONTEND_URL}?strava_token={access_token}")

    except Exception as e:
        print(f"Strava Auth Error: {e}")
        return "Strava Login Failed"

# 2-3. 데이터 가져오기
@app.route('/api/strava/activities', methods=['POST'])
def strava_activities():
    data = request.json
    token = data.get('token')
    
    if not token:
        return jsonify({'success': False, 'message': 'Token required'}), 400

    try:
        response = requests.get(
            'https://www.strava.com/api/v3/athlete/activities?per_page=20',
            headers={'Authorization': f'Bearer {token}'}
        )
        
        if response.status_code != 200:
            return jsonify({'success': False, 'message': 'Failed to fetch data'}), 401
            
        activities = response.json()
        formatted_data = []
        
        for item in activities:
            start_date = item.get('start_date_local', '')
            date_str = start_date[:10].replace('-', '.') # YYYY.MM.DD
            
            distance_meters = item.get('distance', 0)
            km = round(distance_meters / 1000, 2)
            
            moving_time = item.get('moving_time', 0)
            
            pace_sec = 0
            if km > 0:
                pace_sec = moving_time / km
                
            formatted_data.append({
                'date': date_str,
                'km': "{:.2f}".format(km),
                'paceSec': pace_sec,
                'timeSec': moving_time,
                'type': 'strava'
            })
            
        return jsonify({'success': True, 'data': formatted_data})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080)) # Railway 기본 포트 처리
    app.run(host='0.0.0.0', port=port)
