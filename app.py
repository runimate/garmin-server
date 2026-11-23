from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from garminconnect import Garmin
import requests
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)


# ==========================================
# 공통 헬퍼
# ==========================================
def want_sport(sport_type, include_hike):
    """러닝/트레일러닝은 항상 포함. include_hike=True(V3.0)일 때만 하이킹도 포함.
    V2.5는 이 플래그를 보내지 않으므로 기존과 똑같이 러닝 계열만 받음."""
    s = (sport_type or '').lower()
    if 'run' in s:
        return True
    return include_hike and 'hik' in s


def downsample_profile(dist_m, elev_m, n=120):
    """거리(m)·고도(m) 배열을 [[km, m], ...] 형태로 최대 n개 지점까지 줄임 (고도표용)."""
    pts = [(d, e) for d, e in zip(dist_m, elev_m) if d is not None and e is not None]
    if len(pts) < 2:
        return None
    if len(pts) > n:
        step = (len(pts) - 1) / (n - 1)
        pts = [pts[round(i * step)] for i in range(n)]
    return [[round(d / 1000, 3), round(e, 1)] for d, e in pts]


def garmin_elev_profile(details):
    """get_activity_details 응답의 차트 데이터에서 거리·고도만 뽑아냄."""
    try:
        idx = {m.get('key'): m.get('metricsIndex') for m in (details.get('metricDescriptors') or [])}
        ei = idx.get('directElevation')
        di = idx.get('sumDistance')
        if ei is None or di is None:
            return None
        dist, elev = [], []
        for row in details.get('activityDetailMetrics') or []:
            m = row.get('metrics') or []
            if max(ei, di) < len(m):
                dist.append(m[di])
                elev.append(m[ei])
        return downsample_profile(dist, elev)
    except Exception as e:
        print(f"Garmin elevation parse failed: {e}")
        return None

# ==========================================
# [1] GARMIN
# ==========================================
@app.route('/api/garmin', methods=['POST'])
def garmin_login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    include_hike = bool(data.get('includeHike'))

    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and password required'}), 400

    try:
        client = Garmin(email, password)
        client.login()
        activities = client.get_activities(0, 40)

        formatted_data = []
        for act in activities:
            sport_type = act.get('activityType', {}).get('typeKey', 'unknown')
            if not want_sport(sport_type, include_hike):  # 러닝/트레일러닝(+V3.0은 하이킹)만 포함
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
                'elevGain': round(elev_gain),
                'activityId': act.get('activityId'),
                'polyline': None, # 아래에서 제한된 개수만 채움 (Railway 부담 방지)
                'elevProfile': None # 고도표용 [[km, m], ...] — 폴리라인과 같은 상세 응답에서 함께 채움
            })

        # 지도 경로용 GPS 폴리라인 추가 조회.
        # Garmin은 목록 API에 좌표가 없어 활동별로 상세 조회(get_activity_details)를 한 번 더 호출해야 함.
        # 활동마다 추가 요청이 발생하므로, Railway/Garmin 비공식 API 부담을 막기 위해
        # "가장 최근 N개" 활동에 대해서만 폴리라인을 채워 넣음 (그 외 활동은 Map 탭이 자동으로 숨겨짐).
        GARMIN_POLYLINE_LIMIT = 8
        for item in formatted_data[:GARMIN_POLYLINE_LIMIT]:
            activity_id = item.get('activityId')
            if not activity_id:
                continue
            try:
                details = client.get_activity_details(activity_id, maxchart=400, maxpoly=300)
                raw_points = (details.get('geoPolylineDTO') or {}).get('polyline') or []
                coords = [
                    {'lat': p.get('lat'), 'lon': p.get('lon')}
                    for p in raw_points
                    if p.get('lat') is not None and p.get('lon') is not None
                ]
                item['polyline'] = coords if len(coords) >= 2 else None
                item['elevProfile'] = garmin_elev_profile(details)
            except Exception as poly_err:
                print(f"Garmin polyline fetch failed for activity {activity_id}: {poly_err}")
                item['polyline'] = None

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
        include_hike = bool(data.get('includeHike'))
        # V3.0은 기록을 고를 때 고도 데이터를 따로 받아오기 위해 토큰을 돌려받음 (서버에는 저장하지 않음)
        return_token = bool(data.get('returnToken'))

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
            if not want_sport(sport_type, include_hike):  # 러닝/트레일러닝(+V3.0은 하이킹)만 포함
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

            # 지도 경로용 GPS 폴리라인. summary_polyline은 활동 목록 조회(/athlete/activities) 응답에
            # 이미 포함되어 있어 추가 API 호출이 전혀 필요 없음 (Railway/Strava 쿼터 부담 없음).
            polyline = (activity.get("map") or {}).get("summary_polyline", "") or ""

            formatted_data.append({
                "date": local_date,
                "km": km,
                "paceSec": pace_sec,
                "timeSec": moving_time,
                "type": "strava",
                "sportType": sport_type, # <--- 프론트엔드로 전달!
                "elevGain": round(elev_gain),
                "polyline": polyline, # <--- 인코딩된 문자열. 디코딩은 프론트에서 처리.
                "activityId": activity.get("id")
            })

        result = {"success": True, "data": formatted_data}
        if return_token:
            result["token"] = access_token
            result["tokenExpiresAt"] = token_data.get("expires_at")
        return jsonify(result)

    except Exception as e:
        print(f"Strava Error: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/strava/elevation', methods=['POST'])
def get_strava_elevation():
    """V3.0: 사용자가 기록 하나를 고르면 그 기록의 고도 스트림만 받아옴 (기록 1개당 API 1회)."""
    try:
        data = request.get_json(silent=True) or {}
        token = data.get('token')
        activity_id = data.get('activityId')
        if not token or not activity_id:
            return jsonify({"success": False, "message": "token and activityId required"}), 400

        resp = requests.get(
            f"https://www.strava.com/api/v3/activities/{int(activity_id)}/streams",
            headers={"Authorization": f"Bearer {token}"},
            params={"keys": "altitude,distance", "key_by_type": "true"},
            timeout=15
        )
        if resp.status_code == 401:
            return jsonify({"success": False, "message": "token expired"}), 401
        resp.raise_for_status()
        streams = resp.json() or {}
        dist = (streams.get('distance') or {}).get('data') or []
        elev = (streams.get('altitude') or {}).get('data') or []
        return jsonify({"success": True, "elevProfile": downsample_profile(dist, elev)})

    except Exception as e:
        print(f"Strava elevation Error: {str(e)}")
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
