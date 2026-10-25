# Universal Android Hardware Monitor

Satu dashboard untuk berbagai merek/model Android yang mendukung ADB. Didesain untuk USB dan browser laptop.

## Isi
- `app.py`: backend Flask dan pengambilan data ADB
- `templates/index.html`: dashboard dark mode, grafik garis, gauge semicircle
- `requirements.txt`: dependensi Python
- `logs/android_telemetry.csv`: dibuat otomatis

## Instalasi Windows
1. Instal Python 3.10+, VS Code, dan ekstensi Python.
2. Unduh Android SDK Platform-Tools: https://developer.android.com/tools/releases/platform-tools
3. Ekstrak, tambahkan folder `platform-tools` ke Environment Variables > Path, lalu restart VS Code.
4. Di HP Android: buka About Phone, ketuk Build Number 7 kali jika diperlukan, buka Developer Options, aktifkan USB Debugging.
5. Sambungkan kabel USB data dan setujui dialog RSA/USB debugging.
6. Ekstrak ZIP dan buka folder proyek di VS Code.
7. Terminal:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   adb devices
   python app.py
   ```

8. Buka http://127.0.0.1:5000 di browser laptop.

Jika PowerShell memblokir aktivasi, gunakan `python -m pip install -r requirements.txt` lalu `python app.py`, atau gunakan Command Prompt dengan `.venv\Scripts\activate.bat`.

## Multi-device
Backend membaca perangkat ADB pertama dengan status `device`. Jika lebih dari satu HP terhubung, cabut perangkat lain agar tidak ambigu.

## Dukungan dan keterbatasan universal
Perintah ADB standar dipakai untuk model, versi Android, core count, CPU, RAM, storage, baterai dan beberapa sensor. Namun tidak semua Android/vendor mengekspose sensor dengan format sama.
- CPU usage dihitung dari delta `/proc/stat`.
- GPU name hanya tampil jika properti EGL/Vulkan atau SurfaceFlinger mengungkapkannya.
- GPU load tidak universal melalui ADB biasa, jadi akan N/A.
- Thermal parser bersifat best-effort; suhu CPU/GPU dapat N/A.
- FPS live tidak bisa dijamin melalui ADB standar. Dashboard sengaja menampilkan N/A daripada mengarang angka; implementasi FPS spesifik perangkat memerlukan metode frame-statistics yang kompatibel.
- RAM memakai `/proc/meminfo`; storage adalah partisi `/data`.
- CSV otomatis ditambahkan setiap polling sukses (2.5 detik).
- Chart.js diambil dari CDN; perlu internet saat pertama kali dashboard dibuka.
- Dashboard hanya bind ke localhost (`127.0.0.1`) dan tidak dipublikasikan ke jaringan.
- Matikan USB debugging saat tidak dibutuhkan dan hanya otorisasi komputer tepercaya.
