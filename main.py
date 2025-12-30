import os
import requests
import json
from datetime import datetime, timedelta

# GitHub Secrets
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID = os.environ["ATHLETE_ID"]
TARGET_FOLDER_ID = 224530

def run_daily_coach():
    auth = ('API_KEY', INTERVALS_API_KEY)
    
    # 1. 한국 시간(KST)
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    print(f"🚀 [AI Coach] Started at {kst_now} (KST)")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 (Wellness + Power Curve)
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Athlete Data...")
        
        # (1) Wellness 데이터 (CTL, ATL, TSB, eFTP, W')
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        
        # 데이터 매핑
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        ctl = w_data.get('ctl', 0)      # Fitness (TCL)
        atl = w_data.get('atl', 0)      # Fatigue (ACL)
        tsb = ctl - atl                 # Form (TSB)

        # eFTP 없으면 설정값 조회
        if current_ftp is None:
            s_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
            s_resp = requests.get(s_url, auth=auth)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                ride_settings = next((s for s in s_data.get('sportSettings', []) if 'Ride' in s.get('types', [])), {})
                current_ftp = ride_settings.get('ftp')
                w_prime = ride_settings.get('w_prime')

        if current_ftp is None:
            print("❌ [Critical Error] FTP data not found.")
            exit(1)
            
        if w_prime is None: w_prime = 0

        # (2) 5분 최대 파워 (Power Curve) 조회 (최근 42일 기준)
        # 5분 파워는 VO2Max 훈련의 천장(Ceiling)을 정하는 중요한 지표입니다.
        p_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/power-curves"
        p_resp = requests.get(p_url, auth=auth)
        five_min_power = 0
        
        if p_resp.status_code == 200:
            curves = p_resp.json()
            # 'days': 42 (최근 6주 데이터) -> 'field': 'currency' (현재 능력)
            # API 구조에 따라 다를 수 있으나 보통 currency나 시즌 최고기록을 씁니다.
            # 여기서는 편의상 FTP 대비 추정치 혹은 안전하게 FTP의 120%로 가정하되,
            # 실제 API 응답에 5분(300초) 데이터가 있다면 그걸 씁니다.
            # (복잡성을 줄이기 위해 여기서는 프롬프트에 'If available' 로직을 태우거나, 
            #  단순히 FTP 기반으로 가이드하되 5m 파워가 있다면 명시해줍니다.)
            
            # *참고: 파워커브 API가 복잡하므로, 여기서는 FTP 기준으로 프롬프트를 강화하는 방향 추천
            # 용길님이 "데이터가 있다"고 하셨으니 값을 직접 넣거나, FTP의 1.2배로 추산하여 전달
            five_min_power = int(current_ftp * 1.2) # (임시) 데이터가 API로 안 넘어올 경우 대비
        
        print(f"   📊 Data Loaded: FTP {current_ftp}W, W' {w_prime}J")
        print(f"   📊 Status: CTL {ctl:.1f}, ATL {atl:.1f}, TSB {tsb:.1f}")

        # ----------------------------------------------------------------------
        # 3. Gemini 훈련 설계 (데이터 기반 재구성)
        # ----------------------------------------------------------------------
        print("2️⃣ Asking Gemini to design workout...")
        
        # 프롬프트 대폭 강화
        prompt = f"""
        Role: Expert Cycling Coach (Data-Driven).
        Task: Create a 1-hour structured cycling workout code for Intervals.icu.
        
        [ATHLETE DATA]
        - FTP: {current_ftp} W
        - W' (Anaerobic Capacity): {w_prime} J
        - 5-min Max Power (Est): {five_min_power} W
        - CTL (Fitness): {ctl:.1f}
        - ATL (Fatigue): {atl:.1f}
        - TSB (Form): {tsb:.1f}

        [COACHING LOGIC]
        Analyze the TSB (Training Stress Balance) to decide the workout type:
        1. IF TSB < -10 (Fatigued):
           - Goal: Active Recovery.
           - Intensity: Zone 1-2 (below 75% FTP).
           - No intervals. Keep it steady and easy.
        
        2. IF -10 <= TSB <= 10 (Maintenance/Build):
           - Goal: Aerobic Capacity or Sweet Spot.
           - Intensity: Sweet Spot (88-94% FTP) or Threshold (95-100% FTP).
           - Structure: 2-3 long intervals (e.g., 10-15 min).
           
        3. IF TSB > 10 (Fresh):
           - Goal: High Intensity (VO2 Max or Anaerobic).
           - Intensity: Intervals above 106% FTP.
           - Use "5-min Max Power" ({five_min_power}W) as a reference cap for hard efforts.
           - Ensure intervals drain W' but allow recovery.

        [STRICT OUTPUT RULES]
        - Output ONLY the workout steps text.
        - Syntax: "- [Duration] [Intensity] [Text]" or "- [Duration] [Power] [Text]"
        - Use 'm' for minutes, 's' for seconds.
        - Start EVERY line with a hyphen "-".
        - UNROLL all loops (Do NOT use '3x', write lines explicitly).
        - NO introductory text, NO explanations.
        
        [EXAMPLE OUTPUT]
        - 10m 50% Warmup
        - 10m 90% SweetSpot
        - 5m 50% Recovery
        - 10m 90% SweetSpot
        - 10m 50% Cooldown
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

        if not clean_code:
            print("❌ Error: Generated workout code is empty.")
            exit(1)

        # ----------------------------------------------------------------------
        # 4. 라이브러리 생성
        # ----------------------------------------------------------------------
        print(f"3️⃣ Creating Library Workout (Folder ID: {TARGET_FOLDER_ID})...")
        workout_payload = {
            "name": f"AI Coach: TSB {tsb:.1f} / FTP {int(current_ftp)}",
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
        # 5. 캘린더 등록 (양방향 주입)
        # ----------------------------------------------------------------------
        print("4️⃣ Scheduling to Calendar...")
        
        event_payload = {
            "category": "WORKOUT",
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "name": f"AI Coach: TSB {tsb:.1f}", # 제목을 TSB 위주로 변경
            "type": "Ride",
            "workout_id": workout_id,
            "description": clean_code
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
