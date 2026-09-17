# Referensvideo: checklista

Ingen referensvideo fanns bifogad när det här skrevs, så listan nedan är
vad vi *antagit* att appen i referensen gör, utifrån kravlistan. Fyll i
kolumnen "I referensen?" när ni tittat på den, och justera prioriteringen
om något avviker.

| # | Funktion | Antagen | I referensen? | Vår MVP |
|---|----------|---------|---------------|---------|
| 1 | Spela in match i appen | ja | | iOS: M1. Desktop: n/a |
| 2 | Importera från Bilder | ja | | `tennishl analyze fil` |
| 3 | Statisk kamera, hela banan | ja | | antaget överallt |
| 4 | Analys efter matchen med progress | ja | | ja, stage-progress |
| 5 | Klipper bort dödtid mellan poäng | ja | | ja |
| 6 | Automatiska highlights (topp-N) | ja | | ja, `--top` |
| 7 | Kategorier per klipp | ja (serve, forehand, backhand, rally, winner?) | | serve/winner/long_rally/net_play/fast_exchange. **Inte** forehand/backhand. |
| 8 | Lista där klipp kan slås av/på | ja | | review.html + selection.json |
| 9 | Färdig sammanhängande video | ja | | highlights.mp4 |
| 10 | Bollspår i klippen | ja, valbart | | ja, bara vid hög confidence |
| 11 | Exportera/dela | ja | | fil på disk |
| 12 | Poäng-/matchställning i overlay | okänt | | nej |
| 13 | Statistik (antal slag, längsta rally) | okänt | | finns i report.md |
| 14 | Slow-motion på vinnande slag | okänt | | nej, enkelt att lägga till i ffmpeg-steget |
| 15 | Musik/övergångar | okänt | | fade in/ut |
| 16 | Spelarnamn / "vem vann poängen" | okänt | | nej |

## Saker att titta efter i referensen

- Hur lång marginal före/efter poängen? Vår default är 2.0 / 2.5 s.
- Klipps servebollen med, eller börjar klippet vid uppkastet?
- Hur många klipp för en timmes match? Styr `max_highlights`.
- Är bollspåret ett streck, prickar, eller en glödande kurva? Vi ritar
  ett streck som tjocknar mot bollen, 0.45 s långt.
- Visar de något när bollen *inte* hittas? Vi visar inget.
- Om de har forehand/backhand: ser det ut att stämma? Det avgör om pose
  hamnar på roadmapen.
