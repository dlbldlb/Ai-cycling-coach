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
        # 4. Gemini 3.0 Flash Preview 훈련 설계 (SDK 사용)
        # ----------------------------------------------------------------------
        print("3️⃣ Asking Gemini 3.0 Flash Preview to design workout...")
        
        prompt = f"""
        Role: Expert Cycling Coach. 전문적인 연구결과 기반의 워크아웃을 짜주는 코치
        Task: Create a 1-hour structured cycling workout code for Intervals.icu. 단, 너무 지루하지 않게 다채로운 스테이지로 구성할 것.
        
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

        [STRICT OUTPUT FORMAT - INTERVALS.ICU SYNTAX
