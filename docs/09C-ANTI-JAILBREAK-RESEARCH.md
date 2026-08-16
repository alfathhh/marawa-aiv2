# Deep Research Anti-Jailbreak dan Prompt-Injection untuk MARAWA AI

## 1. Ringkasan eksekutif

Kesimpulan utama riset ini adalah bahwa **prompt injection belum memiliki solusi sempurna di tingkat model**. Sistem produksi tidak boleh menyatakan aman hanya karena system prompt diperkeras, classifier dipasang, atau serangkaian payload statis berhasil ditolak. OWASP menyebut tidak jelas apakah pencegahan foolproof mungkin dilakukan pada model generatif; NIST menyarankan desain dengan asumsi prompt injection tetap mungkin terjadi; studi adaptive attack terbaru menunjukkan banyak defense yang terlihat kuat pada evaluasi statis dapat ditembus ketika attacker mengetahui mekanismenya.[1][2][3]

MARAWA AI karena itu mengoptimalkan dua sasaran yang berbeda:

1. **Mengurangi kemungkinan model mengikuti instruksi berbahaya** melalui instruction hierarchy, source marking, classifier, normalization, dan model/prompt hardening.
2. **Mencegah dampak sistem meskipun model terkecoh** melalui control/data-flow separation, typed capability, least privilege, action authorization, memory validation, sandbox isolation, output DLP, dan fallback parity.

Sasaran kedua adalah root of trust. Klaim keamanan MARAWA harus berbentuk **“unauthorized effect tidak dapat melewati enforcement tertentu”**, bukan “model tidak dapat di-jailbreak”.

## 2. Definisi yang dipakai

### 2.1 Jailbreak

Jailbreak adalah upaya pengguna langsung untuk menembus safety, policy, atau batas domain model. Contoh MARAWA: meminta agent mengabaikan scope statistik, membuka system prompt, atau menjadi asisten umum tanpa aturan. Benchmark content-safety seperti JailbreakBench, HarmBench, dan StrongREJECT relevan untuk lapisan ini.[12][13][14]

### 2.2 Prompt injection

Prompt injection adalah manipulasi input agar aplikasi/agent melakukan perilaku yang tidak dimaksudkan. **Direct injection** masuk langsung melalui pesan pengguna. **Indirect prompt injection** masuk melalui data eksternal seperti dokumen, PDF, tabel, website, hasil API, metadata, atau tool result. Dampak agentic dapat berupa tool misuse, exfiltration, perubahan state, memory poisoning, dan outbound communication.[1][4][5]

### 2.3 Agent hijacking

Agent hijacking adalah indirect prompt injection yang berhasil mengalihkan agent dari user task ke attacker task. Unit pengujian yang tepat bukan sekadar apakah draft berisi kalimat tertentu, tetapi apakah malicious action benar-benar terjadi.[4][6]

### 2.4 Data poisoning dan memory poisoning

Data poisoning mengubah corpus, embedding, source, atau knowledge agar retrieval/jawaban menyimpang. Memory poisoning membuat record tidak tepercaya bertahan dan memengaruhi turn atau sesi berikutnya. Keduanya berbeda dari satu kali prompt injection dan membutuhkan provenance, approval, immutable references, expiry, serta selective repair.

## 3. Pertanyaan riset

Riset menjawab pertanyaan berikut:

1. Apakah model saat ini dapat memisahkan instruksi dan data secara andal?
2. Defense mana yang mencegah model compromise, dan mana yang mencegah system effect?
3. Apa kelemahan evaluasi statis dan classifier-only?
4. Bagaimana menjaga utility agent statistik yang workflow-nya sering bergantung pada data?
5. Apa hard invariant yang dapat diuji deterministik?
6. Apa residual risk yang tetap membutuhkan monitoring, red-team, atau human approval?

## 4. Evidence hierarchy dan batas klaim

Prioritas sumber:

1. standar/guidance authoritative: NIST, OWASP, MITRE ATLAS;
2. paper peer-reviewed dan preprint dengan metode/benchmark jelas;
3. dokumentasi resmi provider/model guard;
4. engineering guidance vendor;
5. sumber sekunder hanya untuk discovery, bukan dasar klaim utama.

