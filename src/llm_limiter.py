"""Pembatas global jumlah panggilan LLM yang berjalan serentak.

AI_MAX_WORKERS membatasi paralelisme PER dokumen. Bila beberapa pengguna memeriksa
dokumen bersamaan, jumlahnya berlipat (3 pengguna x 10 worker = 30 permintaan serentak)
dan mudah melewati batas laju penyedia model (error 429). Pembatas ini membatasi total
permintaan serentak dari seluruh pemeriksaan di proses ini; pemeriksaan yang melewati
batas hanya menunggu giliran, tidak gagal.

Berlaku per proses: server dijalankan sebagai satu proses uvicorn, sehingga efektif global.
Bila kelak dijalankan dengan beberapa proses, batasnya berlaku per proses.
"""
import threading
from contextlib import contextmanager

from src.config import settings


class ConcurrencyLimiter:
    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._semaphore = threading.BoundedSemaphore(self.limit)

    @contextmanager
    def slot(self):
        """Tunggu sampai ada slot kosong, tahan selama blok `with`, lalu lepaskan."""
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()


llm_limiter = ConcurrencyLimiter(settings.AI_MAX_CONCURRENT_LLM)
