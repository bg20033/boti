# Boti 0.14 — puna e drejtpërdrejtë dhe rendimenti

Paneli tregon punën e secilit PC: fazën, videon, kohën në detyrë, segmentin, CPU/RAM, diskun e lirë, versionin, lidhjen e fundit dhe detyrat e përfunduara në 24 orë. Shkarkimi/përgatitja e VOD-it nga shërbimi main shfaqet si punë e main-it edhe kur agjenti i analizës nuk ka detyrë. Radha dhe aktiviteti tregojnë pronësinë e detyrave. Listat janë të kufizuara; numrat e fazave tregojnë radhën e plotë.

Statistikat e prodhimit ndajnë Normal / Provë / Të gjitha. Numërohen klipet e krijuara në 24 orët e fundit dhe statuset e tyre aktuale. Përqindja e pranimit përdor vetëm klipet me vendim përfundimtar. Arsyet e refuzimit numërojnë raportin e fundit të versionit aktual, një herë për klip; nuk numërojnë çdo tentativë si klip tjetër.

Main-i ruan progres numerik të shkarkimit, afërsisht një herë në sekondë: byte-t, totalin kur dihet, shpejtësinë dhe ETA-n. Totali/ETA e përafërt shënohen si të tilla. Audioja dhe përgatitja e segmenteve kanë faza të veçanta. Tokenët, URL-të e brendshme të shkarkimit dhe payload-et e transkriptimit nuk futen në telemetri.

Shkarkimet HLS/DASH përdorin `download_fragments=4` si parazgjedhje, të ndryshueshme 1–16 nga Konfigurimi. Kjo lejon përdorim më të mirë të rrjetit kur serveri e mbështet; nuk garanton katërfishim shpejtësie ose një numër fiks klipesh. Hapësira dhe formati i burimit mbeten të kontrolluara si më parë.

Para shkarkimit llogaritet edhe madhësia e burimit nga filesize/bitrate-i kur dihet, duke zbritur pjesën që është ruajtur për resume. Rezerva e audios dhe pragu i lirë mbahen veçmas. Një VOD i gjatë që nuk mund të përfundojë me diskun aktual pret, ndërsa radha mund të zgjedhë një më të vogël. Paneli shfaq sa hapësirë të lirë kërkohet afërsisht për secilin burim. Kur madhësia nuk dihet, mbetet kontrolli gjatë shkarkimit.

Kur modeli refuzon titrat, por transkriptimi i audios përfundimtare, koha dhe prerjet teknike kalojnë, bëhet një lexim i veçantë vizual i mostrave. Lexuesi nuk merr fjalët e pritura. Të gjitha mostrat duhet të lexohen dhe të përputhen fjalë për fjalë (duke normalizuar shkrimin e madh/vogël dhe pikësimin), pa indekse të humbura/dublikuara. Vetëm atëherë lejohet një vlerësim i ri i historisë, titullit dhe kompozimit. Edhe ky duhet të miratojë dhe të arrijë pragun ekzistues. Gabimi/mospërputhja ruan refuzimin. Vendimi fillestar, leximi vizual dhe rivlerësimi ruhen në raport. Nuk ka miratim vetëm nga përputhja e audios, as shndërrim automatik të provave të vjetra në postime.

Një moment që kaloi vlerësimin por ka titull të pavlefshëm merr një rishkrim të kontrolluar, para se të hidhet poshtë. Prerja dhe rezultati nuk ndryshohen nga kjo përpjekje. Titujt mbeten në vetën e tretë dhe me provë brenda klipit.

Në një provë vetëm diagnostike mbi MP4 ekzistuese, lexuesi pa transkript i përputhi saktë 6/6 mostrat e titrave; përputhja me audion ishte 0.968 dhe devijimi mesatar 0.02 sekonda. Ky është kontroll i një klipi, jo provë e një norme të përgjithshme pranimi ose rritjeje të prodhimit. Provat e vjetra nuk u rivlerësuan në masë dhe nuk u publikuan.
