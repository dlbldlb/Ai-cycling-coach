import os
import requests
import json
from datetime import datetime, timedelta

# ------------------------------------------------------------------------------
# [설정] GitHub Secrets에서 환경변수 가져오기
# ------------------------------------------------------------------------------
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID = os.environ["ATHLETE_ID"]
TARGET_FOLDER_ID = 224530  # 용길님 Workouts 폴더 ID (검증 완료)

def run_daily_coach():
    auth = ('API_KEY', INTERVALS_API_KEY)
    
    # 1. 한국 시간(KST) 설정 (서버가 어디에 있든 한국 시간 기준)
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    print(f"🚀 [AI Coach] Started at {kst_now} (KST)")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 (Wellness -> Settings 순서로 검색)
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Athlete Data...")
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        
        # Wellness에 없으면 설정(Settings)에서 2차 검색
        if current_ftp is None:
            print("   ⚠️ eFTP not found in Wellness. Checking Settings...")
            s_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
            s_resp = requests.get(s_url, auth=auth)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                ride_settings = next((s for s in s_data.get('sportSettings', []) if 'Ride' in s.get('types', [])), {})
                current_ftp = ride_settings.get('ftp')
                w_prime = ride_settings.get('w_prime')

        # 데이터 검증 (가정 금지)
        if current_ftp is None:
            print("❌ [Critical Error] FTP data not found. Aborting.")
            exit(1)
        
        if w_prime is None: w_prime = 0 
        tsb = w_data.get('ctl', 0) - w_data.get('atl', 0)
        
        print(f"   📊 Data Loaded: FTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}")

        # ----------------------------------------------------------------------
        # 3. Gemini 훈련 설계
        # ----------------------------------------------------------------------
        print("2️⃣ Asking Gemini to design workout...")
        prompt = f"""
        Role: Professional Cycling Coach.
        Task: Create a 1-hour cycling workout based on athlete's condition.
        Athlete Data: FTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}.
        
        STRICT OUTPUT RULES:
        - Write ONLY the workout steps.
        - Format: "- [Duration] [Intensity] [Text]"
        - Example:
          - 10m 50% Warmup
          - 5m 90% (200W) Tempo
          - 5m 50% Recovery
        - NO intro, NO outro.
        - Unroll loops (do not use '3x', write each step).
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        
        if res.status_code != 200:
            print(f"❌ Gemini Error: {res.text}")
            exit(1)

        raw_text = res.json()['candidates'][0]['content']['parts'][0]['text']
        
        # 텍스트 정제 (하이픈 강제 적용 및 빈 줄 제거)
        lines = raw_text.split('\n')
        clean_lines = []
        for line in lines:
            line = line.strip()
            if not line: continue
            # 숫자로 시작하면 앞에 "- " 붙여줌 (Intervals 문법 준수)
            if line[0].isdigit():
                line = "- " + line
            if line.startswith('-'):
                clean_lines.append(line)
                
        clean_code = "\n".join(clean_lines)
        print(f"   📝 Generated Code:\n{'-'*20}\n{clean_code}\n{'-'*20}")

        if not clean_code:
            print("❌ Error: Generated workout code is empty.")
            exit(1)

        # ----------------------------------------------------------------------
        # 4. 라이브러리 생성 (ID 발급용)
        # ----------------------------------------------------------------------
        print(f"3️⃣ Creating Library Workout (Folder ID: {TARGET_FOLDER_ID})...")
        workout_payload = {
            "name": f"AI Coach: FTP {int(current_ftp)} / TSB {tsb:.1f}",
            "description": clean_code,
            "type": "Ride",
            "sport": "Ride",
            "folder_id": TARGET_FOLDER_ID
        }
        
        create_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/workouts"
        create_resp = requests.post(create_url, auth=auth, json=workout_payload)
        
        if create_resp.status_code != 200:
            print(f"❌ Failed to create library workout: {create_resp.text}")
            exit(1)
            
        workout_data = create_resp.json()
        workout_id = workout_data['id']
        print(f"   ✅ Library Workout Created! ID: {workout_id}")

        # ----------------------------------------------------------------------
        # 5. 캘린더 등록 (양방향 주입: ID + Text) - 그래프 보장 비법
        # ----------------------------------------------------------------------
        print("4️⃣ Scheduling to Calendar...")
        
        event_payload = {
            "category": "WORKOUT",
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "name": f"AI Coach: FTP {int(current_ftp)} / TSB {tsb:.1f}",
            "type": "Ride",
            "workout_id": workout_id,
            "description": clean_code # [핵심] 텍스트를 한 번 더 주입하여 그래프 강제 렌더링
        }
        
        event_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true"
        final_res = requests.post(event_url, auth=auth, json=[event_payload])
        
        if final_res.status_code == 200:
            print(f"🎉 Success! Workout scheduled for {today_str} 19:00 (KST).")
        else:
            print(f"❌ Failed to schedule event: {final_res.text}")
            exit(1)

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_daily_coach()
