# sigap/

Backend Sigap. Di sini ada semua yang tidak butuh AI: login, database, bot Telegram, panel admin, portal mahasiswa, dan gateway MCP. Bagian "otak" (memahami pesan dan memilih alat) ada di Langflow, bukan di sini.

Kenapa dipisah? Supaya data pribadi mahasiswa tidak pernah masuk ke model AI. Langflow hanya menerima pertanyaan dan kode mata kuliah, tanpa nama, NIM, atau akun chat.

## Alat-alat

| Berkas | Isinya |
|---|---|
| `tools.py` | Lima alat Sigap: tenggat dan ujian, periode akademik, aturan kampus, pengingat, dan meneruskan pertanyaan ke staf. Semua tanggal dan angka dihitung di sini, bukan oleh AI, supaya tidak ada yang dikarang. |
| `retrieval.py` | Mencari pasal pedoman yang cocok. Kalau tidak ada yang benar-benar cocok, Sigap bilang tidak tahu daripada menebak. |
| `data.py`, `textutil.py` | Membaca data kampus, mengenali bahasa, dan menulis tanggal dengan rapi. |

Kode alat ini juga dikemas ke Langflow oleh `langflow/build_flows.py`, jadi kalau diubah, flow-nya perlu dibangun ulang.

## Percakapan

| Berkas | Isinya |
|---|---|
| `agent.py` | Alur percakapan: meneruskan pesan ke agen di Langflow, menyiapkan tombol konfirmasi, dan memakai aturan cadangan kalau AI sedang bermasalah. |
| `chat.py` | Mengenali siapa mahasiswanya, mencatat tiket dan pengingat, dan memulai percakapan baru setelah lama tidak aktif. |
| `telegram_bot.py` | Bot Telegram beserta perintahnya (`/login`, `/tiket`, `/token`, dan lainnya). |
| `langflow_client.py` | Cara backend memanggil Langflow. |
| `channels.py` | Mengirim pesan keluar, misalnya pengingat dan balasan staf. |

## Akun dan data

| Berkas | Isinya |
|---|---|
| `models.py`, `db.py` | Bentuk tabel database (SQLite) dan koneksinya. |
| `security.py` | Kata sandi dan sesi login. |
| `services.py` | Pekerjaan sehari-hari di database: menautkan akun chat, token, tiket, pengingat. |
| `sync.py` | Mengirim jadwal, kalender, dan pedoman terbaru ke Langflow setiap kali staf mengubahnya. |
| `retention.py` | Menghapus data lama secara otomatis, sesuai UU PDP. |
| `mailer.py` | Mengirim email kode masuk. |

## Web dan lainnya

| Berkas | Isinya |
|---|---|
| `web.py` | Titik mulai server: halaman web, bot, pengingat, dan pembersihan data jalan dari sini. |
| `admin_api.py`, `student_api.py` | Fungsi panel admin dan portal mahasiswa. Tiap peran hanya bisa melakukan yang menjadi haknya. |
| `handbook_formatter.py` | Mengubah pedoman resmi (Word, PDF, teks) jadi format Sigap lewat agen, lalu menandai bagian yang perlu dicek admin. |
| `mcp_server.py` | Pintu untuk agen AI milik mahasiswa (Claude Code, IBM Bob) dengan token pribadi. |
| `cli.py`, `evaluate.py` | Perintah terminal: isi data demo, buat pengguna, dan uji jawaban Sigap. |
| `config.py` | Pengaturan dasar: membaca `.env`, zona waktu WIB, dan alamat publik. |
| `static/` | Tampilan web, lihat `static/README.md`. |
