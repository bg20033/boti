# Cilësia e klipeve — 0.12

Kontrolli lokal më 24 nëntor 2026 gjeti 102 klipe: 1 gati, 100 të refuzuara dhe 1 me gabim teknik. Në 203 raporte historike të versioneve, 141 vendime kërkonin korrigjim titrash; 128 prej tyre kishin kaluar matjet teknike, përfshirë përputhjen e transkriptit me audion. Kjo tregon një mospajtim mes matjeve dhe gjykimit vizual; nuk provon se titrat janë të sakta ose se të gjitha refuzimet janë të gabuara.

## Ndryshimet

- Prerjet nuk ndalen brenda fjalëve. Nisjet dhe fundet në mes të një fjalie të vazhdueshme sinjalizohen nga konteksti i fjalëve. Fundet me `...`/`…` nuk konsiderohen mbyllje fjalie.
- Kërkimi i fundit të përshtatshëm shtrihet deri 6 sekonda nga propozimi, pa dalë nga origjinali ose kufijtë e kohëzgjatjes. Hapësira 80/120 ms rreth fjalisë kufizohet nga fjala fqinje që të mos kapë fillimin e fjalisë tjetër.
- Main-i e përsërit kontrollin para planifikimit, edhe kur analiza vjen nga worker më i vjetër. Nuk shpiket dialog dhe nuk shtohet heshtje për të plotësuar minimumin. Kur nuk ka prerje të përshtatshme, kandidati hiqet dhe arsyeja regjistrohet në aktivitet.
- Verifikimi i kandidatit merr vetëm fjalët brenda klipit, me kohë relative. Nuk i atribuohet klipit një përfundim që gjendet jashtë tij.
- Kontrolli final sheh kompozimin e plotë dhe një fletë të dytë me deri 6 pamje të zmadhuara të zonës së titrave të djegur në MP4. Mostrat merren brenda frazave aktive, mbulojnë fillimin/fundin dhe kanë kohët/përshkrimet e tyre. Pozita e titrave merret nga renderimi përkatës.
- Raporti ruan `technical.story_boundaries` dhe `technical.caption_samples`. Refuzimi teknik për fjalë të prera/fund të papërfunduar vlen edhe kur modeli e aprovon videon.
- Te Biblioteka, “Përmirëso prerjen” e zbaton algoritmin edhe në një klip ekzistues me origjinal të ruajtur dhe kontekst transkripti. Krijohet version i ri, rindërtohen kohët e titrave dhe përsëritet kontrolli; ruhet versioni i vjetër. Prerja e pandryshuar nuk krijon version tjetër, ndërsa mungesa e një prerjeje të përshtatshme shpjegohet pa ndryshuar videon. Llogaritë me publikim të nisur nuk rieditohen.

Një kontroll pa ndryshime mbi 102 metadatat ekzistuese gjeti 16 prerje alternative, 17 raste pa prerje të përshtatshme brenda 20–60 sekondave dhe 69 prerje të pandryshuara. Ky është kontroll algoritmik i kufijve, jo vlerësim njerëzor i tregimit. Videot historike nuk u ndryshuan nga ky kontroll.

Titrat ekzistuese me theksim, titujt sipër, niveli i audios dhe kompozimi 1080×1920 mbeten në përdorim. Main-i i përditësuar e zbaton kontrollin e prerjeve dhe vlerësimin final; workerat duhen përditësuar për të përdorur të njëjtin përmirësim gjatë analizës dhe riparimit. Revisioni i renderimit mbetet 2.

## Çfarë kërkon ende punë

Një video provë 3-minutëshe nuk zbulon momentin më të mirë të krejt VOD-it. Një fushatë reale kërkon material të lejuar dhe punë normale, pasi të zgjidhet hapësira e diskut. Përmirësimet e tjera me vlerë janë kalibrimi i modelit me një mostër të vlerësuar nga operatori, profile të sakta të kamerës për gaming dhe kontrolli i titrave/emrave në gjuhën e burimit.

Testet verifikojnë algoritmin e prerjeve, pamjet e titrave dhe ruajtjen e kontrolleve. Nuk matin viralitetin ose provojnë një rritje të cilësisë në prodhim. Kjo kërkon krahasimin njerëzor të të paktën 30 klipeve me të njëjtat burime. Nuk janë bërë postime reale si pjesë e këtij përditësimi.
