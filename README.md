# rdt-to-tt-2.0

Локальный пайплайн: сбор Reddit-тредов, перевод через NLLB и озвучка через CosyVoice3.

## Установка

Проверенный вариант для Windows: Python `3.10.x` и отдельное окружение `.venv-cosyvoice`.

Установка зависимостей озвучки и перевода:

```powershell
py -3.10 -m venv .venv-cosyvoice
.\.venv-cosyvoice\Scripts\Activate.ps1
python -m pip install --upgrade pip wheel
python -m pip install -r .\translator\requirements.txt
python -m pip install -r .\voiceover\requirements.txt --no-build-isolation
```

`translator/requirements.txt` и `voiceover/requirements.txt` уже зафиксированы под `torch==2.7.1+cu128` и `torchaudio==2.7.1+cu128`, то есть под CUDA 12.8.

Почему так:

- `openai-whisper==20231117` в этом стеке ломается на свежем build-isolation с новым `setuptools`, поэтому установка запускается с `--no-build-isolation`.
- В `voiceover/requirements.txt` уже добавлен `setuptools<81`, чтобы окружение оставалось совместимым с `openai-whisper`.
- На практике проверен запуск на `Python 3.10.11`.

Скачать модель перевода:

```powershell
huggingface-cli download facebook/nllb-200-3.3B --local-dir .\translator\models\nllb-200-3.3B
```

По умолчанию [translator/config.json](D:/shit/rdt-to-tt-2.0/translator/config.json:1) ожидает модель именно в `translator/models/nllb-200-3.3B` и работает в `local_files_only=true`.

После установки проверьте, что путь в `voiceover/configs/cosyvoice.json` совпадает с локальным окружением:

```json
{
  "python": "../.venv-cosyvoice/Scripts/python.exe"
}
```

Быстрый smoke-test:

```powershell
python main.py --voice upvote_2 --text "Привет, это проверка запуска." --output out\smoke.wav
```

Ожидаемый результат: появится WAV-файл, а в консоли будет что-то вроде `Готово: ...\out\smoke.wav`.

## Как пользоваться

Внешний код передает только параметры генерации. Внутренние конфиги и голоса модуль подгружает сам из своей папки.

```python
from pathlib import Path
from voiceover import VoiceoverOptions, generate_voiceover

result = generate_voiceover(
    "Текст для озвучки.",
    VoiceoverOptions(
        voice="upvote",
        pitch=-2,
        speed=1.0,
        output_path=Path("out/output--2.wav"),
    ),
)

print(result.output_path)
```

То же самое словарем:

```python
from voiceover import generate_voiceover

result = generate_voiceover(
    "Текст для озвучки.",
    {
        "voice": "upvote",
        "pitch": -2,
        "speed": 1.0,
        "output_path": "out/output--2.wav"
    },
)
```

CLI:

```powershell
python main.py --voice upvote --pitch -2 --speed 1.0 --text "Текст" --output out\test.wav
```

Полный пайплайн для Reddit:

```powershell
python -m collector.cli --subreddit AskReddit --keyword lesson --keyword useful
python main.py --voice upvote_2 --latest-thread
```

Полный пайплайн с вертикальным MP4:

```powershell
python main.py --voice upvote_2 --latest-thread --video
```

Для рендера видео нужен `ffmpeg` в `PATH`. Фоновые клипы модуль ищет в `video/assets/backgrounds/`.
Практичный вариант - держать свою локальную библиотеку клипов:

```text
video/assets/backgrounds/
  minecraft/
    clip_01.mp4
  satisfying/
    clip_01.mp4
  driving/
    clip_01.mp4
```

Для выбора контента фона используйте теги. Теги совпадают с подпапками и ключами из `video/configs/video.json`, поле `backgrounds.tags`:

```powershell
python main.py --voice upvote_2 --latest-thread --video --background-tag minecraft
python main.py --voice upvote_2 --latest-thread --video --background-tag satisfying --background-tag driving
```

Если локальных клипов нет, модуль может скачать фон только по прямым ссылкам из `video/configs/video.json`. Это не stock API: вы сами добавляете проверенные `.mp4/.mov/.mkv/.webm` URL в `backgrounds.urls` или в нужный тег `backgrounds.tags.minecraft`. Скачанные файлы сохраняются в `video/cache/backgrounds/` и переиспользуются между запусками.

