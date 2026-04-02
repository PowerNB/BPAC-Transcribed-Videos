#!/usr/bin/env python3
"""
Скрипт для создания чистых, сфокусированных конспектов из существующих summary.

Двухэтапный процесс для экономии токенов:

  Этап 1 (1 запрос на модуль):
    Claude читает все summary модуля и возвращает JSON:
    {
      "1": "релевантный текст для видео 1...",
      "2": "релевантный текст для видео 2...",
      ...
    }

  Этап 2 (1 запрос на видео):
    Claude получает только маленький кусок из JSON
    и делает красивый структурированный конспект.

  Бонус (1 запрос на модуль):
    Итоговый конспект по всему модулю на основе clean-файлов.

Входные данные:  claude-summary/Модуль N/summary-N-M-*.md
Выходные данные: claude-summary/Модуль N/clean/summary-N-M-*.md
                 claude-summary/Модуль N/summary-module-N.md
"""

import os
import re
import sys
import json
import subprocess
import glob
from pathlib import Path
from dataclasses import dataclass


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

CLAUDE_CMD    = r"C:\Users\Proger4\AppData\Roaming\npm\claude.cmd"
SUMMARY_DIR   = "claude-summary"
SKIP_EXISTING = True
CACHE_FILENAME  = ".extract_cache.json"  # кеш этапа 1, хранится в папке каждого модуля
BATCH_SIZE      = 4                       # макс. видео в одном батч-запросе этапа 1
VERBOSE       = True
TIMEOUT       = 1800


# ─────────────────────────────────────────────
# СИСТЕМНЫЕ ПРОМПТЫ
# ─────────────────────────────────────────────

SYSTEM_PROMPT_EXTRACTOR = (
    "Ты - аналитик учебных материалов. "
    "Твоя задача - вернуть ТОЛЬКО валидный JSON без какого-либо текста до или после него. "
    "Никаких пояснений, никакого markdown, никаких ```json блоков. Только сам JSON объект."
)

SYSTEM_PROMPT_WRITER = (
    "Ты - эксперт по составлению учебных конспектов. "
    "Твоя задача - выводить ТОЛЬКО готовый конспект в формате Markdown, "
    "без каких-либо вступлений, пояснений и описаний своих действий. "
    "Начинай ответ сразу с символа # (заголовок). Ничего лишнего после конспекта."
)


# ─────────────────────────────────────────────
# ПРОМПТЫ
# ─────────────────────────────────────────────

EXTRACT_PROMPT_TEMPLATE = """\
Ниже приведены саммари всех видео одного модуля курса по Bitrix24 "Автоматизация бизнес-процессов".
Каждое саммари содержит информацию по всему модулю, но создавалось с фокусом на конкретное видео.

Список видео модуля и их темы:
{video_list}

ЗАДАЧА:
Для каждого видео из списка выше — выбери из всех саммари ТОЛЬКО те куски текста,
которые реально относятся к теме этого видео. Не перефразируй — бери текст как есть.
Если какой-то факт или пример из другого видео напрямую дополняет тему — включи его тоже.

Верни результат строго в формате JSON:
{{
  "1": "весь релевантный текст для темы первого видео...",
  "2": "весь релевантный текст для темы второго видео...",
  ...
}}

Ключи — номера видео из списка выше. Значения — извлечённый текст, можно с переносами строк.
ВАЖНО: верни ТОЛЬКО JSON, без какого-либо текста до или после.

САММАРИ ВСЕХ ВИДЕО МОДУЛЯ:
{all_summaries}"""


WRITE_PROMPT_TEMPLATE = """\
Ниже приведён извлечённый текст по теме конкретного видео курса по Bitrix24 "Автоматизация бизнес-процессов".
Тема видео: "{video_title}"

На основе этого текста создай ПОДРОБНЫЙ и СТРУКТУРИРОВАННЫЙ конспект в формате Markdown.
Конспект будет использоваться как инструкция для будущей работы, поэтому:

1. Не теряй ни одной детали, определения, примера или шага из текста
2. Структурируй по смысловым разделам с заголовками
3. Если есть шаги работы в Bitrix24 — опиши их пошагово
4. В конце — раздел "Главное": 5-10 ключевых выводов по теме

Формат:
# {video_title}

## О чём этот урок
## Основные понятия
## [Тематические разделы]
## Пошаговые инструкции (если есть)
## Главное

ИЗВЛЕЧЁННЫЙ ТЕКСТ:
{extracted_text}

ВАЖНО: Начни ответ сразу с # (заголовок). Никаких вступлений и пояснений."""


