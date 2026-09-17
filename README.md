# tennishl - automatiska tennis-highlights

Lägg en iPhone på stativ bakom banan, filma hela matchen, mata in filen och
få tillbaka en highlight-video plus feedback om varje klipp. Ingen manuell
genomtittning av två timmar råfilm.

Det här repot innehåller:

| Del | Status |
|-----|--------|
| `tennishl/` | **Körbar desktop-pipeline (Python).** Hela kedjan video -> poäng -> ranking -> klipp -> montage. Detta är MVP:n. |
| `tests/` | Enhetstester för pipeline-logiken + långsamma end-to-end-tester på syntetisk video. |
| `docs/` | Arkitektur, risker, milstolpar och iOS-portningsplan (AVFoundation/Vision). |

Prioritering just nu: **den ska fungera på datorn först.** iOS-appen är en
port av samma steg och beskrivs i `docs/IOS_PLAN.md`.

## Kom igång

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # numpy, opencv-python-headless, imageio-ffmpeg
pytest -q                   # snabba tester (< 1 s)
pytest -q -m slow           # end-to-end på syntetisk video (~1-2 min)
```

`imageio-ffmpeg` drar med sig en statisk ffmpeg-binär, så inget mer behöver
installeras. Finns `ffmpeg` i PATH används den istället.

### Kör på en riktig match

```bash
tennishl analyze match.mp4 -o match_out/
```

Efter körningen finns i `match_out/`:

| Fil | Vad |
|-----|-----|
| `highlights.mp4` | Färdigt montage av valda klipp (med fade in/ut, ljud bevarat). |
| `clips/NN_hXXX_SSSSSs.mp4` | Varje klipp separat, rankordning i filnamnet. Bollspår inritat när confidence räcker. |
| `report.md` | Tabell med tid, längd, score, kategori och en rad feedback per klipp. |
| `review.html` | Öppna i webbläsaren: tumnaglar, bocka i/ur klipp, ladda ner `selection.json`. |
| `selection.json` | Vilka klipp som ingår i montaget. Redigera för hand eller via review-sidan. |
| `highlights.json` | Allt om varje poäng: features, bidrag till score, bollpositioner, confidence. |
| `analysis.json` | Debug: banmodell, trösklar, aktivitetssignalen, alla förkastade segment med orsak. |

Ändra urvalet och rendera om (klipp som redan finns återanvänds, montaget
byggs på sekunder):

```bash
tennishl render match_out/
```

Bra flaggor:

```bash
tennishl analyze match.mp4 -o out/ --top 8 --pre 1.5 --post 3   # färre klipp, annan marginal
tennishl analyze match.mp4 -o out/ --max-seconds 600             # testa på första 10 min
tennishl analyze match.mp4 -o out/ --no-ball                     # hoppa över bollspårning
tennishl analyze match.mp4 -o out/ --config tuning.json          # egna trösklar
tennishl debug match.mp4 -o out/debug/                           # signalplot + annoterad video
tennishl synth demo.mp4 --seconds 90                             # syntetisk testfilm
```

`tuning.json` är en partiell version av `Config` i `tennishl/config.py`, t.ex.
`{"segmentation": {"min_duration_s": 3.0}, "clip": {"max_highlights": 6}}`.

### Testa utan riktig film

```bash
tennishl synth demo.mp4 --seconds 90
tennishl analyze demo.mp4 -o demo_out/
open demo_out/review.html
```

Den syntetiska filmen är inte tennis, den är *det pipelinen ser*: statisk
bana, två spelarblobbar som rör sig snabbt under poäng och sakta mellan dem,
en liten ljus boll som flyger i bågar, sensorbrus. Facit hamnar i
`demo.truth.json`.

På den filmen hittar pipelinen 5 av 5 poäng inom ±1 s, räknar slagen ur
bollspåret rätt (11/11, 11/11, 3/3, 4/4) och producerar en falsk positiv
där spelarna joggar mellan två poäng (den hamnar sist i rankingen).

![Bollspår i ett klipp](docs/img/trail_demo.jpg)

`tennishl debug` ger signalplotten som är första stället att titta när ett
klipp blev fel: gul = a(t), blå = spelarhastighet, grå = förgrund, grön/röd
linje = start-/stopptröskel, gröna fält = hittade poäng.

![Aktivitetssignal](docs/img/signal_demo.jpg)

## Hur det fungerar (kort)

```
video --> proxy (480px, 10 fps) --> bakgrundsmodell --> rörelseblobbar per bild
      --> banmodell (ROI + nätlinje ur ackumulerad rörelse)
      --> aktivitetssignal a(t) = rörelsemängd + spelarhastighet + "båda sidor"
      --> hysteres-tröskling --> rallysegment (+ förkastade med orsak)
      --> features per rally (längd, slag, tempo, täckning, avslut, serve-start)
      --> viktad score --> ranking --> kategorier
      --> bollspårning (960px, alla frames) bara i valda fönster
      --> klipp med marginal --> montage
```

Varje steg är en funktion på vanlig data, ingen delad state. Segmentering,
features och scoring är ren numpy utan OpenCV: de är testade på syntetiska
signaler och är det som portas rakt av till Swift.

Den viktigaste designidén: **hastighet bär mer information än rörelsemängd.**
Att "något rör sig på banan" skiljer knappt rally från promenad. Att någon
sprintar och byter riktning gör det. Därför väger spelarnas hastighet tyngst
i a(t), och antalet hastighetstoppar blir vår uppskattning av antal slag när
bollen inte går att lita på.

Detaljer, val av heuristik kontra ML och riskerna: `docs/ARCHITECTURE.md`.

## Vad den kan och inte kan

Kan:
- Hitta när poäng börjar och slutar, klippa bort väntan, sidbyten, bollplock.
- Ranka poäng efter längd, tempo, slagfrekvens, täckning och avslut.
- Sätta ungefärliga etiketter: serve, vinnande slag, lång duell, snabbt utbyte, nätspel.
- Spåra bollen och rita spår när banan är stabil och bollen syns; annars avstå.
- Förklara varje val: alla siffror och trösklar finns i JSON.

Kan inte (än):
- Skilja forehand från backhand. Det kräver pose-estimering (`docs/ROADMAP.md`).
- Dubbel: spelarlogiken antar en spelare per planhalva.
- Kamera som panorerar eller zoomar. Kamerarörelse förkastar segmentet.
- Hjälpa till om bollen är mindre än ~6 px i 1080p (för långt bort).

Alla trösklar är kalibrerade på syntetisk film. Steg ett med riktig film
är att köra `tennishl debug` och titta på `signal.png`.

## Repo-layout

```
tennishl/
  config.py          alla trösklar, laddbara från JSON
  types.py           datastrukturer (även JSON-format)
  pipeline.py        orkestrering, progress, filer ut
  cli.py             kommandon
  synth.py           syntetisk testfilm
  video/             ffmpeg-wrapper, proxy-läsare
  analysis/
    observe.py       grovpass: bakgrundssubtraktion -> blobbar per bild
    court.py         ROI + nätlinje
    activity.py      a(t)
    segmentation.py  rally in/ut (ren numpy)
    features.py      per rally
    scoring.py       score, kategorier, padding, merge
    ball.py          kandidater + temporal länkning
    signals.py       normalisering, peaks, utjämning
  render/
    clips.py         klippning, bollspår-overlay
    montage.py       concat
    review.py        rapport, review.html, tumnaglar
    debug.py         signalplot, debugvideo
tests/
docs/
```