Во время запуска видео-модуль пишет прогресс в консоль: сколько локальных клипов найдено, какие прямые ссылки доступны, сколько файлов взято из кэша и сколько скачано.

Фон выбирается как один случайный исходный файл из локальной библиотеки или кэша. После измерения длительности озвучки модуль вырезает из него диапазон ровно под итоговый ролик и записывает прогресс в SQLite `video/cache/background_usage.sqlite3`: до какой секунды файл уже использован. Если остатка файла не хватает на следующий ролик, файл помечается полностью использованным и модуль выбирает другой. Когда все подходящие файлы исчерпаны, рендер останавливается с сообщением, что нужно добавить новые фоны или вручную сбросить SQLite.

Финальная сборка видео теперь делает полный TikTok-пайплайн: на этапе озвучки добавляет случайный SFX из `video/sfx` между комментариями с громкостью `1.5`, затем видео добавляет 1 секунду после окончания голоса, миксует случайную музыку из `video/music` с громкостью `0.03`, вырезает фон нужной длины и накладывает субтитры по схеме старого `rdt-to-tt`: ffmpeg `drawtext` filter, CAPS, размер 96, черная обводка и короткая pop-анимация размера. В режиме `word_timestamps=true` тайминги слов берутся из Whisper; если распознавание недоступно, включается approximate-раскладка по исходному тексту.

Можно указать конкретный фон и выходной MP4:

```powershell
python main.py --voice upvote_2 --latest-thread --video --background path\to\background.mp4 --video-output out\final.mp4
```

Для тредов из базы пайплайн теперь делает это по сегментам:

- отдельно переводит вопрос
- отдельно переводит каждый ответ
- отдельно озвучивает вопрос и каждый ответ
- склеивает итоговый WAV с паузами `1.0` сек после вопроса и `0.5` сек между ответами
- при `--video` собирает вертикальный MP4 `1080x1920` с фоновым клипом, аудио и ASS-субтитрами

## Модуль

`voiceover` теперь содержит свои внутренние настройки и голосовые референсы:

```text
voiceover/
  configs/
    cosyvoice.json
    voices.json
  requirements.txt
  models/
    Fun-CosyVoice3-0.5B/
  third_party/
    CosyVoice/
  voices/
    gerald.wav
    upvote.wav
    upvote_2.wav
```

`voiceover/configs/cosyvoice.json` описывает движок CosyVoice:

```json
{
  "voiceover_engine": "cosyvoice",
  "module_root": ".",
  "cosyvoice_repo": "third_party/CosyVoice",
  "cosyvoice_model": "models/Fun-CosyVoice3-0.5B",
  "python": "../.venv-cosyvoice/Scripts/python.exe",
  "cache_dir": "cache",
  "device": "cpu",
  "default_pitch": 0.0,
  "default_speed": 1.0,
  "max_prompt_seconds": 12,
  "default_instruction": "Speak Russian naturally.<|endofprompt|>",
  "voices_config": "voices.json"
}
```

`voiceover/configs/voices.json` хранит доступные голоса. `audio` указывает на файл внутри `voiceover/voices`.

Вес модели лежит внутри `voiceover/models/Fun-CosyVoice3-0.5B`, поэтому модуль не зависит от внешней папки `models`.

CosyVoice runtime лежит внутри `voiceover/third_party/CosyVoice`, поэтому модуль не зависит от внешней папки `third_party`.

Runtime-кэш направлен в `voiceover/cache` и игнорируется git.

Зависимости модуля описаны в `voiceover/requirements.txt`.

Активный голос и имя выходного файла не хранятся в конфиге. `default_pitch` и `default_speed` задают дефолты модуля, а `--pitch` и `--speed` переопределяют их для конкретного запуска.

## Параметры

`voice` - имя голоса из `voiceover/configs/voices.json`.

`pitch` - сдвиг высоты в полутонах от `-12` до `12`.

`speed` - множитель скорости от `0.5` до `2.0`.

`output_path` - точный путь к WAV-файлу. Также можно использовать `output_dir` и `filename`.

`result` содержит путь к сохраненному файлу, длительность и sample rate.

## Структура Кода

