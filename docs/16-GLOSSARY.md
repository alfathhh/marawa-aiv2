# Glosarium

| Istilah | Definisi dalam proyek |
|---|---|
| MARAWA AI | Asisten Statistik Padang Pariaman berbasis AI |
| Conversational AI agent | Sistem yang menjaga konteks, merencanakan langkah, memakai tools berulang, menganalisis, membuat artefak, dan melanjutkan follow-up |
| Domain-bounded | Kemampuan agent luas di dalam batas statistik/BPS, tetapi tidak menjadi general assistant |
| Agent run | Eksekusi satu turn pengguna yang terdiri dari ordered steps dan satu stop reason |
| Agent step | Scope/context/plan/tool/analysis/validation action terstruktur; bukan private chain-of-thought |
| Working memory | Typed state aktif lintas turn yang menunjuk evidence/result/analysis/artifact IDs |
| Context compaction | Ringkasan terstruktur history panjang yang mempertahankan goal dan typed references |
| Skill | Prosedur statistik versioned yang membantu memilih tools, metode, checks, dan output |
| Query result | Output data terstruktur immutable dengan dataset/source/evidence lineage |
| Analysis | Hasil olahan reproducible dengan method/version, parameters, input results, diagnostics, dan caveats |
| Artifact | Grafik, tabel, CSV, XLSX, atau PDF yang dibuat dari result/analysis IDs |
| Analysis sandbox | Lingkungan terisolasi tanpa network/secrets/host access untuk analisis statistik kompleks |
| PST | Pelayanan Statistik Terpadu BPS |
| BPS domain 1306 | Domain WebAPI/site untuk BPS Kabupaten Padang Pariaman |
| RAG | Retrieval-Augmented Generation; salah satu tool agent untuk mengambil evidence dokumen/knowledge |
| Hybrid retrieval | Kombinasi lexical/full-text dan vector semantic retrieval |
| Evidence | Potongan sumber atau result data terstruktur yang benar-benar mendukung jawaban |
| Evidence snapshot | Salinan immutable evidence yang dipakai sebuah jawaban |
| Grounding | Pengikatan klaim jawaban ke evidence |
| Abstention | Agent memilih tidak menjawab karena bukti/scope tidak memenuhi gate |
| Hallucination | Klaim yang tidak didukung evidence atau menyimpang dari sumber |
| Dataset registry | Katalog server-side typed datasets, filters, measures, query templates, dan citation rules |
| Structured data path | Jalur deterministik untuk angka/tabel melalui approved dataset tools |
| Knowledge path | Jalur hybrid RAG untuk dokumen, definisi, metadata, dan layanan |
| pgvector | PostgreSQL extension untuk vector embeddings/search |
| FTS | Full-text search PostgreSQL |
| Baileys | Library Node/TypeScript unofficial untuk WhatsApp Web WebSocket |
| Internal webhook | Endpoint signed dari Baileys worker ke FastAPI; bukan Meta Cloud webhook |
| Inbox/outbox | Pattern durability/idempotency untuk event masuk dan message keluar |
| Handover | Pengalihan percakapan dari bot ke petugas PST |
| `ADMIN_ACTIVE` | State ketika admin menangani chat dan bot wajib diam |
| RBAC | Role-Based Access Control |
| TOTP | Time-based One-Time Password untuk 2FA |
| PII/data pribadi | Data yang mengidentifikasi/terkait individu, misalnya nomor WhatsApp dan isi chat |
| Source DB | PostgreSQL existing milik BPS yang diakses read-only |
| App DB | PostgreSQL MARAWA untuk operasi, knowledge, audit, dan analytics |
| Prompt release | Versi prompt yang telah melalui draft/eval/approval dan dipublish |
| Capability probe | Test startup/publish untuk memastikan model mendukung schema/tool yang dibutuhkan |
| SLI/SLO | Indikator dan target tingkat layanan |
| RPO/RTO | Toleransi kehilangan data dan target waktu pemulihan |
| BRS | Berita Resmi Statistik |
| Metadata statistik | Keterangan tentang kegiatan, variabel, indikator, konsep, definisi, unit, dan metodologi |
| Periode referensi | Waktu yang direpresentasikan data; berbeda dari tanggal publikasi/rilis |
| Percentage point | Selisih langsung dua persentase, bukan persen perubahan relatif |
