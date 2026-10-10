"""Uji kapasitas jumlah worker paralel untuk pemeriksaan blok.

Memproses blok yang SAMA dengan jumlah worker berbeda lewat OpenRouterReviewer
(model dari .env), lalu mengukur: waktu total, blok per menit, kegagalan
(termasuk 429/batas laju), memori container, dan rata-rata core CPU terpakai.
Fallback dimatikan supaya batas laju API terlihat apa adanya.

Jalankan di container ai-checker:
  python tools/bench_workers.py --docs input/uji1.docx --blocks 24 --workers 1,2,4,6
"""
import argparse
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.chunker import split_into_blocks  # noqa: E402
from src.config import settings  # noqa: E402
from src.document_extractor import extract_document_pages  # noqa: E402
from src.openrouter_reviewer import OpenRouterReviewer  # noqa: E402
from src.vector_store import VectorStore  # noqa: E402

MIN_CHARS = 80


def cgroup_mem_mb():
    for p in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            return int(Path(p).read_text().strip()) / 1024 / 1024
        except Exception:
            continue
    return float("nan")


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak_mem = 0.0
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            m = cgroup_mem_mb()
            if m == m:
                self.peak_mem = max(self.peak_mem, m)
            time.sleep(0.25)

    def stop(self):
        self._stop.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", nargs="+", default=["input/uji1.docx"])
    ap.add_argument("--blocks", type=int, default=24)
    ap.add_argument("--workers", default="1,2,4,6")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    blocks = []
    for d in args.docs:
        blocks += [b for b in split_into_blocks(extract_document_pages(d)) if len(b.text.strip()) >= MIN_CHARS]
    rng = random.Random(args.seed)
    sample = rng.sample(blocks, min(args.blocks, len(blocks)))

    store = VectorStore()
    docs = store.list_document_names()
    cases = []
    for b in sample:
        emb = store._embed([b.text])[0]
        chunks = store.query(top_k=settings.RETRIEVAL_TOP_K, document_names=docs, precomputed_embedding=emb)[:4]
        cases.append((b, chunks))

    print(f"Model: {settings.OPENROUTER_MODEL} | {settings.OPENROUTER_BASE_URL}")
    print(f"Blok diuji per konfigurasi: {len(cases)} | memori container awal: {cgroup_mem_mb():.0f} MB\n")

    reviewer = OpenRouterReviewer()
    reviewer.fallback_client = None
    reviewer.groq_client = None

    def work(case):
        b, chunks = case
        t0 = time.perf_counter()
        res = reviewer.review_block(b, chunks)
        return res.status, (res.issue or ""), time.perf_counter() - t0

    results = []
    for w in [int(x) for x in args.workers.split(",")]:
        sampler = Sampler()
        sampler.start()
        cpu0, t0 = time.process_time(), time.perf_counter()
        with ThreadPoolExecutor(max_workers=w) as ex:
            out = list(ex.map(work, cases))
        wall = time.perf_counter() - t0
        cpu = time.process_time() - cpu0
        sampler.stop()

        fails = [o for o in out if o[0] == "gagal_dianalisis"]
        n429 = sum(1 for o in fails if "429" in o[1] or "rate" in o[1].lower())
        lat = [o[2] for o in out if o[0] != "gagal_dianalisis"]
        row = {
            "worker": w,
            "wall_s": round(wall, 1),
            "blok_per_menit": round(len(out) / wall * 60, 1),
            "gagal": f"{len(fails)}/{len(out)}",
            "dari_429": n429,
            "median_blok_s": round(statistics.median(lat), 1) if lat else None,
            "mem_puncak_mb": round(sampler.peak_mem),
            "core_cpu_rata2": round(cpu / wall, 2),
        }
        results.append(row)
        print(row, flush=True)
        time.sleep(3)

    print("\n=== RINGKASAN ===")
    cols = list(results[0].keys())
    print(" | ".join(cols))
    for r in results:
        print(" | ".join(str(r[c]) for c in cols))


if __name__ == "__main__":
    main()
