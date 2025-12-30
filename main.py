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
    
    # 한국 시간(KST) 계산
    kst_now = datetime.now() + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    yesterday_str = (kst_now - timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"🕒 Korea Time(KST): {kst_now}")

    # 1. 어제 미수행 훈련 정리
    try:
        url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"
        resp = requests.get(url, auth=auth, params={"oldest": yesterday_str, "newest": yesterday_str})
        for e in resp.json():
            if (e.get('category') == 'WORKOUT' and "AI" in e.get('name', "") and e.get('activity_id') is None):
                requests.delete(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/{e['id']}", auth=auth)
                print(f"🗑️ Deleted missed workout: {e['name']}")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")

    try:
        # 2. 데이터 추출
        w_url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
        w_resp = requests.get(w_url, auth=auth, params={"oldest": today_str})
        w_data = w_resp.json()[-1] if w_resp.json() else {}
        
        ride_info = next((i for i in w_data.get('sportInfo', []) if i.get('type') == 'Ride'), {})
        current_ftp = ride_info.get('eftp') or 175
        w_prime = ride_info.get('wPrime') or 14000
        tsb = w_data.get('ctl', 0) - w_data.get('atl', 0)
        
        print(f"📊 Data: eFTP {current_ftp}, W' {w_prime}, TSB {tsb}")

        # 3. Gemini 2.5 Flash 훈련 설계
        prompt = f"""
        Athlete Data: eFTP {current_ftp}W, W' {w_prime}J, TSB {tsb:.1f}
        Task: Create a 1-hour cycling workout code.
        Rules:
        - Output ONLY the workout code lines. No text, no explanation.
        - Do NOT use loops (like 3x). Unroll all steps.
        - Start every line with a hyphen (-).
        """
        
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]})
        workout_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        
        clean_code = "\n".join([l.strip() for l in workout_text.split('\n') if l.strip().startswith('-')])

        # 4. Intervals.icu 파싱 및 등록
        # 먼저 코드를 파싱해서 구조화된 데이터를 받습니다.
        parse_resp = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/workouts/parse", 
                                   auth=auth, json={"description": clean_code})
        parsed_data = parse_resp.json()
        
        # [핵심 수정] workout_doc 대신 'workout' 키에 파싱된 데이터를 통째로 넣습니다.
        event = {
            "start_date_local": kst_now.replace(hour=19, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S"),
            "type": "Ride", 
            "category": "WORKOUT",
            "name": f"AI Coach: eFTP {int(current_ftp)} / TSB {tsb:.1f}",
            "description": clean_code,  # 텍스트 설명 (사람용)
            "workout": parsed_data      # 구조화된 데이터 (그래프용) <-- 여기가 수정됨
        }
        
        final_res = requests.post(f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/bulk?upsert=true", auth=auth, json=[event])
        
        if final_res.status_code == 200:
            print(f"✅ Workout created successfully for {today_str} (KST)!")
        else:
            print(f"❌ Failed to create workout: {final_res.text}")

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        exit(1)

if __name__ == "__main__":
    run_coach()
