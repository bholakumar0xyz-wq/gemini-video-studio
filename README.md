# 🎬 Gemini Video Studio

Script likho → **Gemini API** se har scene ki **image** + **Hindi voiceover** → automatic **editing** → video ready.
Har scene ki image uski voice line se 100% match hoti hai (scene-by-scene pipeline).

## Kaise kaam karta hai

1. **Script** paste karo (Hindi — Devanagari ya Roman, dono chalega)
2. Gemini script ko **scenes** me todta hai — har scene ke liye:
   - English **image prompt** (scene ke hisaab se)
   - Exact **voiceover line**
3. Har scene ki **image** banti hai (Nano Banana image model)
4. Har scene ki **voiceover** banti hai (Gemini TTS — purush/mahila voice)
5. **ffmpeg** har scene ko jodta hai: slow zoom motion + Hindi captions + loudness normalize
6. **Download** karo — 1080x1920 (Shorts/Reels) ya 1920x1080 (YouTube)

## Step 1: Gemini API Key (free, mobile se)

1. Phone ke browser me kholo: **aistudio.google.com/apikey**
2. Google account se login karo
3. **"Create API key"** dabao → key copy karo
4. Website par **Step 1** me paste karke **Save Key** dabao

> Free tier me roz kaafi videos ban jayengi. Bahut zyada banane par Google thoda charge kar sakta hai — AI Studio ke billing page par limit set kar sakte ho.

## Step 2: Website ko free hosting par chalao (Render)

Website ko internet par chalane ke liye (taaki phone se kabhi bhi use ho):

1. **GitHub** account banao (mobile browser se: github.com)
2. Naya repository banao → is folder ki saari files upload karo (web se "uploading an existing file")
3. **render.com** par free account banao → **New +** → **Web Service** → GitHub repo connect karo
4. Render khud `Dockerfile` pehchan lega → **Deploy** dabao
5. 5-10 minute me website live — link milega jaise `https://gemini-video-studio.onrender.com`
6. Us link ko phone ke browser me kholo → API key save karo → video banao!

> Render free plan me 15 minute bina use ke server so jata hai — pehla khulne me 30-60 second lag sakta hai, ye normal hai.

**API key hosting par:** Render dashboard → apni service → **Environment** → `GEMINI_API_KEY` add karo (zyada safe), ya website ke Step 1 me seedha save karo.

## Apne computer par chalana ho to

```bash
pip install -r requirements.txt
python app.py
# browser me kholo: http://localhost:5000
```

## Settings

| Option | Matlab |
|---|---|
| Voice | 👨 Purush / 👩 Mahila |
| Quality | ⚡ Fast (tez, sasta) / 💎 Pro (best image+voice) |
| Format | 📱 9:16 vertical / 🖥️ 16:9 horizontal |
| Image Style | Cinematic / Realistic photo / Cartoon 3D / Painting |
| Ken Burns | Slow zoom motion on/off |
| Captions | Hindi captions video par on/off |

## Demo mode

Bina API key ke **"Demo chalao"** dabao — poori editing pipeline test ho jayegi (sample images + tone audio ke saath). Isse pata chal jayega ki website sahi chal rahi hai.

## Files

- `app.py` — web server
- `pipeline.py` — Gemini + ffmpeg pipeline
- `templates/index.html` — mobile UI
- `Dockerfile` / `render.yaml` — free hosting deploy
- `static/fonts/Mukta-Bold.ttf` — Hindi+English caption font (bundled)
