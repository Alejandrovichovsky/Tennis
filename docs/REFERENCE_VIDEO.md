# Referens: TennisCut

Källor: TennisCuts promovideo (youtu.be/GlL6XyTbhLA, 1:25), App Store-/
Google Play-texter och recensioner. tenniscut.com går inte att nå från
utvecklingsmiljön.

## Vad promon visar, bild för bild

| Tid | Skärm | Vad det betyder för oss |
|-----|-------|-------------------------|
| 0:26 | Stativ i hörnet bakom baslinjen, hela banan i bild, grus, sol | samma uppställning som vi antar |
| 0:37 | "Recording in Progress 00:02:07" i appen | inspelning i appen, med räknare |
| 0:58 | Uppspelning med "0.5x Speed"-knapp | slow motion-uppspelning, inte en overlay |
| 0:64 | "Date Filter", kalender | bibliotek filtrerat på datum |
| 0:67 | Hem: flikarna All / **Matches / Serve Practice / Rally / Ball...** | kategorierna är *sessionstyper*, inte per-klipp-etiketter |
| 0:70 | **"Video Insights": lista "Point 25, 3:06 - 3:27, 0:21", "Point 22, 2:39 - 2:57, 0:18", ...** sorterad på längd | det är hela deras "insight": poäng rankade efter längd, med tidsstämplar |
| 0:79 | "Trim Video" med handtag, Start/Duration/End, Export | manuell finjustering av ett klipps gränser |
| 0:82 | "Success! Saved to gallery", Share | export till Bilder + dela |

Tre saker att ta med:

1. **Ranking = längd.** "Video Insights" är en lista över poäng sorterad på
   varaktighet. Vår score väger längd 22 %. Ett läge "sortera på längd" är
   trivialt och bör finnas som default i listan, med vår score som
   alternativ sortering.
2. **Kategorier är per session**, inte per poäng: Match, Serveträning,
   Rally, Boll(maskin?). Våra per-poäng-etiketter (serve, vinnande slag...)
   har ingen motsvarighet hos dem. Behåll, men gör dem mindre framträdande.
3. **Trim-handtag per klipp** är den enda manuella redigering de erbjuder.
   Vi har det via `selection.json` (start_s/end_s) men inte i UI:t. Bör in
   på tidslinjen.

Ingen bollbana syns i promon trots att den marknadsförs i Pro.

## Vad TennisCut gör

| Funktion | Hos dem | Hos oss (webapp) |
|----------|---------|------------------|
| Kärnlöfte: "två timmar på banan blir två minuter rallyn" | ja, det är produkten | ja, `select_mode: all` är default |
| Klipper bort dödtid: väntan, servar mellan rallyn, bollplock, pauser | ja, on-device | ja |
| Rally-till-rally-navigering | ja (Pro) | ja: korten i listan hoppar och spelar exakt det klippet |
| Export av enskild poäng | ja (Pro) | ja: varje klipp är en egen fil |
| Bollbana ("Ball Trajectory") | ja (Pro) | ja, bara när confidence räcker |
| Video Insights (statistik) | ja (Pro), oklart exakt vad | report.md: antal poäng, längd, slag, tempo per poäng |
| 4K-inspelning | ja (Pro) | n/a, vi analyserar; 4K-import går men proxyn gör jobbet |
| Fjärrkontroll: en telefon filmar, en styr | ja | nej, inte i scope |
| Coach Eye: bild-för-bild-granskning | ja | nej; kan bli nästa steg i spelaren (`,`/`.`) |
| Delning till Instagram/WhatsApp/TikTok | ja | nej, fil på disk |
| Highlight-kategorier (serve, forehand...) | **nämns inte** | serve/winner/lång duell/nätspel/snabbt utbyte |
| Topp-N-urval / ranking | **nämns inte** | ja, valfritt läge |
| On-device / privat | ja, tydligt marknadsfört | ja, lokalt |
| Pris | gratis + Pro-abonnemang (längre matcher, HD-export, snabbare) | n/a |

Slutsats: TennisCut är i första hand ett *dödtidsfilter*, inte en
highlight-rankare. Vår ranking och kategorisering är utöver referensen. Det
gör "alla rallyn" till rätt default och topp-N till ett tillval.

## Recensionerna säger

- Den enda konkreta kritiken: **klippen börjar för tidigt och slutar 1-2 s för
  tidigt.** Det är exakt vår `pre_roll_s`/`post_roll_s` och segmentets
  slutgräns. Vi ligger på 2.0 s före och 2.5 s efter, och sluttiden sätts
  efter hysteres + `min_gap_s` dröjsmål, så vi bör snarare ha problemet
  "slutar för sent". Mät på riktig film.
- Folk uppskattar att originalet behålls. Vi rör aldrig källfilen.

## Öppna frågor att svara på när ni provat appen

- Hur lång marginal använder de i praktiken? Filma samma poäng, jämför.
- Ritar de bollbanan som streck, prickar eller glöd? Visar de något när
  bollen tappas?
- Vad ingår i "Video Insights"?
- Hur många rallyn per timme ger deras filter på en riktig match, och hur
  många ger vårt på samma fil? Det är vårt precision/recall-mått.

Källor: [App Store](https://apps.apple.com/us/app/tennis-cut/id6754323620),
[Google Play](https://play.google.com/store/apps/details?id=com.tenniscut.mobile),
[mwm.ai](https://mwm.ai/apps/tennis-cut/6754323620).
