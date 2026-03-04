#!/usr/bin/env python3
"""Simple queue worker that polls Azure Queue and runs batch processing.
Supports concurrent processing of multiple jobs using ThreadPoolExecutor."""
import os
import sys
import json
import time
import subprocess
import fcntl
import signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock, Thread
from azure.storage.queue import QueueClient
from dotenv import load_dotenv

# systemd watchdog support
def sd_notify(state: str):
    """Send notification to systemd via sd_notify protocol."""
    import socket
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if addr[0] == "@":
            addr = "\0" + addr[1:]
        sock.sendto(state.encode(), addr)
        sock.close()
    except Exception:
        pass

# Load .env from project root
project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")

# Add batch directory to path so we can import if needed
BATCH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "batch"))
REALTIME_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "realtime"))
sys.path.insert(0, BATCH_DIR)

# Maximum concurrent jobs
MAX_WORKERS = int(os.getenv("WORKER_MAX_CONCURRENT", "1"))

# Maximum retry attempts before treating message as poison and deleting it
MAX_DEQUEUE_COUNT = int(os.getenv("WORKER_MAX_RETRIES", "3"))

# Visibility timeout: 15 minutes (renewed every 5 min while job is active)
VISIBILITY_TIMEOUT = 15 * 60  # 900 seconds

# Visibility renewal interval: renew every 5 minutes to keep message invisible
VISIBILITY_RENEW_INTERVAL = 5 * 60  # 300 seconds

# Track active jobs: job_id -> {"future": Future, "msg_id": str, "pop_receipt": str}
active_jobs: dict[str, dict] = {}
active_jobs_lock = Lock()

# Separate executor for lightweight live_monitor jobs (not subject to MAX_WORKERS)
live_monitor_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="live-monitor")
live_monitor_jobs: dict[str, dict] = {}
live_monitor_lock = Lock()

# Graceful shutdown flag
shutdown_requested = False


def signal_handler(signum, frame):
    global shutdown_requested
    print(f"\n[worker] Received signal {signum}, shutting down gracefully...")
    print(f"[worker] Waiting for {get_active_count()} active jobs to complete before exit...")
    shutdown_requested = True


def get_queue_client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    queue_name = os.getenv("AZURE_QUEUE_NAME", "video-jobs")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING required")
    return QueueClient.from_connection_string(conn_str, queue_name)


def delete_message_safe(msg_id: str, pop_receipt: str):
    """Safely delete a message from the queue after job completion."""
    try:
        client = get_queue_client()
        client.delete_message(msg_id, pop_receipt)
        return True
    except Exception as e:
        print(f"[worker] Warning: Failed to delete message {msg_id}: {e}")
        return False


def renew_visibility(msg_id: str, pop_receipt: str, job_id: str):
    """Renew message visibility to prevent it from reappearing while processing.
    Returns the new pop_receipt or None on failure."""
    try:
        client = get_queue_client()
        result = client.update_message(
            msg_id,
            pop_receipt,
            visibility_timeout=VISIBILITY_TIMEOUT,
        )
        return result.pop_receipt
    except Exception as e:
        print(f"[worker] Warning: Failed to renew visibility for job {job_id}: {e}")
        return None


def visibility_renewal_loop():
    """Background thread that periodically renews visibility for active jobs."""
    while not shutdown_requested:
        time.sleep(VISIBILITY_RENEW_INTERVAL)
        with active_jobs_lock:
            for job_id, info in list(active_jobs.items()):
                if info["future"].done():
                    continue
                new_receipt = renew_visibility(
                    info["msg_id"], info["pop_receipt"], job_id
                )
                if new_receipt:
                    info["pop_receipt"] = new_receipt


