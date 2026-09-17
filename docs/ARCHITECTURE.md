# Arkitektur

Målet med MVP:n: en pipeline som är *robust* snarare än *smart*. Varje steg
gör en sak, tar in vanlig data och lämnar ifrån sig vanlig data, och loggar
tillräckligt för att vi ska kunna se varför ett klipp blev fel.

```
                     ┌──────────────┐
  match.mp4 ───────► │ video/reader │  proxy: 480 px @ 10 fps (grovpass)
                     └──────┬───────┘         960 px @ native fps (bollpass, bara i fönster)
                            ▼
                  ┌──────────────────┐
                  │ analysis/observe │  MOG2-bakgrund → förgrundsmask → blobbar per bild
                  └────────┬─────────┘  + rörelsekarta + kamerarörelse
                           ▼
        ┌───────────────┐  ┌─────────────────┐
        │ analysis/court│  │ analysis/activity│  ROI + nätlinje; a(t)
        └───────┬───────┘  └────────┬────────┘
                └──────────┬────────┘
                           ▼
                ┌────────────────────────┐
                │ analysis/segmentation  │  hysteres → rallyn + förkastade (orsak)
                └───────────┬────────────┘
                            ▼
                ┌────────────────────────┐
                │ analysis/features      │  längd, slag, tempo, täckning, avslut, serve
                └───────────┬────────────┘
                            ▼
                ┌────────────────────────┐
                │ analysis/scoring       │  viktad summa → rank → kategorier → padding
                └───────────┬────────────┘
                            ▼
                ┌────────────────────────┐
                │ analysis/ball (valfri) │  3-frame diff → kandidater → temporal länkning
                └───────────┬────────────┘  → confidence; bara i valda fönster
                            ▼
                ┌────────────────────────┐
                │ render/clips, montage  │  ffmpeg-klipp med marginal → concat
                │ render/review          │  report.md, review.html, tumnaglar
                └────────────────────────┘
```

## Stegen, och varför de ser ut som de gör

### 1. Proxy (`video/reader.py`)

Analys sker aldrig i 1080p. Grovpasset läser var tredje bild (30 → 10 fps)
och skalar till 480 px bredd. `VideoCapture.grab()` hoppar över bilder utan
att färgkonvertera, så de överhoppade bilderna kostar en bråkdel.

Bollpasset körs på 960 px och *alla* bilder, men bara i de fönster vi redan
valt ut (typiskt 10-15 klipp à 10-20 s av en 90-minutersmatch).

Varför två upplösningar: en boll i 1080p är 8-15 px. På 480 px är den 2-4
px, vilket är brus. På 960 px är den 4-8 px, vilket räcker för
differensbaserad detektion.

### 2. Observationer (`analysis/observe.py`)

Enda steget som avkodar hela filen. Per samplad bild sparar vi:

- `fg_ratio` andel förgrund (från MOG2 med ~12 s historik)
- `camera_motion` andel pixlar som ändrats globalt (stativ-stöt / scenklipp)
- upp till 12 blobbar med area och aspect inom rimliga gränser för människor

Aldrig pixlar. Två timmar blir ~72 000 små poster. Alla senare steg kan
köras om i minnet medan man justerar trösklar.

MOG2-historiken på ~12 s är ett medvetet val: en spelare som står stilla
absorberas i bakgrunden. Vi vill mäta *rörelse*, inte *närvaro*.

### 3. Banmodell (`analysis/court.py`)

Ingen linjedetektion. Grus, hardcourt, inomhus och strålkastare ger helt
olika kontrast, medan "var rörde sig saker under två timmar" är stabilt på
stativ. ROI = bounding box (1:a-99:e percentilen) av de hetaste pixlarna i
rörelsekartan. Nätlinjen = dalen mellan de två moderna i histogrammet av
blobbarnas y-koordinat (spelare nära kameran hamnar lågt i bild, bortre
spelaren högt).

Confidence rapporteras. Om vi bara ser en mod (en spelare, eller dubbel)
faller vi tillbaka till ROI-mitten, vilket ändå är ungefär där nätet är.

### 4. Aktivitetssignal (`analysis/activity.py`)

```
a(t) = 0.35 · n(fg_ratio) + 0.45 · n(spelarhastighet) + 0.20 · båda_sidor
```

`n()` är percentilbaserad normalisering (20:e-92:a), vilket gör att samma
kod fungerar i solig grus-miljö och i mörk hall utan omkalibrering.

Hastigheten är den viktigaste termen. "Något rör sig på banan" skiljer
knappt rally från promenad, men "någon sprintar och byter riktning" gör det.
Hastigheten mäts som största centroid-förflyttning per sekund bland de två
spelarblobbarna, i enheten banhöjder/s så att den är oberoende av
upplösning. Matchning mellan bilder är närmaste-granne med en gräns; om en
blob hoppar orimligt långt rapporteras hastighet 0 istället för en falsk
sprint.

