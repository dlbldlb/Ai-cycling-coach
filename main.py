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
    
    # 한국 시간(KST)
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    
    print(f"🕒 Korea Time(KST): {kst_now}")

    try:
        # -----------------------------------------------------------
        # 1. 데이터 추출 (가정(Assumption) 제거 버전)
        # -----------------------------------------------------------
        
        # (1) Wellness에서 eFTP 시도
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        
        source = "eFTP (Wellness)"

        # (2) eFTP 없으면 -> 사용자 설정(Settings)에서 FTP 가져오기
        if current_ftp is None:
            print("⚠️ eFTP not found. Fetching User Settings...")
            settings_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
            s_resp = requests.get(settings_url, auth=auth)
            
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                # sportSettings에서 Ride 타입 찾기
                ride_settings = next((s for s in s_data.get('sportSettings', []) if 'Ride' in s.get('types', [])), {})
                current_ftp = ride_settings.get('ftp')
                w_prime = ride_settings.get('w_prime')
                source = "FTP (Settings)"
        
        # (3) 그래도 없으면? 에러 발생시키고 종료 (175로 가정하지 않음)
        if current_ftp is None:
            print("❌ Critical Error: FTP/eFTP data not found in Intervals.icu.")
            print("   Please check if your 'Ride' FTP is set in Settings.")
            exit(1) # 강제 종료

        # W'가 설정에 없는 경우 (0 또는 None일 수 있음)
        if w_prime is None:
            w_prime = 0 # W'는 없으면 0으로 두어 AI가 참고만 하게 함 (임의의 14000 설정 금지)

        tsb = w_data.get('ctl', 0) - w_data.get('atl', 0)
        
        print(f"📊 Using Data [{source}]: FTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}")

        # -----------------------------------------------------------
        # 2. Gemini 2.5 Flash 훈련 설계
        # -----------------------------------------------------------
        prompt = f"""
        Athlete Data: FTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}
        Task: Create a 1-hour cycling workout code.
        Rules:
        - Output ONLY the workout code lines. No text, no explanation.
        - Do NOT use loops (like 3x). Unroll all steps.
        - Start every line with a hyphen (-).
        - Format Example:
          - 10m 50% Warmup
          - 5m 90% Interval
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        workout_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # 코드 정제
        clean_code = "\n".join([l.strip() for l in workout_text.split('\n') if l.strip().startswith('-')])

        # -----------------------------------------------------------
        # 3. Intervals.icu 등록 (그래프 생성 포함)
        # -----------------------------------------------------------
        event = {
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "type": "Ride", 
            "category": "WORKOUT",
            "name": f"AI Coach: FTP {int(current_ftp)} / TSB {tsb:.1f}",
            "workout": {
                "description": clean_code,
                "sport": "Ride"
            }
        }
        
        final_res = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true", auth=auth, json=[event])
        
        if final_res.status_code == 200:
            print(f"✅ Workout created successfully for {today_str} (KST)!")
            print(f"📝 Code:\n{clean_code}")
        else:
            print(f"❌ Failed to create workout: {final_res.text}")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_coach()
