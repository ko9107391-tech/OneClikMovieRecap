# One Click Movie Recap AI V2

## V2 features
- Movie upload
- Runway Burmese voice upload
- AI scene analysis
- Voice/movie duration-aware scene plan
- 9:16 recap video
- Burmese SRT
- PWA installable web app

## Run
1. Install Python 3.10+
2. Install FFmpeg and ensure `ffmpeg` and `ffprobe` are on PATH.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and add `OPENAI_API_KEY`
5. `python app.py`
6. Open `http://127.0.0.1:7860`

For Android, open the web app in Chrome and use "Add to Home screen" / "Install app" when available.

This is a starter app, not a hosted public service. Copyright ownership/licensing remains separate; editing does not guarantee a platform copyright claim will be avoided.


## V3 improvements
- Runway/Burmese voice is transcribed first.
- Scene planning uses the actual narration transcript plus movie frames.
- Added Dockerfile for online deployment.
