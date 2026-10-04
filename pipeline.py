# -*- coding: utf-8 -*-
"""
Gemini Video Studio - core pipeline
Script -> scenes (Gemini text) -> images (Gemini image gen) ->
voiceover (Gemini TTS) -> video edit (ffmpeg, scene-by-scene match)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave

# ---- Model IDs (latest verified Oct 2026, user-changeable in UI) ----
TEXT_MODELS = ["gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
IMAGE_MODELS = {
    "fast": "gemini-3.1-flash-image-preview",   # Nano Banana 2 - tez/sasta
    "pro": "gemini-3-pro-image-preview",        # Nano Banana Pro - best quality
}
TTS_MODELS = {
    "fast": "gemini-3.8-flash-lite-tts",
    "pro": "gemini-3.8-flash-tts",
}
DEFAULT_VOICES = {
    "male": "Kore",
    "female": "Aoede",
}

FORMATS = {
    "vertical": {"w": 1080, "h": 1920, "aspect": "9:16"},
    "horizontal": {"w": 1920, "h": 1080, "aspect": "16:9"},
}

STYLES = {
    "cinematic": "cinematic photorealistic film still, dramatic lighting, high detail",
    "cartoon": "vibrant 3D cartoon animation style, Pixar-like, colorful, kid-friendly",
    "realistic": "ultra photorealistic photograph, natural lighting, National Geographic style",
    "painting": "beautiful digital painting, rich colors, artistic illustration",
}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ffprobe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path])
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def find_caption_font(text=""):
    """Bundled Mukta-Bold (Devanagari + Latin dono), phir system fonts."""
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "static", "fonts", "Mukta-Bold.ttf")
    cands = [bundled]
    if re.search(r"[\u0900-\u097F]", text or ""):
        cands += [
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        ]
    else:
        cands += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    for p in cands:
        if os.path.exists(p):
            return p
    r = run(["fc-match", "sans-serif:weight=bold", "--format=%{file}\n"])
    p = r.stdout.strip().splitlines()
    return p[0] if p and os.path.exists(p[0]) else None


# ---------------- Gemini helpers ----------------

def get_client(api_key):
    from google import genai
    return genai.Client(api_key=api_key)


def split_scenes(api_key, script, progress=None):
    """Script ko scene-wise todo: har scene = voiceover line + English image prompt."""
    system = (
        "You split a Hindi voiceover script into scenes for an AI video. "
        "Return ONLY a JSON array, no markdown fences. Each item: "
        '{"voice_text": "exact Hindi line as in script (1-2 sentences)", '
        '"image_prompt": "detailed English visual prompt describing the scene, '
        'cinematic, no text, no watermark, no humans unless narrated"}. '
        "Keep voice_text EXACTLY from the script (no rewording). "
        "Make 6-14 scenes for a typical script. Cover the whole script in order."
    )
    last_err = None
    try:
        from google.genai import types
        client = get_client(api_key)
        for model in TEXT_MODELS:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=script,
                    config=types.GenerateContentConfig(system_instruction=system,
                                                       temperature=0.4),
                )
                txt = (resp.text or "").strip()
                txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S)
                scenes = json.loads(txt)
                if isinstance(scenes, list) and scenes:
                    clean = []
                    for s in scenes:
                        vt = str(s.get("voice_text", "")).strip()
                        ip = str(s.get("image_prompt", "")).strip()
                        if vt and ip:
                            clean.append({"voice_text": vt, "image_prompt": ip})
                    if clean:
                        return clean
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
    except Exception as e:  # noqa: BLE001
        last_err = e
    # Fallback: script ko khud lines me todo
    if progress:
        progress("AI scene-split fail hua, manual split use kar raha hoon")
    parts = [p.strip() for p in re.split(r"\n+|।\s*", script) if p.strip()]
    scenes = []
    buf = ""
    for p in parts:
        buf = (buf + " " + p).strip()
        if len(buf) > 60:
            scenes.append(buf)
            buf = ""
    if buf:
        scenes.append(buf)
    out = []
    for vt in scenes[:14]:
        out.append({"voice_text": vt,
                    "image_prompt": f"Illustration of: {vt[:120]}"})
    if not out:
        raise RuntimeError(f"Scene split fail: {last_err}")
    return out


def generate_image(client, prompt, aspect, model_id, retries=3):
    """Gemini image generation -> PNG bytes."""
    from google.genai import types
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=aspect),
    )
    last = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=model_id, contents=prompt, config=cfg)
            for part in getattr(resp, "parts", []) or []:
                data = getattr(part, "inline_data", None)
                if data and getattr(data, "data", None):
                    return data.data
            cands = getattr(resp, "candidates", []) or []
            for c in cands:
                for part in getattr(c.content, "parts", []) or []:
                    data = getattr(part, "inline_data", None)
                    if data and getattr(data, "data", None):
                        return data.data
            last = RuntimeError("Image data nahi mila response me")
        except Exception as e:  # noqa: BLE001
            last = e
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(8 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Image gen fail: {last}")


def generate_tts(client, text, voice, model_id, wav_path, retries=3):
    """Gemini TTS -> 24kHz mono WAV."""
    from google.genai import types
    cfg = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )
    last = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=model_id, contents=text, config=cfg)
            pcm = None
            for part in getattr(resp, "parts", []) or []:
                data = getattr(part, "inline_data", None)
                if data and getattr(data, "data", None):
                    pcm = data.data
                    break
            if pcm is None:
                for c in getattr(resp, "candidates", []) or []:
                    for part in getattr(c.content, "parts", []) or []:
                        data = getattr(part, "inline_data", None)
                        if data and getattr(data, "data", None):
                            pcm = data.data
                            break
            if pcm:
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(pcm)
                return wav_path
            last = RuntimeError("Audio data nahi mila")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"TTS fail: {last}")


# ---------------- FREE engines (lifetime free, koi billing nahi) ----------------

FREE_EDGE_VOICES = {"male": "hi-IN-MadhurNeural", "female": "hi-IN-SwaraNeural"}
FREE_IMG_SIZE = {"vertical": (768, 1360), "horizontal": (1360, 768)}


def friendly_ai_error(e):
    s = str(e)
    if "429" in s or "RESOURCE_EXHAUSTED" in s or "quota" in s.lower():
        return ("Google AI ki free limit khatam ho gayi hai ya is model par "
                "free quota nahi hai. Thodi der ruk kar dobara try karo.")
    if "API key" in s or "API_KEY" in s or "401" in s or "403" in s:
        return "API key me problem hai. Settings me key dobara save karo."
    return s[:300]


def generate_image_free(prompt_en, w, h, retries=5):
    """Pollinations.ai — free, bina API key ke. Thodi kam quality, lifetime free.
    Free tier par kabhi-kabhi rate-limit (402) lagta hai — lambe wait ke saath
    retry karta hai taaki video rukhe nahi."""
    import urllib.request
    import urllib.parse
    import random
    backoffs = [5, 15, 40, 90, 180]
    last = None
    for model in ("turbo", None):  # turbo free; default fallback
        for attempt in range(retries):
            try:
                seed = random.randint(1, 999999)
                q = urllib.parse.quote((prompt_en or "")[:600])
                url = (f"https://image.pollinations.ai/prompt/{q}"
                       f"?width={w}&height={h}&nologo=true&seed={seed}")
                if model:
                    url += f"&model={model}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "")
                if ctype.startswith("image/") and len(data) > 15000:
                    return data
                last = RuntimeError(f"bad image (type={ctype}, {len(data)} bytes)")
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
    raise RuntimeError("Free image service bahut busy hai. 10-15 minute ruk kar "
                       "dobara try karo. "
                       f"({str(last)[:120]})")


def generate_tts_free(text, voice, out_mp3, speed=100):
    """edge-tts (Microsoft, free Hindi voices) primary, gTTS backup. Dono free."""
    # 1) edge-tts
    try:
        rate = f"{int(speed) - 100:+d}%"
        r = subprocess.run(
            [sys.executable, "-m", "edge_tts", "--voice", voice,
             "--rate", rate, "--text", text, "--write-media", out_mp3],
            capture_output=True, text=True, timeout=150)
        if r.returncode == 0 and os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 2000:
            return out_mp3
    except Exception:  # noqa: BLE001
        pass
    # 2) gTTS backup
    try:
        from gtts import gTTS
        gTTS(text, lang="hi").save(out_mp3)
        if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 2000:
            return out_mp3
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("Free voice service busy hai, thodi der me dobara try karo. "
                           f"({str(e)[:120]})")
    raise RuntimeError("Free voice service se audio nahi bana, dobara try karo.")


# ---------------- ffmpeg edit ----------------

def make_scene_clip(image_path, audio_path, clip_path, w, h,
                     kenburns=True, caption_text=None, font=None):
    # Single-pass: caption isi encode me jalao (doosra decode+encode pass
    # memory double kar deta tha). ultrafast + threads 2 = ~150MB peak,
    # taaki 512MB free server par OOM na ho.
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if kenburns:
        vf += (",zoompan=z='min(zoom+0.0012,1.12)':d=1:"
               f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=30")
    cap_file = None
    if caption_text and font:
        import textwrap
        wrapped = "\n".join(textwrap.wrap(caption_text, width=32))
        cap_file = clip_path + ".caption.txt"
        with open(cap_file, "w", encoding="utf-8") as f:
            f.write(wrapped)
        fs = int(w * 0.055)
        vf += (f",drawtext=fontfile='{font}':textfile='{cap_file}':"
               f"fontsize={fs}:fontcolor=white:borderw=3:bordercolor=black@0.8:"
               f"line_spacing=8:"
               f"x=(w-text_w)/2:y=h-text_h-{int(h*0.08)}")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-i", audio_path,
           "-vf", vf, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-shortest", clip_path]
    r = run(cmd)
    if cap_file and os.path.exists(cap_file):
        os.remove(cap_file)
    if r.returncode != 0 or not os.path.exists(clip_path):
        raise RuntimeError(f"Scene clip fail: {r.stderr[-500:]}")
    return clip_path


def concat_and_finish(clip_paths, final_path, fps=30):
    lst = final_path + ".list.txt"
    with open(lst, "w") as f:
        for c in clip_paths:
            f.write(f"file '{os.path.abspath(c)}'\n")
    tmp = final_path + ".joined.mp4"
    r = run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c", "copy", tmp])
    os.remove(lst)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError(f"Concat fail: {r.stderr[-500:]}")
    # Loudness normalize LAST (ffmpeg gotcha: loudnorm must come after adelay/pad).
    # Video ko dobara encode NAHI karte (-c:v copy) — sirf audio normalize,
    # taaki memory low rahe (poora 1080x1920 re-encode OOM kar deta tha).
    r = run(["ffmpeg", "-y", "-i", tmp,
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
             "-c:v", "copy",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
             final_path])
    os.remove(tmp)
    if r.returncode != 0 or not os.path.exists(final_path):
        raise RuntimeError(f"Final encode fail: {r.stderr[-500:]}")
    # Verify: audio stream MUST exist
    r = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", final_path])
    if not r.stdout.strip():
        raise RuntimeError("Final video me AUDIO TRACK NAHI HAI!")
    return final_path


# ---------------- full job ----------------

def demo_assets(job_dir, scenes, fmt):
    """Bina API key ke demo: placeholder images + tone audio."""
    assets = []
    colors = ["0x1a2a6c", "0xb21f1f", "0x1a6c2a", "0x6c1a5e", "0x8a6d1a",
              "0x1a6c6c", "0x4a1a6c", "0x6c3a1a"]
    for i, sc in enumerate(scenes):
        img = os.path.join(job_dir, f"scene_{i:02d}.png")
        wav = os.path.join(job_dir, f"scene_{i:02d}.wav")
        col = colors[i % len(colors)]
        run(["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"color=c={col}:s={fmt['w']}x{fmt['h']}:d=3",
             "-frames:v", "1", img])
        run(["ffmpeg", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=2.5",
             "-ar", "24000", "-ac", "1", wav])
        assets.append((img, wav))
    return assets


def run_job(job_dir, cfg, progress):
    """
    cfg: {script, api_key|None, demo:bool, voice, quality, format,
          style, kenburns, captions, speed}
    """
    os.makedirs(job_dir, exist_ok=True)
    fmt = FORMATS[cfg.get("format", "vertical")]
    style_txt = STYLES.get(cfg.get("style", "cinematic"), STYLES["cinematic"])
    demo = bool(cfg.get("demo"))

    progress(5, "Script ko scenes me tod raha hoon...")
    if demo:
        parts = [p.strip() for p in re.split(r"\n+|।\s*", cfg["script"]) if p.strip()]
        scenes, buf = [], ""
        for p in parts:
            buf = (buf + " " + p).strip()
            if len(buf) > 50:
                scenes.append({"voice_text": buf, "image_prompt": buf})
                buf = ""
        if buf:
            scenes.append({"voice_text": buf, "image_prompt": buf})
        scenes = scenes[:8] or [{"voice_text": "Demo scene", "image_prompt": "demo"}]
    else:
        # NOTE: client sirf paid/Gemini path me chahiye; free path me nahi.
        client = None
        try:
            scenes = split_scenes(cfg.get("api_key"), cfg["script"],
                                  progress=lambda t: progress(8, t))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(friendly_ai_error(e))

    n = len(scenes)
    assets = []
    if demo:
        progress(15, "Demo assets bana raha hoon...")
        assets = demo_assets(job_dir, scenes, fmt)
    else:
        use_free = (cfg.get("quality", "free") == "free")
        if use_free:
            # Lifetime free: images Pollinations se, voice edge-tts/gTTS se.
            # Scene-split Gemini text se (free tier par chalta hai).
            fw, fh = FREE_IMG_SIZE[cfg.get("format", "vertical")]
            edge_voice = FREE_EDGE_VOICES.get(cfg.get("voice", "male"),
                                              "hi-IN-MadhurNeural")
        else:
            client = get_client(cfg["api_key"])
            img_model = IMAGE_MODELS[cfg.get("quality", "fast")]
            tts_model = TTS_MODELS[cfg.get("quality", "fast")]
            voice = DEFAULT_VOICES.get(cfg.get("voice", "male"), "Kore")
        for i, sc in enumerate(scenes):
            base = 10 + int(60 * i / n)
            progress(base, f"Image bana raha hoon... ({i+1}/{n})")
            iprompt = (f"{sc['image_prompt']}, {style_txt}, "
                       f"vertical composition, no text, no watermark, no logo")
            img_path = os.path.join(job_dir, f"scene_{i:02d}.png")
            if use_free:
                try:
                    img_bytes = generate_image_free(iprompt, fw, fh)
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(friendly_ai_error(e))
            else:
                try:
                    img_bytes = generate_image(client, iprompt, fmt["aspect"], img_model)
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(friendly_ai_error(e))
            with open(img_path, "wb") as f:
                f.write(img_bytes)

            progress(base + 2, f"Voiceover bana raha hoon... ({i+1}/{n})")
            if use_free:
                audio_path = os.path.join(job_dir, f"scene_{i:02d}.mp3")
                generate_tts_free(sc["voice_text"], edge_voice, audio_path,
                                  speed=cfg.get("speed", 100))
            else:
                audio_path = os.path.join(job_dir, f"scene_{i:02d}.wav")
                # TTS speed: Gemini 3.8 line-by-line direction support karta hai
                speed_dir = ""
                if cfg.get("speed", 100) != 100:
                    speed_dir = ("[speak faster]" if cfg["speed"] > 100 else "[speak slower]") + " "
                try:
                    generate_tts(client, speed_dir + sc["voice_text"], voice,
                                 tts_model, audio_path)
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(friendly_ai_error(e))
            dur = ffprobe_duration(audio_path)
            if dur < 0.8:
                raise RuntimeError(f"Scene {i+1} ka audio khaali hai, dobara try karo")
            assets.append((img_path, audio_path))
            # Free image service par rate-limit na lage, isliye halka gap
            time.sleep(12 if use_free else 1)

    progress(75, "Scenes ko video me jod raha hoon (editing)...")
    clips = []
    for i, (img, wav) in enumerate(assets):
        clip = os.path.join(job_dir, f"clip_{i:02d}.mp4")
        cap = scenes[i]["voice_text"] if cfg.get("captions") else None
        font = find_caption_font(cap) if cap else None
        make_scene_clip(img, wav, clip, fmt["w"], fmt["h"],
                        kenburns=bool(cfg.get("kenburns", True)),
                        caption_text=cap, font=font)
        clips.append(clip)
        progress(75 + int(20 * (i + 1) / len(assets)), f"Editing... ({i+1}/{len(assets)})")

    progress(97, "Final touches (loudness + check)...")
    final = os.path.join(job_dir, "final_video.mp4")
    concat_and_finish(clips, final)
    # cleanup intermediates (images/wavs/clips rakho debug ke liye? nahi - space bachao)
    for c in clips:
        try:
            os.remove(c)
        except OSError:
            pass
    progress(100, "Ho gaya! Video ready hai.")
    return final
