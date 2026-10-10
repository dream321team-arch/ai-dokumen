"""Benchmark model LLM untuk pemeriksaan blok dokumen.

Membandingkan beberapa model pada blok teks dan rujukan pedoman yang SAMA,
lewat kelas OpenRouterReviewer yang sama dengan aplikasi. Fallback (model
cadangan & Groq) dimatikan supaya kegagalan model terlihat apa adanya.

Yang diukur per model:
  - waktu per blok (median, p95, rata-rata)
  - tingkat gagal (status "gagal_dianalisis": error API / JSON rusak / timeout)
  - recall typo sengaja: sebagian blok disisipi typo (dua huruf ditukar),
    lalu dicek apakah model menandainya
  - rata-rata temuan di blok yang tidak disisipi (indikasi temuan berlebih)
  - kecocokan status dengan model pertama (baseline)

Jalankan di dalam container ai-checker (butuh kunci API & koneksi embedding):
  python tools/bench_models.py --blocks 12 --models gpt-4o,deepseek-v4-flash

Catatan: biaya (token) tidak diukur karena API tidak mengembalikannya ke kode ini.
"""
import argparse
import csv
import json
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.chunker import split_into_blocks  # noqa: E402
from src.config import settings  # noqa: E402
from src.document_extractor import extract_document_pages  # noqa: E402
from src.openrouter_reviewer import OpenRouterReviewer  # noqa: E402
from src.schemas import Block  # noqa: E402
from src.vector_store import VectorStore  # noqa: E402

DEFAULT_MODELS = (
    "gpt-4o,gemini-3.7-flash-low,gemini-3.8-flash-low,"
    "deepseek-v4-flash,gemini-3.5-flash-lite"
)
MIN_BLOCK_CHARS = 80
TOP_CHUNKS = 4


def load_blocks(paths):
    blocks = []
    for p in paths:
        pages = extract_document_pages(str(p))
        for b in split_into_blocks(pages):
            if len(b.text.strip()) >= MIN_BLOCK_CHARS:
                blocks.append(b)
    return blocks


def pick_blocks(blocks, n, rng):
    """Ambil n blok tersebar merata (deterministik lewat seed)."""
    if len(blocks) <= n:
        return list(blocks)
    step = len(blocks) / n
    idx = sorted({min(int(i * step + rng.random() * step), len(blocks) - 1) for i in range(n)})
    return [blocks[i] for i in idx]


def inject_typo(text, rng):
    """Tukar dua huruf tengah pada satu kata alfabet panjang. Kembalikan (teks_baru, kata_salah)."""
    words = [m for m in re.finditer(r"[A-Za-z]{7,}", text)]
    if not words:
        return text, None
    m = rng.choice(words)
    w = m.group(0)
    i = rng.randint(1, len(w) - 3)
    wrong = w[:i] + w[i + 1] + w[i] + w[i + 2:]
    if wrong == w:
        return text, None
    return text[: m.start()] + wrong + text[m.end():], wrong


def retrieve(store, docs, text):
    if not docs:
        return []
    emb = store._embed([text])[0]
    chunks = store.query(
        top_k=settings.RETRIEVAL_TOP_K, document_names=docs, precomputed_embedding=emb
    )
    return chunks[:TOP_CHUNKS]


def make_reviewer(model):
    r = OpenRouterReviewer(model=model)
    r.fallback_client = None  # matikan fallback supaya hasil murni milik model ini
    r.groq_client = None
    return r


def flagged_typo(result, wrong):
    """True kalau salah satu span_error menyebut kata salah yang disisipkan."""
    w = wrong.lower()
    for se in getattr(result, "span_errors", None) or []:
        blob = " ".join(
            str(getattr(se, f, "") or "") for f in ("original_snippet", "explanation", "suggestion")
        ).lower()
        if w in blob:
            return True
    return False


