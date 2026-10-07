# eval/

Daftar pertanyaan uji untuk mengecek apakah Sigap menjawab dengan benar.

`golden_set.csv` berisi 48 pertanyaan dalam dua bahasa, dibagi empat kelompok:

- **policy**: pertanyaan aturan kampus, harus menyebut pasal yang tepat.
- **dates**: pertanyaan tenggat dan periode, harus memberi tanggal yang tepat.
- **trap**: pertanyaan yang jawabannya tidak ada di pedoman. Sigap harus bilang tidak tahu dan menawarkan meneruskan ke staf, bukan mengarang.
- **reminder**: permintaan pengingat dengan berbagai cara menyebut waktu.

Cara menjalankan:

```bash
uv run sigap-eval            # memakai aturan cadangan, cepat
uv run sigap-eval --agent    # lewat agen AI di Langflow, lebih lambat
```

Tes memakai tanggal tetap (4 Oktober 2026 pukul 19.00), supaya jawaban yang diharapkan tidak berubah dari hari ke hari. Hasil tiap tes disimpan di `results/` (tidak masuk git).