def process_job(payload: dict, msg_id: str, pop_receipt: str):
    """Process a single job. Runs in a thread.
    Deletes the queue message only after successful completion.
    On failure, the message will reappear after visibility timeout."""
    job_type = payload.get("job_type", "video_analysis")
    job_id = payload.get("video_id", payload.get("clip_id", "unknown"))

    try:
        if job_type == "generate_clip":
            success = process_clip_job(payload)
        elif job_type == "live_capture":
            success = process_live_capture_job(payload)
        elif job_type == "live_monitor":
            success = process_live_monitor_job(payload)
        else:
            success = process_video_job(payload)

        if success:
            # Only delete message from queue after successful processing
            with active_jobs_lock:
                info = active_jobs.get(job_id, {})
                current_receipt = info.get("pop_receipt", pop_receipt)
            delete_message_safe(msg_id, current_receipt)
        else:
            print(f"[worker] Job {job_id} failed, message will reappear after visibility timeout for retry")

        return success
    except Exception as e:
        print(f"[worker] Error processing job {job_id}: {e}")
        print(f"[worker] Message will reappear after visibility timeout for retry")
        return False
    finally:
        with active_jobs_lock:
            active_jobs.pop(job_id, None)


def process_live_monitor_job(payload: dict):
    """Handle TikTok live real-time monitoring job.
    Runs the live_monitor.py script which connects to TikTok WebSocket,
    collects metrics, generates AI advice, and pushes to backend SSE."""
    video_id = payload.get("video_id")
    live_url = payload.get("live_url", "")
    username = payload.get("username", "")

    if not video_id or not username:
        print("[worker] Invalid live_monitor payload, skipping")
        return False

    print(f"[worker] Starting live monitor for @{username} (video_id={video_id})")
    cmd = [
        sys.executable,
        os.path.join(REALTIME_DIR, "live_monitor.py"),
        "--unique-id", username,
        "--video-id", video_id,
    ]

    result = subprocess.run(
        cmd,
        cwd=REALTIME_DIR,
        env={**os.environ, "PYTHONPATH": f"{REALTIME_DIR}:{BATCH_DIR}"},
    )

    if result.returncode == 0:
        print(f"[worker] Live monitor completed for @{username} (video_id={video_id})")
        return True
    else:
        print(f"[worker] Live monitor failed for @{username} with exit code {result.returncode}")
        return False


def process_live_capture_job(payload: dict):
    """Handle TikTok live stream capture job.
    Captures the stream, uploads to blob, then enqueues a video_analysis job.
    Also starts a live_monitor subprocess in parallel for real-time analysis."""
    video_id = payload.get("video_id")
    live_url = payload.get("live_url")
    email = payload.get("email", "")
    user_id = str(payload.get("user_id", ""))
    duration = payload.get("duration", 0)

    if not video_id or not live_url:
        print("[worker] Invalid live_capture payload, skipping")
        return False

    # Extract username from URL for live monitor
    import re
    match = re.search(r"@([^/]+)", live_url)
    username = match.group(1) if match else ""

    # Start live monitor as a background subprocess (non-blocking)
    # Uses WORKER_API_KEY env var for backend auth (no user JWT needed)
    monitor_proc = None
    if username:
        try:
            monitor_cmd = [
                sys.executable,
                os.path.join(REALTIME_DIR, "live_monitor.py"),
                "--unique-id", username,
                "--video-id", video_id,
            ]
            monitor_proc = subprocess.Popen(
                monitor_cmd,
                cwd=REALTIME_DIR,
                env={**os.environ, "PYTHONPATH": f"{REALTIME_DIR}:{BATCH_DIR}"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print(f"[worker] Live monitor started for @{username} (pid={monitor_proc.pid})")
        except Exception as e:
            print(f"[worker] Warning: Failed to start live monitor: {e}")

    print(f"[worker] Starting live capture for video_id={video_id}, url={live_url}")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "tiktok_stream_capture.py"),
        "--video-id", video_id,
        "--live-url", live_url,
        "--email", email,
        "--user-id", str(user_id),
    ]
    if duration > 0:
        cmd.extend(["--duration", str(duration)])

    result = subprocess.run(
        cmd,
        cwd=BATCH_DIR,
        env={**os.environ, "PYTHONPATH": BATCH_DIR},
    )

    # Stop live monitor when capture ends
    if monitor_proc and monitor_proc.poll() is None:
        print(f"[worker] Stopping live monitor (pid={monitor_proc.pid})")
        monitor_proc.terminate()
        try:
            monitor_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor_proc.kill()

    if result.returncode == 0:
        print(f"[worker] Live capture completed for {video_id}")
        return True
    elif result.returncode == 2:
        print(f"[worker] Live capture: user is not currently live (video_id={video_id})")
        return True  # Don't retry - user is offline
    else:
        print(f"[worker] Live capture failed for {video_id} with exit code {result.returncode}")
        return False