def pct(values, q):
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", nargs="+", default=[str(ROOT / "tests" / "sample.docx")],
                    help="satu atau lebih dokumen sumber blok (docx/pdf)")
    ap.add_argument("--models", default=DEFAULT_MODELS, help="daftar model dipisah koma")
    ap.add_argument("--blocks", type=int, default=12, help="jumlah blok yang diuji")
    ap.add_argument("--typo-ratio", type=float, default=0.5, help="porsi blok yang disisipi typo")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    rng = random.Random(args.seed)

    all_blocks = load_blocks(args.docs)
    if not all_blocks:
        sys.exit("Tidak ada blok dengan panjang cukup di dokumen sumber.")
    sample = pick_blocks(all_blocks, args.blocks, rng)

    store = VectorStore()
    docs = store.list_document_names()
    print(f"Blok tersedia: {len(all_blocks)} | dipakai: {len(sample)} | model: {len(models)}")
    print(f"Pedoman ter-index: {len(docs)} dokumen, {store.count()} chunk")
    if not docs:
        print("PERINGATAN: pedoman belum ter-index di database. Pemeriksaan jalan tanpa rujukan "
              "sehingga kurang mewakili kondisi nyata. Jalankan Re-index dulu.")
    print(f"Total panggilan API: {len(sample) * len(models)}\n")

    # Siapkan kasus uji: sebagian blok disisipi typo, rujukan dicari sekali.
    cases = []
    n_typo = int(round(len(sample) * args.typo_ratio))
    typo_idx = set(rng.sample(range(len(sample)), n_typo)) if n_typo else set()
    for i, b in enumerate(sample):
        text, wrong = (b.text, None)
        if i in typo_idx:
            text, wrong = inject_typo(b.text, rng)
        blk = Block(block_id=b.block_id, text=text, page_number=b.page_number)
        cases.append({"block": blk, "wrong": wrong, "chunks": retrieve(store, docs, text)})
    print(f"Blok dengan typo sengaja: {sum(1 for c in cases if c['wrong'])}\n")

    reviewers = {m: make_reviewer(m) for m in models}
    rows = []  # satu baris per (blok, model)
    for ci, case in enumerate(cases, start=1):
        for m in models:  # diselang-seling per blok
            t0 = time.perf_counter()
            try:
                res = reviewers[m].review_block(case["block"], case["chunks"], max_retries=0)
                err = ""
            except Exception as e:  # jaga skrip tetap jalan
                res, err = None, f"{type(e).__name__}: {e}"
            dt = time.perf_counter() - t0
            status = getattr(res, "status", "exception")
            n_span = len(getattr(res, "span_errors", None) or [])
            caught = flagged_typo(res, case["wrong"]) if (res and case["wrong"]) else None
            rows.append({
                "blok": ci, "block_id": case["block"].block_id, "model": m,
                "detik": round(dt, 2), "status": status, "span_errors": n_span,
                "typo_sengaja": case["wrong"] or "", "typo_tertangkap": caught,
                "error": err or (getattr(res, "issue", "") if status == "gagal_dianalisis" else ""),
            })
            print(f"[{ci:>2}/{len(cases)}] {m:<26} {dt:5.1f}s  {status:<18} span={n_span} "
                  f"{'typo:' + ('OK' if caught else 'LOLOS') if case['wrong'] else ''}")

    # Ringkasan
    base = models[0]
    base_status = {r["blok"]: r["status"] for r in rows if r["model"] == base}
    summary = []
    for m in models:
        rs = [r for r in rows if r["model"] == m]
        ok = [r for r in rs if r["status"] not in ("gagal_dianalisis", "exception")]
        times = [r["detik"] for r in ok]
        typo_rows = [r for r in ok if r["typo_sengaja"]]
        clean_rows = [r for r in ok if not r["typo_sengaja"]]
        agree = [r for r in ok if r["status"] == base_status.get(r["blok"])]
        summary.append({
            "model": m,
            "gagal_%": round(100 * (1 - len(ok) / len(rs)), 1) if rs else 0,
            "median_s": round(statistics.median(times), 2) if times else None,
            "p95_s": round(pct(times, 0.95), 2) if times else None,
            "rata2_s": round(statistics.mean(times), 2) if times else None,
            "recall_typo_%": round(100 * sum(1 for r in typo_rows if r["typo_tertangkap"]) / len(typo_rows), 1)
            if typo_rows else None,
            "temuan_blok_bersih": round(statistics.mean(r["span_errors"] for r in clean_rows), 2)
            if clean_rows else None,
            f"cocok_dg_{base}_%": round(100 * len(agree) / len(ok), 1) if ok else None,
        })

    print("\n=== RINGKASAN ===")
    cols = list(summary[0].keys())
    print(" | ".join(cols))
    for s in summary:
        print(" | ".join(str(s[c]) for c in cols))
    print("\nStatus per model:")
    by_model = defaultdict(Counter)
    for r in rows:
        by_model[r["model"]][r["status"]] += 1
    for m in models:
        print(f"  {m}: {dict(by_model[m])}")

    out = ROOT / "output" / f"bench_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "per_blok.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out / "ringkasan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nHasil lengkap disimpan di: {out}")


if __name__ == "__main__":
    main()