MODULE_SUMMARY_PROMPT_TEMPLATE = """\
Ниже приведены чистые конспекты всех видео модуля курса по Bitrix24 "Автоматизация бизнес-процессов".
Модуль: {module_name}

Твоя задача: создать ИТОГОВЫЙ ОБЗОРНЫЙ конспект по всему модулю.

Требования:
1. Охвати все темы модуля — каждое видео должно найти отражение
2. Покажи связи между темами — как они дополняют друг друга
3. Структурируй по смысловым блокам, а не по порядку видео
4. В начале — краткое описание о чём весь модуль (3-5 предложений)
5. В конце — раздел "Ключевые выводы модуля" (10-15 пунктов)

ЧИСТЫЕ КОНСПЕКТЫ ВСЕХ ВИДЕО:
{all_clean_summaries}

ВАЖНО: Начни ответ сразу с # (заголовок вида "# Модуль N: ..."). Никаких вступлений."""


# ─────────────────────────────────────────────
# СТРУКТУРЫ ДАННЫХ
# ─────────────────────────────────────────────

@dataclass
class VideoInfo:
    module_num: str
    video_num: str
    video_slug: str
    video_title: str
    source_path: str   # существующий summary-N-M-....md
    clean_path: str    # claude-summary/Модуль N/clean/summary-N-M-....md


# ─────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────

def log(msg):
    if VERBOSE:
        print(msg, flush=True)


def read_file(filepath: str) -> str:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        log(f"  Не удалось прочитать {filepath}: {e}")
        return ""


