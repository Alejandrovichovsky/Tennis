# Referens: TennisCut

Källa: App Store-/Google Play-beskrivningar och recensioner (tenniscut.com
själv går inte att nå från utvecklingsmiljön). Fyll på när ni provat appen.

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
