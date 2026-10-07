# migrations/

Riwayat perubahan struktur database, dikelola dengan Alembic.

Tidak perlu dijalankan manual: setiap kali server menyala, database diperbarui ke versi terbaru secara otomatis.

Kalau menambah atau mengubah tabel di `sigap/models.py`, buat versi baru:

```bash
uv run alembic revision --autogenerate -m "jelaskan perubahannya"
```

Lalu periksa file baru di `versions/` sebelum dipakai.