def process_clip_job(payload: dict):
    """Handle clip generation job."""
    clip_id = payload.get("clip_id")
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url")
    time_start = payload.get("time_start")
    time_end = payload.get("time_end")

    if not all([clip_id, video_id, blob_url, time_start is not None, time_end is not None]):
        print("[worker] Invalid clip payload, skipping")
        return False

    phase_index = payload.get("phase_index", -1)
    speed_factor = payload.get("speed_factor", 1.0)

    print(f"[worker] Starting clip generation for clip_id={clip_id} (speed={speed_factor}x, timeout={CLIP_PROCESS_TIMEOUT}s)")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "generate_clip.py"),
        "--clip-id", clip_id,
        "--video-id", video_id,
        "--blob-url", blob_url,
        "--time-start", str(time_start),
        "--time-end", str(time_end),
        "--phase-index", str(phase_index),
        "--speed-factor", str(speed_factor),
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=BATCH_DIR,
            env={**os.environ, "PYTHONPATH": BATCH_DIR},
            timeout=CLIP_PROCESS_TIMEOUT,
        )

        if result.returncode == 0:
            print(f"[worker] Clip generation completed for {clip_id}")
            return True
        else:
            print(f"[worker] Clip generation failed for {clip_id} with exit code {result.returncode}")
            return False
    except subprocess.TimeoutExpired:
        print(f"[worker] Clip generation TIMED OUT for {clip_id} after {CLIP_PROCESS_TIMEOUT}s")
        return False


# Timeout for video analysis subprocess (20 minutes hard limit)
VIDEO_PROCESS_TIMEOUT = int(os.getenv("WORKER_VIDEO_TIMEOUT", str(20 * 60)))

# Timeout for clip generation subprocess (10 minutes)
CLIP_PROCESS_TIMEOUT = int(os.getenv("WORKER_CLIP_TIMEOUT", str(10 * 60)))


def process_video_job(payload: dict):
    """Handle video analysis job."""
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url")

    if not video_id or not blob_url:
        print("[worker] Invalid payload, skipping")
        return False

    print(f"[worker] Starting batch for video_id={video_id}")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "process_video.py"),
        "--video-id", video_id,
        "--blob-url", blob_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=BATCH_DIR,
            env={**os.environ, "PYTHONPATH": BATCH_DIR},
            timeout=VIDEO_PROCESS_TIMEOUT,
        )

        if result.returncode == 0:
            print(f"[worker] Batch completed successfully for {video_id}")
            return True
        else:
            print(f"[worker] Batch failed for {video_id} with exit code {result.returncode}")
            return False
    except subprocess.TimeoutExpired:
        print(f"[worker] Batch TIMED OUT for {video_id} after {VIDEO_PROCESS_TIMEOUT}s")
        # Mark as ERROR so it doesn't stay stuck
        try:
            sys.path.insert(0, BATCH_DIR)
            from db_ops import init_db_sync, update_video_status_sync, close_db_sync
            from video_status import VideoStatus
            init_db_sync()
            update_video_status_sync(video_id, VideoStatus.ERROR)
            close_db_sync()
            print(f"[worker] Marked timed-out video {video_id} as ERROR")
        except Exception as db_err:
            print(f"[worker] Failed to mark timed-out video as ERROR: {db_err}")
        return False