### 5. Segmentering (`analysis/segmentation.py`)

Ren numpy, ingen video. Två idéer:

- **Hysteres.** Starttröskel högre än sluttröskel, båda relativa till videons
  egen dynamik (20:e resp 95:e percentilen). En termostat.
- **Backtracking av start.** Signalen passerar starttröskeln mitt i första
  slaget, inte vid uppkastet. När vi triggat går vi bakåt till där signalen
  senast lämnade sluttröskeln.

Sedan: brygga över avbrott < 1.2 s, kräv 2.5-45 s längd, kräv spelare på
båda planhalvor i ≥ 45 % av bilderna, förkasta segment med kamerarörelse.
Alla förkastade segment loggas med orsak (`too_short`, `too_long`,
`players_not_on_both_sides`, `camera_moved`).

Segment längre än 45 s förkastas hellre än trunkeras. De är nästan alltid
uppvärmning, och ett trunkerat uppvärmningssegment skulle annars vinna på
längd.

### 6. Features (`analysis/features.py`)

| Feature | Hur | Varför den korrelerar med "kul att se" |
|---------|-----|----------------------------------------|
| `duration_s` | segmentlängd | långa poäng |
| `shot_count` | bollens riktningsbyten i y (+1) om bollspår finns, annars toppar i hastighetssignalen | fler slag |
| `shot_rate` | slag/s | snabba utbyten |
| `mean/peak_intensity` | a(t) i segmentet | tempo |
| `coverage` | spelarnas x-spridning / ROI-bredd | duell hörn till hörn |
| `finish` | topp i sista 1.5 s relativt medel + stillhet 2 s efter | vinnande slag slutar med smäll och sedan stopp |
| `serve_onset` | stillhet 2 s före + brant stigning första 1.5 s | serve-start |
| `net_approach` | minsta avstånd till nätlinjen | nätspel |

Från bakom baslinjen byter bollen riktning i bild-y vid varje slag men inte
vid studsen (en boll som flyger bort fortsätter uppåt i bild genom studsen).
Därför `slag ≈ riktningsbyten + 1`. Grovt, men monotont, och monotont är
allt rankingen behöver.

### 7. Scoring (`analysis/scoring.py`)

Viktad summa med mättnadskurvor (`v / (v + k)`), normaliserad till 0-1.
Varje features bidrag sparas i `highlights.json`. Ingen modell, med flit:
när ett klipp ser fel ut ska vi kunna se vilken term som drev upp det.

Kategorier är etiketter för UI:t, inte tennisanalys, max två per klipp:
`serve` (tydlig start, ≤ 3 slag), `winner` (tydligt avslut), `long_rally`
(≥ 8 slag), `net_play`, `fast_exchange`, annars `baseline_rally`.
Forehand/backhand är ärligt talat utom räckhåll utan pose-estimering.

### 8. Bollspårning (`analysis/ball.py`)

Helt separat modul. Returnerar den inget blir det inget spår, inget annat
påverkas.

- **Kandidater:** `min(|f_t − f_{t−1}|, |f_{t+1} − f_t|)` behåller bara det
  som rörde sig *in* till en position och sedan *ut*. Statisk bakgrund ger
  noll, en långsam spelare ger en tunn kontur, en boll ger en kompakt blob.
  Filter på area, aspect, fyllnadsgrad och spelarboxar (från grovpasset).
- **Länkning:** kedjor med konstant-hastighets-prediktor, upp till 3 bilders
  lucka. Girigt, en kedja i taget.
- **Confidence:** 0.4·längd + 0.4·exp(−residual/6 px) + 0.2·täckning, där
  residualen är RMS från lokala kvadratiska anpassningar. En boll följer
  fysik; ett flimmer på staketet gör det inte.
- **Stitching:** alla kedjor över tröskeln i tidsordning, överlapp trimmas,
  luckor förblir luckor. Vi interpolerar aldrig över en kedjegräns.

Spår ritas bara om confidence ≥ 0.55. Under det står det "inget bollspår" i
rapporten tillsammans med den faktiska siffran.

### 9. Klippning (`render/`)

Marginal 2.0 s före, 2.5 s efter. Överlappande paddade fönster delas vid
mittpunkten av dödtiden; poäng med < 2.5 s mellan sig blir ett klipp (det
högre rankade överlever). Klipp kodas om med libx264 för bildexakta snitt
(stream-copy snappar till keyframes, flera sekunder fel på mobilfilm).
Montaget concat:as utan omkodning, så omrendering efter ändrat urval tar
sekunder.

Med bollspår: OpenCV avkodar i full upplösning, ritar, pipar råa bilder till
ffmpeg, och originalljudet muxas in efteråt.

