import threading
import time

from src.llm_limiter import ConcurrencyLimiter


def _run_workers(limiter, n_threads, hold=0.05):
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "done": 0}

    def work():
        with limiter.slot():
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(hold)
            with lock:
                state["active"] -= 1
                state["done"] += 1

    threads = [threading.Thread(target=work) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return state


def test_serentak_tidak_pernah_melebihi_batas():
    state = _run_workers(ConcurrencyLimiter(4), n_threads=30)
    assert state["max_active"] <= 4
    assert state["done"] == 30  # semua selesai, hanya menunggu giliran


def test_batas_tercapai_bila_ada_cukup_permintaan():
    state = _run_workers(ConcurrencyLimiter(4), n_threads=30)
    assert state["max_active"] == 4  # tidak terlalu ketat: slot terpakai penuh


def test_slot_dilepas_walau_terjadi_error():
    limiter = ConcurrencyLimiter(1)
    try:
        with limiter.slot():
            raise RuntimeError("model gagal")
    except RuntimeError:
        pass
    state = _run_workers(limiter, n_threads=3)  # bila slot bocor, ini akan macet selamanya
    assert state["done"] == 3


def test_batas_minimal_satu():
    assert ConcurrencyLimiter(0).limit == 1