def save_file(path: str, content: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def find_module_dirs(summary_dir: str) -> list[Path]:
    modules = []
    for item in Path(summary_dir).iterdir():
        if item.is_dir() and re.match(r'Модуль\s+\d+', item.name):
            modules.append(item)
    modules.sort(key=lambda p: int(re.search(r'\d+', p.name).group()))
    return modules


def find_videos_in_module(module_dir: Path) -> list[VideoInfo]:
    """Найти все существующие summary-N-M-*.md (не в clean/, не module-summary)."""
    videos = []
    pattern = str(module_dir / "summary-*.md")
    for filepath in sorted(glob.glob(pattern)):
        path = Path(filepath)
        if re.match(r'summary-module-\d+\.md', path.name):
            continue
        match = re.match(r'summary-(\d+)-(\d+)-(.*?)\.md', path.name)
        if not match:
            continue

        module_num  = match.group(1)
        video_num   = match.group(2)
        video_slug  = match.group(3)
        video_title = video_slug.replace('-', ' ')
        clean_path  = module_dir / "clean" / path.name

        videos.append(VideoInfo(
            module_num=module_num,
            video_num=video_num,
            video_slug=video_slug,
            video_title=video_title,
            source_path=str(path),
            clean_path=str(clean_path),
        ))
    return videos


def run_claude(prompt: str, system_prompt: str) -> str:
    """Запускает Claude CLI, передавая промпт через stdin."""
    proc = subprocess.Popen(
        [
            CLAUDE_CMD,
            "--print",
            "--input-format", "text",
            "--dangerously-skip-permissions",
            "--system-prompt", system_prompt,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = proc.communicate(
            input=prompt.encode('utf-8'), timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"Превышено время ожидания {TIMEOUT} сек.")

    stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
    stderr = stderr_bytes.decode('utf-8', errors='replace').strip()

    if stderr:
        log(f"         stderr: {stderr[:300]}")

    if proc.returncode != 0 or not stdout:
        raise RuntimeError(
            f"Claude CLI завершился с кодом {proc.returncode}. "
            f"stderr: {stderr[:400] if stderr else '(пусто)'}"
        )

    return stdout


def parse_json_response(raw: str) -> dict:
    """Парсит JSON из ответа Claude, устойчиво к лишним символам."""
    # Убираем возможные markdown-блоки если Claude всё же добавил их
    raw = re.sub(r'^```json\s*', '', raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r'^```\s*$', '', raw.strip(), flags=re.MULTILINE)
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Пробуем найти JSON объект внутри текста
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"Не удалось распарсить JSON: {e}\nОтвет Claude:\n{raw[:500]}")


# ─────────────────────────────────────────────
# ОСНОВНАЯ ЛОГИКА
# ─────────────────────────────────────────────

def step1_extract_batch(batch: list["VideoInfo"], all_videos: list["VideoInfo"]) -> dict[str, str]:
    """
    Вспомогательная функция: запускает один батч-запрос к Claude.
    batch     — видео, для которых извлекаем текст (3-4 штуки)
    all_videos — все видео модуля (для контекста в саммари)
    """
    sep = "-" * 40

    # Список только тех видео, для которых нужна выборка
    video_list = "\n".join(
        f"  Видео {v.video_num}: {v.video_title}"
        for v in batch
    )

    # Все саммари модуля как контекст
    all_summaries = ""
    for v in all_videos:
        text = read_file(v.source_path)
        if text:
            all_summaries += (
                f"\n\n{sep}\n"
                f"# ВИДЕО {v.video_num}: {v.video_title}\n"
                f"{sep}\n\n"
                f"{text}"
            )

    prompt = EXTRACT_PROMPT_TEMPLATE.format(
        video_list=video_list,
        all_summaries=all_summaries,
    )

    log(f"    [батч] Видео {[v.video_num for v in batch]}, промпт: {len(prompt)} символов")
    raw = run_claude(prompt, SYSTEM_PROMPT_EXTRACTOR)
    return parse_json_response(raw)


def step1_extract(videos: list["VideoInfo"], module_dir: Path) -> dict[str, str]:
    """
    Этап 1: извлечение релевантного текста для каждого видео.
    Если видео <= BATCH_SIZE — один запрос.
    Если больше — разбивает на батчи по BATCH_SIZE и объединяет JSON.
    Результат кешируется.
    """
    cache_path = module_dir / CACHE_FILENAME

    # Проверяем кеш
    if cache_path.exists():
        log(f"  [Этап 1] Найден кеш: {cache_path}")
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                extracted = json.load(f)
            log(f"  [Этап 1] Загружен из кеша. Блоков: {len(extracted)}")
            for video_num, text in extracted.items():
                log(f"           Видео {video_num}: {len(text)} символов")
            return extracted
        except Exception as e:
            log(f"  [Этап 1] Кеш повреждён ({e}), запускаю заново...")

    extracted: dict[str, str] = {}

    if len(videos) <= BATCH_SIZE:
        # Небольшой модуль — один запрос
        log(f"  [Этап 1] Один запрос ({len(videos)} видео)...")
        extracted = step1_extract_batch(videos, videos)
    else:
        # Большой модуль — разбиваем на батчи
        batches = [videos[i:i + BATCH_SIZE] for i in range(0, len(videos), BATCH_SIZE)]
        log(f"  [Этап 1] Батч-режим: {len(videos)} видео → {len(batches)} батча по ~{BATCH_SIZE}")

        for i, batch in enumerate(batches, 1):
            log(f"  [Этап 1] Батч {i}/{len(batches)}...")
            try:
                batch_result = step1_extract_batch(batch, videos)
                extracted.update(batch_result)
                log(f"  [Этап 1] Батч {i} готов. Блоков в батче: {len(batch_result)}")
            except RuntimeError as e:
                log(f"  [Этап 1] [ошибка] Батч {i}: {e}")

    # Сохраняем кеш
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(extracted, f, ensure_ascii=False, indent=2)
        log(f"  [Этап 1] Кеш сохранён: {cache_path}")
    except Exception as e:
        log(f"  [Этап 1] Не удалось сохранить кеш: {e}")

    log(f"  [Этап 1] Готово. Извлечено блоков: {len(extracted)}")
    for video_num, text in extracted.items():
        log(f"           Видео {video_num}: {len(text)} символов")

    return extracted

def step2_write(video: VideoInfo, extracted_text: str) -> str:
    """
    Этап 2: один запрос на видео — маленький input,
    делает красивый структурированный конспект.
    """
    prompt = WRITE_PROMPT_TEMPLATE.format(
        video_title=video.video_title,
        extracted_text=extracted_text,
    )

    log(f"  [Этап 2] Видео {video.video_num}: {video.video_title}")
    log(f"           Размер промпта: {len(prompt)} символов")
    log("           Запускаю Claude (написание конспекта)...")

    result = run_claude(prompt, SYSTEM_PROMPT_WRITER)
    log(f"           Готово! ({len(result)} символов)")
    return result


def step3_module_summary(module_dir: Path, videos: list[VideoInfo], module_num: str):
    """
    Бонусный этап: итоговый конспект по всему модулю
    на основе уже готовых clean-файлов.
    """
    module_summary_path = module_dir / f"summary-module-{module_num}.md"

    log(f"\n  [Этап 3] Итоговый конспект модуля → {module_summary_path}")

    if SKIP_EXISTING and module_summary_path.exists():
        log("  [Этап 3] [пропуск] Уже существует.")
        return

    # Читаем clean-файлы (они уже созданы на этапе 2)
    all_clean = ""
    for v in videos:
        content = read_file(v.clean_path)
        if content:
            all_clean += (
                f"\n\n{'─'*40}\n"
                f"## Видео {v.video_num}: {v.video_title}\n"
                f"{'─'*40}\n\n"
                f"{content}"
            )

    if not all_clean:
        log("  [Этап 3] Нет clean-файлов, пропускаю.")
        return

    prompt = MODULE_SUMMARY_PROMPT_TEMPLATE.format(
        module_name=module_dir.name,
        all_clean_summaries=all_clean,
    )

    log(f"  [Этап 3] Размер промпта: {len(prompt)} символов")
    log("  [Этап 3] Запускаю Claude...")

    try:
        result = run_claude(prompt, SYSTEM_PROMPT_WRITER)
        save_file(str(module_summary_path), result)
        log(f"  [Этап 3] Сохранён! ({len(result)} символов)")
    except RuntimeError as e:
        log(f"  [Этап 3] [ошибка] {e}")


def process_module(module_dir: Path, module_idx: int, total_modules: int):
    module_name = module_dir.name
    log(f"\n{'='*60}")
    log(f"[Модуль {module_idx}/{total_modules}] {module_name}")
    log(f"{'='*60}")

    videos = find_videos_in_module(module_dir)
    if not videos:
        log(f"  summary-файлы не найдены, пропускаю.")
        return

    log(f"  Найдено видео: {len(videos)}")

    # Проверяем есть ли хоть один файл требующий обработки
    need_processing = [v for v in videos
                       if not (SKIP_EXISTING and os.path.exists(v.clean_path))]

    if not need_processing:
        log("  Все clean-файлы уже существуют, пропускаю Этап 1 и 2.")
        # Сразу к итоговому конспекту
        step3_module_summary(module_dir, videos, videos[0].module_num)
        return

    log(f"  Требуют обработки: {len(need_processing)} из {len(videos)}")

    # ── Этап 1: один запрос — извлекаем релевантный текст для всех видео ──
    try:
        extracted = step1_extract(videos, module_dir)
    except RuntimeError as e:
        log(f"  [ошибка] Этап 1 провалился: {e}")
        return

    # ── Этап 2: для каждого видео делаем конспект из маленького куска ──
    log("")
    for video in videos:
        if SKIP_EXISTING and os.path.exists(video.clean_path):
            log(f"  [Этап 2] Видео {video.video_num}: [пропуск] уже существует.")
            continue

        extracted_text = extracted.get(video.video_num, "")
        if not extracted_text:
            log(f"  [Этап 2] Видео {video.video_num}: [ошибка] нет данных в JSON.")
            continue

        try:
            result = step2_write(video, extracted_text)
            save_file(video.clean_path, result)
        except RuntimeError as e:
            log(f"           [ошибка] {e}")

    # ── Этап 3: итоговый конспект по модулю ──
    step3_module_summary(module_dir, videos, videos[0].module_num)


def main():
    summary_dir = sys.argv[1] if len(sys.argv) > 1 else SUMMARY_DIR

    if not os.path.isdir(summary_dir):
        print(f"Директория не найдена: {summary_dir}")
        print("Использование: python refine_summaries.py [claude-summary/]")
        sys.exit(1)

    print(f"Источник саммари:        {summary_dir}")
    print(f"Claude CLI:              {CLAUDE_CMD}")
    print(f"Пропускать существующие: {SKIP_EXISTING}")
    print(f"Таймаут:                 {TIMEOUT} сек", flush=True)

    module_dirs = find_module_dirs(summary_dir)
    if not module_dirs:
        print("Папки модулей не найдены.")
        sys.exit(1)

    print(f"\nНайдено модулей: {len(module_dirs)}", flush=True)

    for idx, module_dir in enumerate(module_dirs, 1):
        try:
            process_module(module_dir, idx, len(module_dirs))
        except KeyboardInterrupt:
            print("\nПрервано пользователем.")
            break
        except Exception as e:
            log(f"\n[ошибка] Неожиданная ошибка в модуле {module_dir.name}: {e}")

    print(f"\n{'='*60}")
    print("Готово!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()