# Milstolpar

Status per 2026-09-17. "Desktop" = Python-pipelinen i repot, "iOS" = appen.

## M1: Import/inspelning + spelare + export av manuella tidsstämplar

| | Desktop | iOS |
|-|---------|-----|
| Importera video | `tennishl analyze fil.mp4` | PhotosPicker → `PHAsset` → `AVURLAsset` |
| Spela in i appen | n/a | `AVCaptureSession` 1080p30, låst exponering/fokus, stativläge |
| Videospelare | review.html + clips/ | `VideoPlayer` (AVKit) med markörer |
| Manuella klipp → export | `selection.json` med `start_s`/`end_s` → `tennishl render` | `AVMutableComposition` + `AVAssetExportSession` → `PHPhotoLibrary` |

Desktop: **klart.** iOS: se `IOS_PLAN.md`, ~2-3 dagar.

## M2: Automatisk detektion av aktivt spel, bortklippt dödtid

Desktop: **klart.** `observe → court → activity → segmentation`. Verifierat
på syntetisk film (5/5 poäng, ±1 s) och tester. Inte verifierat på riktig
film - det är det första vi gör när vi har en.

Kvar: kalibrera `segmentation`-trösklar på 3-5 egna matcher. Mät:
recall (andel riktiga poäng som hittas), precision (andel segment som är
riktiga poäng), gränsfel i sekunder.

## M3: Highlight-scoring

Desktop: **klart.** Viktad summa, bidrag per feature loggade.

Kvar: vikterna är gissade. Efter 3-5 matcher: låt båda titta igenom alla
segment i review.html, markera "hade jag tagit med den?", och jämför med
rankingen. Om korrelationen är dålig: logistisk regression på samma features.

## M4: Kategorisering

Desktop: **klart (ungefärligt).** serve / winner / long_rally / net_play /
fast_exchange / baseline_rally. Forehand/backhand medvetet utelämnat.

Kvar om vi vill ha forehand/backhand: `VNDetectHumanBodyPoseRequest` på
10 fps i valda fönster, slagsida = vilken sida om kroppen handleden är vid
hastighetstoppen. Bara på närmsta spelaren (bortre är för liten). Uppskattat
2-3 dagar, och kräver M6-data för att veta om det fungerar.

## M5: Bollspårning + trail

Desktop: **klart.** Differens + temporal länkning + confidence + stitching.
Spår ritas bara över tröskel. Positioner sparas i `highlights.json`.

Kvar: verkligheten. Syntetisk boll är en perfekt gul disk utan blur.
Förväntad utfall på riktig film: spår i kanske hälften av klippen till att
börja med. Första åtgärder om det är sämre: `diff_threshold` ned, HSV-prior
för gul boll som viktning, `max_area_px` upp för blur-streck.

## M6: Precision på egna matcher

Inte påbörjat. Behöver:

1. 3-5 matcher, 1080p30, stativ bakom baslinjen, hela banan i bild, olika
   ljus (sol, moln, hall).
2. Facit: en person tittar igenom och skriver ner start/slut för varje poäng
   i en JSON som `synth.py` producerar (`[{start_s, end_s, shots}]`).
3. Ett litet skript `tennishl eval match.mp4 truth.json` som rapporterar
   recall/precision/gränsfel och slag-räkningsfel. (Inte skrivet än, ~1 h.)
4. Iterera trösklar. Allt är i `config.py`, körs om på sparade observationer
   utan att avkoda igen.

## Efter MVP, i prioritetsordning

1. **Ljud-onsets för slagräkning.** Billigt, troligen den största
   precisionsvinsten för `shot_count`.
2. **Riktningsbyten i a(t)** för att skilja jogg från rally.
3. **Vision person-detektion** istället för blobbar (iOS-native, gratis).
4. **Poäng-/gameräkning** ur pauslängder (sidbyte var udda game) - ger
   "matchbollen" som highlight.
5. **Dubbel.**
6. **Pose → forehand/backhand.**
