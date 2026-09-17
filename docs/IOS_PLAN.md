# iOS-port: samma pipeline på AVFoundation/Vision

Desktop-pipelinen är referensimplementationen. Den här sidan säger vilket
Apple-ramverk som gör vad, vilken kod som portas rakt av, och vad som
faktiskt måste skrivas om.

Inget här är kompilerat. Det är en plan med bedömda tidsåtgångar, inte kod.

## Appstruktur (SwiftUI, iOS 17+)

```
TennisHL/
  App/
    TennisHLApp.swift
    ContentView.swift            flikar: Bibliotek, Spela in
  Features/
    Import/    PhotosPicker → PHAsset → lokal kopia
    Record/    AVCaptureSession, 1080p30, lås exponering/fokus, vattenpass-hint
    Analyze/   AnalysisViewModel: kör pipelinen i en Task, publicerar progress
    Review/    lista med tumnaglar, toggles, spelare (AVKit VideoPlayer)
    Export/    komposition → export → PHPhotoLibrary / ShareLink
  Pipeline/    ← portad kärna, ren Swift, inga UI-beroenden
    Config.swift            = config.py
    Types.swift             = types.py
    ProxyReader.swift       AVAssetReader → nedskalade CVPixelBuffer
    Observer.swift          bakgrundsmodell + blobbar (Accelerate/vImage)
    Court.swift             = court.py
    Activity.swift          = activity.py
    Segmentation.swift      = segmentation.py
    Features.swift          = features.py
    Scoring.swift           = scoring.py
    BallTracker.swift       = ball.py (diff i vImage, länkning rakt av)
    ClipPlanner.swift       padding + merge
    Exporter.swift          AVMutableComposition + AVVideoComposition (trail)
  PipelineTests/            samma syntetiska signaler som tests/
```

Pipeline-paketet ska vara ett Swift Package utan UIKit så det går att köra
`swift test` på Mac utan simulator.

## Ramverk per steg

| Steg | Desktop | iOS | Kommentar |
|------|---------|-----|-----------|
| Avkodning + proxy | `cv2.VideoCapture` + `grab()` | `AVAssetReader` + `AVAssetReaderTrackOutput` (kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange) | Läs Y-planet direkt: gråskala gratis. Skala med `vImageScale_Planar8`. Hoppa över bilder genom att bara inte skala var n:te sample. HW-avkodning 1080p30 går i 300-600 fps på A16+. |
| Bakgrundsmodell | MOG2 | egen: löpande medel + varians per pixel i Float16 (`vDSP`), tröskel 2.5σ | MOG2 finns inte i Vision. En enkel gaussisk modell per pixel är 40 rader vDSP och räcker på stativ. `VNGenerateForegroundInstanceMaskRequest` finns men är för dyr per bild. |
| Blobbar | `connectedComponentsWithStats` | egen 2-pass connected components på 480x270 (billigt), *eller* `VNDetectHumanRectanglesRequest` @ 5 fps | Rekommendation: börja med blobbar (identiskt beteende som desktop), lägg Vision-personer som förbättring. Vision-personer löser också dubbel. |
| Kamerarörelse | global diff | samma på Y-planet med `vDSP_distancesq` | |
| Bana, aktivitet, segmentering, features, scoring | numpy | ren Swift på `[Float]` | Rad-för-rad-port. Inga beroenden. Testerna portas med. |
| Boll: kandidater | 3-frame diff + CC | `vImage` absdiff + min, tröskel, CC | Samma algoritm. På 960 px är det ~0.5 ms/bild med vImage. |
| Boll: länkning | python | Swift, identisk | ren logik |
| Klippning | ffmpeg re-encode | `AVMutableComposition` med `insertTimeRange`, `AVAssetExportSession` preset `AVAssetExportPresetHighestQuality` | Kompositionen är bildexakt utan omkodning av *källan*; exporten kodar en gång. |
| Fade | ffmpeg-filter | `AVMutableVideoCompositionInstruction` + opacity ramp, `AVMutableAudioMixInputParameters` volume ramp | |
| Bollspår-overlay | OpenCV ritar → ffmpeg | `AVVideoCompositionCoreAnimationTool` med ett `CAShapeLayer` per klipp vars `path` animeras via `CAKeyframeAnimation` (beginTime = klippstart) | Ingen per-bild-rendering i Swift. Positioner i normaliserade koordinater direkt från `highlights.json`-formatet. |
| Montage | concat | samma komposition, klippen läggs efter varandra | En export för hela montaget. |
| Spara/dela | filer | `PHPhotoLibrary.shared().performChanges` / `ShareLink(item:)` | |
| Progress | stderr | `AsyncStream<Progress>` → `@Observable` viewmodel | samma stage-namn som JSON-progressen |

## Batteri och värme

- Kör analysen i `Task.detached(priority: .userInitiated)` men lyssna på
  `ProcessInfo.thermalState`; vid `.serious` sänk `coarseFps` 10 → 6.
- Grovpasset dominerar. Uppskattning på iPhone 15: 2 h film ≈ 4-6 min med
  HW-avkodning, skärm avstängd OK. Bollpass + export ≈ 2-3 min till.
- Be om `beginBackgroundTask` så att en låst skärm inte dödar jobbet; visa
  beräknad tid kvar (vi vet total bildmängd).
- Aldrig 1080p i minnet: proxy-buffertar återanvänds via `CVPixelBufferPool`.

## Vad som *inte* ska portas

- `synth.py`: testfilmen genereras på Mac, checkas in i testpaketet som en
  10 s mp4 (eller genereras av ett Swift-script en gång).
- `review.html`: ersätts av en SwiftUI-lista med samma data.
- ffmpeg: allt går via AVFoundation.

## Ordning

1. Pipeline-paketet med `Segmentation`, `Scoring`, `Features` + tester
   (1 dag, ren port).
2. `ProxyReader` + `Observer` + `Court` + `Activity`, verifiera att a(t)
   på samma fil ser ut som desktopens `signal.png` (2 dagar).
3. Import + Analyze-view med progress + Review-lista + export utan trail
   (2 dagar). **Här är M1-M3 klara på telefonen.**
4. `BallTracker` + overlay via Core Animation (2 dagar).
5. Inspelning i appen (1 dag).

Håll desktop och iOS i synk genom att båda skriver samma `highlights.json`.
Då kan vi jämföra utfall på samma fil och se exakt var porten avviker.
