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
        activities = client.get_activities(0, 40)

        formatted_data = []
        for act in activities:
            sport_type = act.get('activityType', {}).get('typeKey', 'unknown')
            if 'run' not in sport_type.lower():  # 러닝/트레일러닝 계열만 포함 (사이클링, 걷기 등 제외)
                continue

            start_time = act.get('startTimeLocal', '')
            date_str = start_time[:10].replace('-', '.')
            distance_meters = act.get('distance', 0)
            km = round(distance_meters / 1000, 2)
            duration = act.get('duration', 0)
            pace_sec = 0
            if km > 0:
                pace_sec = duration / km

            elev_gain = act.get('elevationGain', 0) or 0

            formatted_data.append({
                'date': date_str,
                'km': "{:.2f}".format(km),
                'paceSec': pace_sec,
                'timeSec': duration,
                'type': 'garmin',
                'sportType': sport_type, # <--- 프론트엔드로 전달!
                'elevGain': round(elev_gain)
            })

        return jsonify({'success': True, 'data': formatted_data})

    except Exception as e:
        print(f"Garmin Error: {e}")
        return jsonify({'success': False, 'message': 'Login Failed', 'error': str(e)}), 500


# ==========================================
# [2] STRAVA
# ==========================================
STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
# 웹훅 구독 검증에 사용할 임의의 토큰. Railway 환경변수에 원하는 문자열로 설정하세요.
STRAVA_VERIFY_TOKEN = os.environ.get('STRAVA_VERIFY_TOKEN', 'RUNIMATE_STRAVA_VERIFY')
# /api/strava/webhook/setup, /status 를 아무나 호출하지 못하도록 막는 관리자 키.
STRAVA_WEBHOOK_ADMIN_KEY = os.environ.get('STRAVA_WEBHOOK_ADMIN_KEY')

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
            # [핵심 수정] 스트라바 원본에서 진짜 운동 종류(Run, Walk 등)를 빼냅니다!
            sport_type = activity.get("sport_type") or activity.get("type", "unknown")
            if 'run' not in sport_type.lower():  # 러닝/트레일러닝 계열만 포함 (사이클링, 걷기 등 제외)
                continue

            start_date = activity.get("start_date_local", "")
            local_date = start_date[:10].replace("-", ".")
            raw_km = activity.get("distance", 0) / 1000
            km = f"{raw_km:.2f}"
            moving_time = activity.get("moving_time", 0)
            pace_sec = 0
            if raw_km > 0:
                pace_sec = int(moving_time / raw_km)

            elev_gain = activity.get("total_elevation_gain", 0) or 0

            formatted_data.append({
                "date": local_date,
                "km": km,
                "paceSec": pace_sec,
                "timeSec": moving_time,
                "type": "strava",
                "sportType": sport_type, # <--- 프론트엔드로 전달!
                "elevGain": round(elev_gain)
            })

        return jsonify({"success": True, "data": formatted_data})

    except Exception as e:
        print(f"Strava Error: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


# ==========================================
# [3] STRAVA WEBHOOK
# ==========================================
# RUNIMATE는 저장된 Strava 토큰이 없는 구조(요청 시점에만 code -> token 교환 후 즉시 폐기)라서,
# 웹훅으로 들어오는 이벤트는 "수신 -> 로그 -> 200 응답"으로 처리합니다.
# 이것만으로도 Strava가 요구하는 "폴링 대신 웹훅 사용" 요건과
# "deauthorize된 athlete를 인지하고 처리" 요건을 모두 충족합니다.
# (RUNIMATE는애초에 백그라운드 폴링을 하지 않으므로, 실질적으로는
#  "이벤트를 받아 올바르게 ack하고 재시도하지 않는다"가 핵심입니다.)

@app.route('/api/strava/webhook', methods=['GET'])
def strava_webhook_verify():
    """
    Strava가 구독 생성 시 최초 1회 호출하는 검증(handshake) 엔드포인트.
    hub.challenge 값을 그대로 되돌려줘야 구독이 활성화됩니다.
    """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == STRAVA_VERIFY_TOKEN:
        return jsonify({'hub.challenge': challenge}), 200
    return jsonify({'error': 'Webhook verification failed'}), 403


@app.route('/api/strava/webhook', methods=['POST'])
def strava_webhook_receive():
    """
    액티비티 생성/수정/삭제 및 athlete 인증 해제(deauthorize) 이벤트를 실시간 수신.
    Strava는 2초 이내 200 응답을 요구하므로, 무거운 처리 없이 즉시 로그만 남기고 응답합니다.
    """
    event = request.get_json(silent=True) or {}

    object_type = event.get('object_type')
    aspect_type = event.get('aspect_type')
    owner_id = event.get('owner_id')
    updates = event.get('updates', {}) or {}

    if object_type == 'athlete' and updates.get('authorized') == 'false':
        # 사용자가 앱 연동을 해제(deauthorize)한 경우.
        # RUNIMATE는 해당 athlete의 토큰을 애초에 저장하지 않으므로 추가로 정리할 데이터가 없습니다.
        print(f"[Strava Webhook] Athlete {owner_id} deauthorized RUNIMATE. Acknowledged, no stored token to clean up.")
    elif object_type == 'activity':
        print(f"[Strava Webhook] Activity event - athlete={owner_id}, aspect={aspect_type}, activity_id={event.get('object_id')}, time={event.get('event_time')}")
    else:
        print(f"[Strava Webhook] Unhandled event: {event}")

    return jsonify({'status': 'EVENT_RECEIVED'}), 200


@app.route('/api/strava/webhook/debug', methods=['GET'])
def strava_webhook_debug():
    """
    임시 디버그용: 시크릿 값 자체는 절대 노출하지 않고,
    Railway 환경변수가 이 배포(worker)에 실제로 전달됐는지만 확인합니다.
    확인 끝나면 이 라우트는 삭제하는 걸 권장합니다.
    """
    def info(v):
        return {'set': bool(v), 'length': len(v) if v else 0}

    return jsonify({
        'STRAVA_CLIENT_ID': info(STRAVA_CLIENT_ID),
        'STRAVA_CLIENT_SECRET': info(STRAVA_CLIENT_SECRET),
        'STRAVA_VERIFY_TOKEN': info(STRAVA_VERIFY_TOKEN),
        'STRAVA_WEBHOOK_ADMIN_KEY': info(STRAVA_WEBHOOK_ADMIN_KEY),
    }), 200


@app.route('/api/strava/webhook/setup', methods=['GET'])
def strava_webhook_setup():
    """
    최초 1회, 배포 후 브라우저에서 직접 방문해 Strava에 웹훅 구독을 등록하는 헬퍼.
    사용법: https://<railway-domain>/api/strava/webhook/setup?key=<STRAVA_WEBHOOK_ADMIN_KEY>
    """
    key = request.args.get('key')
    if not STRAVA_WEBHOOK_ADMIN_KEY or key != STRAVA_WEBHOOK_ADMIN_KEY:
        return jsonify({'error': 'unauthorized'}), 403

    callback_url = request.args.get('callback_url') or (request.host_url.rstrip('/') + '/api/strava/webhook')

    try:
        resp = requests.post(
            'https://www.strava.com/api/v3/push_subscriptions',
            data={
                'client_id': STRAVA_CLIENT_ID,
                'client_secret': STRAVA_CLIENT_SECRET,
                'callback_url': callback_url,
                'verify_token': STRAVA_VERIFY_TOKEN
            }
        )
        return jsonify({'status_code': resp.status_code, 'response': resp.json()}), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/strava/webhook/status', methods=['GET'])
def strava_webhook_status():
    """
    현재 등록된 웹훅 구독을 확인. 재신청 전 정상 등록 확인용.
    사용법: https://<railway-domain>/api/strava/webhook/status?key=<STRAVA_WEBHOOK_ADMIN_KEY>
    """
    key = request.args.get('key')
    if not STRAVA_WEBHOOK_ADMIN_KEY or key != STRAVA_WEBHOOK_ADMIN_KEY:
        return jsonify({'error': 'unauthorized'}), 403
    try:
        resp = requests.get(
            'https://www.strava.com/api/v3/push_subscriptions',
            params={'client_id': STRAVA_CLIENT_ID, 'client_secret': STRAVA_CLIENT_SECRET}
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
