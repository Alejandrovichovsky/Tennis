# Filma en match som pipelinen kan använda

Skriven för iPhone 16 Pro på stativ. Ordnad efter hur mycket varje sak
påverkar resultatet, inte efter i vilken ordning du gör den.

## 1. Bildfrekvens före upplösning

**Spela in i 60 fps.** Om du måste välja: `4K60 > 1080p60 > 4K30 > 1080p30`.

Varför 60 fps slår upplösning: vid 30 fps flyttar sig bollen 30–60 px mellan
två bildrutor och blir ett suddigt streck. Ett streck faller på våra
form- och areafilter, så bollen detekteras inte alls — det är den vanligaste
orsaken till att spåret tappas. Vid 60 fps halveras både oskärpan och hoppet
mellan rutorna, vilket gör både detektionen och länkningen mycket lättare.

Format spelar ingen roll: "High Efficiency" (HEVC) är helt okej, appen
konverterar själv en visningskopia. Slåss inte med inställningen.

Kolla utrymmet först. iOS visar MB/min under **Inställningar → Kamera →
Spela in video** — räkna på matchens längd innan du börjar.

## 2. Ställ kameran så högt du kan

Det här är den enskilt viktigaste fysiska detaljen, och den fixar vårt
värsta fel.

Med kameran i ögonhöjd hamnar bortre baslinjen nära horisonten i bild, så
bollen flyger mot **himmel och trädkant**. En gul boll mot ljusgrå himmel
har nästan ingen kontrast, och då ser vår rörelsedetektor ingenting alls.
Det var orsaken till den enda riktigt långa luckan i förra matchen: 6,6
sekunder med sju slag och noll spårning.

Ju högre kameran står, desto mer av bildrutan är banunderlag, och desto
oftare ses bollen mot en kontrastrik yta i stället för mot himlen.

- Finns ett staket eller räcke bakom baslinjen: spänn fast telefonen högt
  upp på det, hellre än att använda stativet i ögonhöjd.
- Annars: dra ut stativet helt.

## 3. Position och bildutsnitt

- **Bakom baslinjen, mitt för mittmarkeringen.** Snett bakifrån funkar,
  sidovy gör att nätlinje-logiken går sönder helt.
- **Hela banan i bild**, inklusive båda baslinjerna med lite marginal.
- **Använd 1x (huvudkameran), inte 0,5x.** Ultravidvinkeln böjer linjerna
  och krymper bortre spelaren till några få pixlar. Får du inte in banan
  på 1x: backa i stället för att gå vidare till ultravidvinkel.
- **Stående telefon fungerar inte.** Liggande.

## 4. Lås det som kan ändra sig

- **AE/AF-lås:** tryck och håll på banan i sökaren tills "AE/AF-lås" visas.
  Utan det jagar telefonen fokus och exponering när spelarna rör sig, och
  vår bakgrundsmodell läser en exponeringsändring som att hela bilden
  ändrats, ungefär som en kamerastöt.
- **Action mode AV.** Den beskär och stabiliserar, alltså simulerar
  kamerarörelse. På stativ är den onödig och direkt skadlig för oss.
- **Rör inte telefonen under matchen.** Varje stöt förkastar det
  segmentet. Behöver du flytta den, gör det mellan game.

## 5. Ljudet är inte valfritt

Pipelinen hittar numera poäng delvis på att den **hör** racketträffarna —
det är så vi klarar att bortre spelaren bara är några pixlar bred. Ett
segment utan ett enda hörbart slag förkastas som dödtid.

- Täck inte mikrofonerna med stativfästet eller ett skal.
- Vind är det som förstör ljudet. Blåser det: ställ telefonen i lä om det
  går, och nämn i så fall att det blåste när du skickar filen.
- Spela in en match, inte en tyst uppvärmning.

## 6. Ström och värme

90 minuter 4K60 tömmer batteriet och värmer telefonen. Blir den för varm
avbryts inspelningen utan förvarning.

- Koppla in en powerbank.
- Ställ den inte i direkt sol. Ta av ett tjockt skal.
- Starta inspelningen **en gång** och låt den gå. Vi behöver sammanhängande
  tidsstämplar; starta och stoppa mellan game ger oss en hög lösa filer.

## 7. Filma 20 sekunder tom bana först

Starta inspelningen innan ni går in på banan och låt den gå på tom bana i
~20 sekunder. Bakgrundsmodellen får då en ren start innan spelarna kommer
in i bild. Kostar ingenting och gör de första poängen bättre.

## 8. Extra: fem minuters kalibreringsklipp

Om du har tid, filma ett separat kort klipp med kända situationer. Det är
värt mer per minut än matchen själv, för då kan jag mäta exakt vad som går
fel i stället för att leta:

1. **10 servar i rad** (mot tom bana går bra) — kalibrerar servedetektionen.
2. **3–4 lobbar** — det fall där bollen går mot himlen och vi tappar den.
3. **Ett kort växlingsspel nära nätet** — nätspelsetiketten sätts för ofta
   idag.
4. **Gå och plocka bollar i ett par minuter, utan att slå** — vår viktigaste
   falska positiv. Jag vill se att den förkastas.

Säg vilken sekund varje del börjar, ungefär, så räcker det.

## 9. Skicka filen

GitHub Releases tar **max 2 GB per fil**, så hela matchen i 4K60 får inte
plats. Gör så här:

```bash
# hela matchen behåller du själv och kör i webbappen
# till mig: ett representativt utdrag på 10–15 min
ffmpeg -ss 00:20:00 -i match.mov -t 900 -c copy utdrag.mp4

# och kalibreringsklippet i original
gh release create footage-v2 utdrag.mp4 kalibrering.mov \
  -R Alejandrovichovsky/Tennis -t "Match 2" \
  -n "4K60, hardcourt, mulet, stativ på staket bakom baslinjen"
```

Skriv underlag, väder, tid på dygnet och kamerahöjd i release-texten. Det
är de variabler som avgör vad som går fel.

## Snabbchecklista

- [ ] 60 fps valt
- [ ] Utrymme och powerbank
- [ ] Telefonen så högt som möjligt, bakom baslinjen, liggande, 1x
- [ ] Hela banan i bild
- [ ] AE/AF-lås på, Action mode av
- [ ] Mikrofonerna fria
- [ ] Inspelning startad på tom bana, 20 s före
- [ ] En enda sammanhängande fil
