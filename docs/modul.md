# Penjelasan Modul Sigap

Penjelasan singkat tiap bagian kode Sigap: apa gunanya, tanpa detail teknis.

## Gambaran besar

Sigap punya dua bagian utama:

- **Langflow** berisi "otak" Sigap: alat-alat yang menjawab pertanyaan dan agen AI yang memahami pesan mahasiswa.
- **Backend** (folder `sigap/`) mengurus semua yang lain: login, data, bot Telegram, panel admin, dan portal mahasiswa. Backend tidak memakai AI sama sekali.

## Alat-alat Sigap

| Modul | Fungsinya |
|---|---|
| `tools.py` | Lima alat inti Sigap: mencari tenggat dan ujian, mengecek periode akademik, menjawab aturan kampus, membuat pengingat, dan meneruskan pertanyaan ke staf. Semua tanggal dan angka dihitung di sini, bukan oleh AI. |
| `retrieval.py` | Mencari pasal pedoman yang cocok dengan pertanyaan. Kalau tidak ada yang benar-benar cocok, Sigap jujur bilang tidak tahu. |
| `data.py` | Membaca data kampus: mata kuliah, jadwal tugas dan ujian, kalender akademik, dan pedoman. |
| `textutil.py` | Alat bantu teks: mengenali bahasa (Indonesia atau Inggris), memahami kata santai, dan menulis tanggal dengan rapi. |
| `config.py` | Pengaturan dasar: membaca `.env`, zona waktu WIB, dan jam demo. |

## Percakapan

| Modul | Fungsinya |
|---|---|
| `agent.py` | Mengatur alur percakapan. Meneruskan pesan ke agen AI di Langflow, lalu menyiapkan tombol konfirmasi. Kalau AI sedang bermasalah, memakai aturan cadangan supaya mahasiswa tetap dijawab. |
| `chat.py` | Penghubung antara saluran chat dan agen: mengenali siapa mahasiswanya, mencatat tiket dan pengingat, dan memulai percakapan baru setelah lama tidak aktif. |
| `langflow_client.py` | Cara backend "berbicara" dengan Langflow: menjalankan alat dan agen, lalu membaca hasilnya. |
| `telegram_bot.py` | Bot Telegram. Menerima pesan, menampilkan balasan beserta tombolnya, dan menyediakan perintah seperti `/login` dan `/tiket`. |
| `channels.py` | Mengirim pesan keluar ke saluran yang tepat, misalnya pengingat atau balasan staf. |

## Akun dan data

| Modul | Fungsinya |
|---|---|
| `models.py` | Bentuk data di database: mahasiswa, staf, tiket, pengingat, pedoman, dan lainnya. |
| `db.py` | Membuka database SQLite dan memperbarui strukturnya bila ada perubahan. |
| `security.py` | Keamanan akun: menyimpan kata sandi dengan aman dan mengatur sesi login. |
| `services.py` | Kumpulan pekerjaan sehari-hari di database: menautkan akun chat, membuat token, mencatat tiket, menjadwalkan pengingat, dan memeriksa format pedoman. |
| `sync.py` | Mengirim data terbaru (jadwal, kalender, pedoman) ke Langflow setiap kali staf mengubahnya. |
| `retention.py` | Menghapus data lama secara otomatis, sesuai UU PDP. |
| `mailer.py` | Mengirim email kode masuk untuk mahasiswa. |

## Halaman web

| Modul | Fungsinya |
|---|---|
| `web.py` | Menyalakan seluruh backend: halaman web, bot Telegram, pengiriman pengingat, dan pembersihan data. |
| `admin_api.py` | Semua fungsi panel admin: kotak masuk tiket, dasbor, data mahasiswa, jadwal, kalender, pedoman, dan pengguna. Setiap peran hanya bisa melakukan yang menjadi haknya. |
| `student_api.py` | Fungsi portal mahasiswa: masuk dengan kode email, persetujuan privasi, menautkan akun chat, token, dan permintaan hapus data. |
| `static/admin/` | Tampilan panel admin untuk staf, dosen, dan admin. |
| `static/student/` | Tampilan portal mahasiswa. |
| `static/admin/template-pedoman.md` | Template pedoman yang bisa diunduh admin. |
| `handbook_formatter.py` | Mengubah pedoman resmi (Word, PDF, atau teks) menjadi format Sigap dengan bantuan agen AI, lalu menandai bagian yang perlu diperiksa admin. |

## Untuk agen AI milik mahasiswa

| Modul | Fungsinya |
|---|---|
| `mcp_server.py` | Pintu bagi agen AI pribadi mahasiswa (misalnya Claude Code atau IBM Bob) untuk memakai alat Sigap dengan token masing-masing. |
| `bob/` | Petunjuk dan contoh pengaturan untuk menghubungkan IBM Bob ke Sigap. |

## Langflow

| Modul | Fungsinya |
|---|---|
| `langflow/build_flows.py` | Membangun semua flow di Langflow secara otomatis: lima alat, agen percakapan, dan agen format pedoman. |
| `langflow/components/` | Kode lima alat Sigap dalam bentuk komponen Langflow, dibuat otomatis oleh `build_flows.py`. |
| `langflow/flows/` | Salinan flow yang sudah jadi, untuk cadangan dan impor ulang. |

## Alat bantu

| Modul | Fungsinya |
|---|---|
| `cli.py` | Perintah admin dari terminal: mengisi data demo, membuat pengguna, dan membuat undangan. |
| `evaluate.py` | Menguji Sigap dengan daftar pertanyaan baku dan menghitung berapa yang dijawab dengan benar. |
| `migrations/` | Riwayat perubahan struktur database. |

## Data dan dokumen

| Berkas | Isinya |
|---|---|
| `data/` | Data contoh kampus: mata kuliah, jadwal, kalender akademik, dan pedoman. |
| `eval/golden_set.csv` | Daftar pertanyaan uji beserta jawaban yang diharapkan. |
| `compose.yaml` | Pengaturan untuk menjalankan Langflow di Docker. |
| `DESIGN.md` | Panduan tampilan: warna, huruf, dan ukuran. |
| `CHANGELOG.md` | Catatan perubahan penting. |
| `anti-slop/` | Hasil audit tampilan dan perbaikannya. |
