# gunicorn이 시작할 때 자동으로 읽는 설정 파일 (Railway 대시보드 수정 불필요)
# 서버는 대부분 가민·스트라바·코로스 응답을 "기다리는" 일이라,
# 일꾼 2명 × 각 8개 스레드로 최대 16개 요청을 동시에 처리합니다.
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 2
worker_class = "gthread"
threads = 8
timeout = 120          # 가민 로그인·기록 조회가 느릴 때 30초(기본값)에 끊기지 않도록
graceful_timeout = 30
keepalive = 5
accesslog = "-"        # 요청 기록을 Railway 로그에 남김 (요청 수 확인용)
