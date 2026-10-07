# langflow/

Semua yang berhubungan dengan Langflow, tempat "otak" Sigap berjalan.

- `build_flows.py`: membangun semua flow Sigap di Langflow secara otomatis: lima alat, agen percakapan (`sigap_agent`), dan agen format pedoman (`handbook_formatter`). Flow dibuat dari kode, bukan disusun manual di layar, supaya selalu sama dengan kode di `sigap/`.
- `components/`: kode lima alat dalam bentuk komponen Langflow. Dibuat otomatis oleh `build_flows.py`, jadi jangan diedit langsung; perubahan akan tertimpa.
- `flows/`: salinan flow yang sudah jadi, untuk cadangan atau diimpor ke Langflow lain.

Setelah mengubah `sigap/tools.py` atau isi agen, bangun ulang flow-nya:

```bash
set -a; . ./.env; set +a
python3 langflow/build_flows.py
```

Model AI yang dipakai diatur lewat `SIGAP_AGENT_PROVIDER` dan `SIGAP_AGENT_MODEL` di `.env`.
