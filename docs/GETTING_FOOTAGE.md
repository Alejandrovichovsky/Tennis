# Skaffa riktig film

**Filmar du själv?** Läs `FILMING.md` i stället — kamerainställningarna
avgör mer än något vi kan göra i koden efteråt.


Utvecklingsmiljön där koden skrivs når bara GitHub och PyPI. YouTube,
tenniscut.com, arkiv och stock-sajter är blockerade på nätverksnivå. Så
filmen måste hämtas på en vanlig dator och läggas där pipelinen kan nå den.

## Vad vi letar efter

Kameran ska stå **stilla, bakom baslinjen, med hela banan i bild**. Det är
antagandet i banmodellen (nätet horisontellt, spelarna åtskilda i höjdled).
Bra sökningar på YouTube:

- `tennis match full unedited fixed camera`
- `tennis practice match raw footage behind baseline`
- `club tennis match full 1080p` (klubbkanaler laddar ofta upp oredigerat)
- `tennis "full match" 4.5 NTRP` / `UTR 6 full match`
- svenska: `tennis match hel match klubb`, `seriematch tennis`

Undvik: TV-sändningar (kameran zoomar och klipper), telefonfilm från
läktaren (skakar), sidovinkel, matcher där halva banan är utanför bild.

Bra att ha variation: sol med hårda skuggor, mulet, inomhus med
strålkastare, grus och hardcourt, en dubbelmatch (vi vet att den går fel,
vi vill se *hur*).

## Ladda ner

```bash
pip install yt-dlp
# 1080p mp4 med ljud, H.264 så att webbläsaren kan spela den
yt-dlp -f "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[height<=1080]" \
       --merge-output-format mp4 -o "match_%(id)s.mp4" "<url>"
```

Om videon bara finns i VP9/AV1 (vanligt på YouTube) spelar Chromium den
ändå, men klipp/montage blir H.264 oavsett. Vill du ha H.264-källa:

```bash
ffmpeg -i match_x.mp4 -c:v libx264 -preset veryfast -crf 20 -c:a aac match_x_h264.mp4
```

Från iPhone: exportera som "Mest kompatibel" (H.264) eller låt webappen
göra en 720p-förhandsvisning automatiskt (HEVC spelas inte i Chromium).

## Lägg den där jag når den

Repot är publikt, GitHub Releases tar filer upp till 2 GB:

```bash
gh release create footage-v1 match_x.mp4 match_y.mp4 -t "Testfilm" -n "oredigerade matcher"
```

Eller ett 3-5 minuters utdrag (< 100 MB) direkt i repot:

```bash
ffmpeg -ss 00:10:00 -i match_x.mp4 -t 300 -c copy samples/match_x_10min.mp4
git add samples/ && git commit -m "sample footage" && git push
```

Skriv i commit/release vilken vinkel, underlag och ljus det är.

## Märk facit

Kör `tennishl serve`, öppna matchen, klicka "Märk poäng", spela och tryck
`I` när servrörelsen börjar och `O` när bollen är död. Spara. Recall,
precision och gränsfel visas direkt. 15-20 minuter för en timmes match.
Facit hamnar i `tennishl_data/jobs/<id>/truth.json`; lägg det i repot
bredvid filmen.
