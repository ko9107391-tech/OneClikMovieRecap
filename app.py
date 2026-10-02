 
import os
import uuid
import json
import subprocess
from pathlib import Path
from flask import Flask, request, render_template, jsonify, send_from_directory

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"

for folder in (UPLOADS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 800 * 1024 * 1024


def run(cmd):
    return subprocess.run(
        cmd, check=True, capture_output=True, text=True
    )


def duration(path):
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ])
    return float(result.stdout.strip())


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/generate")
def generate():
    try:
        video = request.files.get("video")
        voice = request.files.get("voice")

        if not video or not video.filename:
            return jsonify(error="Movie video ရွေးပါ။"), 400

        job = uuid.uuid4().hex[:10]
        src = UPLOADS / f"{job}_movie.mp4"
        out = OUTPUTS / f"{job}_recap.mp4"

        video.save(src)
        movie_duration = duration(src)

        if movie_duration <= 0:
            return jsonify(error="Video ဖိုင်ကို ဖတ်မရပါ။"), 400

        vf = (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1"
        )

        cmd = ["ffmpeg", "-y", "-i", str(src)]

        if voice and voice.filename:
            audio = UPLOADS / f"{job}_voice"
            voice.save(audio)
            cmd += [
                "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0",
                "-vf", vf, "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-shortest",
                "-movflags", "+faststart", str(out)
            ]
        else:
            cmd += [
                "-map", "0:v:0", "-map", "0:a?",
                "-vf", vf, "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-movflags", "+faststart",
                str(out)
            ]

        run(cmd)

        srt = OUTPUTS / f"{job}_subtitles.srt"
        srt.write_text(
            "",
            encoding="utf-8"
        )

        plan = OUTPUTS / f"{job}_plan.json"
        plan.write_text(json.dumps({
            "mode": "free",
            "ai_scene_analysis": False,
            "movie_duration_seconds": movie_duration,
            "output_format": "1080x1920",
            "note": "AI scene selection and automatic subtitles are not enabled."
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        return jsonify(
            video=f"/download/{out.name}",
            srt=f"/download/{srt.name}",
            plan=f"/download/{plan.name}"
        )

    except subprocess.CalledProcessError as e:
        return jsonify(error="Video ပြုလုပ်ရာတွင် FFmpeg အမှားဖြစ်နေပါတယ်။ ဖိုင်ကိုစစ်ပြီး ထပ်စမ်းပါ။"), 500
    except Exception as e:
        app.logger.exception("Generate failed")
        return jsonify(error="လုပ်ဆောင်မှု မအောင်မြင်ပါ။ ဖိုင်အရွယ်အစားနှင့် ပုံစံကို စစ်ပါ။"), 500


@app.get("/download/<path:name>")
def download(name):
    return send_from_directory(OUTPUTS, name, as_attachment=True)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "7860"))
    )
