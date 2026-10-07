# sigap/static/

Tampilan web Sigap. Ditulis dengan HTML, CSS, dan JavaScript biasa, tanpa framework, supaya ringan dan mudah diubah tanpa proses build.

- `admin/`: panel untuk staf, dosen, dan admin. Kotak masuk tiket, dasbor, data mahasiswa, jadwal, kalender, pedoman, pengguna, dan uji chat.
- `student/`: portal mahasiswa. Masuk dengan kode email, persetujuan privasi, menautkan akun Telegram, token agen AI, dan permintaan hapus data.
- `admin/template-pedoman.md`: template pedoman yang bisa diunduh admin dari halaman Pedoman.

Kedua halaman memakai `admin/app.css` yang sama. Warna, huruf, dan ukurannya mengikuti `DESIGN.md` di folder utama, termasuk mode gelap.
