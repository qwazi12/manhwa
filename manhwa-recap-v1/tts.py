import os
import ssl
import json
import urllib.request
import urllib.error
import subprocess
import base64
import time
from pathlib import Path

def synthesize_text(text: str, output_path: str, api_key: str) -> bool:
    """
    Synthesizes text to speech using Google Cloud Text-to-Speech REST API.
    Uses Chirp 3: HD Charon male voice (en-US-Chirp3-HD-Charon).
    """
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    payload = {
        "input": {
            "text": text
        },
        "voice": {
            "languageCode": "en-US",
            "name": "en-US-Chirp3-HD-Charon"
        },
        "audioConfig": {
            "audioEncoding": "MP3"
        }
    }
    
    # Create unverified context to bypass SSL certificate issues
    context = ssl._create_unverified_context()
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, context=context) as response:
            result = json.loads(response.read().decode("utf-8"))
            if "audioContent" in result:
                audio_bytes = base64.b64decode(result["audioContent"])
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)
                return True
            else:
                print(f"Error: No audioContent in response: {result}")
                return False
    except urllib.error.HTTPError as e:
        print(f"\n[TTS ERROR] HTTP {e.code}: {e.read().decode('utf-8')}")
        return False
    except Exception as e:
        print(f"\n[TTS ERROR] Connection failed: {e}")
        return False


def generate_voice_track(beats, output_path: str, api_key: str, has_timestamps: bool) -> bool:
    """
    Synthesize all beats using Google Cloud TTS and assemble the final voice track.
    If has_timestamps is True, mixes each clip at its exact start time using FFmpeg adelay.
    If False, concatenates clips sequentially with a 0.5s pause in between.
    """
    build_dir = Path("build/tts")
    build_dir.mkdir(parents=True, exist_ok=True)
    
    inputs = []
    filter_complex_parts = []
    amix_inputs = []
    
    print(f"\n[TTS] Synthesizing {len(beats)} beats using Google Cloud TTS...")
    
    for i, beat in enumerate(beats):
        idx = beat.get("index", i)
        text = beat.get("text", "")
        start_sec = beat.get("start", 0.0)
        
        # Skip empty text beats
        if not text.strip():
            continue
            
        clip_path = build_dir / f"beat_{idx:03d}.mp3"
        
        # Caching logic
        if not clip_path.exists():
            print(f"  [{i+1}/{len(beats)}] Synthesizing beat {idx} ({start_sec:.3f}s): {text[:45]}...")
            success = synthesize_text(text, str(clip_path), api_key)
            if not success:
                print(f"  [Warning] Synthesis failed for beat {idx}. Creating silent fallback.")
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", 
                    "-t", "0.5", str(clip_path)
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Throttle requests slightly
            time.sleep(0.15)
            
        inputs.append(str(clip_path))
        
        if has_timestamps:
            # Delay in ms for adelay
            delay_ms = int(start_sec * 1000)
            in_idx = len(inputs) - 1
            filter_complex_parts.append(f"[{in_idx}:a]adelay={delay_ms}|{delay_ms}[a{in_idx}]")
            amix_inputs.append(f"[a{in_idx}]")
            
    if not inputs:
        print("[TTS ERROR] No text beats found to synthesize.")
        return False
        
    if has_timestamps:
        print(f"\n[TTS] Mixing {len(inputs)} audio clips at their exact timestamp start times...")
        # Build filter complex for amix
        filter_complex_str = ";".join(filter_complex_parts)
        filter_complex_str += ";" + "".join(amix_inputs) + f"amix=inputs={len(inputs)}:normalize=0[out]"
        
        cmd = ["ffmpeg", "-y"]
        for inp in inputs:
            cmd.extend(["-i", inp])
            
        cmd.extend([
            "-filter_complex", filter_complex_str,
            "-map", "[out]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            output_path
        ])
    else:
        print(f"\n[TTS] Concatenating {len(inputs)} audio clips sequentially...")
        # Concatenate sequentially using FFmpeg concat filter
        # We also want a 0.5s silent gap between clips for natural pacing
        gap_path = build_dir / "gap.mp3"
        if not gap_path.exists():
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "0.5", "-c:a", "libmp3lame", "-b:a", "192k", str(gap_path)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        # Interleave input clips with gaps
        cmd = ["ffmpeg", "-y"]
        concat_inputs = []
        for inp in inputs:
            cmd.extend(["-i", inp, "-i", str(gap_path)])
            in_offset = len(concat_inputs) * 2
            concat_inputs.append(f"[{in_offset}:a][{in_offset+1}:a]")
            
        filter_complex_str = "".join(concat_inputs) + f"concat=n={len(concat_inputs)*2}:v=0:a=1[out]"
        cmd.extend([
            "-filter_complex", filter_complex_str,
            "-map", "[out]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            output_path
        ])
        
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        print("[TTS ERROR] FFmpeg audio assembly failed:")
        print(res.stderr.decode("utf-8"))
        return False
        
    print(f"[TTS] Voice track generated successfully at {output_path}")
    return True
