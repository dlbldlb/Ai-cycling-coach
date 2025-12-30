import os
import requests
import json
from datetime import datetime, timedelta

# ------------------------------------------------------------------------------
# [설정] GitHub Secrets 환경변수
# ------------------------------------------------------------------------------
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID = os.environ["ATHLETE_ID"]
TARGET_FOLDER_ID = 224530  # 용길님 Workouts 폴더

def run_daily_coach():
    auth = ('API_KEY', INTERVALS_API_KEY)
    
    # 1. 한국 시간(KST) 설정
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    print(f"🚀 [AI Coach] Started at {kst_now} (KST)")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 1: Wellness (FTP, W', TSB)
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Wellness Data...")
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        ctl = w_data.get('ctl', 0)
        atl = w_data.get('atl', 0)
        tsb = ctl - atl

        # eFTP가 없으면 Settings에서 가져오기
        if current_ftp is None:
            s_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
            s_resp = requests.get(s_url, auth=auth)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                ride_settings = next((s for s in s_data.get('sportSettings', []) if 'Ride' in s.get('types', [])), {})
                current_ftp = ride_settings.get('ftp')
                w_prime = ride_settings.get('w_prime')

        if current_ftp is None:
            print("❌ [Critical] FTP data not found.")
            exit(1)
            
        if w_prime is None: w_prime = 0

        # ----------------------------------------------------------------------
        # 3. 데이터 추출 2: Power Curve (스마트 탐색)
        # ----------------------------------------------------------------------
        print("2️⃣ Fetching Power Curve (Priority: 42d > Currency > Season > 1y)...")
        p_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/power-curves"
        p_resp = requests.get(p_url, auth=auth, params={'type': 'Ride'})
        
        five_min_power = int(current_ftp * 1.2) # 기본값 (안전빵)
        curve_source = "Estimated (FTP*1.2)"

        if p_resp.status_code == 200:
            p_data = p_resp.json()
            curve_list = p_data.get('list', [])
            
            # [우선순위 로직]
            # 1. 42d (최근 6주)
            target_curve = next((c for c in curve_list if c.get('id') == '42d'), None)
            
            # 2. Currency (현재 상태)
            if not target_curve:
                target_curve = next((c for c in curve_list if c.get('id') == 'currency'), None)
                
            # 3. Season (이번 시즌)
            if not target_curve:
                target_curve = next((c for c in curve_list if c.get('id') == 'season'), None)
                
            # 4. 1y (1년 - 최후의 보루, 현재 208W 확인됨)
            if not target_curve and len(curve_list) > 0:
                target_curve = curve_list[0] # 보통 리스트 첫번째가 가장 대표적인 커브

            if target_curve:
                c_id = target_curve.get('id')
                c_label = target_curve.get('label', c_id)
                secs_list = target_curve.get('secs', [])
                watts_list = target_curve.get('watts', [])
                
                if 300 in secs_list:
                    idx = secs_list.index(300)
                    five_min_power = watts_list[idx]
                    curve_source = f"{c_label} ({c_id})"
                else:
                     print(f"   ⚠️ 300s data not found in {c_id}. Using estimate.")

        print(f"   📊 Final Data: FTP {current_ftp}W, 5m Power {five_min_power}W ({curve_source})")
        print(f"   📊 Condition: TSB {tsb:.1f} (Fitness {ctl:.1f} / Fatigue {atl:.1f})")

        # ----------------------------------------------------------------------
        # 4. Gemini 훈련 설계 (데이터 기반 프롬프트)
        # ----------------------------------------------------------------------
        print("3️⃣ Asking Gemini to design workout...")
        
        prompt = f"""
        Role: Expert Cycling Coach (Data-Driven).
        Task: Create a 1-hour structured cycling workout code for Intervals.icu.
        
        [ATHLETE DATA]
        - FTP: {current_ftp} W
        - W' (Anaerobic Capacity): {w_prime} J
        - 5-min Max Power: {five_min_power} W
        - TSB (Form): {tsb:.1f}

        [COACHING LOGIC]
        Analyze TSB to decide intensity:
        1. TSB < -10 (Fatigued):
           - Focus: Active Recovery (Zone 1-2).
           - NO intervals. Pure endurance.
        
        2. -10 <= TSB <= 10 (Optimal):
           - Focus: Sweet Spot or Threshold.
           - Intensity: 88-100% FTP.
           - Build endurance with long intervals (10m+).
           
        3. TSB > 10 (Fresh):
           - Focus: VO2 Max or Anaerobic.
           - Interval Target: 90-95% of "5-min Max Power" ({int(five_min_power*0.9)}W - {int(five_min_power*0.95)}W).
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
        
        # 텍스트 정제
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
        # 5. 라이브러리 생성 (ID 발급)
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
        # 6. 캘린더 등록 (그래프 보장 - Dual Injection)
        # ----------------------------------------------------------------------
        print("5️⃣ Scheduling to Calendar...")
        event_payload = {
            "category": "WORKOUT",
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "name": f"AI Coach: TSB {tsb:.1f}",
            "type": "Ride",
            "workout_id": workout_id,
            "description": clean_code # [핵심] 텍스트 재주입으로 그래프 강제화
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