def get_active_count():
    """Get the number of currently active jobs."""
    with active_jobs_lock:
        # Clean up completed futures
        completed = [k for k, v in active_jobs.items() if v["future"].done()]
        for k in completed:
            active_jobs.pop(k, None)
        return len(active_jobs)


def poll_and_process(executor: ThreadPoolExecutor):
    """Poll queue and submit jobs to the thread pool.
    live_monitor jobs bypass MAX_WORKERS and run on a separate executor."""
    active_count = get_active_count()
    heavy_slots_full = active_count >= MAX_WORKERS

    client = get_queue_client()

    # Always peek up to 5 messages (we may still accept live_monitor even when heavy slots full)
    messages = client.receive_messages(
        messages_per_page=5,
        visibility_timeout=VISIBILITY_TIMEOUT,
    )

    for msg in messages:
        try:
            payload = json.loads(msg.content)
            job_type = payload.get("job_type", "video_analysis")
            job_id = payload.get("video_id", payload.get("clip_id", "unknown"))

            # --- Poison message detection: delete after too many retries ---
            if hasattr(msg, 'dequeue_count') and msg.dequeue_count is not None:
                if msg.dequeue_count >= MAX_DEQUEUE_COUNT:
                    print(f"[worker] POISON MESSAGE detected: job={job_id}, type={job_type}, "
                          f"dequeue_count={msg.dequeue_count} >= {MAX_DEQUEUE_COUNT}. "
                          f"Deleting message and marking video as ERROR.")
                    delete_message_safe(msg.id, msg.pop_receipt)
                    # Mark video as ERROR in DB so it doesn't stay in 'uploaded' forever
                    if job_type in ("video_analysis", None) and job_id != "unknown":
                        try:
                            sys.path.insert(0, BATCH_DIR)
                            from db_ops import init_db_sync, update_video_status_sync, close_db_sync
                            from video_status import VideoStatus
                            init_db_sync()
                            update_video_status_sync(job_id, VideoStatus.ERROR)
                            close_db_sync()
                            print(f"[worker] Marked video {job_id} as ERROR")
                        except Exception as db_err:
                            print(f"[worker] Failed to mark video as ERROR: {db_err}")
                    continue

            # --- Orphan message detection: video_id not in DB → delete immediately ---
            if job_type in ("video_analysis", None) and job_id != "unknown":
                try:
                    sys.path.insert(0, BATCH_DIR)
                    from db_ops import init_db_sync, get_user_id_of_video_sync, close_db_sync
                    init_db_sync()
                    owner = get_user_id_of_video_sync(job_id)
                    close_db_sync()
                    if owner is None:
                        print(f"[worker] ORPHAN MESSAGE: video_id={job_id} not found in DB. "
                              f"Deleting message (dequeue_count={getattr(msg, 'dequeue_count', '?')}).")
                        delete_message_safe(msg.id, msg.pop_receipt)
                        continue
                except Exception as db_err:
                    print(f"[worker] DB check failed for {job_id}, proceeding anyway: {db_err}")

            # --- live_monitor: runs on separate lightweight executor ---
            if job_type == "live_monitor":
                with live_monitor_lock:
                    if job_id in live_monitor_jobs and not live_monitor_jobs[job_id]["future"].done():
                        print(f"[worker] Live monitor {job_id} already running, skipping")
                        continue
                print(f"[worker] Received live_monitor job: id={job_id} (bypasses MAX_WORKERS)")
                future = live_monitor_executor.submit(process_job, payload, msg.id, msg.pop_receipt)
                with live_monitor_lock:
                    live_monitor_jobs[job_id] = {
                        "future": future,
                        "msg_id": msg.id,
                        "pop_receipt": msg.pop_receipt,
                    }
                continue

            # --- Heavy jobs: subject to MAX_WORKERS ---
            if heavy_slots_full:
                # Put message back by not processing it (visibility will expire)
                continue

            # Check if this job is already being processed
            with active_jobs_lock:
                if job_id in active_jobs and not active_jobs[job_id]["future"].done():
                    print(f"[worker] Job {job_id} already in progress, skipping duplicate")
                    continue

            print(f"[worker] Received job: type={job_type}, id={job_id} (active: {get_active_count()}/{MAX_WORKERS})")

            # Submit job to thread pool
            future = executor.submit(process_job, payload, msg.id, msg.pop_receipt)
            with active_jobs_lock:
                active_jobs[job_id] = {
                    "future": future,
                    "msg_id": msg.id,
                    "pop_receipt": msg.pop_receipt,
                }
            heavy_slots_full = get_active_count() >= MAX_WORKERS

        except Exception as e:
            print(f"[worker] Error parsing message: {e}")
            # Don't delete on parse error; message will reappear after visibility timeout


