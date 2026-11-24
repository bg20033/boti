# Boti 0.13.1

Titujt e analizave të reja shkruhen me kuriozitet, në vetën e tretë dhe në gjuhën e klipit. Kontrolli i veçantë i momentit rishkruan titullin/përshkrimin duke përdorur pamjet dhe vetëm fjalët brenda prerjes. Ruhet edhe prova tekstuale që mbështet titullin. Titulli ka maksimum 90 karaktere; kontrolli refuzon përgjigjet pa prova, pa konfirmim të burimit/vetës së tretë dhe formulimet e zakonshme angleze në vetën e parë/dytë. Ky është kontroll i modelit lokal, jo garanci që çdo titull është i saktë ose tërheqës; shqyrtimi i videos përfundimtare mbetet në fuqi. Titrat e të folurit nuk rishkruhen.

Titulli i pranuar përdoret në renderimet e reja dhe për publikim. Teksti global ose ndryshimi manual i titullit sipër videos vazhdon të ketë përparësi për atë mbishkrim. Klipet ekzistuese, versionet dhe vlerësimet e tyre nuk ndryshohen nga përditësimi.

Përmbledhja tani tregon katër kontrolle të automatizimit: përpunimin, burimet normale kundrejt provave, llogaritë për të katra platformat në çdo grup destinacion dhe çelësin e publikimit. Shfaqen edhe workerat që kërkojnë përditësim për titujt/arkivin. Raporti është vetëm për lexim; nuk aktivizon postime dhe nuk anashkalon cilësinë ose statuset e paqarta të një publikimi.

Fushata është opsionale për postimet e zakonshme. Nëse burimi lidhet me një fushatë, vazhdojnë të vlejnë rregullat dhe konfirmimet e saj. Kalimi i një ndjekjeje nga Provë në VOD të plotë prek vetëm punët e ardhshme; provat e vjetra nuk shndërrohen në video të publikueshme.

Një VOD i gjatë që pret rezervën e diskut nuk e bllokon më një punë më të vogël që mund të përpunohet me hapësirën aktuale.

Burimet e përkohshme të provave të përfunduara kanë afat të veçantë `trial_source_retention_hours` (24 orë si parazgjedhje; 6 orë në këtë main). Pastrohen burimi, audioja dhe proxy-t, ndërsa ruhen MP4-të e klipit dhe raportet. Afati 7-ditor i origjinaleve të punëve normale mbetet në fuqi. Pas pastrimit, rieditimi që kërkon burimin bëhet duke e përpunuar sërish VOD-in. Pastrimi bllokohet gjatë një rieditimi aktiv.

Për përditësimin e Mint-it, nga checkout-i i Botit:

```sh
git pull --ff-only
uv sync --extra worker
systemctl --user restart boti-worker-pc-1.service
# Në W2 përdor boti-worker-pc-2.service.
```

Main-i vazhdon gjetjen dhe kontrollin; W1/W2 duhet të jenë së paku 0.13 për të marrë renderime me tituj dhe për të ruajtur MP4-të në arkivin e tyre. Origjinalet ende përgatiten në main dhe puna pret nëse nuk ka hapësirë të mjaftueshme.
