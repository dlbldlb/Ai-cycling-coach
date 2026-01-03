import os
import requests
import csv
import io
import json
from datetime import datetime, timedelta
from google import genai

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
    print(f"🚀 [AI Coach] Started at {kst_now} (KST) using Gemini 3.0 Flash Preview")

    try:
        # ----------------------------------------------------------------------
        # 2. 데이터 추출 1: Wellness (HRV sdnn 우선 탐색)
        # ----------------------------------------------------------------------
        print("1️⃣ Fetching Wellness Data...")
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        # [Debug] 실제 들어오는 데이터 키값 확인 (로그 확인용)
        if w_data:
            print(f"   🔍 Available Data Keys: {list(w_data.keys())}")
            print(f"   🔍 Target Values -> sdnn: {w_data.get('sdnn')}, hrv: {w_data.get('hrv')}")
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        
        current_ftp = ride_info.get('eftp')
        w_prime = ride_info.get('wPrime')
        ctl = w_data.get('ctl', 0)     # Fitness
        atl = w_data.get('atl', 0)     # Fatigue
        tsb = ctl - atl                # Form
        
        # [NEW] HRV 데이터 추출 로직 (sdnn 우선)
        # 1순위: 'sdnn' (Intervals.icu API 표준 키값)
        hrv_val = w_data.get('sdnn')
        hrv_type = "SDNN"

        # 2순위: 'sdnn'이 없으면 'hrv' (rMSSD) 사용
        if hrv_val is None:
            hrv_val = w_data.get('hrv')
            if hrv_val:
                hrv_type = "rMSSD" # SDNN이 없어서 대체됨
            else:
                hrv_type = "None"
            
        # HRV 표시 문자열 생성
        if hrv_val:
            hrv_display = f"{hrv_val} ms ({hrv_type})"
        else:
            hrv_display = "N/A"

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

        print(f"   📊 Status: FTP {current_ftp}W, CTL {ctl:.1f}, TSB {tsb:.1f}, HRV {hrv_display}")

        # ----------------------------------------------------------------------
        # 4. Gemini 3.0 Flash Preview 훈련 설계
        # ----------------------------------------------------------------------
        print("3️⃣ Asking Gemini 3.0 Flash Preview to design workout...")
        
        prompt = f"""
        Role: Expert Cycling Coach. 전문적인 연구결과 기반의 워크아웃을 짜주는 코치
        Task: Create a structured cycling workout code for Intervals.icu. 단, 너무 지루하지 않게 다채로운 스테이지로 구성할 것. 총 운동시간은 1시간 전후로, 운동 강도에 따라 유동적으로 조절해도 무방.
        
        [ATHLETE DATA]
        - FTP: {current_ftp} W
        - W': {w_prime} J
        - CTL: {ctl:.1f}
        - ATL: {atl:.1f}
        - TSB: {tsb:.1f}
        - Recent 5m Max: {five_min_power} W
        - HRV Status: {hrv_display}

        [INTELLIGENT COACHING LOGIC]
        1. DETRAINING CHECK:
           ** IF CTL < 30 OR Recent 5m Max Power == 0 **:
           - Diagnosis: DETRAINED.
           - Action: STRICTLY Zone 2 (55-65% FTP). NO High Intensity.
        
        2. PHYSIOLOGICAL STRESS CHECK (HRV):
           ** Analyze the provided HRV value ({hrv_display}). **
           - IF HRV is significantly lower than usual (indicating high stress/poor recovery):
             -> Diagnosis: HIGH PHYSIOLOGICAL STRESS.
             -> Action: Priority is RECOVERY. Limit intensity to Zone 2 or low Sweet Spot. Avoid VO2 Max/Anaerobic.
           - Note: SDNN and rMSSD have different scales. Use general physiological principles to judge.
           - If HRV is "N/A", ignore this check and rely on TSB.
           
        3. NORMAL TRAINING (If CTL >= 30 and HRV is stable):
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
           
           - UNROLL LOOPS: Do NOT use "3x" or loop headers. Write every single step explicitly.
             (e.g., Instead of "2x -> 5m z2, 5m z4", write:
              "- 5m z2"
              "- 5m z4"
              "- 5m z2"
              "- 5m z4")
              
           - 만약 free ride 세션을 넣고 싶은 경우, 강도 대신 freeride 라고 써 준다. (e.g. "- 5m freeride").
           - (중요!) 새로운 Header(Warmup 등)가 나올 때는, 그 위에 반드시 빈 줄을 추가해 줄 것.
        
        3. The VERY LAST LINE must be the status summary:
           "Status: FTP {current_ftp}W | W' {w_prime}J | CTL {ctl:.1f} | ATL {atl:.1f} | TSB {tsb:.1f} | HRV {hrv_display}"
           
        4. No intro/outro text.
        """
        
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # [모델] gemini-3-flash-preview
        response = client.models.generate_content(
            model='gemini-3-flash-preview', 
            contents=prompt
        )
        
        if not response.text:
            print(f"❌ Gemini Error: No response text generated.")
            exit(1)

        raw_text = response.text
        
        # ----------------------------------------------------------------------
        # 텍스트 정제
        # ----------------------------------------------------------------------
        lines = raw_text.split('\n')
        workout_lines = []
        status_line = ""
        valid_headers = ["Warmup", "Cooldown"]

        for line in lines:
            line = line.strip()
            if not line: continue
            
            # 1. 상태 라인 분리
            if line.startswith("Status:"):
                status_line = line
                continue
            
            # 2. 헤더 처리 (앞에 빈 줄 추가)
            is_valid_header = False
            for h in valid_headers:
                if line.lower().startswith(h.lower()):
                    if workout_lines: 
                        workout_lines.append("") 
                    workout_lines.append(line)
                    is_valid_header = True
                    break
            if is_valid_header: continue
            
            # 3. Main Set / 반복문 헤더 삭제
            if "main set" in line.lower(): continue
            if line[0].isdigit() and line.lower().endswith('x'): continue

            # 4. 일반 스텝
            if line[0].isdigit():
                line = "- " + line
            
            if line.startswith('-'):
                workout_lines.append(line)
        
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
        print(f"🎉 Success! Workout scheduled using Gemini 3.0 Flash Preview.")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_daily_coach()
