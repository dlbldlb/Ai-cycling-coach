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
        # 2. 데이터 추출 1: Wellness
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Wellness Data...")
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        ctl = w_data.get('ctl', 0)     # Fitness
        atl = w_data.get('atl', 0)     # Fatigue
        tsb = ctl - atl                # Form

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
        
        if five_min_power is None:
            print("   ⚠️ 42일간 기록이 없습니다. (초기화 상태 추정)")
            five_min_power = 0

        print(f"   📊 Status: FTP {current_ftp}W, CTL {ctl:.1f}, TSB {tsb:.1f}")

        # ----------------------------------------------------------------------
        # 4. Gemini 훈련 설계 (Ramp 문법 + Main Set 헤더 삭제)
        # ----------------------------------------------------------------------
        print("3️⃣ Asking Gemini to design workout...")
        
        prompt = f"""
        Role: Expert Cycling Coach.
        Task: Create a 1-hour structured cycling workout code for Intervals.icu.
        
        [ATHLETE DATA]
        - FTP: {current_ftp} W
        - W': {w_prime} J
        - CTL: {ctl:.1f}
        - ATL: {atl:.1f}
        - TSB: {tsb:.1f}
        - Recent 5m Max: {five_min_power} W

        [INTELLIGENT COACHING LOGIC]
        1. DETRAINING CHECK:
           ** IF CTL < 30 OR Recent 5m Max Power == 0 **:
           - Diagnosis: DETRAINED.
           - Action: STRICTLY Zone 2 (55-65% FTP). NO High Intensity.
           
        2. NORMAL TRAINING (CTL >= 30):
           - TSB < -10: Recovery (Zone 1).
           - -10 <= TSB <= 10: Sweet Spot.
           - TSB > 10: VO2 Max (90-95% of 5m Max {five_min_power}W).

        [STRICT OUTPUT FORMAT - INTERVALS.ICU SYNTAX]
        1. STRUCTURE:
           Warmup
           - [step]
           
           [Just list the main workout steps here. Do NOT use "Main Set" header]
           
           Cooldown
           - [step]

        2. SYNTAX RULES:
           - Warmup/Cooldown: MUST use 'ramp' keyword for slopes. (e.g., "- 10m ramp 40-60%")
           - 만약 파워존 단위로 만들고 싶을 경우, '%' 대신 'z1', 'z4'와 같이 'z'와 숫자를 써 준다.(e.g. "- 10m30s ramp z1-z2")
           - Intervals: Start with "-". (e.g., "- 5m 65%")
           - 반복하고 싶은 경우, "3x", "4x" 와 같이 반복할 횟수를 header로서 써 준다.
               (e.g. 
                    "2x
                     - 5m 40%
                     - 10m z2
                     - 5m z4-z5").
           - 만약 free ride 세션을 넣고 싶은 경우, 강도 대신 freeride 라고 써 준다. (e.g. "- 5m freeride").
           - warmup세션, main 세션, cooldown세션은 구분을 위해 엔터를 2번 쳐 준다.
        
        3. The VERY LAST LINE must be the status summary:
           "Status: FTP {current_ftp}W | W' {w_prime}J | CTL {ctl:.1f} | ATL {atl:.1f} | TSB {tsb:.1f}"
           
        4. No intro/outro text.

        [작성 예시 (문법 참고만 할 것)] 
            "
            Warmup
            - 10m ramp z1-z2

            3x
            - 5m z2
            - 5m z3
            - 3m z4
            - 2m Freeride

            Cooldown
            - 5m ramp z2-z1

            Status: FTP 168w | W' 13500J | CTL 14 | ATL 3 | TSB 11
            "
            
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        
        if res.status_code != 200:
            print(f"❌ Gemini Error: {res.text}")
            exit(1)

        raw_text = res.json()['candidates'][0]['content']['parts'][0]['text']
        
        # ----------------------------------------------------------------------
        # [수정됨] 텍스트 정제: Warmup, Cooldown만 허용 (Main Set 제거)
        # ----------------------------------------------------------------------
        lines = raw_text.split('\n')
        workout_lines = []
        status_line = ""
        
        # 허용할 헤더 (Main Set은 일부러 뺌)
        valid_headers = ["Warmup", "Cooldown"]

        for line in lines:
            line = line.strip()
            if not line: continue
            
            # 1. 상태 표시줄 찾기
            if line.startswith("Status:"):
                status_line = line
                continue
            
            # 2. 헤더 라인인지 확인
            is_header_line = False
            for h in valid_headers:
                if line.lower().startswith(h.lower()):
                    workout_lines.append(line)
                    is_header_line = True
                    break
            
            if is_header_line: continue
            
            # "Main Set"이라고 쓴 줄은 무시 (Gemini가 실수로 써도 삭제)
            if "main set" in line.lower():
                continue

            # 3. 워크아웃 스텝 라인 (숫자나 대시로 시작)
            if line[0].isdigit():
                line = "- " + line
            
            if line.startswith('-'):
                workout_lines.append(line)
        
        # 재조립
        clean_code = "\n".join(workout_lines)
        if status_line:
            clean_code += f"\n\n{status_line}"
        
        print(f"   📝 Generated Code:\n{'-'*20}\n{clean_code}\n{'-'*20}")
        if not clean_code: exit(1)

        # ----------------------------------------------------------------------
        # 5. 라이브러리 및 캘린더 등록
        # ----------------------------------------------------------------------
        print(f"4️⃣ Uploading to Intervals.icu...")
        
        if ctl < 30 or five_min_power == 0:
            workout_name = f"AI Coach: Detrained (CTL {ctl:.1f})"
        else:
            workout_name = f"AI Coach: TSB {tsb:.1f}"

        workout_payload = {
            "name": workout_name,
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
            "name": workout_name,
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