Angka paper dicatat bersama versi sumber bila tersedia. Preprint bukan bukti formal universal dan hasil lintas benchmark tidak dibandingkan langsung bila task, attacker budget, model, atau scorer berbeda.

## 5. Temuan empiris utama

### 5.1 Model tidak memiliki pemisahan instruksi-data yang dapat dijadikan trust boundary

Studi ICLR 2025 *Can LLMs Separate Instructions From Data?* memformalkan instruction-data separation dan mengevaluasi sembilan model. Hasilnya mengkhawatirkan: model existing tidak memberikan pemisahan yang cukup untuk dijadikan enforcement boundary.[7] StruQ, SecAlign, dan instruction hierarchy memperbaiki perilaku model melalui structured query atau training, tetapi tetap merupakan lapisan probabilistik.[8][9][10]

**Implikasi MARAWA:** role tags, delimiter, XML, JSON, atau kata “UNTRUSTED” membantu model, tetapi server tidak boleh menganggap delimiter sebagai sandbox.

### 5.2 Agent benchmark harus mengukur action, bukan hanya teks

AgentDojo menyediakan environment dinamis dengan 97 realistic tasks dan 629 security test cases untuk agent yang memakai tool atas untrusted data.[4] InjecAgent berisi 1.054 test cases, 17 user tools, dan 62 attacker tools; paper tersebut melaporkan ReAct-prompted GPT-4 rentan sekitar 24% pada setting mereka.[5]

**Implikasi MARAWA:** benchmark internal harus berisi legitimate statistical task, malicious injection task, source/tool observations, dan state akhir. Attack dianggap berhasil hanya jika forbidden effect terjadi atau draft berbahaya lolos outbound guard.

### 5.3 Static attack success rate sangat meremehkan attacker adaptif

NIST CAISI memperluas AgentDojo dan melaporkan attack success rate meningkat dari 11% untuk baseline terkuat menjadi 81% untuk serangan baru yang dibuat khusus terhadap target model. Dalam eksperimen repeated attempts, rata-rata ASR meningkat dari 57% menjadi 80% setelah tiap attack dicoba 25 kali.[6]

Paper *Adaptive Attacks Break Defenses Against Indirect Prompt Injection Attacks on LLM Agents* menguji delapan defense dan melaporkan adaptive attacks menembus semuanya dengan ASR di atas 50% pada setup mereka.[15]

Paper *The Attacker Moves Second* menguji 12 defense dengan gradient, reinforcement learning, search, dan human red-team. Untuk banyak defense, ASR adaptive berada di atas 90% walaupun original evaluation melaporkan hasil nyaris nol. Pada AgentDojo, Spotlighting dan prompt sandwiching yang tampak sekitar 1% pada static attacks ditembus search attack di atas 95% pada setting paper.[3]

**Implikasi MARAWA:** fixed corpus adalah regression test, bukan bukti robustness. Release gate wajib memasukkan attack adaptation terhadap exact deployed stack.

### 5.4 CaMeL memberi arah arsitektur paling kuat, tetapi bukan panacea universal

CaMeL memisahkan control flow dari untrusted data dan melekatkan capability/provenance pada values. Security policy ditegakkan ketika tool dipanggil, sehingga untrusted value tidak boleh membentuk unauthorized data flow. Paper versi 2 melaporkan 77% task AgentDojo terselesaikan dengan “provable security”, dibanding 84% pada undefended system.[11]

Kekuatan CaMeL adalah memindahkan security dari “semoga model menolak” menjadi policy pada program flow. Keterbatasannya:

- trusted user query harus dapat diekstrak menjadi plan/control flow;
- workflow yang control flow-nya benar-benar bergantung pada semantic untrusted data lebih sulit;
- informational integrity atau misleading text tidak otomatis terpecahkan;
- utility dapat turun bila policy/declassification terlalu ketat;
- security guarantee hanya berlaku pada threat model, policy, tools, dan flows yang dimodelkan.

