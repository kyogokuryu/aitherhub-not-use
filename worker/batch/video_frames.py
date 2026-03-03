# video_frames.py
import os
import cv2
import numpy as np
import subprocess
import logging
from decouple import config

logger = logging.getLogger("video_frames")


def env(key, default=None):
    return os.getenv(key) or config(key, default=default)

FFMPEG_BIN = env("FFMPEG_PATH", "ffmpeg")

# v4: Sampling interval for phase detection scoring
# Instead of comparing every frame (3600 for 1h video),
# compare every Nth frame (720 for 5s interval at fps=1)
SCORE_SAMPLE_INTERVAL = int(env("SCORE_SAMPLE_INTERVAL", "3"))

# ======================================================
# STEP 0 – EXTRACT FRAMES
# ======================================================


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _detect_video_codec(video_path: str) -> str:
    """Detect the video codec using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip().lower()
    except Exception:
        return "unknown"


def _check_gpu_available() -> bool:
    """Check if NVIDIA GPU is available for hardware decoding."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except Exception:
        return False


# Map video codecs to NVDEC cuvid decoder names
_CUVID_DECODERS = {
    "h264": "h264_cuvid",
    "hevc": "hevc_cuvid",
    "h265": "hevc_cuvid",
    "vp9": "vp9_cuvid",
    "vp8": "vp8_cuvid",
    "av1": "av1_cuvid",
    "mpeg4": "mpeg4_cuvid",
    "mpeg2video": "mpeg2_cuvid",
    "mpeg1video": "mpeg1_cuvid",
}


