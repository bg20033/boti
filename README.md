# Boti Studio

Main + katër agjentë për të kthyer video të gjata (Kick, Twitch, YouTube ose skedarë të fushatës) në klipe për TikTok, Instagram dhe YouTube, sipas rregullave të fushatës në clipping.net. AI lokale, mjete falas, pa abonim për përpunim. Paneli është në shqip dhe punon pa CDN ose shërbim AI në cloud.

**12 grupe / 36 destinacione** krijohen fillimisht. Grupet shtohen nga paneli. Një klip caktohet në një grup dhe ruan destinacionin kur detyra kalon te një PC tjetër. YouTube/Instagram kanë radhë të pavarura; TikTok merr një paketë për postim manual.

## Çfarë është ndërtuar

Versioni **0.8** shton marrjen automatike të VOD-eve nga kanalet Kick. Te **Konfigurimi → Marrja automatike nga Kick → Shto kanale**, ngjit emrat e kanaleve, një për rresht, dhe cakto grupet/fushatën. Main-i kontrollon kanalet çdo 5 minuta dhe mbush radhën sapo lirohet kapaciteti. Mund të përfshish deri 50 VOD-et ekzistuese që kthen lista e kanalit në kontrollin e parë ose të ndjekësh vetëm videot e reja.

Historia e zbulimit ruhet në SQLite. Një restart ose shkëputje e rrjetit e vazhdon radhën e ruajtur; videoja ekzistuese deduplikohet nga pipeline-i. Radha automatike fillimisht mban deri dy video aktive/në pritje gjithsej. Pezullimi i përgjithshëm, grupet/fushatat e pezulluara dhe kufiri i diskut respektohen. Linket live/private dhe videot ende në përpunim anashkalohen.

Zgjidh **VOD i plotë** për punë normale ose **Provë me segment** për përpunim të kufizuar pa publikim. Krijimi i klipeve varet nga përzgjedhja dhe kontrolli i cilësisë. Kur mungon material i përshtatshëm ose hapësira në disk, radha pret. Ndjekja e kanaleve dhe kufiri i radhës menaxhohen nga paneli. **Hiq ndjekjen** ruan punët dhe videot që janë shtuar tashmë.

Versioni **0.7** shton **“Provoje këtë VOD”**:

- Zgjedh 30 sekonda deri 15 minuta nga videoja dhe maksimumi 3–5 kandidatë. Përzgjedhja, editimi dhe kontrolli përdorin agjentët ekzistues; numri final varet nga cilësia.
- Raporti tregon kohët në VOD-in origjinal, arsyet e përzgjedhjes, kontrollet, gabimet dhe videon për shikim.
- Prova **nuk publikon**, edhe kur publikimi i përgjithshëm është aktiv. Kur mungojnë agjentët, pret dhe tregon çfarë mungon.
- Provat dhe punët normale ruhen veçmas; provat nuk numërohen në kapacitetin e prodhimit. Hollësitë: [Shënime 0.7](docs/release-0.7.md).

Versioni **0.6** forcon stabilitetin para pilotit:

- Burimet e lejuara përdorin emra të saktë; linket e kanaleve kufizohen sipas platformës, ndërsa një link videoje lejon vetëm atë video. `deenthegreat` nuk pranon më `notdeenthegreat`. Burimi kontrollohet edhe pas rifillimit dhe para publikimit.
- Skedarët lokalë identifikohen me SHA256 të plotë. Dy video me fillim/fund të njëjtë dhe mes të ndryshëm mbeten punë të ndryshme. Kopja e main-it është e pavarur nga `inbox`; një skedar i ndryshuar pas shtimit në radhë ndalet para transkriptimit.
- Importet 0.5 përditësohen në identifikuesin e ri vetëm nga origjinali i ruajtur. Kur ai mungon dhe riimporti është i paqartë, sistemi kërkon rikthimin e origjinalit në vend që të bashkojë historitë.
- Paneli tregon versionin real të main-it dhe cilat aftësi mungojnë te agjentët online. Kjo nuk vërteton cilësinë e modelit: piloti me 30 klipe dhe prova 24-orëshe mbeten matje reale.

Identifikimi i plotë lexon tërë skedarin me memorie të kufizuar. Importi lokal ruan një kopje të veçantë, prandaj kërkon hapësirë edhe për atë kopje. Hollësitë: [Shënime 0.6](docs/release-0.6.md).

