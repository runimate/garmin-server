from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from garminconnect import Garmin
import requests
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ==========================================
# [1] GARMIN
# ==========================================
@app.route('/api/garmin', methods=['POST'])
def garmin_login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and password required'}), 400

    try:
        client = Garmin(email, password)
        client.login()
        activities = client.get_activities(0, 20)
        
        formatted_data = []
        for act in activities:
            start_time = act.get('startTimeLocal', '')
            date_str = start_time[:10].replace('-', '.')
            distance_meters = act.get('distance', 0)
            km = round(distance_meters / 1000, 2)
            duration = act.get('duration', 0)
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
# [2] STRAVA (Multi-User Safe Logic)
# ==========================================
# [중요] 코드에 직접 적지 말고 os.environ.get만 남겨둡니다.
# Railway 대시보드 Variables 탭에 값을 입력해야 작동합니다.
STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')

@app.route('/api/strava', methods=['POST'])
def get_strava_data():
    try:
        data = request.get_json()
        code = data.get('code')
        
        if not code:
            return jsonify({"success": False, "message": "Authorization code is missing"}), 400

        # 1. 토큰 교환
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

        # 2. 활동 기록 조회
        activities_url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"per_page": 30}
        
        act_response = requests.get(activities_url, headers=headers, params=params)
        act_response.raise_for_status()
        activities = act_response.json()

        # 3. 데이터 가공
        formatted_data = []
        for activity in activities:
            if activity.get("type") == "Run": 
                start_date = activity.get("start_date_local", "")
                local_date = start_date[:10].replace("-", ".")
                raw_km = activity.get("distance", 0) / 1000
                km = f"{raw_km:.2f}"
                moving_time = activity.get("moving_time", 0)
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
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
