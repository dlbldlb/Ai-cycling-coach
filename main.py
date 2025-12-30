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
TARGET_FOLDER_ID = 224530  # 용길님 Workouts 폴더 ID

def run_daily_coach():
    auth = ('API_KEY', INTERVALS_API_KEY)
    
    # 1. 한국 시간(KST) 설정
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    print(f"🚀 [AI Coach] Started at {kst_now} (KST)")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 1: Wellness (기본 스펙 - FTP, W', TSB)
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Wellness Data (Base Specs)...")
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        ctl = w_data.get('ctl', 0)
        atl = w_data.get('atl', 0)
        tsb = ctl - atl

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
        # 3. 데이터 추출 2: Power Curve (CSV - 42d 5분 파워 181W 채굴)
        # ----------------------------------------------------------------------
        print("2️⃣ Fetching Power Curve via CSV (Targeting 5m Power)...")
        
        from_date = kst_now.isoformat()
        csv_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/power-curves.csv"
        params = {
            'curves': '42d',
            'type': 'Ride',
            'from': from_date
        }
        
        csv_resp = requests.get(csv_url, auth=auth, params=params)
        
        # [수정] 안전장치 제거: 변수 초기화 없음. 실패시 즉시 종료.
        five_min_power = None
        curve_source = None
        
        if csv_resp.status_code == 200:
            f = io.StringIO(csv_resp.text)
            reader = csv.DictReader(f)
            
            if reader.fieldnames:
                clean_headers = [name.replace('\ufeff', '').strip() for name in reader.fieldnames]
                reader.fieldnames = clean_headers
                
                target_col = next((col for col in clean_headers if '42' in col), None)
                
                if target_col:
                    print(f"   👉 Target Column Found: '{target_col}'")
                    for row in reader:
                        secs_val = row.get('secs') or row.get('Time')
                        if secs_val and float(secs_val) == 300.0:
                            p_val = row.get(target_col)
                            if p_val:
                                five_min_power = int(float(p_val))
                                curve_source = f"CSV ({target_col})"
                                print(f"   🎯 Found 5m Power: {five_min_power} W")
                            break
                    
                    # 5분 파워를 못 찾았으면 종료
                    if five_min_power is None:
                        print(f"❌ [Error] 300s (5m) data not found in CSV. Exiting.")
                        exit(1)
                else:
                     print(f"❌ [Error] Column with '42' not found in CSV headers: {clean_headers}. Exiting.")
                     exit(1)
            else:
                print("❌ [Error] Empty CSV headers. Exiting.")
                exit(1)
        else:
            print(f"❌ [Error] CSV Download Failed: {csv_resp.status_code}. Exiting.")
            exit(1)

        print(f"   📊 Final Data: FTP {current_ftp}W, W' {w_prime}J")
        print(f"   📊 5m Max Power (42d): {five_min_power}W ({curve_source})")
        print(f"   📊 Condition: TSB {tsb:.1f} (Fitness {ctl:.1f} / Fatigue {atl:.1f})")

        # ----------------------------------------------------------------------
        # 4. Gemini 훈련 설계 (데이터 기반)
        # ----------------------------------------------------------------------
        print("3️⃣ Asking Gemini to design workout...")
        
        prompt = f"""
        Role: Expert Cycling Coach (Data-Driven).
        Task: Create a 1-hour structured cycling workout code for Intervals.icu.
        
        [ATHLETE DATA]
        - FTP (Base): {current_ftp} W
        - W' (Anaerobic Capacity): {w_prime} J
        - 5-min Max Power (Recent 42d Actual): {five_min_power} W
        - TSB (Form): {tsb:.1f}

        [COACHING LOGIC]
        Analyze TSB to decide intensity:
        1. TSB < -10 (Fatigued):
           - Focus: Active Recovery (Zone 1-2).
           - NO intervals. Pure endurance.
        
        2. -10 <= TSB <= 10 (Optimal):
           - Focus: Sweet Spot or Threshold.
           - Intensity: 88-100% of FTP ({current_ftp}W).
           - Build endurance with long intervals (10m+).
           
        3. TSB > 10 (Fresh):
           - Focus: VO2 Max or Anaerobic.
           - Interval Target: 90-95% of "5-min Max Power" ({int(five_min_power*0.9)}W - {int(five_min_power*0.95)}W).
           - Note: Do NOT use FTP for VO2Max targets. Use the provided 5-min max power ({five_min_power}W) as the ceiling.
           - Short, hard efforts (2-4 min) to drain W'.

        [STRICT OUTPUT FORMAT]
        - Output ONLY the workout lines.
        - Start every line with "-".
        - Format: "- [Duration] [Intensity] [Text]"
        - Example:
          - 10m 50% Warmup
          - 5m 92% SweetSpot
        - NO intro/outro text.
        - UNROLL LOOPS (Write each step explicitly).
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
        # 5. 라이브러리 생성
        # ----------------------------------------------------------------------
        print(f"4️⃣ Creating Library Workout (Folder ID: {TARGET_FOLDER_ID})...")
        workout_payload = {
            "name": f"AI Coach: TSB {tsb:.1f} / FTP {int(current_ftp)}",
            "description": clean_code,
            "type": "Ride",
            "sport": "Ride",
            "folder_id": TARGET_FOLDER_ID
        }
        
        create_resp = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/workouts", auth=auth, json=workout_payload)
        if create_resp.status_code != 200:
            print(f"❌ Library Error: {create_resp.text}")
            exit(1)
            
        workout_id = create_resp.json()['id']
        print(f"   ✅ ID Created: {workout_id}")

        # ----------------------------------------------------------------------
        # 6. 캘린더 등록
        # ----------------------------------------------------------------------
        print("5️⃣ Scheduling to Calendar...")
        event_payload = {
            "category": "WORKOUT",
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "name": f"AI Coach: TSB {tsb:.1f}",
            "type": "Ride",
            "workout_id": workout_id,
            "description": clean_code
        }
        
        final_res = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true", auth=auth, json=[event_payload])
        
        if final_res.status_code == 200:
            print(f"🎉 Success! Workout scheduled for {today_str} 19:00.")
        else:
            print(f"❌ Schedule Error: {final_res.text}")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_daily_coach()
