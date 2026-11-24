# Boti 0.16 — pamje Pro e lehtë dhe kontroll paralel

0.16.1 e thellon vignette-n në qoshe, duke ruajtur qendrën dhe tekstet sipër efektit. Për këtë pamje duhen rinisur workerat me versionin 0.16.1.

0.16.2 e përshtat vignette-n me raportin vertikal 9:16: errësimi përfshin të katër anët, edhe mesin lart/poshtë, me kalim të butë drejt qendrës. Titrat dhe logot mbeten sipër efektit.

Konfigurimi → Titra dhe efekte ka stilin “Pro të lehtë”. Shton një vignette të zezë të butë rreth skajeve dhe errëson pak vetëm sfondin e turbullt në paraqitjen fit. Pamja kryesore ruan ndriçimin në qendër. Titrat, titulli, logoja dhe watermark-u vendosen sipër vignette-s. Efekti përdor hartë statike në FFmpeg, pa thirrje AI ose kalim shtesë renderimi. Nuk shton shirita të zinj dhe nuk pretendon të rikrijojë detaje të munguara në burim.

Stili Pro ruan titrat me theksim/pop dhe kontrastin e lehtë; titullin e vendos me kontur, madhësi normale dhe kohëzgjatje 4 sekonda. Titulli mund të përdorë deri tri rreshta me font të përshtatur, që titujt më të gjatë të mos priten aq herët. Çdo kontroll mund të ndryshohet veçmas. Ndryshimet përdoren në renderimet e reja; videot ekzistuese nuk ndryshojnë pa rieditim dhe kontroll të ri cilësie.

Efektet kërkojnë `render_revision=3`. Workeri i vjetër nuk mund të marrë një render me këto opsione. Stilet e vjetra vazhdojnë të dërgohen me fushat dhe revision-in që mbështesin workerat ekzistues. Nëse vetëm main-i është përditësuar, ai mbulon renderimin e ri deri në përditësimin e Mint-it.

Dalja mbetet 30 FPS. Kufizimi në 30 FPS bëhet para shkallëzimit, turbullimit dhe efekteve, që burimet 50/60 FPS të mos përpunojnë kuadro që do të hiqeshin në fund. Nuk ulet rezolucioni 1080×1920, nuk ndryshohen parametrat e enkodimit dhe nuk hiqet kontrolli i rezultatit.

`quality_parallel_workers` lejon 1–4 workerë të kontrollojnë klipe të ndryshme kur zgjidhet `quality_worker_id`. Parazgjedhja mbetet 1. Me vlerën 2 dhe dy workerë modernë online, të dy marrin kontrolle nga radha, ndërsa main-i mban analizën. Workerat pa `quality_revision=1` përjashtohen. Në mungesë të workerave modernë, main-i mbulon kontrollin. Çdo worker vazhdon të marrë vetëm një detyrë në një kohë; radha dhe mbrojtja nga uria e detyrave të vjetra vlejnë për renderim/transkriptim gjithashtu. Pragu dhe provat e cilësisë mbeten të njëjta. Fitimi real në sasi duhet matur mbi punë të reja; nuk premtohet dyfishim.

Përditëso nga dosja ekzistuese në W1/W2:

```sh
git pull --ff-only
uv sync --extra worker
```

Pastaj `systemctl --user restart boti-worker-pc-1.service` në W1 dhe `systemctl --user restart boti-worker-pc-2.service` në W2. Paneli duhet të tregojë 0.16.0 dhe rolet e reja. Arkivat, lidhjet dhe kredencialet ekzistuese ruhen.