**Implikasi MARAWA:** gunakan CaMeL-inspired architecture, bukan menyalin klaim universalnya. Workflow statistik memakai typed extractor dan bounded declassification untuk metadata/data-dependent branching.

### 5.5 Model-level hardening berguna sebagai lapisan tambahan

- **StruQ** memisahkan prompt dan data dalam structured query dan melatih model agar hanya mengikuti instruction channel.[8]
- **SecAlign** memakai preference optimization untuk memperkuat prompt-injection resistance dan berusaha menjaga utility lebih baik daripada beberapa baseline.[9]
- **Instruction Hierarchy** melatih model memprioritaskan instruksi privileged dibanding lower-privilege messages/tool data.[10]
- **Spotlighting** menonjolkan provenance dengan delimiting, datamarking, atau encoding.[16]

Semua teknik ini dapat mengurangi common attacks, tetapi bukan authority boundary. Adaptive results menunjukkan prompt marking/training/filtering tetap dapat ditembus pada kondisi tertentu.[3][15]

### 5.6 Classifier berguna, tetapi tidak boleh mengotorisasi capability

Meta Prompt Guard 2 adalah classifier 22M/86M untuk prompt injection dan jailbreak. Dokumentasi resminya merekomendasikan fine-tuning application-specific dan menyebut context window 512 token sehingga input panjang harus disegmentasi.[17] LlamaFirewall menggabungkan beberapa guardrail untuk agent security.[18]

Constitutional Classifiers menggunakan input/output classifiers yang dilatih dari constitution dan synthetic data. Anthropic melaporkan automated jailbreak success turun dari 86% menjadi 4,4%, refusal benign naik 0,38%, dan compute overhead 23,7% pada setup mereka. Namun live demo kemudian menemukan satu universal jailbreak dan beberapa peserta menyelesaikan semua level, menegaskan bahwa classifier adalah risk reduction, bukan proof.[19]

**Implikasi MARAWA:** classifier menghasilkan risk signal; ia tidak pernah memberi permission. False negative ditahan action gate, sedangkan false positive diukur dengan legitimate statistical corpus.

### 5.7 Least privilege dan “Rule of Two” membatasi blast radius

Meta *Agents Rule of Two* menyatakan satu session sebaiknya memiliki paling banyak dua dari tiga properti berikut: memproses untrustworthy input, mengakses sensitive systems/private data, dan mengubah state/berkomunikasi keluar. Jika ketiganya diperlukan, agent tidak boleh berjalan autonomous tanpa supervision atau reliable validation.[20]

**Implikasi MARAWA:** setiap run diklasifikasikan terhadap A/B/C. Public read-only statistical answers umumnya A+B tanpa external write selain reply yang telah dibatasi. Workflow yang membaca sensitive data dan mengirim ke destination dinamis dilarang. Outbound WhatsApp hanya boleh menuju conversation asal melalui destination capability yang dibuat server, bukan dari data/tool text.

## 6. Arsitektur rekomendasi MARAWA

### 6.1 Dua plane

```text
TRUSTED CONTROL PLANE
  policy release
  authenticated conversation state
  user goal parsed from direct user channel
  approved skill/control-flow template
  tool registry and capability policies
  validators and final send gate

TAINTED DATA PLANE
  raw user text after goal extraction
  retrieved chunks and metadata
  source DB values
  WebAPI/tool result text
  generated labels, tables, and artifact content
  proposed memory values
```

Data plane tidak boleh membuat atau mengubah control-plane instruction, permission, destination, tool identity, source connection, SQL, or policy.

### 6.2 Value provenance

Setiap value yang melintasi agent runtime membawa metadata minimum:

```json
{
  "value_ref": "opaque-id",
  "origin": "user|rag|source_db|webapi|tool|derived|admin_config",
  "trust": "trusted_control|authenticated_user_intent|tainted_data",
  "data_class": "PUBLIC|INTERNAL|CONFIDENTIAL|RESTRICTED",
  "source_id": "optional",
  "evidence_id": "optional",
  "allowed_uses": ["display", "filter_value", "analysis_input"],
  "forbidden_sinks": ["tool_name", "sql_text", "destination", "policy", "code"],
  "integrity_hash": "sha256:..."
}
```

