import os, uuid, json, subprocess, base64
from pathlib import Path
from flask import Flask, request, render_template, send_from_directory, jsonify

BASE=Path(__file__).resolve().parent
UPLOADS=BASE/"uploads"; OUTPUTS=BASE/"outputs"; WORK=BASE/"work"
for p in (UPLOADS,OUTPUTS,WORK): p.mkdir(exist_ok=True)
app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=800*1024*1024

def run(cmd):
    return subprocess.run(cmd,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

def dur(p):
    return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(p)]))

def ff_audio(src,out):
    run(["ffmpeg","-y","-i",str(src),"-vn","-ac","1","-ar","16000",str(out)])

def frames(src,folder,step=6):
    folder.mkdir(exist_ok=True)
    run(["ffmpeg","-y","-i",str(src),"-vf",f"fps=1/{step},scale=480:-1","-q:v","5",str(folder/"f_%04d.jpg")])
    return sorted(folder.glob("*.jpg"))

def img64(p):
    return "data:image/jpeg;base64,"+base64.b64encode(p.read_bytes()).decode()

def srt(segments):
    def t(x):
        ms=int(round((x-int(x))*1000)); z=int(x); h,z=divmod(z,3600); m,z=divmod(z,60)
        return f"{h:02}:{m:02}:{z:02},{ms:03}"
    return "\n".join(f"{i}\n{t(x['start'])} --> {t(x['end'])}\n{x['text']}\n"
                     for i,x in enumerate(segments,1))

def normalize(obj,total):
    out=[]
    for x in obj.get("segments",[]):
        try:
            a=max(0,float(x["start"])); b=min(total,float(x["end"]))
            txt=str(x["text"]).strip()
            if b>a and txt: out.append({"start":a,"end":b,"text":txt})
        except: pass
    return out

def make_plan(client, fs, transcript, total, voice_seconds=None):
    prompt=f"""You are a professional Burmese movie recap editor.
Use only facts supported by the transcript and visible frames; do not invent scenes.
Create concise Burmese narration segments in chronological order.
Each segment must map to a visually meaningful interval.
Return ONLY JSON: {{"title":"...","segments":[{{"start":0,"end":8,"text":"..."}}]}}
Movie duration={total:.2f}s.
Narration audio duration={voice_seconds if voice_seconds else 'unknown'}s.
Transcript:
{transcript[:10000]}"""
    content=[{"type":"input_text","text":prompt}]
    for f in fs[:30]:
        content.append({"type":"input_image","image_url":img64(f),"detail":"low"})
    r=client.responses.create(model=os.getenv("OPENAI_TEXT_MODEL","gpt-5.6-luna"),
                               input=[{"role":"user","content":content}])
    txt=r.output_text.strip()
    if txt.startswith("```"):
        txt=txt.replace("```json","").replace("```","").strip()
    return normalize(json.loads(txt),total)

def concat_clips(src,segments,wd,out):
    clips=[]
    for i,s in enumerate(segments):
        c=wd/f"c{i:03}.mp4"
        vf="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        run(["ffmpeg","-y","-ss",str(s["start"]),"-i",str(src),"-t",str(max(.5,s["end"]-s["start"])),
             "-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","24","-pix_fmt","yuv420p",str(c)])
        clips.append(c)
    txt=wd/"list.txt"
    txt.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips),encoding="utf8")
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(txt),"-c","copy",str(out)])

@app.get("/")
def home(): return render_template("index.html")

