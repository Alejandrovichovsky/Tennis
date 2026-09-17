# Kalibreringslogg

Vad riktig film lärde oss, i den ordning det hände. Varje post: vad vi
såg, varför, vad vi ändrade, vad det gav.

## Film 1: Y-43vDIyiwI (17:16, 1080p30, hardcourt, mulet)

Kamera på stativ i hörnet bakom baslinjen, vidvinkel. Närspelaren blir
enorm när han går mot kameran, bortre spelaren är ~14x5 px i 480-proxyn.
Åskådare bakom staketet till vänster. Grannbanor hörs.

### Körning 1: syntetiskt kalibrerade trösklar

**1 poäng godkänd av 66 kandidater.** 58 förkastade som
`players_not_on_both_sides`.

Orsak: min-arean för spelarblobbar (0.06 % av bilden) var satt för två
lika stora spelare, som i synth. Bortre spelaren låg under. Utan bortre
spelare föll nätlinjen tillbaka till ROI-mitten, mitt i närspelaren, som
då klövs i "huvud = nära" och "ben = bortre". Det var det enda som gav
`spread = 1`.

Ändring: per-sida min-area (0.06 % nära, 0.008 % bortre), sida avgörs av
boxens underkant (fötterna) istället för centroid, nätlinjen ur fötternas
histogram med viktning så att små blobbar räknas.

### Körning 2: perspektivmedveten spelardetektion

**34 poäng, 389 s.** 19 förkastade på `players_not_on_both_sides`, ett
45-sekunderssegment förkastat som `too_long`.

Jämförelse mot ljudet (slagtransienter, oberoende detektor): 7 av 10
ljud-rallyn hittade. Tre missade, alla med bortre spelaren osynlig. Sju
videosegment utan ljud-rally, varav ett granskat bild för bild: spelaren
går och hämtar en boll i 12 s medan bortre står stilla. Falsk positiv.

Slutsats: "spelare på båda sidor" är svag på riktig film. Bortre spelaren
är för liten för att synas pålitligt, men hans slag hörs.

Ändring: ljudkanal. 2-6 kHz-energi vid 100 Hz, minus löpande median,
toppar över 7 robusta sigma räknat på de positiva värdena (median och MAD
på hela signalen kollapsar till 0 eftersom hälften av ramarna är exakt 0).
Slagtäthet in i a(t) med vikt 0.30. Segment utan ett enda slag förkastas.
Slagräkning från ljudet. `too_long` delas i djupaste aktivitetsdalen
istället för att förkastas.

Tröskelvalet, mätt i fyra kända fönster:

| k | tröskel | slag totalt | rally 14-20 s | promenad 25-37 s | dödtid 81-99 s |
|---|---------|-------------|---------------|------------------|----------------|
| 5 | 3.39 | 521 | 12 | 5 | 2 |
| 6 | 3.96 | 365 | 9 | 1 | 1 |
| **7** | **4.54** | **244** | **7** | **0** | **0** |
| 8 | 5.12 | 168 | 5 | 0 | 0 |

### Körning 3: med ljud

**43 poäng, 355 s.** 10 av 10 ljud-rallyn hittade, promenaden borta.
Förkastade: 11 `too_short`, 9 `no_ball_hits`, 1 `camera_moved`.

Granskning av de korta segmenten (3-8 s): fyra riktiga korta poäng (serve
plus returmiss), ett falskt: **studsar före serven**. Bollen mot marken
låter som ett slag. Ligger 1.6 s före poängen, så `merge_gap_s` höjdes
till 2.5 s: studsarna dras in i poängen, som i TennisCut. Riktiga poäng
ligger aldrig närmare varandra än ~5 s.

Bugg hittad: alla klipp fick "Nätspel". Bortre planhalvan är 20 px hög i
proxyn, så bortre spelarens centroid är alltid "vid nätet". Nätspel mäts
nu bara på närspelarens fötter, relativt närmre halvans djup.

Gränser: starter 0-2 s tidiga (backtracking tar med uppkastet, bra),
slut 2-4 s sena (spelaren joggar ut ur bilden, aktiviteten faller
långsamt). TennisCuts recensioner klagar på det motsatta.

### Körning 4: bollspår

36 klipp, 31 av 43 poäng fick spår över tröskeln. Stickprov i ett långt
rally: tre av fyra spår rimliga bollbanor, **ett följde spelarens rygg**
(hackig L-form). Två orsaker:

1. Närspelaren nära kameran är större än max-arean, kastades i grovpasset
   och fick därför ingen exkluderingsbox i bollpasset. Hans kropp blev
   bollkandidater.
2. Confidence vägde längd och täckning lika tungt som jämnhet. En kedja
   längs en kropp är lång och tät.

Ändring: grovpasset behåller blobbar upp till 50 % av bilden (bara
`select_players` begränsar spelare till 6 %), exkluderingsboxar för alla
blobbar över bortre-golvet, confidence = 0.25 längd + 0.55 jämnhet + 0.20
täckning, tröskel 0.65.

Efter: rallyt 201-224 s ger 131 punkter, residual 0.16 px, varje slag en
ren parabel över nätet. Ett spår i 111-113 s visade sig vara bollen som
studsar ut längs sidlinjen efter poängen, tre studsar synliga. Riktigt,
men inte spel.

![Bollkedjor i ett rally](img/ball_chains_real.jpg)

### Kvar att verifiera

- Ett komplett facit. Ljud-rallyn är en andra åsikt, inte sanning.
- Serve-etiketten: `serve_onset` bygger på stillhet före segmentet, men
  studsarna före serven ger nu aktivitet. Troligen behöver den räknas
  från första slaget istället.
- Segmentens slut ligger 2-4 s sena. Kandidat: sluta vid sista slaget +
  1.5 s när ljud finns.
- Bollspår vid bortre baslinjen: bollen är ~3 px där, troligen inget spår.
  Acceptabelt.

## Film 2: GlL6XyTbhLA (TennisCut-promo, 1:25)

Inte en match. Använd för UI-referens, se REFERENCE_VIDEO.md.