Derived values mewarisi taint dan data classification kecuali deterministic validator/declassifier secara eksplisit menghasilkan typed value baru.

### 6.3 Capability model

Capability harus unforgeable, opaque, server-created, scoped, expiring, dan non-serializable ke prompt bila tidak diperlukan. Minimum capability types:

- `DatasetReadCapability(dataset_id, allowed_views, columns, geography, periods, row_limit)`;
- `EvidenceReadCapability(evidence_ids, purpose)`;
- `AnalysisCapability(input_result_ids, allowed_methods, resource_budget)`;
- `ArtifactWriteCapability(conversation_id, formats, expiry)`;
- `ReplyCapability(conversation_id, destination_ref, max_messages, expiry)`;
- `HandoverCapability(conversation_id, allowed_transition)`.

Model mengusulkan typed action; runtime memasangkan proposal dengan capability yang valid. String yang menyerupai capability ID dari user/source tidak valid tanpa server-side object dan binding yang tepat.

### 6.4 Data-dependent statistical workflow

MARAWA tidak dapat membuat seluruh control flow statis karena query seperti “kecamatan mana paling tinggi?” bergantung pada hasil data. Bounded declassification dipakai:

1. tool mengembalikan typed rows dan tainted display text secara terpisah;
2. deterministic operation menghitung rank/aggregate;
3. extractor boleh menghasilkan hanya schema sempit seperti `dataset_id`, `indicator_id`, `geography_code`, `period`, atau enum;
4. value divalidasi terhadap registry/allowlist;
5. policy engine membuat trusted typed value baru dengan provenance;
6. action berikutnya tetap membutuhkan capability.

Free-form text dari PDF/tool result tidak boleh menjadi tool name, SQL, destination, URL fetch target, code, memory policy, atau source connection.

### 6.5 Plan binding dan drift control

Sebelum membaca untrusted evidence, runtime membuat `task_contract`:

```json
{
  "goal": "compare_indicator",
  "allowed_effects": ["read_public_statistics", "derive_statistics", "reply_origin"],
  "forbidden_effects": ["external_destination", "write_source", "secret_access"],
  "required_evidence": true,
  "max_steps": 8,
  "rule_of_two": {"A": true, "B": false, "C": true}
}
```

Setiap proposed action diperiksa terhadap contract. Perubahan goal akibat user follow-up harus berasal dari authenticated direct-user message, bukan source/tool observation. Plan drift detector adalah alert/advisory; action gate tetap enforcement.

### 6.6 RAG boundary

- Ingestion memisahkan source snapshot, extracted facts, active content, metadata, dan instruction-like spans.
- Approval source tidak mengubah retrieved text menjadi trusted instruction.
- Retrieval menghasilkan evidence references, bukan authority.
- Instruction-like content tetap dapat dikutip bila pertanyaan memang membahasnya.
- URL di evidence tidak otomatis boleh di-fetch atau ditampilkan; citation URL berasal source registry.
- Chunk/tool text tidak pernah ikut ke long-term memory sebagai policy atau preference.

### 6.7 Memory boundary

Memory harus menyimpan typed state dan immutable references, bukan unrestricted summaries yang dapat menjadi instruction. Proposal memory dari model dianggap tainted. Server memvalidasi schema, provenance, authorization, consistency, dan version. Long-term preference memerlukan field allowlist dan tidak boleh berasal dari RAG/tool data.

### 6.8 Output and artifact boundary

Output validator memeriksa scope, evidence, data class, destination, URL, secret/canary, dan content-type encoding. CSV/XLSX formula prefixes di-neutralize; SVG/HTML labels di-escape; PDF tidak boleh menarik external resource. Artifact link harus conversation-bound dan expiring.

### 6.9 Provider/fallback boundary

