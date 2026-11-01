# ggMacro — AI-created smart macro flows

[English](#english) · [Türkçe](#türkçe) · [Agent skill](ggmacro/SKILL.md)

### Demo / Tanıtım

[Watch the Pacman demo — 45 seconds](pacman_cropped.mp4)

[Pacman tanıtımını izle — 45 saniye](pacman_cropped.mp4)

Recorded gameplay example; results depend on the game and controller setup.

Oyun kaydı örneğidir; sonuçlar oyuna ve kontrol betiğinin ayarlarına bağlıdır.

## English

ggMacro turns AI-guided desktop interaction into reusable smart macro flows:
**classical automation + screen feedback + optional local d1 decisions.**

An AI can navigate a task, create and test a controller, then hand over a
standalone Python script. That script can run without the creating AI remaining
connected. Its flexibility comes from the observations, rules, and decision
choices it was designed and tested to handle; unfamiliar situations may still
need human or AI intervention.

### Three workflows

1. **Live control:** the AI reads screenshots and navigates through ggMacroLib's
   keyboard, mouse, and window functions at an ordinary interaction rate.
2. **Create and hand over:** the AI writes a standalone classical macro, a local
   d1 controller, or a combination. It can test and supervise execution when
   requested, or simply deliver the file and its launch instructions.
3. **Adaptive control:** the AI switches between direct interaction and local
   scripts as the task requires, observing progress and revising routines.
   It stops the script's input worker before taking direct control.

```text
AI         plan · observe · create/test routines · intervene or hand over
 ↕         goals / progress, screenshots, errors
Decision   classical rules and functions · optional local d1 through Ollama
 ↕         permitted actions / extracted state
Macro      keyboard · mouse · capture · repeated bounded routines
```

These are responsibilities, not three mandatory processes. Single actions need
no model. Predictable work uses classical code; d1 helps choose between allowed
alternatives when the state varies. The AI can supervise a running task at a
lower rate while the local controller handles frequent observations and input.

### Setup

Python 3.10+; run library examples from this checkout:

```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# Optional fast capture and fast Pacman example:
python -m pip install mss
```

OCR also needs a separately installed Tesseract executable. Window management
uses [PyWinCtl](https://pywinctl.readthedocs.io/en/latest/) with native backends.
The same library API targets Windows, macOS, and Linux/X11. Actual access depends
on OS permissions and desktop support; macOS needs Accessibility/Screen Recording
permissions. Native Wayland window management is explicitly unsupported. Linux
has been live-tested here; Windows and macOS have not.

### Direct desktop use

```python
import ggmacrolib as macro

windows = macro.find_windows(title="Firefox")  # case-insensitive substring
if windows:
    window = windows[0]  # inspect titles when there are multiple matches
    if macro.focus_window(window):
        print(macro.is_focused(window))
        macro.screenshot().save("screen.png")
        # Send input only to the intended focused application:
        # macro.move([400, 300])
        # macro.click()
        # macro.typeString("Hello")
```

`find_windows` returns PyWinCtl window objects; no match returns `[]`.
`focus_window(window, timeout=2.0)` restores and verifies focus. A closed window
or rejected focus returns `False`; backend failures raise `DesktopError`, and
unsupported sessions raise `UnsupportedDesktopError`.

`screenshot(region=None, path=None, backend="pillow", capture=None)` returns a
PIL image. Regions are **left, top, right, bottom**, not width/height. For MSS:

```python
image = macro.screenshot(region=(0, 0, 640, 480), backend="mss")
```

MSS returns RGB through this wrapper. With no region it captures the combined
desktop. Reuse an MSS object via `capture=...` in loops; standalone controllers
can use raw MSS/NumPy capture to avoid PIL conversion. Input functions act on
the desktop directly, so callers supply focus, deadline, and stop checks.

### Agent skill and standalone flows

Load [ggmacro/SKILL.md](ggmacro/SKILL.md) in your agent. For example:

> Use the ggmacro skill to complete this desktop task. Turn repeated work into
> a standalone Python controller, test it in a bounded session, and hand over
> the script with its dependencies and launch command.

Library mode needs the complete checkout. **Generated standalone flows do not
need this repository**: they contain their own task logic, capture, input,
stop/focus checks, and optional Ollama HTTP client. Third-party dependencies
still need installation. Use current screen calibration or CLI configuration,
not coordinates or images inherited from a previous session.

For fast tasks, the skill guides persistent MSS capture, lightweight local
vision, reliable input timing, stall recovery, and selective model calls.
Standalone files are not inherently faster: measure capture, decision quality,
inference, input, and focus checks. Avoid disk I/O and model calls on every frame.

### Optional d1 through Ollama

Install [Ollama](https://ollama.com/download), then start a project server:

```bash
python -m ggmacro.serve_local
```

It uses port **11435**, model storage in `.local/models`, and Ollama on PATH or
an existing `.local/ollama/bin/` executable (`--ollama PATH` overrides it).
Pull d1 through that same server in a second terminal:

```bash
# Linux / macOS
OLLAMA_HOST=127.0.0.1:11435 ollama pull d1
```

```powershell
# Windows PowerShell
$env:OLLAMA_HOST = "127.0.0.1:11435"
ollama pull d1
```

```python
from ggmacro import LocalDecider

result = LocalDecider().choose(
    state={"dialog_visible": True},
    actions={"inspect": "Inspect the dialog", "wait": "Wait for the UI"},
    goal="Choose the next observation step.",
)
print(result.action, result.latency_ms)  # selects only; no desktop input
```

The default is `d1:latest` at `http://127.0.0.1:11435`. Override with `model=`,
`url=`, `GGMACRO_MODEL`, or `GGMACRO_OLLAMA_URL`; a normal Ollama installation
may already be serving on 11434. d1 needs a compatible runtime supporting
[`/v1/systemone`](https://ollama.com/library/d1). Explicit chat models use
structured `/api/chat`. Confidence measures preference among choices, not the
probability of correctness. No fixed latency or accuracy is guaranteed.

`ActionGate` maps named decisions to trusted callbacks and checks freshness,
focus/stop predicates, and an action budget. Model output is data, never Python
code to execute. These checks are not an OS sandbox. Pause/stop cannot interrupt
an in-flight callback or HTTP request; keep callbacks bounded. `MacroLoom` and
`SupervisedMacroRunner` provide optional workers; errors appear in `last_error`.

### Examples and checks

```bash
# No desktop input, model, or display required:
python -m ggmacro.demo --seconds 6
python -m unittest discover -s tests -v

# Requires a running Ollama model:
python -m ggmacro.benchmark_local --model d1:latest

# Pacman examples: calibrate regions/vision thresholds before sending input.
python -m pacman.pacman_control capture
python -m pacman.fast_pacman --seconds 45 --model d1:latest
```

Pacman scripts are experimental application examples, not ready-made controllers
for every Pacman page. The fast example uses synchronous junction decisions;
it does not supply the outer AI monitor or gate every action with `ActionGate`.
Autonomous examples support Escape and `artifacts/pacman/STOP`.

## Türkçe

ggMacro, yapay zekâ ile yürütülen masaüstü etkileşimlerini tekrar kullanılabilir
**akıllı makro akışlarına** dönüştürür: **klasik otomasyon + ekran geri bildirimi
+ isteğe bağlı yerel d1 kararları.**

Yapay zekâ görevi inceleyebilir, bir kontrol betiği oluşturup test edebilir ve
sonunda bağımsız bir Python dosyası olarak teslim edebilir. Teslim edilen betik,
onu oluşturan yapay zekâya sürekli bağlı olmadan çalışabilir. Esnekliği,
tasarlanıp test edildiği durumlarla sınırlıdır; beklenmeyen bir durumda insanın
veya yapay zekânın tekrar devreye girmesi gerekebilir.

### Üç kullanım biçimi

1. **Canlı kontrol:** yapay zekâ ekranı okuyarak ggMacroLib'in klavye, fare ve
   pencere işlevleriyle normal hızda masaüstünde gezinir.
2. **Oluştur ve teslim et:** klasik makrolardan, yerel d1 kararlarından veya
   ikisinin birleşiminden oluşan bağımsız bir Python betiği yazar. İstenirse
   çalıştırıp test eder ve izler; yalnızca dosya istenmişse otomasyonu başlatmaz.
3. **Uyarlanabilir kontrol:** görev sırasında doğrudan ekran kontrolü ile yerel
   betikler arasında geçiş yapar; ilerlemeyi izler ve gerektiğinde akışı düzeltir.
   Doğrudan kontrolü devralmadan önce betiğin girdi gönderen bölümünü durdurur.

Üç katman **Yapay zekâ → Karar → Makro** şeklindedir. Bunlar zorunlu üç ayrı
süreç değildir. Basit bir işlem için model gerekmez; öngörülebilir işler klasik
kodla yürütülür. Değişen durumlarda d1 izin verilen seçenekler arasından karar
verebilir. Yerel betik hızlı döngüyü yürütürken yapay zekâ daha düşük sıklıkta
ekranı ve ilerlemeyi denetleyebilir.

### Kurulum ve kullanım

Python 3.10 veya üzeri gerekir. Yukarıdaki kurulum komutları iki dil için de
geçerlidir: sanal ortam oluşturun, etkinleştirin ve `requirements.txt` dosyasını
yükleyin. Hızlı ekran yakalama için ayrıca `python -m pip install mss` çalıştırın.
OCR kullanacaksanız Tesseract'ı da sisteminize kurun.

Tek makro kütüphanesi **`ggmacrolib.py`** dosyasıdır:

```python
import ggmacrolib as macro

pencereler = macro.find_windows("Firefox")
if pencereler and macro.focus_window(pencereler[0]):
    macro.screenshot(backend="mss").save("ekran.png")
```

Aynı API Windows, macOS ve Linux/X11 için tasarlanmıştır. Ekran/girdi erişimi
sistem izinlerine bağlıdır; macOS'ta Erişilebilirlik ve Ekran Kaydı izinleri
gerekir. Yerel Wayland pencere yönetimi desteklenmez. Burada canlı olarak Linux
test edilmiştir; Windows ve macOS henüz canlı test edilmemiştir.

`find_windows` başlık içinde büyük/küçük harf duyarsız arama yapar. Birden fazla
sonuç varsa doğru pencereyi seçin. `focus_window` odaklamayı doğrular;
`is_focused` güncel odağı kontrol eder. Ekran bölgesi sırası
**sol, üst, sağ, alt** şeklindedir; genişlik/yükseklik değildir.

### Yapay zekâya verilecek örnek istek

> ggmacro becerisini kullanarak bu masaüstü görevini tamamla. Tekrarlanan işleri
> bağımsız bir Python betiğine dönüştür, sınırlı süreyle test et ve betiği
> bağımlılıklarıyla ve çalıştırma komutuyla bana teslim et.

Beceri dosyası: [ggmacro/SKILL.md](ggmacro/SKILL.md). Kütüphane kullanımı için
projenin tamamı gerekir. Üretilen bağımsız betikler ise bu depoya ihtiyaç duymaz;
gerekli üçüncü taraf paketlerin kurulması yeterlidir. Güncel ekran düzenini
algılamalı veya açık yapılandırma kullanmalıdır; eski bir oturumun sabit
koordinatlarına ya da ekran görüntülerine bağımlı olmamalıdır.

Hızlı işlerde kalıcı MSS yakalama nesnesi, yerel görüntü işleme, doğru tuş basma
süreleri, takılma tespiti ve yalnızca gereken noktalarda model çağrısı kullanılır.
Bir dosyanın bağımsız olması tek başına hız garantisi değildir.

### Ollama ve d1

Ollama kurulduktan sonra `python -m ggmacro.serve_local` ile proje sunucusunu
başlatın. Varsayılan adres `http://127.0.0.1:11435`, model ise `d1:latest`tir.
Yukarıdaki işletim sisteminize uygun komutla d1'i **aynı sunucuya** indirin.
Mevcut Ollama sunucunuz 11434 kullanıyorsa adresi açıkça değiştirin.

Model yalnızca izin verilen eylem adlarını seçer; model çıktısı Python kodu
olarak çalıştırılmaz. Klasik karar işlevleriyle Ollama olmadan da çalışılabilir.
Model güven skoru doğruluk olasılığı değildir; hız ve karar kalitesi gerçek
görevde ölçülmelidir. Durdurma, odak kontrolü ve süre sınırı betiğin içinde de
bulunmalıdır. İstendiğinde yapay zekâ daha düşük sıklıkta izlemeye devam eder.

Örnekler `pacman/`, destek kodu ve beceri `ggmacro/`, testler `tests/` altındadır.
Pacman örnekleri farklı oyun sayfalarına doğrudan uyumlu değildir; ekran bölgesi
ve görsel eşikler ayarlanmalıdır. Testler masaüstüne girdi göndermez:

```bash
python -m ggmacro.demo --seconds 6
python -m unittest discover -s tests -v
```

## Repository contents / Depo içeriği

```text
ggmacrolib.py    desktop controls, windows, screenshots / masaüstü işlevleri
ggmacro/        agent skill, decision client, runtime and tools / beceri ve destek
pacman/         task-specific examples / göreve özel örnekler
tests/          automated checks / otomatik testler
```

The selected demo video `pacman_cropped.mp4` is included. Other generated
captures, recordings, logs, caches, environments, runtime binaries,
and model weights are excluded. Existing local Git history contains old session
artifacts: publish a fresh source snapshot rather than that history.

Seçilmiş tanıtım videosu `pacman_cropped.mp4` depoya dahildir. Diğer ekran
görüntüleri, kayıtlar, günlükler, önbellekler, sanal ortamlar, çalışma zamanı
dosyaları ve model ağırlıkları yayımlanmaz. Eski yerel Git geçmişi oturum
çıktıları içerir; ilk yayın için temiz bir kaynak kopyası kullanın.

Copyright © Gediz GÜRSU. Supply a source-code license in `LICENSE` when
publishing. Dependencies, Ollama, model weights, and the game shown in the
recording retain their respective licenses and rights.

Telif hakkı © Gediz GÜRSU. Yayımlarken kaynak kodun lisansını `LICENSE` dosyasında
belirtin. Bağımlılıklar, Ollama, model ağırlıkları ve kayıtta gösterilen oyun
kendi lisanslarına ve haklarına tabidir.
