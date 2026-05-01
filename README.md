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

Для тредов из базы пайплайн теперь делает это по сегментам:

- отдельно переводит вопрос
- отдельно переводит каждый ответ
- отдельно озвучивает вопрос и каждый ответ
- склеивает итоговый WAV с паузами `1.0` сек после вопроса и `0.5` сек между ответами

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
  "max_prompt_seconds": 12,
  "default_instruction": "You are a Russian voice actor. Speak clearly and naturally, with warm male timbre and restrained emotion.<|endofprompt|>",
  "voices_config": "voices.json"
}
```

`voiceover/configs/voices.json` хранит доступные голоса. `audio` указывает на файл внутри `voiceover/voices`.

Вес модели лежит внутри `voiceover/models/Fun-CosyVoice3-0.5B`, поэтому модуль не зависит от внешней папки `models`.

CosyVoice runtime лежит внутри `voiceover/third_party/CosyVoice`, поэтому модуль не зависит от внешней папки `third_party`.

Runtime-кэш направлен в `voiceover/cache` и игнорируется git.

Зависимости модуля описаны в `voiceover/requirements.txt`.

Активный голос, имя выходного файла, pitch и speed не хранятся в конфиге. Это параметры конкретной генерации.

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

## Collector

`collector` - модуль для выборки тредов Reddit, оценки их пригодности и сохранения в локальную SQLite-базу.

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
