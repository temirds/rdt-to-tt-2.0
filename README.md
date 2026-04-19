# rdt-to-tt-2.0

Минимальный локальный модуль озвучки на CosyVoice3.

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