def acquire_lock():
    """Acquire a file lock to prevent multiple worker instances."""
    lock_file = Path("/tmp/simple_worker.lock")
    fp = open(lock_file, "w")
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(os.getpid()))
        fp.flush()
        return fp
    except IOError:
        print("[worker] Another worker instance is already running. Exiting.")
        sys.exit(1)


# Disk cleanup interval: run every 30 minutes
DISK_CLEANUP_INTERVAL = 30 * 60  # 1800 seconds
_last_disk_cleanup = 0


def periodic_disk_cleanup():
    """Periodically check disk space and clean up old files.
    Delegates to the centralised disk_guard module so that ALL temp
    directories (uploadedvideo, output, splitvideo, artifacts, logs)
    are covered in one place."""
    global _last_disk_cleanup
    now = time.time()
    if now - _last_disk_cleanup < DISK_CLEANUP_INTERVAL:
        return
    _last_disk_cleanup = now

    try:
        # Ensure disk_guard runs with the correct cwd
        original_cwd = os.getcwd()
        os.chdir(BATCH_DIR)

        from disk_guard import periodic_disk_check

        # Collect currently active video IDs
        active_ids = set()
        with active_jobs_lock:
            active_ids = set(active_jobs.keys())

        periodic_disk_check(active_ids=active_ids)

        os.chdir(original_cwd)
    except Exception as e:
        print(f"[worker][disk] Cleanup error: {e}")


def main():
    # Acquire lock to prevent duplicate instances
    lock_fp = acquire_lock()

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print(f"[worker] Starting simple queue worker (max_concurrent={MAX_WORKERS})...")
    print(f"[worker] Queue: {os.getenv('AZURE_QUEUE_NAME', 'video-jobs')}")
    print(f"[worker] Visibility timeout: {VISIBILITY_TIMEOUT}s ({VISIBILITY_TIMEOUT // 60}min, renewed every {VISIBILITY_RENEW_INTERVAL // 60}min)")
    print(f"[worker] Video process timeout: {VIDEO_PROCESS_TIMEOUT}s ({VIDEO_PROCESS_TIMEOUT // 60}min)")
    print(f"[worker] Max retries (dequeue count): {MAX_DEQUEUE_COUNT}")
    print(f"[worker] Message deletion: after successful completion only (retry on failure)")

    # Start background visibility renewal thread
    renewal_thread = Thread(target=visibility_renewal_loop, daemon=True)
    renewal_thread.start()

    # Initial disk cleanup on startup
    periodic_disk_cleanup()

    # Notify systemd that we're ready
    sd_notify("READY=1")
    print("[worker] Notified systemd: READY")

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    try:
        while not shutdown_requested:
            try:
                sd_notify("WATCHDOG=1")  # Tell systemd we're alive
                periodic_disk_cleanup()
                poll_and_process(executor)
                time.sleep(5)  # Poll every 5 seconds
            except Exception as e:
                print(f"[worker] Unexpected error: {e}")
                time.sleep(10)
    finally:
        print(f"[worker] Waiting for {get_active_count()} active jobs to complete...")
        executor.shutdown(wait=True)
        lock_fp.close()
        print("[worker] Worker shut down.")


if __name__ == "__main__":
    main()
