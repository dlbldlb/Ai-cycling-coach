import os
import requests
import csv
import io
import json
from datetime import datetime, timedelta

# ------------------------------------------------------------------------------
# [설정] GitHub Secrets 환경변수
# ------------------------------------------------------------------------------
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID = os.environ["ATHLETE_ID"]
TARGET_FOLDER_ID = 224530

def run_daily_coach():
    auth = ('API_KEY', INTERVALS_API_KEY)
    
    # 1. 한국 시간(KST) 설정
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    print(f"🚀 [AI Coach] Started at {kst_now} (KST)")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 1: Wellness (FTP, CTL 확인)
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Wellness Data...")
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        ctl = w_data.get('ctl', 0)     # Fitness (체력)
        atl = w_data.get('atl', 0)     # Fatigue (피로)
        tsb = ctl - atl                # Form (컨디션)

        # FTP 백업 로직
        if current_ftp is None:
            s_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
            s_resp = requests.get(s_url, auth=auth)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                ride_settings = next((s for s in s_data.get('sportSettings', []) if 'Ride' in s.get('types', [])), {})
                current_ftp = ride_settings.get('ftp')
                w_prime = ride_settings.get('w_prime')

        if current_ftp is None:
            print("❌ [Critical] FTP data not found. Exiting.")
            exit(1)
            
        if w_prime is None: w_prime = 0 

        # ----------------------------------------------------------------------
        # 3. 데이터 추출 2: Power Curve (CSV)
        # ----------------------------------------------------------------------
        print("2️⃣ Fetching Power Curve via CSV...")
        
        from_date = kst_now.isoformat()
        csv_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/power-curves.csv"
        params = {
            'curves': '42d',
            'type': 'Ride',
            'from': from_date
        }
        
        csv_resp = requests.get(csv_url, auth=auth, params=params)
        
        five_min_power = None
        
        # CSV 로직 (실패시 종료하는 엄격 모드 유지)
        if csv_resp.status_code == 200:
            f = io.StringIO(csv_resp.text)
            reader = csv.DictReader(f)
            
            if reader.fieldnames:
                clean_headers = [name.replace('\ufeff', '').strip() for name in reader.fieldnames]
                reader.fieldnames = clean_headers
                target_col = next((col for col in clean_headers if '42' in col), None)
                
                if target_col:
                    for row in reader:
                        secs_val = row.get('secs') or row.get('Time')
                        if secs_val and float(secs_val) == 300.0:
                            p_val = row.get(target_col)
                            if p_val:
                                five_min_power = int(float(p_val))
                                print(f"   🎯 Found 5m Power: {five_min_power} W")
                            break
        
        # 5분 파워가 없으면(2달간 기록 없음) -> 0으로 처리해서 프롬프트에 넘김 (종료하지 않음)
        # 초기화 상태에서는 5분 파워가 없을 수도 있으므로 유연하게 대처
        if five_min_power is None:
            print("   ⚠️ 42일간 기록이 없습니다. (초기화 상태 추정)")
            five_min_power = 0

        print(f"   📊 Status: FTP {current_ftp}W, CTL(Fitness) {ctl:.1f}, TSB {tsb:.1f}")

        # ----------------------------------------------------------------------
        # 4. Gemini 훈련 설계 (초기화 감지 로직 추가)
        # ----------------------------------------------------------------------
        print("3️⃣ Asking Gemini to design workout (Auto-Scaling Mode)...")
        
        prompt = f"""
        Role: Expert Cycling Coach.
        Task: Create a 1-hour structured cycling workout code for Intervals.icu.
        
        [ATHLETE DATA]
        - FTP (Stored): {current_ftp} W
        - CTL (Fitness): {ctl:.1f}
        - TSB (Form): {tsb:.1f}
        - Recent 5m Max Power: {five_min_power} W

        [INTELLIGENT COACHING LOGIC - PRIORITY ORDER]
        
        1. PHASE CHECK: DETRAINING / RETURN TO SPORT
           ** IF CTL < 30 OR Recent 5m Max Power == 0 **:
           - Diagnosis: Athlete is DETRAINED (reset state).
           - ACTION: IGNORE TSB. Do NOT prescribe High Intensity.
           - Focus: Base Building / Re-adaptation.
           - Intensity: STRICTLY Zone 2 (Endurance).
           - Structure: Composed in various ways so as not to be boring.
           
        2. PHASE CHECK: NORMAL TRAINING (Only if CTL >= 30)
           Analyze TSB:
           - TSB < -10 (Fatigued): Recovery (Zone 1).
           - -10 <= TSB <= 10 (Optimal): Sweet Spot (88-93% FTP).
           - TSB > 10 (Fresh): VO2 Max (Hard Intervals).

        [STRICT OUTPUT FORMAT]
        - Output ONLY the workout lines.
        - Start every line with "-".
        - Format: "- [Duration] [Intensity] [Text]"
        - Example:
          - 10m 50% Warmup
          - 40m 60% Base Ride
          - 10m 50% Cooldown
        - UNROLL LOOPS.
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        
        if res.status_code != 200:
            print(f"❌ Gemini Error: {res.text}")
            exit(1)

        raw_text = res.json()['candidates'][0]['content']['parts'][0]['text']
        
        lines = raw_text.split('\n')
        clean_lines = []
        for line in lines:
            line = line.strip()
            if not line: continue
            if line[0].isdigit():
                line = "- " + line
            if line.startswith('-'):
                clean_lines.append(line)
        clean_code = "\n".join(clean_lines)
        
        print(f"   📝 Generated Code:\n{'-'*20}\n{clean_code}\n{'-'*20}")
        if not clean_code: exit(1)

        # ----------------------------------------------------------------------
        # 5. 라이브러리 및 캘린더 등록
        # ----------------------------------------------------------------------
        print(f"4️⃣ Uploading to Intervals.icu...")
        workout_payload = {
            "name": f"AI Coach: CTL {ctl:.1f} (Return)" if ctl < 30 else f"AI Coach: TSB {tsb:.1f}",
            "description": clean_code,
            "type": "Ride",
            "sport": "Ride",
            "folder_id": TARGET_FOLDER_ID
        }
        
        create_resp = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/workouts", auth=auth, json=workout_payload)
        workout_id = create_resp.json()['id']
        
        event_payload = {
            "category": "WORKOUT",
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "name": f"AI Coach: {'Detrained Mode' if ctl < 30 else 'Training Mode'}",
            "type": "Ride",
            "workout_id": workout_id,
            "description": clean_code
        }
        
        requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true", auth=auth, json=[event_payload])
        print(f"🎉 Success! Workout scheduled.")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_daily_coach()