def extract_frames(
    video_path: str,
    fps: int = 1,
    frames_root: str = "frames",
    on_progress=None,
) -> str:
    """
    STEP 0 – Extract frames from video

    v2: GPU-accelerated (NVDEC) with CPU fallback
    - Uses NVIDIA CUVID hardware decoder when GPU is available (5-10x faster)
    - Falls back to CPU decoding if GPU is unavailable or codec unsupported
    - Outputs scaled frames (max 1280px width) to reduce I/O
    - on_progress(percent): optional callback for real-time progress (0-100)
    """
    out_dir = os.path.join(frames_root, "frames")
    os.makedirs(out_dir, exist_ok=True)

    # Get expected total frames for progress tracking
    duration = _get_video_duration(video_path)
    expected_frames = int(duration * fps) if duration > 0 else 0

    # Detect codec and GPU availability
    codec = _detect_video_codec(video_path)
    has_gpu = _check_gpu_available()
    cuvid_decoder = _CUVID_DECODERS.get(codec)

    use_gpu = has_gpu and cuvid_decoder is not None

    if use_gpu:
        # GPU path: NVDEC hardware decode + GPU resize + CPU JPG encode
        cmd = [
            FFMPEG_BIN, "-y",
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-c:v", cuvid_decoder,
            "-i", video_path,
            "-vf", f"fps={fps},scale_cuda=1280:-1,hwdownload,format=nv12",
            "-q:v", "5",
            "-vsync", "0",
            os.path.join(out_dir, "frame_%08d.jpg"),
        ]
        logger.info("[FRAMES] Using GPU decode: %s (codec=%s)", cuvid_decoder, codec)
    else:
        # CPU path: software decode + scale + JPG
        cmd = [
            FFMPEG_BIN, "-y",
            "-threads", "0",
            "-i", video_path,
            "-vf", f"fps={fps},scale=1280:-1",
            "-q:v", "5",
            "-vsync", "0",
            os.path.join(out_dir, "frame_%08d.jpg"),
        ]
        logger.info("[FRAMES] Using CPU decode (gpu=%s, codec=%s, cuvid=%s)",
                    has_gpu, codec, cuvid_decoder)

    import threading, time as _time, shutil

    # --- Disk space pre-check ---
    disk = shutil.disk_usage(out_dir)
    free_gb = disk.free / (1024 ** 3)
    # Estimate required space: ~120KB per frame (JPG 1280px)
    estimated_gb = (expected_frames * 120 * 1024) / (1024 ** 3) if expected_frames > 0 else 5.0
    logger.info("[FRAMES] Disk free: %.1f GB, estimated need: %.1f GB", free_gb, estimated_gb)
    if free_gb < max(estimated_gb * 1.2, 3.0):
        raise RuntimeError(
            f"[FRAMES] Insufficient disk space: {free_gb:.1f} GB free, "
            f"need ~{estimated_gb:.1f} GB for {expected_frames} frames. "
            f"Clean up old files first."
        )

    # Run ffmpeg in background so we can monitor progress
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # --- Stall detection: if no new frames for STALL_TIMEOUT seconds, kill ffmpeg ---
    STALL_TIMEOUT = 120  # 2 minutes with no progress = stalled
    _stall_detected = {"value": False}

    if on_progress and expected_frames > 0:
        def _monitor():
            last_pct = -1
            last_count = 0
            stall_start = _time.time()
            while proc.poll() is None:
                try:
                    count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
                    pct = min(int(count / expected_frames * 100), 99)
                    if pct != last_pct:
                        on_progress(pct)
                        last_pct = pct
                    # Stall detection
                    if count > last_count:
                        last_count = count
                        stall_start = _time.time()
                    elif count == last_count and count > 0:
                        stall_sec = _time.time() - stall_start
                        if stall_sec > STALL_TIMEOUT:
                            # Check disk space
                            disk_now = shutil.disk_usage(out_dir)
                            free_now = disk_now.free / (1024 ** 3)
                            logger.error(
                                "[FRAMES] STALL DETECTED: no new frames for %ds "
                                "(count=%d, disk_free=%.1f GB). Killing ffmpeg.",
                                int(stall_sec), count, free_now,
                            )
                            _stall_detected["value"] = True
                            proc.kill()
                            return
                except Exception:
                    pass
                _time.sleep(2)
            if not _stall_detected["value"]:
                on_progress(100)

        t = threading.Thread(target=_monitor, daemon=True)
        t.start()

    proc.wait()

    if _stall_detected["value"] and use_gpu:
        # GPU stall detected - fall through to CPU fallback instead of raising
        logger.warning(
            "[FRAMES] GPU stall detected (no new frames for %ds). "
            "Extracted %d of %d expected frames. Falling back to CPU.",
            STALL_TIMEOUT,
            len([f for f in os.listdir(out_dir) if f.endswith('.jpg')]),
            expected_frames,
        )
    elif _stall_detected["value"] and not use_gpu:
        # CPU-only mode stalled - this is a real error
        raise RuntimeError(
            f"[FRAMES] ffmpeg stalled (no new frames for {STALL_TIMEOUT}s). "
            f"Likely disk full. Extracted {len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])} "
            f"of {expected_frames} expected frames."
        )

    # If GPU failed or stalled, fallback to CPU
    if (proc.returncode != 0 or _stall_detected["value"]) and use_gpu:
        stderr_out = proc.stderr.read().decode(errors='replace') if proc.stderr else ''
        logger.warning("[FRAMES] GPU decode failed (rc=%d), falling back to CPU. stderr: %s",
                       proc.returncode, stderr_out[:500])
        # Clean partial output
        for f in os.listdir(out_dir):
            if f.endswith('.jpg'):
                os.remove(os.path.join(out_dir, f))

        cmd_cpu = [
            FFMPEG_BIN, "-y",
            "-threads", "0",
            "-i", video_path,
            "-vf", f"fps={fps},scale=1280:-1",
            "-q:v", "5",
            "-vsync", "0",
            os.path.join(out_dir, "frame_%08d.jpg"),
        ]
        proc2 = subprocess.Popen(
            cmd_cpu,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _stall_detected2 = {"value": False}

        if on_progress and expected_frames > 0:
            def _monitor2():
                last_pct = -1
                last_count = 0
                stall_start = _time.time()
                while proc2.poll() is None:
                    try:
                        count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
                        pct = min(int(count / expected_frames * 100), 99)
                        if pct != last_pct:
                            on_progress(pct)
                            last_pct = pct
                        if count > last_count:
                            last_count = count
                            stall_start = _time.time()
                        elif count == last_count and count > 0:
                            stall_sec = _time.time() - stall_start
                            if stall_sec > STALL_TIMEOUT:
                                disk_now = shutil.disk_usage(out_dir)
                                free_now = disk_now.free / (1024 ** 3)
                                logger.error(
                                    "[FRAMES] CPU STALL DETECTED: no new frames for %ds "
                                    "(count=%d, disk_free=%.1f GB). Killing ffmpeg.",
                                    int(stall_sec), count, free_now,
                                )
                                _stall_detected2["value"] = True
                                proc2.kill()
                                return
                    except Exception:
                        pass
                    _time.sleep(2)
                if not _stall_detected2["value"]:
                    on_progress(100)

            t2 = threading.Thread(target=_monitor2, daemon=True)
            t2.start()

        proc2.wait()

        if _stall_detected2["value"]:
            raise RuntimeError(
                f"[FRAMES] ffmpeg CPU fallback stalled (no new frames for {STALL_TIMEOUT}s). "
                f"Likely disk full."
            )

    if on_progress:
        on_progress(100)

    frame_count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
    logger.info("[OK][STEP 0][FFMPEG] %d frames extracted → %s (gpu=%s)",
                frame_count, out_dir, use_gpu)
    return out_dir


# ======================================================
# STEP 1 – PHASE DETECTION
# ======================================================

# ---------- 1.1 SCORE FUNCTIONS ----------

def hist_diff_score(img1, img2):
    img1_hsv = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
    img2_hsv = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)

    hist1 = cv2.calcHist([img1_hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist2 = cv2.calcHist([img2_hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])

    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)

    return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)


