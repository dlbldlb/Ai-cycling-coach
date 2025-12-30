import os
import requests
import json
from datetime import datetime, timedelta

# GitHub Secrets
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID = os.environ["ATHLETE_ID"]

def run_coach():
    auth = ('API_KEY', INTERVALS_API_KEY)
    
    # 한국 시간(KST) 계산
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    
    print(f"🕒 Korea Time(KST): {kst_now}")

    # [중요 변경] '미수행 훈련 삭제' 로직을 제거했습니다. 
    # 페어링 실패로 인한 억울한 삭제를 방지하기 위함입니다.

    try:
        # 1. 데이터 추출 (오늘 날짜 기준)
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        current_ftp = ride_info.get('eftp') or 175
        w_prime = ride_info.get('wPrime') or 14000
        tsb = w_data.get('ctl', 0) - w_data.get('atl', 0)
        
        print(f"📊 Data: eFTP {current_ftp}, W' {w_prime}, TSB {tsb}")

        # 2. Gemini 2.5 Flash 훈련 설계
        prompt = f"""
        Athlete Data: eFTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}
        Task: Create a 1-hour cycling workout code.
        Rules:
        - Output ONLY the workout code lines. No text, no explanation.
        - Do NOT use loops (like 3x). Unroll all steps.
        - Start every line with a hyphen (-).
        - Example format:
          - 10m 50% Warmup
          - 5m 90% Interval
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        workout_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # 코드 정제
        clean_code = "\n".join([l.strip() for l in workout_text.split('\n') if l.strip().startswith('-')])

        # 3. Intervals.icu 파싱 및 등록
        # 먼저 텍스트를 파싱해서 완벽한 워크아웃 객체를 받습니다.
        parse_resp = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/workouts/parse", 
                                   auth=auth, json={"description": clean_code})
        
        if parse_resp.status_code != 200:
            print(f"❌ Parse Failed: {parse_resp.text}")
            exit(1)

        parsed_workout = parse_resp.json()
        
        # [핵심 수정] workout_doc 키에 파싱된 객체 전체를 넣습니다.
        event = {
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "type": "Ride", 
            "category": "WORKOUT",
            "name": f"AI Coach: eFTP {int(current_ftp)} / TSB {tsb:.1f}",
            "description": clean_code,      # 텍스트 설명
            "workout_doc": parsed_workout   # 그래프 데이터 (이 키가 정답입니다)
        }
        
        final_res = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true", auth=auth, json=[event])
        
        if final_res.status_code == 200:
            print(f"✅ Workout created successfully for {today_str} (KST)!")
        else:
            print(f"❌ Failed to create workout: {final_res.text}")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_coach()
