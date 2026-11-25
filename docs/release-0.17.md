# Boti 0.17 — fshirja automatike e burimit pas përfundimit

Te Konfigurimi → Përpunimi dhe publikimi aktivizo “Fshij videon burimore sapo të përfundojnë klipet” (`delete_source_after_complete`). Ky instalim e përdor të aktivizuar. Parazgjedhja për instalime të tjera mbetet e çaktivizuar, me afatet ekzistuese të ruajtjes.

Pasi puna kalon në Përfundoi, pastrohen origjinali i shkarkuar/kopjuar në main, audioja, proxy-t dhe segmentet e përkohshme. Kjo zbatohet edhe për punët tashmë të përfunduara dhe për punët me fushatë kur opsioni është aktiv. Nuk pritet publikimi i të gjitha klipeve: publikimi përdor MP4-të përfundimtare, jo VOD-in e madh. Një kontroll çdo minutë rikap pastrimin pas rinisjes ose kur një skedar ishte ende i kyçur nga Windows.

Punët aktive, klipet ende në kontroll dhe asetet që përdoren nga detyra në radhë/aktive mbrohen. MP4-të aktuale dhe të gjitha versionet e referuara ruhen, përfshirë arkivat në worker dhe eksportet. Skedarët hyrës të pronarit në `data/inbox` ruhen. Historiku, transkriptet e ruajtura në databazë, llogaritë dhe kredencialet nuk fshihen.

Pas fshirjes së burimit, rieditimi nga origjinali kërkon shtimin/shkarkimin e videos sërish. Klipet e përfunduara mbeten të shikueshme dhe mund të publikohen. Çaktivizimi i opsionit ndalon fshirjet e ardhshme dhe rikthen afatet e zakonshme; nuk rikthen burimet e fshira.

Ky ndryshim ekzekutohet në koordinatorin main.
