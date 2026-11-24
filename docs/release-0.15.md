# Boti 0.15 — nisje e pastër, cilësi në worker dhe llogari ditore

Fshirja e mediave krijon shënime të përhershme për asetet e fshira: Biblioteka nuk i shfaq dhe endpoint-i i videos kthen 410. Arkivi nuk mund t'i rikthejë. Ruhen historiku, konfigurimi, kredencialet, fushatat dhe skedarët hyrës në `data/inbox`. Punët e reja kanë një brez të ri identiteti, kështu që i njëjti VOD mund të përpunohet sërish pa ringjallur klipet e fshira. Punët që presin dhe nuk kanë klipe mbajnë radhën e tyre.

Kopjet SHA256 në W1/W2 fshihen nga agjenti i tyre përpara marrjes së punës së re, pas përditësimit. Kërkesa është e qëndrueshme dhe e autentikuar vetëm për workerin përkatës. Një hash i referuar nga media e re mbahet. Pastrohen edhe dosjet e përkohshme të detyrave të përfunduara nga brezat e fshirë; nuk pastrohet dosja e një detyre të re ose aktive. Rrugët kontrollohen për dalje nga arkivi/cache-ja, përfshirë lidhjet simbolike/junctions.

`quality_worker_id` lejon W1 të marrë kontrollin e cilësisë, ndërsa main-i vazhdon gjetjen e momenteve. Ky rol kalon te workeri i zgjedhur vetëm pasi ai njofton `quality_revision=1`; versionet e vjetra nuk marrin kontrollin e ri. Main-i mbulon rolin ndërkohë ose kur workeri shkëputet. Renderimi, transkriptimi dhe korrigjimi vazhdojnë të shpërndahen sipas kapacitetit.

Parazgjedhjet e ritmit të postimit janë 3 postime/llogari/ditë dhe interval minimal 60 minuta, përfshirë YouTube direkt. Kufijtë e një profili publikimi dhe kuotat e platformave kanë përparësi. Një upload i nisur lejohet të përfundojë; statuset e paqarta kërkojnë verifikim dhe nuk ridërgohen verbërisht. Këto janë kufij operatori, jo garanci se një platformë e lejon një numër të caktuar. Publikimi nuk aktivizohet nga ky përditësim.

Te Përmbledhja → “Sa klipe dhe postime mund të dalin në ditë?” gjendet llogaria nga koha reale e detyrave të main-it. Përdoret kostoja historike e analizës dhe kontrolleve për klip të shqyrtuar; llogaritet një skenar me 50–75% shfrytëzim gjatë 24 orëve. Përqindjet e pranimit 25/50/75% janë supozime të paraqitura qartë, jo norma të matura për versionin e ri. Disku, burimet, korrigjimet dhe lidhjet e llogarive kufizojnë prodhimin real.

Për llogarinë e postimit: 1 grup me 4 platforma × 3 klipe = 3 video të veçanta dhe deri 12 postime në ditë. 12 grupe të lidhura/destinuara mund të përdorin deri 36 video të veçanta dhe 144 postime sipas këtij ritmi, nëse ka mjaftueshëm klipe të pranuara dhe kuotat e lejojnë. Kuota standarde e projektit YouTube është [100 `videos.insert` në ditë](https://developers.google.com/youtube/data/determine_quota_cost); [kufijtë e kanalit dhe ngarkimit](https://support.google.com/youtube/answer/57407) mund të jenë të ndryshëm. TikTok ka [kufij sipas krijuesit dhe kërkesa për integrimin](https://developers.tiktok.com/docs/en/content-sharing-guidelines); numri i platformave/profilit të lidhur nuk i anashkalon ato.

## Përditësimi i Mint-it

Nga dosja ekzistuese e Botit në **W1**:

```sh
git pull --ff-only
uv sync --extra worker
systemctl --user restart boti-worker-pc-1.service
```

Në **W2**:

```sh
git pull --ff-only
uv sync --extra worker
systemctl --user restart boti-worker-pc-2.service
```

Paneli duhet të tregojë versionin 0.15.0 dhe të pastrojë njoftimin për kopjet e vjetra sapo workerat ta konfirmojnë fshirjen. Kontrolli i cilësisë kalon te W1 automatikisht. Nëse shërbimi nuk është aktiv, kontrollo `journalctl --user -u boti-worker-pc-1.service -n 30 --no-pager` (pc-2 për W2).

Origjinalet e VOD-eve përgatiten ende në main; arkivi në Mint ruan MP4-të përfundimtare. Main-i duhet të ketë hapësirë për burimin dhe rezervën e tij. VOD-et që nuk futen presin; nuk ulet cilësia e burimit dhe nuk ulet pragu 75 për të shtuar sasi artificiale.