@app.post("/generate")
def generate():
    try:
        from openai import OpenAI
        key=os.getenv("OPENAI_API_KEY")
        if not key: return jsonify(error="OPENAI_API_KEY မထည့်ရသေးပါ။"),400
        video=request.files.get("video"); voice=request.files.get("voice")
        if not video: return jsonify(error="Movie video ရွေးပါ။"),400
        job=uuid.uuid4().hex[:10]; wd=WORK/job; wd.mkdir()
        src=UPLOADS/f"{job}_movie_{video.filename}"; video.save(src)
        client=OpenAI(api_key=key)
        total=dur(src)
        wav=wd/"movie.wav"; ff_audio(src,wav)
        tr=client.audio.transcriptions.create(model=os.getenv("OPENAI_TRANSCRIBE_MODEL","gpt-4o-mini-transcribe"),file=open(wav,"rb"))
        transcript=getattr(tr,"text","")
        fs=frames(src,wd/"frames")
        voice_seconds=None
        if voice:
            vp=UPLOADS/f"{job}_voice_{voice.filename}"; voice.save(vp)
            voice_seconds=dur(vp)
            # Transcribe the actual Runway/Burmese narration so scene planning follows what is spoken.
            voice_wav=wd/"voice.wav"; ff_audio(vp,voice_wav)
            vtr=client.audio.transcriptions.create(
                model=os.getenv("OPENAI_TRANSCRIBE_MODEL","gpt-4o-mini-transcribe"),
                file=open(voice_wav,"rb")
            )
            voice_text=getattr(vtr,"text","")
            # Generate a visual plan using BOTH movie evidence and the exact supplied narration.
            plan_prompt=f"""Create a professional Burmese movie-recap scene plan.
The supplied narration is the authority for what is being said. Choose movie intervals whose visible content
best matches each narration beat. Do not invent visual events. Return JSON only:
{{"segments":[{{"start":0,"end":5,"text":"exact narration beat"}}]}}
Movie duration={total:.2f}s. Narration duration={voice_seconds:.2f}s.
Narration transcript:
{voice_text[:14000]}
Movie/dialogue transcript:
{transcript[:7000]}"""
            content=[{"type":"input_text","text":plan_prompt}]
            for f in fs[:30]:
                content.append({"type":"input_image","image_url":img64(f),"detail":"low"})
            rr=client.responses.create(model=os.getenv("OPENAI_TEXT_MODEL","gpt-5.6-luna"),
                                       input=[{"role":"user","content":content}])
            raw=rr.output_text.strip().replace("```json","").replace("```","").strip()
            segments=normalize(json.loads(raw),total)
            narration=wd/"narration.mp3"
            run(["ffmpeg","-y","-i",str(vp),"-vn","-c:a","aac","-b:a","192k",str(narration)])
        else:
            segments=make_plan(client,fs,transcript,total)
            narration=wd/"narration.mp3"
            parts=[]
            for i,s in enumerate(segments):
                a=wd/f"v{i:03}.mp3"
                with client.audio.speech.with_streaming_response.create(
                    model=os.getenv("OPENAI_TTS_MODEL","gpt-4o-mini-tts"),
                    voice=os.getenv("OPENAI_TTS_VOICE","cedar"),
                    input=s["text"],
                    instructions="Natural Burmese movie recap narration, clear and energetic.",
                    response_format="mp3") as r:
                    r.stream_to_file(a)
                parts.append(a)
            lst=wd/"aud.txt"; lst.write_text("".join(f"file '{x.as_posix()}'\n" for x in parts),encoding="utf8")
            run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),"-c","copy",str(narration)])
        video_only=wd/"video.mp4"; concat_clips(src,segments,wd,video_only)
        out=OUTPUTS/f"recap_{job}.mp4"
        run(["ffmpeg","-y","-i",str(video_only),"-i",str(narration),"-map","0:v:0","-map","1:a:0",
             "-c:v","copy","-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(out)])
        sp=OUTPUTS/f"recap_{job}.srt"; sp.write_text(srt(segments),encoding="utf8")
        jp=OUTPUTS/f"recap_{job}.json"; jp.write_text(json.dumps({"segments":segments},ensure_ascii=False,indent=2),encoding="utf8")
        return jsonify(ok=True,video=f"/download/{out.name}",srt=f"/download/{sp.name}",plan=f"/download/{jp.name}")
    except Exception as e:
        return jsonify(error=str(e)),500

@app.get("/download/<path:name>")
def dl(name): return send_from_directory(OUTPUTS,name,as_attachment=True)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","7860")))

# Inline frontend template
INDEX_HTML = '<!doctype html><html lang="my"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<meta name="theme-color" content="#111318"><link rel="manifest" href="/static/manifest.json"><title>One Click AI</title>\n<style>\n*{box-sizing:border-box}body{margin:0;background:#0d0f13;color:#fff;font-family:system-ui,sans-serif}.wrap{max-width:720px;margin:auto;padding:20px}\n.card{background:#171a21;border:1px solid #292e38;border-radius:24px;padding:22px;box-shadow:0 15px 45px #0007}\nh1{margin:0 0 5px;font-size:28px}.sub{color:#aeb5c1;margin-bottom:20px}.box{padding:18px;margin:12px 0;border:1px solid #303642;border-radius:16px}\nlabel{display:block;font-weight:700;margin-bottom:8px}input{width:100%}button{width:100%;padding:16px;border:0;border-radius:14px;font-weight:800;font-size:16px;background:#fff;color:#111;margin-top:10px}\n#status{white-space:pre-wrap;margin-top:16px}.links a{display:block;color:#fff;padding:10px 0}\n.small{font-size:13px;color:#9ba3b1}\n</style></head><body><div class="wrap"><div class="card">\n<h1>🎬 One Click AI</h1><div class="sub">Movie Recap • Burmese • One Click</div>\n<div class="box"><label>① Movie Video</label><input id="video" type="file" accept="video/*"></div>\n<div class="box"><label>② Burmese Voice (Runway)</label><input id="voice" type="file" accept="audio/*"><div class="small">Runway ကထုတ်ထားတဲ့ narration ကိုထည့်နိုင်ပါတယ်။ မထည့်ရင် AI TTS သုံးပါမယ်။</div></div>\n<button onclick="go()">🚀 ONE CLICK GENERATE</button><div id="status"></div><div class="links" id="links"></div>\n</div></div>\n<script>\nif("serviceWorker" in navigator) navigator.serviceWorker.register("/static/sw.js");\nasync function go(){\n const v=document.getElementById("video").files[0], a=document.getElementById("voice").files[0], st=document.getElementById("status");\n if(!v){st.textContent="Movie video ရွေးပါ။";return}\n st.textContent="⏳ Scene analysis + narration sync လုပ်နေပါတယ်...";\n const fd=new FormData();fd.append("video",v);if(a)fd.append("voice",a);\n try{const r=await fetch("/generate",{method:"POST",body:fd}),j=await r.json();if(!r.ok)throw Error(j.error);\n st.textContent="✅ ပြီးပါပြီ";document.getElementById("links").innerHTML=\n `<a href="${j.video}">⬇️ Final Recap MP4</a><a href="${j.srt}">⬇️ Burmese SRT</a><a href="${j.plan}">⬇️ Scene Plan</a>`;\n }catch(e){st.textContent="❌ "+e.message}\n}\n</script></body></html>'
