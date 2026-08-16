# Sumber Eksternal Terverifikasi

Dokumen ini memetakan sumber yang digunakan saat menyusun desain. Sumber unit kerja utama adalah website BPS Kabupaten Padang Pariaman dan PPID dengan `mfd=1306`.

## Penggunaan sumber

- PPID dan website unit: identitas, kontak, layanan, dan profil unit.[1][2][10]
- Publikasi lokal mengonfirmasi brand MARAWA existing.[8]
- WebAPI BPS: kemampuan API, token, domain, data JSON, tabel dinamis/statis, publikasi, dan konten lain.[3][9]
- PPID BPS pusat: standar layanan dan kebijakan diseminasi umum; tidak menggantikan data/kontak unit.[4]
- Dokumentasi Google dan DeepSeek: capability/model ID yang tetap perlu dipetakan melalui environment/provider.[5][6]
- Dokumentasi Baileys: channel architecture berbasis WhatsApp Web WebSocket.[7]
- Portal peraturan BPK: referensi hukum tingkat tinggi untuk review privacy dan statistik.[11][12]
- Evidence anti-jailbreak/prompt-injection, benchmark, dan provider guard dipetakan lengkap beserta batas klaim di [`09C-ANTI-JAILBREAK-RESEARCH.md`](09C-ANTI-JAILBREAK-RESEARCH.md).
- Exact WebAPI endpoint graph, response quirks, local mirror schema, dan update workflow ada di [`17-BPS-WEBAPI-DATA.md`](17-BPS-WEBAPI-DATA.md).

## Sources

[1] https://ppid.bps.go.id/?mfd=1306 — Portal PPID BPS Kabupaten Padang Pariaman
[2] https://padangpariamankab.bps.go.id — BPS Kabupaten Padang Pariaman
[3] https://webapi.bps.go.id/developer — WebAPI BPS
[4] https://ppid.bps.go.id/app/konten/0000/Layanan-BPS.html — Layanan dan Kebijakan Diseminasi BPS
[5] https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite — Gemini 3.1 Flash-Lite
[6] https://api-docs.deepseek.com/updates — DeepSeek API Change Log
[7] https://baileys.wiki — Baileys Documentation
[8] https://padangpariamankab.bps.go.id/id/publication/2026/02/27/632a70da42c6c2f59eb034ce/kabupaten-padang-pariaman-dalam-angka-2026.html — Kabupaten Padang Pariaman Dalam Angka 2026 / MARAWA
[9] https://webapi.bps.go.id/documentation — Dokumentasi WebAPI BPS
[10] https://ppid.bps.go.id/app/konten/1306/Profil-BPS.html — Profil BPS Kabupaten Padang Pariaman
[11] https://peraturan.bpk.go.id/Details/229798/uuno-27-tahun-2022 — UU No. 27 Tahun 2022 Pelindungan Data Pribadi
[12] https://peraturan.bpk.go.id/Home/Details/45944/1000 — UU No. 16 Tahun 1997 Statistik
