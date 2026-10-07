# data/

Data contoh kampus untuk demo. Semuanya fiktif.

- `courses.csv`: daftar mata kuliah beserta nama lain yang sering dipakai mahasiswa (misalnya "strukdat").
- `course_schedule.csv`: tenggat tugas dan jadwal ujian.
- `academic_calendar.csv`: semester, periode, dan jendela pendaftaran (cuti, pembatalan mata kuliah, pembayaran UKT).
- `handbook/`: pedoman akademik dalam format Sigap. Contoh format pasalnya ada di halaman Pedoman di panel admin.

File ini dipakai untuk mengisi database pertama kali (`uv run sigap-admin seed-demo`) dan sebagai isi awal flow di Langflow. Setelah itu, data yang dipakai bot berasal dari database: staf mengubahnya lewat panel admin, dan backend mengirim versi terbarunya ke Langflow. Mengedit file ini tidak mengubah data bot yang sedang berjalan.