- `voiceover/api.py` - публичный фасад `generate_voiceover(...)` и `VoiceoverOptions`.
- `voiceover/base.py` - общие структуры `VoiceProfile`, `VoiceoverRequest`, `VoiceoverResult` и протокол `VoiceoverEngine`.
- `voiceover/config.py` - загрузка внутренних конфигов и профилей голоса.
- `voiceover/factory.py` - выбор реализации по `voiceover_engine`.
- `voiceover/cosyvoice.py` - текущая реализация на CosyVoice3.
- `voiceover/text.py` - предобработка текста, сейчас нормализация русских чисел.

Чтобы заменить движок озвучки, добавьте новую реализацию `VoiceoverEngine`, зарегистрируйте ее в `voiceover/factory.py` и поменяйте `voiceover_engine` во внутреннем конфиге модуля.

## Video

`video` - модуль для сборки вертикального TikTok-видеоряда поверх готовой озвучки.

```text
video/
  configs/video.json
  api.py
  assets.py
  base.py
  config.py
  renderer.py
  subtitles.py
  timeline.py
```

Публичный фасад:

```python
from video import VideoOptions, VideoSegment, generate_video

result = generate_video(
    [
        VideoSegment("Вопрос треда", role="question", pause_after_seconds=1.0),
        VideoSegment("Ответ пользователя", role="answer"),
    ],
    VideoOptions(
        audio_path="out/audio.wav",
        output_path="out/final.mp4",
    ),
)
```

Фоновые клипы:

- локальные: `video/assets/backgrounds/`, включая подпапки тегов вроде `minecraft/` или `satisfying/`
- прямые URL из `video/configs/video.json`, кэшируются в `video/cache/backgrounds/`
- временные reels: `video/cache/tmp/`, удаляются после рендера
- теги фона: `minecraft`, `subway`, `satisfying`, `driving`, `cooking`, `nature`, `city`, `fitness`

Без локальных клипов и без прямых URL рендер остановится с объяснением, куда положить фон или где настроить ссылки.

## Downloader

`downloader` - модуль для скачивания медиа в локальную папку. Сейчас подключен один провайдер: `youtube` через `yt-dlp`, но публичный API и Web UI уже используют поле `provider`, чтобы позже добавить другие источники.

Установка зависимости:

```powershell
python -m pip install -r .\downloader\requirements.txt
```

Локальный Web UI:

```powershell
python -m downloader.web
```

После запуска откройте:

- `http://127.0.0.1:8765` - скачивание YouTube-видео.
- `http://127.0.0.1:8765/editor` - просмотр и обрезка скачанных видео.

На странице скачивания можно вставить URL, выбрать качество, папку вывода, cookies/ffmpeg и запустить скачивание. Прогресс и итоговые пути показываются в блоке статуса.
Для URL плейлиста включите `Download playlist`, иначе downloader сохранит прежнее поведение и скачает только одно видео из ссылки.
После скачивания имена медиафайлов автоматически нормализуются: нижний регистр, пробелы заменены на `_`, остальные символы кроме букв, цифр и `_` удалены.

Если порт занят:

```powershell
python -m downloader.web --port 8766
```

Скачать видео в папку по умолчанию `out/youtube`:

```powershell
python -m downloader.cli "https://www.youtube.com/watch?v=VIDEO_ID"
```

Скачать в конкретную папку и ограничить качество:

```powershell
python -m downloader.cli "https://www.youtube.com/watch?v=VIDEO_ID" --output-dir out\raw_videos --quality 1080p
```

Скачать все видео из плейлиста:

```powershell
python -m downloader.cli "https://www.youtube.com/playlist?list=PLAYLIST_ID" --playlist
```

Если YouTube пишет `Sign in to confirm you're not a bot`, экспортируйте cookies из браузерного расширения в `cookies.json`, конвертируйте в Netscape `cookies.txt` и укажите этот файл. По умолчанию модуль использует `downloader/cookies.txt`.

```powershell
python -m downloader.cli --parse-cookies path\to\cookies.json --cookies-output downloader\cookies.txt
python -m downloader.cli "https://www.youtube.com/watch?v=VIDEO_ID"
```