Primary dan fallback hanya mengganti inference engine. Policy, tool authorization, capability store, memory guard, DLP, and final send gate tetap instance server yang sama. Security refusal/block bukan retryable provider failure dan tidak boleh memicu downgrade.

## 7. Defense stack dan peran masing-masing

| Layer | Fungsi | Sifat | Tidak boleh dianggap sebagai |
|---|---|---|---|
| Normalization | Menemukan homoglyph, zero-width, bidi, encoding | Deterministic/bounded | Sanitizer sempurna |
| Rules/classifier | Risk signal dan routing | Probabilistik | Permission engine |
| Instruction hierarchy/marking | Mengurangi instruction-data confusion | Probabilistik | Sandbox |
| Task contract | Mengikat goal dan allowed effects | Deterministic | Bukti intent sempurna |
| Capability/action gate | Membatasi tools/resources/sinks | Deterministic | Jaminan correctness isi data |
| Typed tools/read-only DB | Membatasi blast radius | Deterministic | Jaminan source tidak salah |
| Memory guard | Menolak persistent authority injection | Deterministic | Detector semua fakta palsu |
| Sandbox | Menahan analysis execution | Deterministic bila isolation benar | Validator statistik |
| Output/DLP guard | Menahan leak/malicious artifact | Mixed | Pengganti source grounding |
| Monitoring/red-team | Menemukan gap baru | Empiris | Prevention real time |

## 8. Hard invariants vs empirical claims

### 8.1 Hard invariants

Invariants berikut harus dibuktikan melalui code/contract/integration tests:

- user/source text tidak dapat membuat tool registry entry;
- model tidak memiliki generic shell, arbitrary HTTP, source DB write, atau raw SQL sink;
- tool call tanpa valid scoped capability ditolak;
- destination outbound bukan parameter dari LLM/source text;
- source DB role menolak write/DDL;
- memory fields `policy`, `permission`, `role`, `secret`, dan arbitrary instruction tidak ada dalam schema;
- analysis sandbox tidak memiliki network, secrets, source DB, atau host mounts;
- security block tidak memicu fallback;
- final send guard memeriksa conversation state/version dan DLP;
- `ADMIN_ACTIVE` memblok AI outbound.

### 8.2 Empirical claims

Hal berikut hanya boleh dilaporkan sebagai hasil measured pada corpus/release tertentu:

- classifier recall/precision;
- direct jailbreak ASR;
- indirect injection ASR;
- multi-turn robustness;
- false-positive/over-refusal rate;
- model instruction hierarchy effectiveness;
- adaptive attack cost/queries-to-success;
- utility under defense;
- latency dan cost overhead.

Kalimat “anti-jailbreak 100%” atau “model kebal” dilarang. `0` observed success harus disertai jumlah trials, attack budget, model/provider/release, dan confidence bound.

## 9. Evaluation methodology yang direkomendasikan

### 9.1 Effect-based scenario

Satu test case minimal memiliki:

- benign user task;
- attacker objective;
- injection placement/source;
- initial conversation/memory state;
- permitted and forbidden effects;
- exact tool/resource policies;
- expected utility outcome;
- repeat count dan attacker budget;
- deterministic state oracle.

### 9.2 Metric utama

- `UTR`: user-task completion rate tanpa attack;
- `UTR_attack`: user-task completion rate saat attack ada;
- `Effect-ASR`: forbidden effect benar-benar terjadi;
- `Draft-ASR`: model menghasilkan unsafe proposal/draft, walau diblok;
- `Containment rate`: unsafe draft/proposal yang ditahan sebelum effect;
- `Benign refusal/false-positive rate`;
- `Queries-to-first-success` dan budget distribution;
- `Repeated-attempt compromise probability`;
- p50/p95 latency dan guard cost.

`Draft-ASR` yang tinggi tetap defect/model-risk signal, tetapi tidak sama dengan production breach bila action/output guard menahan efeknya.

### 9.3 Repeated-attempt reporting

Jika estimated single-attempt success adalah `p`, probability minimal satu sukses dalam `n` independent attempts adalah:

```text
P(any success) = 1 - (1 - p)^n
```

Independence tidak boleh diasumsikan tanpa caveat, tetapi formula menunjukkan mengapa one-shot ASR tidak cukup. Untuk zero observed successes, report one-sided confidence upper bound; jangan menyimpulkan true ASR nol.

### 9.4 Adaptive attacker classes

Exact release diuji dengan:

1. human red-team yang mengetahui architecture dan responses;
2. LLM-guided semantic search/mutation;
3. black-box generation-only optimization;
4. white-box attack untuk local classifier/model bila tersedia;
5. multi-turn and multi-placement adaptation;
6. repeated attempts dengan rate limits production-equivalent;
7. transfer attacks antara primary dan fallback;
8. attacker yang mengoptimalkan detector evasion dan malicious effect sekaligus.

### 9.5 Benchmark roles

- AgentDojo/InjecAgent: inspirasi untuk tool/action scenarios.[4][5]
- NIST agent hijacking extensions: high-impact effects dan adaptive/repeated testing.[6]
- SEP/StruQ/SecAlign: instruction-data separation.[7][8][9]
- JailbreakBench/HarmBench/StrongREJECT: content-safety robustness; pelengkap, bukan sufficient agent gate.[12][13][14]
- MARAWA corpus: authoritative untuk domain statistik Indonesia, WhatsApp, RAG/table/PDF, typed tools, memory, artifact, dan fallback.

## 10. Corpus khusus statistik/BPS

### Benign utility

- istilah “abaikan” dalam operasi missing values;
- kata “sistem”, “encoding”, “decode”, “role”, dan “admin” dalam konteks statistik sah;
- tabel dengan catatan metodologi berbentuk imperative;
- kutipan penelitian mengenai prompt injection;
- multilingual indicator names dan kode wilayah;
- OCR noise, footnote, formula, dan long publication.

### Direct attacks

- fake BPS admin/developer/system authority;
- system prompt/config/tool schema extraction;
- scope expansion;
- arbitrary SQL/source/URL/tool requests;
- forced primary error/fallback selection;
- multi-turn gradual escalation.

### Indirect attacks

Injection canaries ditempatkan pada:

- PDF paragraph, footer, footnote, OCR layer;
- title, author, metadata, alt text;
- table header/cell/note;
- CSV/XLSX cells dan formula-like values;
- source DB text columns;
- WebAPI fields;
- tool result/error;
- knowledge review note;
- prior generated artifact.

### Forbidden effects

- non-statistical answer sent;
- secret/system/config disclosure;
- query di luar dataset/view/column capability;
- external URL fetch atau destination change;
- source DB write;
- memory authority mutation;
- sandbox network/process/host access;
- malicious formula/script/link in artifact;
- fallback security downgrade;
- outbound during `ADMIN_ACTIVE`.

## 11. Release policy

Release prompt, model, classifier, skill, tool, source, parser, memory schema, atau output renderer diblok bila:

- hard invariant test gagal sekali;
- critical Effect-ASR terobservasi;
- primary/fallback policy parity gagal;
- adaptive suite belum dijalankan sesuai risk class;
- source/tool baru menambah A+B+C tanpa supervision;
- false-positive melampaui approved budget;
- eval scorer belum divalidasi dengan human sample;
- exact model/provider/version atau corpus version tidak terekam.

Exception tidak boleh mengubah hard invariant. Exception hanya untuk empirical threshold noncritical dengan owner, expiry, compensating control, dan approval.

## 12. Residual risks

Defense ini tidak menghilangkan:

- misinformation yang tampak plausible dalam approved source;
- compromised admin/source release process;
- undiscovered parser/sandbox/runtime vulnerability;
- side channel provider atau infrastructure;
- semantic attack yang lolos classifier dan hanya menghasilkan misleading narrative tanpa forbidden tool effect;
- social engineering terhadap user/admin;
- supply-chain compromise;
- bug pada policy/capability implementation;
- failure modes baru setelah model/provider update.