Versioni **0.5** i bën rregullat e fushatës bazën e çdo prerjeje dhe editimi. Udhëzimi i plotë: [Rregullat e fushatës](docs/campaign-rules.md).

- **Rregulla të strukturuara për çdo fushatë:** kohëzgjatja min/maks (5–180s), titra po/jo, titull hyrës, watermark me tekst, logo e ngarkuar, hashtag-ë/përmendje/tekst i detyrueshëm, fjalë të ndaluara, burime të lejuara, klipe maksimale për video dhe postime maksimale për llogari në 24 orë.
- **Nxirr rregullat nga teksti:** ngjit rregullat nga faqja e fushatës dhe Boti propozon vlerat që mund t’i lexojë. Ti i kontrollon para ruajtjes; është përputhje modelesh teksti, jo garanci.
- **Rregullat zbatohen kudo:** AI-ja kërkon momente brenda kohëzgjatjes së fushatës, main-i refuzon çdo kandidat ose video jashtë saj, agjentët djegin watermark-un/logon/titullin, dhe përshkrimi i çdo postimi mban gjithmonë etiketat e kërkuara.
- **Kontroll përputhshmërie nga main-i:** kohëzgjatja, fjalët e ndaluara, përshkrimi dhe mbivendosjet e raportuara nga renderimi kontrollohen pa AI. Klipi që nuk kalon nuk publikohet. Mungesa e watermark-ut/logos korrigjohet me një renderim të ri; kohëzgjatja ose fjalët e ndaluara e anashkalojnë klipin.
- **Burime të reja:** video të gjata YouTube (`watch?v=`, `youtu.be`, `live/`) dhe skedarë lokalë nga dosja `data/inbox` e main-it.

Versioni **0.4** shtoi importin Kick dhe kontrollin automatik pas renderimit. Ti zgjedh fushatën dhe VOD-in; nuk nevojitet aprovim për çdo klip.

- Linke HTTPS të videove të përfunduara: `twitch.tv/videos/ID`, `kick.com/kanali/videos/UUID` dhe `youtube.com/watch?v=ID`. Adapterët provojnë lidhjen para shkarkimit; linket live/private/të paarritshme ndalen me gabim. Shkarkimi i plotë ruan checkpoint lokal, ndërsa pjesët e papërfunduara rifillohen nga yt-dlp.
- Çdo klip i ri automatik kalon `render → quality`. Kontrollohen dekodimi/formati, audioja e matur, kohët e titrave dhe përputhja me një transkriptim të ri të audios përfundimtare. Qwen shqyrton kuptimin, kompozimin dhe kërkesat editoriale me pamje vertikale të videos së eksportuar.
- Një dështim editorial merr **vetëm një korrigjim**: titra nga audioja origjinale, kufij fjalish nga transkripti burimor ose kompozim me pamje të plotë. Versioni i dytë kontrollohet përsëri; dështimi shënohet `skipped`. Gabimet teknike kanë deri tri riprovime të veçanta dhe nuk ndalin klipet e tjera.
- Raportet lidhen me versionin, asset-in, SHA256, metadata-t dhe rregullat. Refuzimi manual e bllokon gjithmonë publikimin. Ndryshimi i skedarit/rregullave nuk trashëgon aprovimin e vjetër. Punët ekzistuese mbeten `manual`; migrimi nuk ndez publikime retroaktive.
- Paneli shfaq kontrollin, korrigjimin, anashkalimet, gabimet teknike, historinë e vlerësimeve dhe lidhjet e platformave që mungojnë.

Detajet e API-së dhe rikuperimit: [Boti 0.4](docs/automatic-processing.md). Udhëzimi i fushatave:


- Te Fushatat krijo Kick, Stake ose streamerët me rregullat e tyre reale. Tarifat, pragjet dhe limitet mund të mbeten të panjohura. Cakto grupet dhe platformat për secilën fushatë; çdo grup ka një llogari për platformë.
- Zgjidh fushatën te formulari i VOD-it para shtimit. Sistemi ruan versionin e rregullave dhe pengon ricaktimin e heshtur të një VOD-i ekzistues te një fushatë tjetër.
- Për fushatat, publikimi automatik kërkon status aktiv, rregulla të konfirmuara, konfirmim të lejimit të automatizimit nga shërbimi dhe kontroll pozitiv për versionin aktual të klipit (automatik në punët e reja, njerëzor në punët `manual`). Këto shënime nuk verifikohen vetë te clipping.net. Punët pa fushatë vazhdojnë me kontrollet ekzistuese.
- Ndryshimi i fushatës krijon version të ri dhe mban publikimet e punëve ekzistuese në pritje. Te Hollësitë e videos mund të rikonfirmosh rregullat; klipet kërkojnë vlerësim përsëri. Ndryshimi i platformave/destinacioneve që bie ndesh me punën ekzistuese nuk pranohet në këtë rikonfirmim.
- Postimet e publikuara shfaqen te Dorëzimet. Kopjo URL-në dhe dorëzoje manualisht në clipping.net. Regjistro statusin dhe referencën e konfirmimit; `paid` është shënim i operatorit, jo pagesë bankare e verifikuar nga Boti. Historia ruhet, ndërsa ruajtja e përsëritur e të njëjtave të dhëna nuk e dyfishon.
- Daljet e fushatave ruhen kur ndonjë dorëzim ende nuk është shënuar i paguar, edhe pas përfundimit të publikimit. Kontrollo hapësirën kur fushatat zgjasin.

Editor me timeline, ndjekje automatike streamerësh dhe panel financiar mbeten faza pasuese. TikTok përfundon manualisht; clipping.net merr URL-në me dorëzim manual. Nuk ka scraping ose API të supozuar për clipping.net. Migrimi ruan ID-të dhe lidhjet ekzistuese; identiteti i VOD-it bëhet platformë + ID videoje.

- Shkarkim i vetëm i VOD-it, transkriptim i shpërndarë, analizë lokale me tekst, energji audioje, ndryshime pamjeje dhe verifikim vizual të kandidatëve.
- Whisper `medium`, Ollama `qwen3.5:4b`, kontroll cilësie 75/100, klipe 20–60s (ose kohëzgjatja e fushatës) dhe heqje e kandidatëve të mbivendosur.
- Render 1080×1920 H.264/AAC: titra, normalizim audioje, sfond i turbulluar, kornizim i folësit kur detektohet në mënyrë të qëndrueshme, ose kamerë sipër/lojë poshtë.
- Një detyrë e rëndë aktive për PC, heartbeat 10s, lease 120s, fencing për rezultate të vjetra, deri tri përpjekje, SQLite WAL dhe rikuperim pas restartimit.
- Ngarkime YouTube të rifillueshme; kontejnerë Instagram të qëndrueshëm; status i veçantë për rezultate të paqarta që kërkojnë kontroll para riprovimit.
- Kufizim disku, pastrim me ruajtjen e videove në pritje, vlerësim i mostrës dhe raport kapaciteti 24-orësh.

**1,000 klipe/ditë është synim, jo kapacitet i garantuar.** Shpejtësia varet nga CPU/GPU, orët e burimit, interneti dhe momentet që kalojnë përzgjedhjen. Pragu i cilësisë nuk ulet për të mbushur numrin. Rryma dhe pajisjet mbeten shpenzime.

## 1. Varësitë në Windows