В Web UI укажите путь к `cookies.json` и нажмите `Parse cookies`. Файл будет сохранен как `downloader/cookies.txt`, а путь автоматически попадет в поле `cookies.txt`. Если `cookies.json` указан при нажатии `Download`, Web UI сначала перепарсит cookies, затем запустит скачивание с полученным `cookies.txt`.

Видео-редактор:

- список файлов берется из папки вывода downloader;
- видео открывается в браузерном preview;
- таймлайн хранит список независимых клипов, а не декоративные метки поверх исходника;
- при открытии видео создается один клип, который ссылается на весь исходный файл;
- кнопка `✂` слева от таймлайна разрезает клип под playhead на два независимых клипа;
- каждый клип можно выбрать отдельно кликом на таймлайне;
- клип можно двигать по таймлайну перетаскиванием;
- края клипа можно двигать ручками, укорачивая или растягивая клип только в пределах исходного видео;
- кнопка `⌫` удаляет выбранный клип;
- кнопка `↺` сбрасывает таймлайн к одному полному клипу;
- `Set start` и `Set end` ставят границы выбранного клипа по текущему времени preview;
- `Result name` задает имя итогового файла без расширения;
- `Export` собирает итоговое видео только из клипов, которые остались на таймлайне;
- экспорт идет подряд в порядке расположения клипов на таймлайне, пустоты между клипами в простой версии не сохраняются;
- `Re-encode export` использует ffmpeg concat через фильтры для точной сборки результата.

Публичный фасад:

```python
from downloader import DownloadOptions, download

results = download(
    "https://www.youtube.com/watch?v=VIDEO_ID",
    DownloadOptions(provider="youtube", output_dir="out/raw_videos", quality="1080p"),
)

for result in results:
    print(result.success, result.filepaths)
```

Совместимые алиасы `YoutubeDownloadOptions` и `download_youtube` пока оставлены внутри `downloader`, но новый код лучше писать через `DownloadOptions` и `download`.

Внутренние настройки лежат в `downloader/configs/downloader.json`. Для высоких качеств с раздельными video/audio потоками нужен `ffmpeg` в `PATH`.

## Collector

`collector` - модуль для выборки тредов Reddit, оценки их пригодности и сохранения в локальную SQLite-базу.

Ограничения длины настраиваются в [collector/config.json](D:/shit/rdt-to-tt-2.0/collector/config.json:1):

- `collector.min_comment_length` - нижняя граница длины usable answer/comment в символах.
- `reddit.max_question_length` - верхняя граница длины полного вопроса в символах.
- `collector.max_comment_length` - верхняя граница длины usable answer/comment в символах.

Перед сохранением `question` и `answers_json` коллектор чистит текст для озвучки: удаляет эмодзи, zero-width/control символы, URL, markdown-картинки, code blocks и reddit username/subreddit ссылки.

Что хранится в `collector/data/`:

- `collector/data/reddit.db` - SQLite база с таблицами `collector_threads` и `collector_comments`.
- В `collector_threads` лежат метаданные поста, `question`, `answers_json`, служебный `original_text`, score анализа и сериализованные ответы.
- В `collector_comments` лежат отдельные комментарии, привязанные к сохраненному треду.

Запуск:

```powershell
python -m collector.cli --subreddit AskReddit --keyword lesson --keyword useful --flair Discussion
```

Локальный demo-режим без сети и без API ключа:

```powershell
python -m collector.cli --demo
```

Он создаст `collector/data/reddit.db` и сохранит туда встроенные демонстрационные треды.

Пример вывода:

```text
Database: D:\shit\rdt-to-tt-2.0\collector\data\reddit.db
Fetched: 2
Prepared: 2
Saved: 2
Skipped: 0

Saved previews:
- [1] What habit improved your life the most?
  question="What habit improved your life the most? Please share practical examples and why they worked."
  answers_json[0]="Daily walking was the turning point for me..."
```

Зависимости модуля перечислены в `collector/requirements.txt`. Сейчас сам модуль использует только стандартную библиотеку Python.

## Git Ignore

В `.gitignore` уже исключены тяжелые артефакты:

- локальные окружения и кэши
- SQLite базы
- папки моделей и runtime-кэшей
- крупные бинарные веса: `*.bin`, `*.pt`, `*.pth`, `*.onnx`, `*.safetensors`, `*.ckpt`