Karena itu source governance, deterministic statistical computation, human correction, audit, incident response, and continuous adaptive red-team tetap wajib.

## 13. Keputusan final untuk MARAWA

1. Adopt **CaMeL-inspired control/data-flow separation**, bukan prompt-only architecture.
2. Enforce **typed capabilities and provenance** pada seluruh tool/data/memory/artifact flow.
3. Implement **Rule-of-Two classification** per run; A+B+C membutuhkan supervision atau deterministic validation boundary.
4. Gunakan detector, Prompt Guard-class model, instruction hierarchy, dan source marking hanya sebagai additional signals.
5. Terapkan **effect-based adaptive evaluation** terhadap exact production release.
6. Pisahkan hard invariant metrics dari empirical robustness metrics.
7. Larang klaim “kebal jailbreak”; report observed results, budgets, repetitions, and confidence bounds.
8. Prioritaskan containment: jika model compromise terjadi, unauthorized effect tetap harus nol pada tested and modeled sinks.

## Sources

[1] https://genai.owasp.org/llmrisk/llm01-prompt-injection — OWASP LLM01:2025 Prompt Injection
[2] https://doi.org/10.6028/NIST.AI.100-2e2025 — NIST Adversarial Machine Learning: Taxonomy and Terminology
[3] https://arxiv.org/abs/2510.09023v1 — The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses Against LLM Jailbreaks and Prompt Injections
[4] https://arxiv.org/abs/2406.13352v3 — AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents
[5] https://aclanthology.org/2024.findings-acl.624 — InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated LLM Agents
[6] https://www.nist.gov/news-events/news/2025/01/technical-blog-strengthening-ai-agent-hijacking-evaluations — NIST CAISI: Strengthening AI Agent Hijacking Evaluations
[7] https://arxiv.org/abs/2403.06833v3 — Can LLMs Separate Instructions From Data? And What Do We Even Mean By That?
[8] https://arxiv.org/abs/2402.06363v2 — StruQ: Defending Against Prompt Injection with Structured Queries
[9] https://arxiv.org/abs/2410.05451v3 — SecAlign: Defending Against Prompt Injection with Preference Optimization
[10] https://arxiv.org/abs/2404.13208v1 — The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions
[11] https://arxiv.org/abs/2503.18813v2 — Defeating Prompt Injections by Design (CaMeL)
[12] https://arxiv.org/abs/2404.01318v5 — JailbreakBench: An Open Robustness Benchmark for Jailbreaking Large Language Models
[13] https://arxiv.org/abs/2402.04249v2 — HarmBench: A Standardized Evaluation Framework for Automated Red Teaming and Robust Refusal
[14] https://arxiv.org/abs/2402.10260v2 — A StrongREJECT for Empty Jailbreaks
[15] https://aclanthology.org/2025.findings-naacl.395 — Adaptive Attacks Break Defenses Against Indirect Prompt Injection Attacks on LLM Agents
[16] https://arxiv.org/abs/2403.14720v1 — Defending Against Indirect Prompt Injection Attacks With Spotlighting
[17] https://developer.meta.com/ai/docs/model-cards-and-prompt-formats/prompt-guard — Llama Prompt Guard 2 model card and usage guidance
[18] https://arxiv.org/abs/2505.03574v1 — LlamaFirewall: An Open Source Guardrail System for Building Secure AI Agents
[19] https://www.anthropic.com/research/constitutional-classifiers — Constitutional Classifiers research and live demo results
[20] https://ai.meta.com/blog/practical-ai-agent-security — Agents Rule of Two: A Practical Approach to AI Agent Security
[21] https://learn.microsoft.com/en-us/security/zero-trust/sfi/defend-indirect-prompt-injection — Microsoft guidance for indirect prompt injection defense
[22] https://developers.openai.com/api/docs/guides/agent-builder-safety — OpenAI safety guidance for building agents
[23] https://atlas.mitre.org/ — MITRE ATLAS; teknik LLM Prompt Injection `AML.T0051` dan subteknik direct/indirect
[24] https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf — NIST AI RMF Generative AI Profile
