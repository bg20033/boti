# Publikimi dhe ruajtja — 0.13

## Upload-Post

1. Krijo/hap llogaritë TikTok, Instagram, YouTube dhe X, dhe siguro rikuperimin/2FA.
2. Krijo llogarinë tënde në [Upload-Post](https://app.upload-post.com), zgjidh planin sipas nevojës dhe lidhi llogaritë në një profil. Boti nuk blen plan dhe nuk i sheh fjalëkalimet e platformave. [Planet dhe kufijtë](https://www.upload-post.com/pricing/).
3. Në Boti → Llogaritë zgjidh grupin → Lidh profilin për publikim automatik. Vendos API key dhe emrin e profilit. Përzgjidh vetëm platformat që i ke lidhur. Çelësi ruhet i enkriptuar në main, nuk kthehet në snapshot dhe nuk vendoset në Git.
4. Cakto kufirin ditor, intervalin dhe privatësinë. Fillimisht janë 3 postime/llogari/24 orë dhe 60 minuta interval; këta janë kufij lokalë të fillimit, jo garanci kundër kufizimeve të platformave. YouTube nis me Private dhe TikTok me Vetëm unë. Instagram/X publikojnë sipas llogarisë së lidhur. Zgjidh publikimin publik për YouTube/TikTok kur e ke provuar dhe je gati.
5. Konfiguro fushatën reale dhe përdor një punë normale që kalon kontrollin. Pastaj aktivizo publikimin te Konfigurimi. Provat nuk publikojnë edhe kur ky çelës është aktiv.

Çdo destinacion ka kërkesë të veçantë, ID të qëndrueshme dhe `Idempotency-Key`. Gjendja ruhet para transferimit. Pas humbjes së përgjigjes, kontrollohet e njëjta kërkesë; nuk niset një postim i ri. Dërgimi në TikTok drafts nuk konsiderohet postim i publikuar. Rezultatet pa link presin deri në 24 orë; rezultatet e paqarta shkojnë në “Kërkon verifikim”. Verifiko profilin/historinë para riprovimit të konfirmuar.

Lidhja verifikon ID-në e saktë të llogarisë. Nëse llogaria në profilin Upload-Post ndryshon, transferimi ndalet për rilidhje. Rilidhja gjatë publikimit të nisur/paqartë ndalet. Platformat e palidhura nuk shënohen si të publikuara. [API-ja e transferimit](https://docs.upload-post.com/api/upload-video/), [gjendjet dhe drafts](https://docs.upload-post.com/api/upload-status/), [profilet](https://docs.upload-post.com/api/user-profiles/).

TikTok kufizon përdorimin e Direct Post në mjete private për llogaritë e ekipit; shërbimi i publikimit siguron rrugën e integrimit. Kjo nuk jep leje për materiale të huaja ose përjashtim nga rregullat e TikTok/fushatës. [Udhëzimet TikTok](https://developers.tiktok.com/doc/content-sharing-guidelines/).

## Arkivi në worker

- Workeri i ri ruan MP4-në me emrin SHA256 në `worker-data/archive/<worker-id>/`. `archive_dir` në konfigurimin e workerit mund të caktojë një disk tjetër. Arkivi është jashtë dosjes së përkohshme të detyrës dhe nuk fshihet me mbylljen e saj. Nëse ruajtja e arkivit dështon, renderimi nuk konfirmohet.
- Main-i pranon dhe verifikon fillimisht bytes e videos, pastaj regjistron konfirmimin e arkivit për atë worker. Kur videoja ka përfunduar kontrollin dhe nuk është në një detyrë aktive/upload, kopja në main mund të hiqet pas 10 minutash pa përdorim (`Settings.worker_cache_minutes`).
- Kur kërkohet shikim/publikim, workeri e kthen përkohësisht të njëjtën video përmes lidhjes dalëse. Verifikohen përsëri madhësia, SHA256, workeri dhe token-i me afat. Një worker tjetër nuk mund ta plotësojë kërkesën e tij.
- Nëse PC-ja me arkivin është offline, shikimi/upload-i pret. Arkivi nuk është replikë në disa PC: humbja e diskut të workerit humb atë kopje. Mbaj backup nëse materialet janë të rëndësishme. Arkivi nuk ka pastrim automatik në këtë version.
- Videot e vjetra pa konfirmim arkivi nuk zhvendosen ose fshihen. Origjinalet e VOD-eve ende përgatiten në main dhe ndjekin rregullat e tyre të ruajtjes; ky ndryshim zhvendos videot finale të reja.

Përditësimi në secilin Mint, nga dosja e projektit:

```sh
git pull
uv sync --extra worker
# Në W1:
systemctl --user restart boti-worker-pc-1.service
# Në W2 përdor boti-worker-pc-2.service.
```

Nuk ka nevojë të ndryshosh token-in ekzistues. Shtegu real i arkivit raportohet në heartbeat-in e workerit. Main-i përdor arkivimin vetëm për rezultate nga workerat që e konfirmojnë protokollin; instalimi në main nuk e përditëson vetë Mint-in.

## Cilësia dhe provat

Prerjet, titrat me theksim, titujt sipër dhe kontrolli i versionuar nga 0.12 mbeten. Korrigjimi i kufijve tani pranohet nga API-ja deri 6.2 sekonda në fund, në përputhje me kërkimin e fjalisë; riparimi i titrave/paraqitjes nuk mund t'i ndryshojë kohët. Nuk anashkalohet kontrolli automatik.

Testet provojnë rikthimin e arkivit, integritetin/autorizimin, migrimin e llogarive dhe publikimin e simuluar në 4 platforma, përfshirë përgjigjen e humbur dhe TikTok drafts. Nuk është kryer postim real pa API key/llogaritë e pronarit. Gjykimi i diskutueshëm i modelit për frazat e titrave mbetet për kalibrim me një mostër njerëzore; një render i suksesshëm nuk është provë e cilësisë editoriale.
