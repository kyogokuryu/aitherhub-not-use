import os
import time
import random
import base64
import json
import asyncio
from functools import partial
from openai import AzureOpenAI, RateLimitError, APIError, APITimeoutError
from decouple import config

# v4: Keep 20 for STEP 4 (fewer total calls, only rep_frames)
# But improve retry logic for rate limits
MAX_CONCURRENCY = 20

# =========================
# ENV & CLIENT
# =========================

def env(key, default=None):
    return os.getenv(key) or config(key, default=default)


OPENAI_API_KEY = env("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = env("AZURE_OPENAI_ENDPOINT")
GPT5_API_VERSION = env("GPT5_API_VERSION")
GPT5_MODEL = env("GPT5_MODEL")

MAX_RETRY = 10


client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=GPT5_API_VERSION
)


# =========================
# UTILS
# =========================

def safe_json_load(text):
    if not text:
        return None

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# =========================
# STEP 4 – GPT IMAGE CAPTION
# =========================
async def gpt_image_caption_async(
    image_path: str,
    sem: asyncio.Semaphore,
    max_retry: int = 5,
):
    """v4: Improved retry with separate 429 handling and longer backoff."""
    async with sem:
        loop = asyncio.get_event_loop()

        for attempt in range(max_retry):
            try:
                return await loop.run_in_executor(
                    None,
                    partial(gpt_image_caption, image_path)
                )

            except RateLimitError:
                # v4: Longer backoff for rate limits
                sleep_time = (3 ** attempt) + random.uniform(0.5, 2.0)
                print(
                    f"[CAPTION][429] {os.path.basename(image_path)} "
                    f"attempt {attempt + 1}/{max_retry}, "
                    f"sleep {sleep_time:.1f}s"
                )
                await asyncio.sleep(sleep_time)

            except (APITimeoutError, APIError):
                sleep_time = (2 ** attempt) + random.uniform(0, 0.5)
                await asyncio.sleep(sleep_time)

            except Exception:
                return None

        print(f"[CAPTION][FAIL] {os.path.basename(image_path)} after {max_retry} retries")
        return None


async def process_one_caption_task(
    frame_idx,
    files,
    frame_dir,
    sem,
    results,
):
    if frame_idx < 0 or frame_idx >= len(files):
        return

    path = os.path.join(frame_dir, files[frame_idx])
    data = await gpt_image_caption_async(path, sem)

    results[frame_idx] = {
        "frame_index": frame_idx,
        "image": files[frame_idx],
        "caption": (
            data.get("visual_phase_description")
            if isinstance(data, dict) else None
        )
    }

def gpt_image_caption(image_path):
    img_b64 = encode_image(image_path)

    prompt = """
Ảnh này là một key frame đại diện cho MỘT PHASE trong livestream bán hàng.

Hãy mô tả trạng thái trực quan của phase này,
theo cách phù hợp để SO SÁNH và NHÓM các phase giống nhau.

YÊU CẦU:
- Chỉ mô tả những gì NHÌN THẤY
- Tập trung vào:
  + Sản phẩm có đang được trình bày / cầm / demo hay không
  + Mức độ tập trung của camera vào sản phẩm
  + Trạng thái chung của khung hình
- KHÔNG suy đoán cảm xúc, ý định, hiệu quả
- KHÔNG nhắc tới thời gian, số liệu, hay người xem

Chỉ trả JSON:
{
  "visual_phase_description": "string"
}
""".strip()

    resp = client.responses.create(
        model=GPT5_MODEL,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{img_b64}"
                }
            ]
        }],
        max_output_tokens=1024
    )

    return safe_json_load(resp.output_text)


# =========================
# STEP 4 – ENTRY POINT
# =========================

def caption_keyframes(
    frame_dir: str,
    rep_frames: list[int],
    on_progress=None,
):
    files = sorted(os.listdir(frame_dir))
    results = {}
    total_tasks = len(rep_frames)
    completed_count = [0]  # mutable for closure

    async def _wrapped_task(idx, files, frame_dir, sem, results):
        await process_one_caption_task(idx, files, frame_dir, sem, results)
        completed_count[0] += 1
        if on_progress and total_tasks > 0:
            pct = min(int(completed_count[0] / total_tasks * 100), 100)
            on_progress(pct)

    async def runner():
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        tasks = []

        for idx in rep_frames:
            tasks.append(
                _wrapped_task(
                    idx, files, frame_dir, sem, results
                )
            )

        await asyncio.gather(*tasks)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(runner(), loop)
        fut.result()
    else:
        asyncio.run(runner())

    return [results[i] for i in sorted(results)]