Përdor Windows 10/11 64-bit. Instalo [uv](https://docs.astral.sh/uv/getting-started/installation/), një [FFmpeg full build për Windows](https://www.gyan.dev/ffmpeg/builds/) me `libass/subtitles`, dhe [Ollama](https://ollama.com/download/windows) në PC-të që bëjnë analizë/kontroll. Kontrolli `quality` kërkon edhe Whisper; të dy modelet përdoren me radhë, jo njëkohësisht. `ffmpeg` dhe `ffprobe` duhet të jenë në PATH. Python 3.12 merret automatikisht nga uv.

Shembull me winget, nëse këto paketa janë të disponueshme në kompjuterin tënd:

```powershell
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
winget install --id Ollama.Ollama -e
```

Për burime YouTube, yt-dlp mund të kërkojë një runtime JavaScript në main: `winget install --id DenoLand.Deno -e`. Rihap terminalin pas instalimit. Kopjo projektin te secili PC, **pa dosjet `.venv`, `data` dhe `worker-data`**. Çdo PC krijon mjedisin e vet. GPU NVIDIA është opsionale: Ollama përdor përshpejtimin që mbështet, renderimi provon NVENC dhe kthehet te CPU. Whisper përdor CPU/int8 fillimisht. Për `whisper_device: "cuda"` duhen bibliotekat përkatëse CUDA/cuDNN sipas [faster-whisper](https://github.com/SYSTRAN/faster-whisper#gpu).

## 2. Përgatit main-in

Nga dosja e projektit në main, zëvendëso IP-në me adresën LAN të main-it:

```powershell
.\scripts\Setup.ps1 -Role Main -MainUrl http://192.168.1.10:8765
uv run boti serve --host 0.0.0.0 --port 8765
```

Hap [panelin lokal](http://localhost:8765). Hyr me përmbajtjen e `data/secrets/admin.token`. Main-i krijon katër konfigurime private: `data/secrets/worker-1.json` deri `worker-4.json`.

**Përmbledhja** shfaq momentet e fundit, gjendjen e kanaleve automatike dhe punën e çdo PC-je. **Biblioteka** (`/#clips`) i mbledh klipet me kërkim, filtra të statusit, luajtje dhe shkarkim MP4. Videot në kontroll ose të refuzuara mund të shikohen pa ndryshuar vlerësimin e cilësisë. “Gati” tregon klipet që kaluan kontrollin. Formulari **Shto video** dhe opsionet e skedarëve lokalë hapen vetëm kur duhen. Te radha mund të shfaqësh punët aktive, historinë ose gabimet.

Për ndarjen main → gjetje momentesh, worker → përpunim, nise edhe agjentin lokal në main me identitetin e veçantë `main-local` (`uv run python scripts/setup_local_worker.py`, pastaj `uv run boti worker --config data/secrets/worker-local.json`). Te **Konfigurimi → PC-ja kryesore për gjetjen e momenteve**, zgjidh `main-local` dhe ruaj. Ai merr analizën e momenteve dhe kontrollin e cilësisë; workerat e lidhur marrin transkriptimin me kohët e fjalëve, renderimin me titra dhe korrigjimet. Main-i shkarkon dhe përgatit segmentet për ta. Ndarja zbatohet nga radha e main-it edhe kur konfigurimet e workerave lejojnë të gjitha aftësitë.

Nëse main-local nuk jep heartbeat për 30 sekonda, është pezulluar ose i mungon aftësia, analiza kalon te workerat e disponueshëm. Nëse nuk ka worker të lidhur për transkriptim/renderim/korrigjim, main-local i merr edhe ato role. Detyrat tashmë aktive përfundojnë në PC-në që i mori; rikthimi i një PC-je ndryshon vetëm caktimet e reja. Paneli shfaq rolet e caktuara dhe numrin real të PC-ve për çdo fazë. Zgjedhja “Ndaje analizën mes PC-ve” rikthen radhën e përbashkët.

Jep rregullin e Windows Firewall vetëm për rrjetin privat dhe subnet-in lokal, në PowerShell si administrator:

```powershell
New-NetFirewallRule -DisplayName "Boti LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private -RemoteAddress LocalSubnet
```

Përdor IP të rezervuar në router. Mos e ekspozo portin në internet. `serve` ka vetëm një proces main; një kyç sistemi pengon dy main-e mbi të njëjtën databazë. Token-i i administratorit dhe ai i agjentit kanë role të ndryshme. Kredencialet e platformave dhe sesionet e transferimit enkriptohen me çelësin lokal në `data/secrets/vault.key`.

HTTP i thjeshtë është për një LAN të besuar. Për TLS:

```powershell
uv run boti serve --host 0.0.0.0 --ssl-cert cert.pem --ssl-key key.pem
```

Vendos URL-në HTTPS në konfigurimet e agjentëve dhe `verify_tls` te rruga e certifikatës së besuar. Mos e çaktivizo verifikimin e TLS në përdorim normal. Shembujt e skriptit të nisjes përdorin HTTP/LAN; për TLS ndrysho argumentet e detyrës së main-it.

## 3. Përgatit katër PC-të

Kopjo **vetëm konfigurimin e PC-së përkatëse** nga main-i, p.sh. te `C:\Boti\worker-1.json`. Mos kopjo vault-in ose token-in e administratorit.

```powershell
.\scripts\Setup.ps1 -Role Worker -WorkerConfig C:\Boti\worker-1.json
uv run boti worker --config C:\Boti\worker-1.json
```

Setup-i shkarkon modelet një herë, kontrollon filtrin e titrave dhe provon modelin lokal. Shkarkimi i modeleve kërkon disa GB dhe internet; më pas përpunimi AI punon lokalisht. `-SkipModels` anashkalon vetëm shkarkimet kur modelet ekzistojnë.

Konfigurimi përfshin:

```json
{
  "id": "pc-1",
  "token": "TOKENI_PRIVAT_I_KRIJUAR_NGA_MAIN",
  "coordinator_url": "http://192.168.1.10:8765",
  "cache_dir": "worker-data",
  "capabilities": ["transcribe", "analyze", "render", "quality", "repair"],
  "ollama_url": "http://127.0.0.1:11434",
  "model": "qwen3.5:4b",
  "whisper_device": "cpu",
  "whisper_model": "medium",
  "encoder": "auto",
  "verify_tls": true
}
```

Në një PC të dobët mund të caktosh vetëm `transcribe` ose `render`; analiza i kalon PC-së që ka modelin. Doctor raporton aftësitë e disponueshme dhe PC-ja merr vetëm ato detyra. Të paktën një PC duhet të mbështetë secilën nga tri fazat.

Në Windows pa përdorues të kyçur, Ollama duhet të jetë duke punuar që analiza të vazhdojë. Mund të shtosh në Task Scheduler edhe `ollama serve` në nisje, nën të njëjtin përdorues që ka shkarkuar modelet. Mos nis dy shërbime Ollama në të njëjtin port.

## 4. Lidh llogaritë

Te **Llogaritë**, zgjidh grupin dhe përdor kartelat TikTok, Instagram dhe YouTube: secila ka linkun për krijimin e llogarisë dhe hapat për lidhjen me Botin. Llogaritë krijohen në shërbimet përkatëse. Hyrja në Botin lokal bëhet me çelësin e main-it.

Për clipping.net, te **Accounts → Link account** shto handle/linkun e të njëjtës llogari dhe kategorinë e saj. Vendose kodin e verifikimit në bio; te YouTube përdoret përshkrimi i kanalit. Përfundo **Check my bio** derisa të shfaqet Verified. Pastaj publiko klipin dhe ngjite linkun e postimit te fushata → Submit clip. Boti përgatit videot dhe regjistron linket; verifikimi dhe dorëzimi përfundohen në clipping.net. Shih [llogaritë](https://clipping.net/docs/clippers/accounts) dhe [dorëzimin](https://clipping.net/docs/clippers/posting) në dokumentacionin zyrtar.

Te **Konfigurimi → Titra dhe efekte**, stili Social vendos titra të mëdhenj me fjalën aktive të verdhë, pop të lehtë, përmirësim të butë ngjyrash dhe shirit progresi. Ngjyra, madhësia, pozita dhe efektet mund të ndryshohen veçmas. Shtyp **Ruaj konfigurimin**; zbatohen në renderimet e reja. Rregullat e fushatës për titrat, logo dhe watermark ruhen.

**Titulli sipër videos (0.11):** aktivizoje te Titra dhe efekte. Boti përdor titullin e momentit; teksti i përbashkët është opsional. Zgjidh madhësi normale/të madhe, sfond të errët/kontur dhe 1–15 sekonda ose gjithë klipin. Titulli shfaqet në dy rreshta me hyrje/dalje të lehtë; titrat e të folurit ruajnë kohët e tyre. Kur ka logo/watermark, titulli vendoset më poshtë në zonën e sipërme.

Te **Biblioteka → Titull sipër** mund t'ia ndryshosh tekstin vetëm një klipi. Kërkon origjinalin e ruajtur, kontroll automatik dhe asnjë publikim të krijuar për atë klip. Rieditimi krijon version të ri, ruan historinë dhe vlerësimin e vjetër te versioni përkatës dhe kërkon kontroll të ri cilësie. Titulli i postimit mbetet veçmas. Workerat 0.11 raportojnë `render_revision: 2`; workerat 0.10 vazhdojnë punët pa këto tituj. Për titujt përditëso dhe rinis workerat.

Efektet e reja kërkojnë worker 0.10.0: pas `git pull --ff-only origin main` dhe `uv sync --locked --extra worker`, rinise agjentin (ose shërbimin systemd). Ai raporton `render_revision: 1`. Main-i nuk ua jep këto renderime workerave të vjetër dhe verifikon stilin e raportuar në dorëzim. Kur vetëm main-local është përditësuar, ai i renderon përkohësisht videot me efektet e reja; transkriptimi dhe korrigjimet vazhdojnë në workerat e tjerë.

### YouTube

Në Google Cloud aktivizo YouTube Data API v3 dhe krijo OAuth Client të tipit **Desktop app**, me projektin dhe ekranin e consent-it të konfiguruar për përdoruesit përkatës. Ruaje JSON-in privatisht si `oauth-client.json`. Në main:

```powershell
uv run boti groups
uv run boti connect-youtube --group pc-1-g1 --client-secrets oauth-client.json --privacy private
```

Zgjidh kanalin e grupit në OAuth. Përsërite për grupet e tjera. Për publikim real rilidhe me `--privacy public` ose `unlisted`, sipas nevojës. Refresh token ruhet i enkriptuar dhe përdoret për rinovim. Autorizimet e revokuara kërkojnë rilidhje. Projekti OAuth në testing mund të ketë tokenë me jetë të kufizuar; për përdorim të vazhdueshëm plotëso kushtet e Google për aplikacionin tënd.

Kuota fillestare në konfigurim është **100 ngarkime/ditë për projekt**, e ndarë mes të gjitha llogarive të atij projekti. Ruhet para çdo nisjeje upload-i dhe rinis në mesnatën Pacific. [Kontrollo kuotën reale dhe kërko rritje sipas nevojës](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits). Ndrysho numrin në panel vetëm pasi kuota e projektit të jetë rritur. Shtimi i PC-ve nuk rrit kuotën.

### Instagram

Përdoret **Facebook Login for Business**, llogari profesionale e lidhur me Facebook Page dhe lejet e publikimit të Meta. Kjo rrugë lejon dërgimin e videos lokale në `rupload.facebook.com`; nuk kërkohet CDN ose URL publike e videos. Shih [shembullin zyrtar të Meta për Reels](https://github.com/fbsamples/reels_publishing_apis/tree/main/insta_reels_publishing_api_sample).

Në panel → Llogaritë → Instagram → Lidh: vendos **Instagram User ID numerik** dhe **Page/access token** për llogarinë. Boti verifikon profilin para ruajtjes. Token-i duhet të ketë aksesin dhe lejet e nevojshme; publikimi real varet nga konfigurimi/review i aplikacionit tënd te Meta. Tokenët që skadojnë ose revokohen rilidhen nga paneli; Boti nuk ruan fjalëkalime Instagram/Facebook.

Versioni Graph API është i konfigurueshëm në `Settings.instagram_api_version` (fillimisht `v24.0`). Adapteri lexon `content_publishing_limit`, pret përpunimin dhe publikon me të njëjtin container ID. Nëse përgjigjja e publikimit humbet dhe statusi mbetet i paqartë, klipi kërkon verifikim në vend që të publikohet përsëri.

### TikTok

Në panel emërto destinacionet, p.sh. `@llogaria`. Çdo klip krijon:

```text
data/exports/<grupi>/<klipi>/
  video.mp4
  caption.txt
  metadata.json
```

Paketa ZIP shkarkohet edhe nga paneli. Pas publikimit manual shtyp **E publikova**. Mund të ruash linkun e postimit. Nuk përdoren botë browseri, proxy apo automatizim CAPTCHA.

Publikimi automatik YouTube/Instagram fillimisht është i çaktivizuar. Pasi i lidh dhe i provon llogaritë, aktivizoje te **Konfigurimi**. Krijimi i klipeve punon edhe pa llogari të lidhura.

## 5. Përdorimi dhe rikuperimi

**Para se të rritësh volumin:** kushtet e clipping.net thonë se postimet me cilësi të ulët, të automatizuara ose me pak mund hiqen, çdo dorëzim duhet të jetë unik për llogarinë, lejohet një llogari Clipper për person dhe fushata mund të kufizojë llogaritë për platformë. Pasoja përfshin humbjen dhe kthimin e pagesave. 12 grupet dhe synimi 1,000 klipe/ditë janë kapacitet i makinave, jo leje për postim. Vendos kufirin e llogarive dhe kufirin ditor nga faqja reale e fushatës dhe shiko çka prodhojnë PC-të.

1. Zgjidh fushatën, pastaj shto linkun e një videoje të përfunduar ose zgjidh një skedar që e ke kopjuar te `data/inbox` në main; mund të zgjedhësh grupet destinacion. Futja e së njëjtës video nuk krijon punë të dyfishtë.
2. Main-i shkarkon një kopje, përgatit audio-segmente 300s me 60s mbivendosje (më të gjata kur fushata lejon klipe mbi 60s) dhe proxy për analizë. Për renderim dërgon vetëm segmentet e zgjedhura.
3. Prerjet afrohen te kufijtë e fjalive/pauzave kur janë afër dhe shmangin prerjen në mes të fjalës. Kandidatët verifikohen me pamje dhe transkript, pastaj deduplikohen sipas rezultatit përfundimtar. Grupet marrin klipe sipas numrit të punëve të caktuara.
4. Për një streamer gaming, te hollësitë e videos gjej ID-në e burimit dhe konfiguro kamerën te Konfigurimi. Koordinatat janë 0–1. Pa profil të sigurt ruhet pamja e plotë me sfond të turbulluar.
5. Punët e dështuara riprovohen nga paneli. Ndryshimi i statusit të një publikimi të paqartë kërkon kontroll në platformë.

Pezullimi ndalon detyrat e reja; detyrat aktive mund të përfundojnë. Në mungesë disku radha pret. Segmenti i përgatitur për renderim fshihet pasi rezultati dhe eksporti janë ruajtur; origjinali mbetet. Origjinalet, audioja dhe proxy-t pastrohen shtatë ditë pas përfundimit. Rezultatet ruhen të paktën 30 ditë dhe **nuk fshihen kur ka publikime ose TikTok në pritje**. Cache-i i detyrës në agjent pastrohet pas përfundimit.

### Cilësia dhe ritmi në versionin 0.2

- **Titrat:** deri në dy rreshta, me ndarje sipas pauzave/fjalive dhe gjatësi të kufizuar. Stili fillestar thekson fjalën aktive sipas kohëve të Whisper; te Konfigurimi mund të zgjedhësh titra të thjeshtë. Kur mungojnë kohët e fjalëve, përdoret tekst pa theksim të rremë. Teksti jashtë prerjes nuk shtohet te titrat.
- **Zëri:** FFmpeg e mat audion para renderimit, pastaj normalizon drejt −16 LUFS dhe kufirit −1.5 dBTP. Përdoret fitim linear kur lejojnë matjet, përndryshe mënyra dinamike. Heshtja trajtohet veçmas. [Dokumentacioni loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).
- **Gjuha dhe termat:** te profili i streamerit cakto `sq`, `en` etj., ose lëre bosh për zbulim automatik. Mund të japësh emra dhe terma të veçantë. Vlejnë për transkriptimet e videove të reja; nuk garantojnë transkriptim të saktë të dialektit. [Opsionet e faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py).
- **Radhë e kufizuar:** fillimisht deri në 2 VOD-e aktive dhe 8 detyra të përgatitura për secilën fazë analizë/renderim. Kufijtë ndryshohen nga paneli. Main-i përgatit maksimumi 2 proxy dhe 4 segmente në një cikël; detyrat që presin mbi 30 minuta marrin përparësi ndaj detyrave të reja, duke respektuar aftësitë e PC-së.
- **Matje:** paneli tregon detyrat në radhë/aktive, PC-të e disponueshme, pritjen më të gjatë dhe kohën mesatare nga detyrat e përfunduara në 24 orët e fundit. Mesatarja përfshin përpjekjen e fundit të suksesshme, jo kohën e pritjes apo gjithë riprovimet. Nuk është premtim kapaciteti.
- **Vlerësimi:** “Përmirëso” ruan shënimin dhe ndal nisjen/vazhdimin e publikimeve automatike për atë klip; “Në rregull” e lejon sërish radhën. Gjendja e upload-it ruhet. Nuk tërhiqet një kërkesë që platforma tashmë e ka marrë; një publikim në fluturim mund të përfundojë. TikTok mbetet manual. Butoni nuk e riediton vetë videon dhe nuk trajnon modelin.

Për përditësim: ndali agjentët dhe main-in në një moment të lirë, kopjo kodin e versionit të ri në të pesë pajisjet dhe ekzekuto `uv sync --locked` në main / `uv sync --locked --extra worker` në agjentë. Ruaj `data`, konfigurimet e agjentëve dhe kredencialet ekzistuese. Pastaj rinisi. Parametrat e rinj marrin vlerat fillestare automatikisht. Klipet e përfunduara dhe detyrat ekzistuese nuk rieditohen nga ky përditësim.

Ruaje kopjen rezervë të `data/boti.sqlite3` bashkë me `data/secrets`. Për kopjim të thjeshtë ndale main-in fillimisht; përfshi `data/media` dhe `data/exports` nëse do të rikthesh edhe videot në pritje. Humbja e `vault.key` e bën të pamundur dekriptimin e kredencialeve të ruajtura. Mos i fut këto skedarë në Git.

## 6. Nisja me Windows

### Linux Mint / Ubuntu — worker-i si shërbim

Nga folderi i repo-s në secilin Mint, ndale procesin e vjetër të worker-it, pastaj:

```bash
git pull --ff-only origin main
uv sync --locked --extra worker
uv run --no-sync boti doctor --config ~/worker-1.json --probe-ai
uv run --no-sync python scripts/install_worker_service.py --config ~/worker-1.json --at-boot
```

Përdor konfigurimin përkatës për secilin PC dhe rrugën reale ku e ke ruajtur. Instaluesi krijon shërbimin e përdoruesit `boti-worker-pc-1.service`, e nis menjëherë dhe e rinis pas ndërprerjes së procesit. Opsioni `--at-boot` kërkon fjalëkalimin lokal për `sudo loginctl enable-linger`, që shërbimi të punojë edhe pa login. Ollama duhet të jetë aktiv në atë PC. Log-et shihen me `journalctl --user -u boti-worker-pc-1.service -f`. Pas përditësimeve, rinise me `systemctl --user restart boti-worker-pc-1.service`.

Pasi roli punon nga terminali, në PowerShell me të drejta për Task Scheduler:

```powershell
.\scripts\Install-StartupTask.ps1 -Role Main
# Në PC 1:
.\scripts\Install-StartupTask.ps1 -Role Worker -WorkerConfig C:\Boti\worker-1.json -IncludeOllama
```

Skripti kërkon llogarinë dhe fjalëkalimin Windows për nisjen pas ndezjes, edhe pa login. Fjalëkalimi trajtohet nga Task Scheduler dhe nuk shkruhet në projekt. Përdor të njëjtin përdorues që ka qasje te skedarët privatë/modelet; nëse fjalëkalimi ndryshon, përditëso detyrën. Log-et rrotullohen në `data/logs` ose `worker-data/logs`.

## 7. Verifikimi

```powershell
uv sync --python 3.12 --extra dev
uv run ruff check boti tests
uv run ruff format --check boti tests
uv run pytest -q
uv run boti doctor
uv run boti doctor --config C:\Boti\worker-1.json --probe-ai
```

Testet (109) mbulojnë rregullat e fushatës nga teksti deri te videoja e renderuar, konkurrencën, autorizimin, CSRF, detyrat e skaduara, rinisjen, kufizimin e përbashkët YouTube dhe humbjen e përgjigjeve pas publikimit. Testi i videos përdor FFmpeg real; transkriptimi/zgjedhja dhe platformat simulohen në atë test. Testet e rrjetit nuk publikojnë asgjë realisht. FFmpeg duhet të ketë libass për testin e plotë. Testet e AI-së nuk provojnë cilësinë e modelit në materialet tua.

Pilot: fillo me një agjent dhe disa VOD-e me leje; shiko 30 klipe në panel dhe regjistro **Në rregull / Përmirëso**. Përfshi gaming, bisedë dhe përmbajtje të përzier. Pastaj aktivizo katër PC-të, provo ndërprerje interneti/PC-je dhe një postim provë për platformë. Mbaj kapacitetin e gjenerimit të ndarë nga kuotat e publikimit.

Për matje 24-orëshe, me main-in/agjentët duke punuar dhe material të mjaftueshëm në radhë:

```powershell
uv run boti benchmark --hours 24 --output benchmark-report.json
uv run boti report
```

Raporti tregon klipe reale, kohën sipas fazave, gabimet dhe publikimet. Synimi konsiderohet i arritur vetëm pas një dite të plotë me të paktën 1,000 klipe dhe një vlerësimi të pranueshëm të cilësisë nga mostra. Kjo provë nuk mund të zëvendësohet me një numër të parashikuar nga specifikat e GPU-së.

Raporti e shënon `target_confirmed` vetëm me ritmin e duhur ditor dhe të paktën 30 klipe të vlerësuara, me 80% të pranuara. Për provat e kryera gjatë zhvillimit dhe kufijtë e tyre, shih [docs/validation.md](docs/validation.md).

Paketa për kopjim në Windows krijohet me `uv run python scripts/package.py` te `dist/boti-windows.zip`. Ajo përfshin kodin, udhëzimet dhe testet; konfigurimet private krijohen veçmas në main.

## API lokale

Main-i shërben dokumentacionin OpenAPI te `/docs`. Rrugët e administratorit pranojnë `Authorization: Bearer <admin-token>`, ose sesionin e panelit me CSRF për ndryshimet. Rrugët `/api/worker/*` kërkojnë token-in e agjentit; leximet/dorëzimet e detyrës kërkojnë edhe lease token-in aktual.

Konfigurimi privat qëndron në `data`; mos e vendos SQLite-n në SMB/shared folder. Nuk kërkohen Redis, Docker apo shërbim i jashtëm publikimi.