def absdiff_score(img1, img2):
    img1_small = cv2.resize(img1, (256, 256))
    img2_small = cv2.resize(img2, (256, 256))

    diff = cv2.absdiff(img1_small, img2_small)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
    return np.sum(thresh > 0)


# ---------- UTILS ----------

def normalize(arr):
    arr = np.array(arr, dtype=np.float32)
    if np.max(arr) - np.min(arr) == 0:
        return np.zeros_like(arr)
    return (arr - np.min(arr)) / (np.max(arr) - np.min(arr))


def moving_average(arr, k=5):
    arr = np.array(arr, dtype=np.float32)
    if len(arr) < k:
        return arr
    return np.convolve(arr, np.ones(k) / k, mode="same")


def peak_detect(arr, th):
    peaks = []
    for i in range(1, len(arr) - 1):
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1] and arr[i] > th:
            peaks.append(i)
    return peaks


# ---------- 1.1 RAW SCORE (v4: sampled for speed) ----------

def compute_raw_scores(frame_dir, on_progress=None):
    """
    Compute histogram and absdiff scores between consecutive frames.

    v4: Samples every SCORE_SAMPLE_INTERVAL frames instead of every frame.
    For a 1-hour video (3600 frames) with interval=3:
      - Before: 3600 comparisons → 5-10 min
      - After:  1200 comparisons → 1-3 min
    Scores for skipped frames are interpolated.
    """
    files = sorted(os.listdir(frame_dir))
    total = len(files)
    interval = max(1, SCORE_SAMPLE_INTERVAL)

    # Build sampled indices
    sampled_indices = list(range(0, total, interval))
    if sampled_indices[-1] != total - 1:
        sampled_indices.append(total - 1)

    logger.info("[SCORES] Total frames: %d, Sample interval: %d, Sampled: %d",
                total, interval, len(sampled_indices))

    # Compute scores at sampled points
    sampled_hist = []
    sampled_absdiff = []
    sampled_positions = []  # frame indices where scores are computed

    prev = None
    prev_idx = None
    for progress_i, idx in enumerate(sampled_indices):
        img = cv2.imread(os.path.join(frame_dir, files[idx]))
        if img is None:
            continue

        if prev is not None:
            h = hist_diff_score(prev, img)
            a = absdiff_score(prev, img)
            sampled_hist.append(h)
            sampled_absdiff.append(a)
            sampled_positions.append(idx)

        prev = img
        prev_idx = idx

        # Report progress
        if on_progress and len(sampled_indices) > 0:
            pct = min(int((progress_i + 1) / len(sampled_indices) * 100), 99)
            if progress_i % max(1, len(sampled_indices) // 50) == 0:
                on_progress(pct)

    if on_progress:
        on_progress(100)

    # Interpolate scores to full frame count
    if interval == 1 or len(sampled_positions) < 2:
        return sampled_hist, sampled_absdiff

    # Create full-length score arrays via linear interpolation
    hist_scores = np.interp(
        range(1, total),  # target positions (score[i] = diff between frame i-1 and i)
        sampled_positions,
        sampled_hist,
    ).tolist()

    absdiff_scores = np.interp(
        range(1, total),
        sampled_positions,
        sampled_absdiff,
    ).tolist()

    return hist_scores, absdiff_scores


# ---------- 1.2 CANDIDATE DETECTION ----------

def detect_candidates(hist_scores, absdiff_scores):
    hist_norm = normalize(hist_scores)
    absdiff_norm = normalize(absdiff_scores)

    hist_inv = 1 - hist_norm
    mix = (hist_inv + absdiff_norm) / 2

    smooth = moving_average(mix, k=5)

    mean_val = np.mean(smooth)
    std_val = np.std(smooth)
    th = mean_val + std_val * 1.0

    peaks = peak_detect(smooth, th)
    return peaks


# ---------- 1.3 YOLO CONFIRM ----------

def yolo_compare(model, frame1, frame2):
    r1 = model(frame1)[0]
    r2 = model(frame2)[0]

    c1 = [model.names[int(b.cls)] for b in r1.boxes]
    c2 = [model.names[int(b.cls)] for b in r2.boxes]

    if set(c1) != set(c2):
        return True
    if len(c1) != len(c2):
        return True

    def total_area(r):
        area = 0
        for b in r.boxes.xyxy:
            x1, y1, x2, y2 = b
            area += (x2 - x1) * (y2 - y1)
        return area

    a1 = total_area(r1)
    a2 = total_area(r2)

    ratio = abs(a1 - a2) / (a1 + 1e-5)
    return ratio > 0.20


def confirm_boundaries(peaks, frame_dir, model):
    confirmed = []
    files = sorted(os.listdir(frame_dir))

    for p in peaks:
        if p <= 0 or p >= len(files):
            continue

        f0 = cv2.imread(os.path.join(frame_dir, files[p - 1]))
        f1 = cv2.imread(os.path.join(frame_dir, files[p]))

        if yolo_compare(model, f0, f1):
            confirmed.append(p)

    return confirmed


# ---------- 1.4 PHASE POST-PROCESS ----------

def merge_close_boundaries(indices, min_gap=3):
    if not indices:
        return []

    merged = [indices[0]]
    for x in indices[1:]:
        if x - merged[-1] >= min_gap:
            merged.append(x)
    return merged


def filter_min_phase(indices, total_frames, min_len=25):
    result = []
    extended = [0] + indices + [total_frames - 1]
    phase_len = np.diff(extended)

    for i in range(1, len(extended) - 1):
        if extended[i] < min_len:
            continue

        if phase_len[i] >= min_len:
            result.append(extended[i])

    return result



def apply_max_phase(indices, total_frames, max_len=150):
    result = []
    extended = [0] + indices + [total_frames - 1]

    for i in range(1, len(extended) - 1):
        start = extended[i - 1]
        end = extended[i]
        length = end - start

        if length > max_len:
            mid = (start + end) // 2
            result.append(mid)

        result.append(end)

    return sorted(list(set(result)))

def pick_representative_frames(model, phases, total_frames, frame_dir, max_samples_per_phase=5):
    """
    Pick representative frames for each phase.
    OPTIMIZED: Instead of scanning ALL frames in each phase,
    sample up to max_samples_per_phase evenly spaced frames.
    This reduces YOLO inference calls from thousands to ~5 per phase.
    """
    files = sorted(os.listdir(frame_dir))
    reps = []

    extended = [0] + phases + [total_frames - 1]

    for i in range(1, len(extended)):
        start = extended[i - 1]
        end = extended[i]
        phase_len = end - start

        if phase_len <= 0:
            reps.append(start)
            continue

        # Sample evenly spaced frames instead of scanning all
        if phase_len <= max_samples_per_phase:
            sample_indices = list(range(start, end))
        else:
            step = phase_len / max_samples_per_phase
            sample_indices = [start + int(step * j) for j in range(max_samples_per_phase)]

        best_frame = start
        best_score = 0

        for f in sample_indices:
            if f >= len(files):
                continue
            img_path = os.path.join(frame_dir, files[f])
            img = cv2.imread(img_path)
            if img is None:
                continue

            result = model(img)[0]

            score = 0
            for box in result.boxes:
                conf = float(box.conf)
                x1, y1, x2, y2 = box.xyxy[0]
                area = (x2 - x1) * (y2 - y1)
                score += conf * area

            if score > best_score:
                best_score = score
                best_frame = f

        reps.append(best_frame)

    return reps


# ---------- MAIN STEP 1 ENTRY ----------

def detect_phases(frame_dir: str, model, on_progress=None):
    files = sorted(os.listdir(frame_dir))
    total_frames = len(files)

    if total_frames == 0:
        raise ValueError(
            f"No frames found in {frame_dir}. "
            f"The video file may be empty or corrupted."
        )
    if total_frames < 2:
        # Need at least 2 frames to compute differences
        logger.warning(f"Only {total_frames} frame(s) in {frame_dir}, returning single phase")
        return [0], [0], total_frames

    # compute_raw_scores is the heavy part (~80% of detect_phases time)
    def _score_progress(pct):
        if on_progress:
            on_progress(min(int(pct * 0.8), 80))  # 0-80% for scoring

    hist_scores, absdiff_scores = compute_raw_scores(frame_dir, on_progress=_score_progress)

    if on_progress:
        on_progress(85)  # Candidate detection

    peaks = detect_candidates(hist_scores, absdiff_scores)
    confirmed = confirm_boundaries(peaks, frame_dir, model)

    if on_progress:
        on_progress(90)  # Boundary merging

    merged = merge_close_boundaries(confirmed, min_gap=3)
    filtered = filter_min_phase(merged, total_frames, min_len=25)
    filtered = apply_max_phase(filtered, total_frames, max_len=150)

    keyframes = filtered.copy()
    rep_frames = pick_representative_frames(model, keyframes, total_frames, frame_dir)

    if on_progress:
        on_progress(100)

    return keyframes, rep_frames, total_frames