## Heuristik eller ML?

| Uppgift | MVP | Motivering | När ML |
|---------|-----|------------|--------|
| Aktiv poäng / dödtid | heuristik | signalen är stark, självkalibrerande, förklarbar | om falska positiva från bollplock/uppvärmning blir ett problem: en liten klassificerare på feature-fönster |
| Spelardetektion | blobbar | två personer, fast kamera, känd geometri | dubbel, eller rörig bakgrund: Vision person-detektion (iOS) / YOLO-nano |
| Banmodell | rörelsekarta | robust mot underlag | aldrig, troligen |
| Slagräkning | riktningsbyten / hastighetstoppar | monotont räcker för ranking | forehand/backhand kräver pose |
| Boll | differens + fysik | liten, snabb, suddig: utseende är inte nog, rörelse är | TrackNet-liknande nät om vi vill ha spår i svåra ljus |
| Highlight-score | viktad summa | 6 features, tydliga bidrag | när vi har ≥ 200 egna klipp med "bra/inte bra"-etikett: en logistisk regression på samma features |

Principen: ML bara där heuristiken bevisligen inte räcker på våra egna
matcher, och då på samma features så att bidragen förblir läsbara.

## Största tekniska riskerna

1. **Bollen.** 8-15 px, 100-200 km/h, motion blur gör den till ett streck i
   bakgrundens färg. Vår differensmetod tappar den mot ljusa fonder, i
   motljus och när den passerar spelaren. Mitigering: vi kräver hög
   confidence och avstår hellre. Mätning: andel klipp med spår + manuell
   koll av 20 slumpade spår per match.
2. **Falska positiva.** Bollplock i jogg, uppvärmning, en spelare som
   springer efter en boll som studsat bort. Uppvärmning fångas av 45 s-taket.
   Jogg får låg score (få riktningsbyten) och hamnar sist, men blir ett
   klipp om vi tar med för många. Mitigering: `--top`, review-sidan, och en
   "riktningsbyten per sekund"-term i a(t) om det behövs.
3. **Ljus.** Moln, skymning, strålkastare som tänds. MOG2:s 12 s-historik
   följer med, percentilnormaliseringen gör trösklarna relativa. En plötslig
   global förändring ser ut som kamerarörelse och förkastar segmentet, vilket
   är rätt beteende.
4. **Långa filer.** 2 h @ 1080p30 = 216 000 bilder. Grovpasset körde 90 s
   syntetisk film på 4 s (≈ 20× realtid på en laptop-kärna); 2 h blir ~6
   min. Bollpasset bara i fönster. Om det inte räcker: sänk `coarse_fps` till
   6, det påverkar segmenteringen marginellt.
5. **Kameravinkel.** Allt antar en vy där nätet är horisontellt i bild och
   spelarna separeras i y. Sidovy bryter nätlinje-logiken (spelarna separeras
   i x). Enkelt att generalisera senare; MVP:n antar bakom/snett bakom.
6. **Dubbel.** En blob per planhalva. Fyra spelare ger fel hastighet
   (närmaste-granne-matchning hoppar mellan personer). Behöver riktig
   multi-object tracking.
7. **Ljud.** Vi använder det inte, men slagljudet är en oerhört tydlig signal
   för slagräkning. Låg kostnad att lägga till: onset-detektion på
   ljudspåret, korrelerat med hastighetstopparna.

## Debug-arbetsflöde på riktig film

1. `tennishl debug match.mp4 -o dbg/ --max-seconds 900` → `signal.png`.
   Gul kurva är a(t), grön/röd linje är trösklarna, gröna fält segment,
   blåa fält förkastade. Ser kurvan "rätt" ut men segmenten fel: justera
   `segmentation`. Ser kurvan fel ut: titta på `debug.mp4`.
2. `debug.mp4` visar ROI, nätlinje, spelarboxar och a(t) per bild. Vanliga
   fel: ROI för stor (åskådare räknas), nätlinje fel (en spelare syns inte),
   blobbar som är skuggor.
3. `analysis.json` → `rejected` med orsak. `too_short` i mängder betyder
   för hög `exit_frac`; `players_not_on_both_sides` betyder fel nätlinje
   eller för strikt `require_both_sides_frac`.
4. `highlights.json` → `contributions`. Fel klipp högst upp: vilken term?

## Prestandabudget (laptop, en kärna, syntetisk 1280x720)

| Steg | Tid per minut video |
|------|--------------------|
| grovpass | ~3 s |
| bana + aktivitet + segmentering | < 0.1 s |
| bollpass | ~1 s per klipp-sekund (bara valda fönster) |
| klippning | ~1× klipplängd (libx264 veryfast) |

Riktiga 1080p-filer avkodar långsammare än syntetisk mp4v. Räkna med 2-3×.
