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
    
    # 1. 한국 시간(KST) 설정
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    
    print(f"🕒 Korea Time(KST): {kst_now}")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 (eFTP & W')
        # ----------------------------------------------------------------------
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        
        # 백업: 설정값 사용
        if current_ftp is None:
            s_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
            s_resp = requests.get(s_url, auth=auth)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                ride_settings = next((s for s in s_data.get('sportSettings', []) if 'Ride' in s.get('types', [])), {})
                current_ftp = ride_settings.get('ftp')
                w_prime = ride_settings.get('w_prime')
        
        if current_ftp is None:
            print("❌ Critical Error: FTP data not found.")
            exit(1)
        if w_prime is None: w_prime = 0 

        tsb = w_data.get('ctl', 0) - w_data.get('atl', 0)
        print(f"📊 Data: FTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}")

        # ----------------------------------------------------------------------
        # 3. Gemini 훈련 설계
        # ----------------------------------------------------------------------
        prompt = f"""
        Athlete Data: FTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}
        Task: Create a 1-hour cycling workout code.
        Rules:
        - Output ONLY the workout lines.
        - Start every line with a hyphen (-).
        - Use simple format: "- 10m 50%" or "- 10m 200w".
        - Example:
          - 10m 50% Warmup
          - 5m 90% Interval
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        workout_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        clean_code = "\n".join([l.strip() for l in workout_text.split('\n') if l.strip().startswith('-')])

        # ----------------------------------------------------------------------
        # 4. [핵심] 라이브러리 생성 (Create Library Workout)
        # ----------------------------------------------------------------------
        print("🔨 Creating workout in Library...")
        workout_payload = {
            "name": f"AI Coach: FTP {int(current_ftp)} / TSB {tsb:.1f}",
            "description": clean_code,
            "type": "Ride",
            "sport": "Ride"
        }
        
        # 워크아웃 생성 API 호출
        create_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/workouts"
        create_resp = requests.post(create_url, auth=auth, json=workout_payload)
        
        if create_resp.status_code != 200:
            print(f"❌ Failed to create library workout: {create_resp.text}")
            exit(1)
            
        workout_data = create_resp.json()
        workout_id = workout_data['id']
        print(f"✅ Library Workout Created! ID: {workout_id}")

        # ----------------------------------------------------------------------
        # 5. [핵심] 캘린더 등록 (Schedule Event)
        # ----------------------------------------------------------------------
        print(f"📅 Scheduling to Calendar (ID: {workout_id})...")
        
        event_payload = {
            "category": "WORKOUT",
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "name": f"AI Coach: FTP {int(current_ftp)} / TSB {tsb:.1f}",
            "type": "Ride",
            "workout_id": workout_id # 여기서 위에서 만든 ID를 연결합니다.
        }
        
        # 이벤트 생성 (bulk upsert 사용)
        event_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true"
        final_res = requests.post(event_url, auth=auth, json=[event_payload])
        
        if final_res.status_code == 200:
            print(f"✅ Success! Workout scheduled for {today_str} (KST).")
            print(f"📝 Code Snippet:\n{clean_code[:100]}...")
        else:
            print(f"❌ Failed to schedule event: {final_res.text}")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_coach()
